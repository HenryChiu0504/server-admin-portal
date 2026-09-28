from __future__ import annotations

import asyncio
import fcntl
import functools
import hashlib
import json
import os
import pwd
import re
import secrets
import signal
import sqlite3
import struct
import subprocess
import termios
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import psutil
from fastapi import FastAPI, Form, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

BASE_DIR = Path(__file__).resolve().parent
PROJECT_DIR = BASE_DIR.parent
TOOLS_DIR = PROJECT_DIR / "tools"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SESSION_SECRET = os.environ.get("SESSION_SECRET") or secrets.token_urlsafe(48)
BASE_PATH = os.environ.get("BASE_PATH", "/tool").rstrip("/") or ""
DEFAULT_LINUX_PASSWORD = os.environ.get("DEFAULT_LINUX_PASSWORD", "").strip()

app = FastAPI(title="Server Admin Portal", docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, same_site="lax", https_only=False)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")


def static_version() -> str:
    # Cache-busting tag for static URLs: changes whenever any static file
    # changes, so browsers fetch the new CSS/JS right after an update.
    digest = hashlib.sha1()
    for path in sorted((BASE_DIR / "static").rglob("*")):
        if path.is_file():
            digest.update(path.name.encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()[:10]


templates.env.globals["asset_version"] = static_version()


def run(
    cmd: list[str], timeout: int = 30, input_text: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            cmd,
            text=True,
            input=input_text,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
            env={**os.environ, **env} if env else None,
        )
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        return subprocess.CompletedProcess(cmd, 124, f"{partial}\n指令逾時（{timeout} 秒）：{' '.join(cmd)}".strip())


async def in_thread(func, *args, **kwargs):
    # asyncio.to_thread() needs Python 3.9; Ubuntu 20.04 ships Python 3.8.
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception):
    # Surface the cause in the UI instead of a bare "HTTP 500".
    return JSONResponse({"detail": f"{type(exc).__name__}: {exc}"}, status_code=500)


def sse_command(cmd: list[str]) -> StreamingResponse:
    async def stream():
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        loop = asyncio.get_running_loop()
        assert proc.stdout is not None
        while True:
            line = await loop.run_in_executor(None, proc.stdout.readline)
            if line:
                yield "data: " + json.dumps({"type": "line", "text": line.rstrip("\n")}, ensure_ascii=False) + "\n\n"
                continue
            if proc.poll() is not None:
                break
            await asyncio.sleep(0.05)
        code = await loop.run_in_executor(None, proc.wait)
        yield "data: " + json.dumps({"type": "done", "ok": code == 0, "code": code}, ensure_ascii=False) + "\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def require_auth(request: Request) -> None:
    if not request.session.get("authenticated"):
        raise HTTPException(status_code=401, detail="請先登入管理介面")


def require_root() -> None:
    if os.geteuid() != 0:
        raise HTTPException(status_code=500, detail="後端服務必須以 root 執行系統管理功能")


def tailscale_installed() -> bool:
    return run(["bash", "-lc", "command -v tailscale >/dev/null 2>&1"]).returncode == 0


def tailscale_state() -> dict[str, Any]:
    if not tailscale_installed():
        return {"installed": False, "logged_in": False, "online": False}

    result = run(["tailscale", "status", "--json"], timeout=10)
    if result.returncode != 0:
        return {"installed": True, "logged_in": False, "online": False, "error": result.stdout.strip()}

    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"installed": True, "logged_in": False, "online": False, "error": "無法解析 Tailscale 狀態"}

    self_node = data.get("Self", {}) or {}
    ips = self_node.get("TailscaleIPs", []) or []
    backend_state = data.get("BackendState", "")
    logged_in = backend_state not in {"NeedsLogin", "NoState", "Stopped"}
    return {
        "installed": True,
        "logged_in": logged_in,
        "online": bool(self_node.get("Online", False)),
        "backend_state": backend_state,
        "hostname": self_node.get("HostName") or self_node.get("DNSName") or "",
        "ips": ips,
    }


def gpu_metrics() -> list[dict[str, Any]]:
    if run(["bash", "-lc", "command -v nvidia-smi >/dev/null 2>&1"]).returncode != 0:
        return []
    query = "index,name,temperature.gpu,fan.speed,memory.used,memory.total,utilization.gpu"
    result = run(["nvidia-smi", f"--query-gpu={query}", "--format=csv,noheader,nounits"], timeout=8)
    if result.returncode != 0:
        return []
    rows = []
    for line in result.stdout.strip().splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) != 7:
            continue
        idx, name, temp, fan, mem_used, mem_total, util = parts
        try:
            used = float(mem_used)
            total = float(mem_total)
            rows.append({
                "index": int(idx),
                "name": name,
                "temperature": float(temp),
                "fan": None if fan in {"N/A", "[N/A]"} else float(fan),
                "vram_used": used,
                "vram_total": total,
                "vram_percent": round((used / total * 100) if total else 0, 1),
                "utilization": float(util),
            })
        except ValueError:
            continue
    return rows


def list_normal_users() -> list[dict[str, Any]]:
    users = []
    for entry in pwd.getpwall():
        if entry.pw_uid < 1000 or entry.pw_name == "nobody":
            continue
        users.append({
            "username": entry.pw_name,
            "uid": entry.pw_uid,
            "gid": entry.pw_gid,
            "home": entry.pw_dir,
            "shell": entry.pw_shell,
        })
    return sorted(users, key=lambda x: x["uid"])


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    if not request.session.get("authenticated"):
        return RedirectResponse(f"{BASE_PATH}/login", status_code=303)
    return templates.TemplateResponse("index.html", {"request": request, "base_path": BASE_PATH, "default_linux_password": DEFAULT_LINUX_PASSWORD})


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "base_path": BASE_PATH})


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...)):
    if not ADMIN_PASSWORD:
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "伺服器尚未設定 ADMIN_PASSWORD，請先完成部署設定。", "base_path": BASE_PATH},
            status_code=500,
        )
    if not secrets.compare_digest(password, ADMIN_PASSWORD):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "密碼錯誤", "base_path": BASE_PATH}, status_code=401
        )
    request.session["authenticated"] = True
    return RedirectResponse(f"{BASE_PATH}/", status_code=303)


@app.post("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(f"{BASE_PATH}/login", status_code=303)


@app.get("/api/metrics")
async def metrics(request: Request):
    require_auth(request)
    vm = psutil.virtual_memory()
    return {
        "cpu_percent": psutil.cpu_percent(interval=0.15),
        "memory_percent": vm.percent,
        "memory_used_gb": round((vm.total - vm.available) / (1024**3), 1),
        "memory_total_gb": round(vm.total / (1024**3), 1),
        "gpus": gpu_metrics(),
    }


@app.get("/api/tailscale/status")
async def api_tailscale_status(request: Request):
    require_auth(request)
    return tailscale_state()


@app.post("/api/tailscale/install")
async def api_tailscale_install(request: Request):
    require_auth(request)
    require_root()
    if tailscale_installed():
        return {"ok": True, "message": "Tailscale 已安裝"}
    cmd = "curl -fsSL https://tailscale.com/install.sh | sh && systemctl enable --now tailscaled"
    result = run(["bash", "-lc", cmd], timeout=180)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout[-4000:])
    return {"ok": True, "message": "Tailscale 安裝完成", "log": result.stdout[-4000:]}


@app.post("/api/tailscale/login")
async def api_tailscale_login(request: Request):
    require_auth(request)
    require_root()
    if not tailscale_installed():
        raise HTTPException(status_code=400, detail="尚未安裝 Tailscale")
    run(["systemctl", "enable", "--now", "tailscaled"], timeout=30)
    # tailscale login 會等待瀏覽器授權；不能讓 HTTP request 卡死。
    # Tailscale 1.102 supports --timeout. Let the CLI ask tailscaled for an
    # authentication URL and exit by itself instead of killing the process;
    # killing `tailscale login` early can make browser hand-off unreliable.
    result = run(["tailscale", "login", "--timeout=5s"], timeout=8)
    output = (result.stdout or "").strip()
    urls = re.findall(r"https://[^\s]+", output)
    login_url = urls[0] if urls else None
    state = tailscale_state()
    return {"ok": bool(login_url) or state.get("logged_in", False), "login_url": login_url, "state": state, "log": output}


@app.post("/api/tailscale/logout")
async def api_tailscale_logout(request: Request):
    require_auth(request)
    require_root()
    result = run(["tailscale", "logout"], timeout=20)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout.strip())
    return {"ok": True, "message": "已登出 Tailscale"}



def fan_service_active() -> bool:
    result = run(["systemctl", "is-active", "--quiet", "nvidia-fan-x.service"], timeout=5)
    return result.returncode == 0


def fan_display() -> str:
    # The installer may choose a private display other than :99 when :99 is
    # already occupied by an unrelated X server. Read the persisted value on
    # every status request so the backend does not need to be restarted just
    # to learn the selected display.
    value = os.environ.get("NVIDIA_FAN_DISPLAY", "").strip()
    env_path = Path("/etc/server-admin-portal.env")
    if env_path.exists():
        try:
            for line in env_path.read_text().splitlines():
                if line.startswith("NVIDIA_FAN_DISPLAY="):
                    candidate = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if re.fullmatch(r":[0-9]+", candidate):
                        value = candidate
        except OSError:
            pass
    return value if re.fullmatch(r":[0-9]+", value) else ":99"


def fan_ready() -> bool:
    if not Path("/usr/local/libexec/nvidia-fanctl").exists():
        return False
    display = fan_display()
    # Listing GPU/Fan targets alone is not enough. Some unrelated X displays
    # expose those targets while NV-CONTROL fan attributes return Bad handle.
    gpus = run(["nvidia-settings", "-c", display, "-q", "gpus"], timeout=5)
    if gpus.returncode != 0:
        return False
    fans = run(["nvidia-settings", "-c", display, "-q", "fans"], timeout=5)
    if fans.returncode != 0:
        return False
    control = run(["nvidia-settings", "-c", display, "-q", "[gpu:0]/GPUFanControlState", "-t"], timeout=5)
    return control.returncode == 0 and bool(control.stdout.strip())


def fan_mode() -> str:
    if not Path("/usr/local/libexec/nvidia-fanctl").exists():
        return "unavailable"
    result = run(["nvidia-settings", "-c", fan_display(), "-q", "[gpu:0]/GPUFanControlState", "-t"], timeout=5)
    if result.returncode != 0:
        return "unknown"
    return "manual" if result.stdout.strip().splitlines()[-1:] == ["1"] else "auto"


@app.get("/api/fan/status")
async def api_fan_status(request: Request):
    require_auth(request)
    installed = Path("/usr/local/libexec/nvidia-fanctl").exists()
    service_active = fan_service_active() if installed else False
    ready = fan_ready() if installed else False
    return {
        "installed": installed,
        "ready": ready,
        "service_active": service_active,
        "display": fan_display() if installed else None,
        "mode": fan_mode() if ready else "unknown",
        "gpus": gpu_metrics(),
    }


@app.post("/api/fan/service/restart")
async def api_fan_service_restart(request: Request):
    require_auth(request)
    require_root()
    if not Path("/usr/local/libexec/nvidia-fanctl").exists():
        raise HTTPException(status_code=400, detail="尚未安裝 NVIDIA fan control 元件")

    # Functional readiness is authoritative.  A usable :99 X server may have
    # been started outside systemd, so never kill a working display merely
    # because systemctl reports inactive.
    if fan_ready():
        return {"ok": True, "message": "NVIDIA fan control 已就緒，無需重新啟動"}

    run(["systemctl", "daemon-reload"], timeout=10)
    # --no-block: a start job stuck behind an unfinished boot target would
    # otherwise hang this request. Use /api/fan/recover-stream for that case.
    result = run(["systemctl", "start", "--no-block", "nvidia-fan-x.service"], timeout=20)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout.strip() or "無法啟動 nvidia-fan-x.service")

    for _ in range(20):
        if fan_ready():
            return {"ok": True, "message": "NVIDIA fan control 已就緒"}
        await asyncio.sleep(0.5)

    status = run(["systemctl", "status", "nvidia-fan-x.service", "--no-pager"], timeout=8)
    raise HTTPException(status_code=500, detail=(status.stdout or "服務已啟動，但 :99 尚未就緒")[-4000:])


@app.post("/api/fan/install")
async def api_fan_install(request: Request):
    require_auth(request)
    require_root()
    result = run([str(TOOLS_DIR / "fan_install.sh")], timeout=240)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout[-4000:])
    return {"ok": True, "message": "風扇控制軟體安裝完成", "log": result.stdout[-4000:]}


@app.get("/api/fan/install-stream")
async def api_fan_install_stream(request: Request):
    require_auth(request)
    require_root()
    return sse_command([str(TOOLS_DIR / "fan_install.sh")])


@app.get("/api/fan/recover-stream")
async def api_fan_recover_stream(request: Request):
    require_auth(request)
    require_root()
    if not Path("/usr/local/libexec/nvidia-fanctl").exists():
        raise HTTPException(status_code=400, detail="尚未安裝 NVIDIA fan control 元件")
    return sse_command(["bash", str(TOOLS_DIR / "fan_recover.sh")])


@app.post("/api/fan/auto")
async def api_fan_auto(request: Request):
    require_auth(request)
    require_root()
    result = run(["/usr/local/libexec/nvidia-fanctl", "auto"], timeout=15)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout.strip())
    return {"ok": True, "message": "已切換為 Auto", "log": result.stdout}


@app.post("/api/fan/set/{speed}")
async def api_fan_set(speed: int, request: Request):
    require_auth(request)
    require_root()
    if speed < 50 or speed > 95:
        raise HTTPException(status_code=400, detail="風扇手動速度只允許 50–95%")
    result = run(["/usr/local/libexec/nvidia-fanctl", str(speed)], timeout=15)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout.strip())
    return {"ok": True, "message": f"風扇已設定為 {speed}%", "log": result.stdout}


PKG_UNIT = "server-admin-portal-pkg-upgrade.service"
PKG_LOG = Path("/var/log/server-admin-portal/pkg-upgrade.log")


def unit_running(unit: str) -> bool:
    state = run(["systemctl", "is-active", unit], timeout=5).stdout.strip()
    return state in {"active", "activating", "reloading"}


def pkg_upgrade_running() -> bool:
    return unit_running(PKG_UNIT)


def start_detached(unit: str, log: Path, cmd: list[str], env: dict[str, str]) -> None:
    # systemd-run detaches the job from this HTTP request / backend process so
    # a closed browser tab or a Portal restart never interrupts it.
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("")
    result = run(
        ["systemd-run", f"--unit={unit}", "--collect", "--quiet"]
        + [f"--setenv={k}={v}" for k, v in env.items()]
        + cmd,
        timeout=15,
    )
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout.strip() or f"無法啟動 {unit}")


def read_job_log(log: Path, offset: int) -> dict[str, Any]:
    data = b""
    if log.exists():
        with log.open("rb") as f:
            f.seek(max(0, offset))
            data = f.read(256 * 1024)
    # Only hand out complete lines so multi-byte characters are never split.
    end = data.rfind(b"\n") + 1
    text = data[:end].decode("utf-8", errors="replace")
    exit_code = None
    match = re.search(r"^__EXIT__ (\d+)$", text, flags=re.M)
    if match:
        exit_code = int(match.group(1))
        text = re.sub(r"^__EXIT__ \d+\n?", "", text, flags=re.M)
    return {"text": text, "offset": max(0, offset) + end, "exit_code": exit_code}


def upgradable_packages() -> list[str]:
    result = run(["apt", "list", "--upgradable"], timeout=60)
    return [line.split("/", 1)[0] for line in result.stdout.splitlines() if "/" in line and "[upgradable" in line]


@app.get("/api/packages/status")
async def api_packages_status(request: Request):
    require_auth(request)
    packages = await in_thread(upgradable_packages)
    return {
        "running": pkg_upgrade_running(),
        "upgradable": packages,
        "reboot_required": Path("/var/run/reboot-required").exists(),
    }


@app.post("/api/packages/refresh")
async def api_packages_refresh(request: Request):
    require_auth(request)
    require_root()
    if pkg_upgrade_running():
        raise HTTPException(status_code=409, detail="套件更新進行中，請稍後再試")
    result = await in_thread(run, ["apt-get", "update", "-o", "DPkg::Lock::Timeout=60"], 180)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout[-4000:])
    packages = await in_thread(upgradable_packages)
    return {"ok": True, "message": f"共有 {len(packages)} 個套件可更新", "upgradable": packages}


@app.post("/api/packages/upgrade")
async def api_packages_upgrade(request: Request):
    require_auth(request)
    require_root()
    body = await request.json()
    keep_nvidia = bool(body.get("keep_nvidia", True))
    if pkg_upgrade_running():
        raise HTTPException(status_code=409, detail="套件更新已在進行中")
    start_detached(
        PKG_UNIT, PKG_LOG, ["/bin/bash", str(TOOLS_DIR / "pkg_upgrade.sh")],
        {"KEEP_NVIDIA": "1" if keep_nvidia else "0", "PKG_UPGRADE_LOG": str(PKG_LOG)},
    )
    return {"ok": True, "message": "已開始更新套件"}


@app.get("/api/packages/log")
async def api_packages_log(request: Request, offset: int = 0):
    require_auth(request)
    return {
        **read_job_log(PKG_LOG, offset),
        "running": pkg_upgrade_running(),
        "reboot_required": Path("/var/run/reboot-required").exists(),
    }


PORTAL_UNIT = "server-admin-portal-self-update.service"
PORTAL_LOG = Path("/var/log/server-admin-portal/portal-update.log")
PORTAL_REPO_URL = "https://github.com/HenryChiu0504/server-admin-portal"


def portal_git(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    # The backend runs as root while the checkout usually belongs to the admin
    # user. Older git (e.g. Ubuntu 22.04's 2.34 backport) ignores
    # `-c safe.directory`, so on "dubious ownership" register the exception in
    # the system config (as git's own hint suggests) and retry once.
    cmd = ["git", "-C", str(PROJECT_DIR), "-c", f"safe.directory={PROJECT_DIR}", *args]
    # Never wait for a password / host-key prompt that nobody can answer.
    env = {"GIT_TERMINAL_PROMPT": "0", "GIT_SSH_COMMAND": "ssh -o BatchMode=yes -o ConnectTimeout=15"}
    result = run(cmd, timeout=timeout, env=env)
    if result.returncode != 0 and "dubious ownership" in result.stdout:
        run(["git", "config", "--system", "--add", "safe.directory", str(PROJECT_DIR)], timeout=5)
        result = run(cmd, timeout=timeout, env=env)
    return result


def portal_repo_url() -> str:
    url = portal_git("remote", "get-url", "origin", timeout=5).stdout.strip()
    match = re.fullmatch(r"(?:https://|git@)github\.com[/:]([\w.-]+/[\w.-]+?)(?:\.git)?/?", url)
    return f"https://github.com/{match.group(1)}" if match else PORTAL_REPO_URL


def portal_commit(ref: str) -> dict[str, str] | None:
    result = portal_git("log", "-1", "--date=short", "--format=%h%x09%cd%x09%s", ref, timeout=5)
    if result.returncode != 0 or not result.stdout.strip():
        return None
    short, date, subject = (result.stdout.strip().split("\t", 2) + ["", ""])[:3]
    return {"hash": short, "date": date, "subject": subject}


@app.get("/api/portal/about")
async def api_portal_about(request: Request):
    require_auth(request)
    is_git = portal_git("rev-parse", "--is-inside-work-tree", timeout=5).stdout.strip() == "true"
    return {
        "repo_url": portal_repo_url() if is_git else PORTAL_REPO_URL,
        "git": is_git,
        "branch": portal_git("rev-parse", "--abbrev-ref", "HEAD", timeout=5).stdout.strip() if is_git else None,
        "current": portal_commit("HEAD") if is_git else None,
        "updating": unit_running(PORTAL_UNIT),
    }


def portal_update_info() -> dict[str, Any]:
    probe = portal_git("rev-parse", "--is-inside-work-tree", timeout=5)
    if probe.stdout.strip() != "true":
        if not (PROJECT_DIR / ".git").exists():
            raise HTTPException(status_code=400, detail=f"{PROJECT_DIR} 不是 Git clone，無法線上更新；請參考 README 重新以 git clone 部署")
        raise HTTPException(status_code=500, detail="無法讀取 Git 版本資訊：" + probe.stdout.strip()[-1000:])
    fetch = portal_git("fetch", "--quiet", "origin", timeout=60)
    if fetch.returncode != 0:
        raise HTTPException(status_code=502, detail="無法連線 GitHub：" + fetch.stdout.strip()[-1000:])
    upstream = portal_git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", timeout=5).stdout.strip() or "origin/main"
    behind = portal_git("rev-list", "--count", f"HEAD..{upstream}", timeout=5).stdout.strip()
    log = portal_git("log", "--format=%h %s", f"HEAD..{upstream}", "-20", timeout=5).stdout.strip()
    count = int(behind) if behind.isdigit() else 0
    info = {
        "update_available": count > 0,
        "behind": count,
        "current": portal_commit("HEAD"),
        "latest": portal_commit(upstream),
        "changes": log.splitlines() if log else [],
        "checked_at": time.time(),
    }
    portal_update_cache.update(info=info, at=time.time())
    return info


portal_update_cache: dict[str, Any] = {"info": None, "at": 0.0}
PORTAL_UPDATE_TTL = 6 * 3600


@app.post("/api/portal/check-update")
async def api_portal_check_update(request: Request):
    require_auth(request)
    require_root()
    return await in_thread(portal_update_info)


@app.get("/api/portal/update-status")
async def api_portal_update_status(request: Request):
    # For the dashboard banner: answer from cache, re-checking GitHub at most
    # every PORTAL_UPDATE_TTL. Failures are reported quietly, never raised.
    require_auth(request)
    if portal_update_cache["info"] is None or time.time() - portal_update_cache["at"] > PORTAL_UPDATE_TTL:
        try:
            await in_thread(portal_update_info)
        except HTTPException as exc:
            portal_update_cache.update(info={"update_available": False, "error": exc.detail}, at=time.time())
    return portal_update_cache["info"]


@app.post("/api/portal/update")
async def api_portal_update(request: Request):
    require_auth(request)
    require_root()
    if unit_running(PORTAL_UNIT):
        raise HTTPException(status_code=409, detail="Portal 更新已在進行中")
    start_detached(
        PORTAL_UNIT, PORTAL_LOG, ["/bin/bash", str(TOOLS_DIR / "portal_update.sh")],
        {"PORTAL_UPDATE_LOG": str(PORTAL_LOG)},
    )
    return {"ok": True, "message": "已開始更新 Server Admin Portal"}


@app.get("/api/portal/update/log")
async def api_portal_update_log(request: Request, offset: int = 0):
    require_auth(request)
    return {**read_job_log(PORTAL_LOG, offset), "running": unit_running(PORTAL_UNIT)}


def websocket_same_origin(websocket: WebSocket) -> bool:
    origin = websocket.headers.get("origin")
    if not origin:
        return True
    # Compare host names only: the Docker Apache / nginx chain rewrites ports.
    host = (websocket.headers.get("x-forwarded-host") or websocket.headers.get("host") or "").split(",")[0].strip()
    return urlsplit(origin).hostname == urlsplit(f"//{host}").hostname


@app.websocket("/api/terminal/ws")
async def terminal_ws(websocket: WebSocket):
    if not websocket.session.get("authenticated") or not websocket_same_origin(websocket):
        await websocket.close(code=4401)
        return
    if os.geteuid() != 0:
        await websocket.close(code=4500)
        return
    await websocket.accept()

    master, slave = os.openpty()
    # /bin/login asks for a Linux account and password, so every person gets a
    # shell as their own user instead of sharing the backend's root identity.
    # `setsid -c` makes the pty the controlling terminal of the new session.
    proc = subprocess.Popen(
        ["setsid", "-c", "/bin/login"],
        stdin=slave, stdout=slave, stderr=slave,
        env={"TERM": "xterm-256color", "LANG": os.environ.get("LANG", "C.UTF-8"), "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
        close_fds=True,
    )
    os.close(slave)

    loop = asyncio.get_running_loop()
    output: asyncio.Queue[bytes | None] = asyncio.Queue()

    def on_readable() -> None:
        try:
            data = os.read(master, 65536)
        except OSError:
            data = b""
        if not data:
            loop.remove_reader(master)
        output.put_nowait(data or None)

    loop.add_reader(master, on_readable)

    async def pump_output() -> None:
        try:
            while (data := await output.get()) is not None:
                await websocket.send_bytes(data)
            # login/shell exited: end the session from the server side.
            await websocket.close()
        except (WebSocketDisconnect, RuntimeError):
            pass

    sender = asyncio.create_task(pump_output())
    try:
        while True:
            msg = json.loads(await websocket.receive_text())
            if msg.get("type") == "input":
                os.write(master, str(msg.get("data", "")).encode())
            elif msg.get("type") == "resize":
                rows = max(1, min(500, int(msg.get("rows", 24))))
                cols = max(1, min(1000, int(msg.get("cols", 80))))
                fcntl.ioctl(master, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except (WebSocketDisconnect, RuntimeError, OSError, ValueError, AttributeError):
        pass
    finally:
        sender.cancel()
        loop.remove_reader(master)
        try:
            os.killpg(proc.pid, signal.SIGHUP)
        except ProcessLookupError:
            pass
        os.close(master)
        try:
            await in_thread(proc.wait, 5)
        except subprocess.TimeoutExpired:
            proc.kill()
            await in_thread(proc.wait)


# ---------------------------------------------------------------------------
# GPU processes: who is using which GPU
# ---------------------------------------------------------------------------

def container_of(pid: int) -> str | None:
    """Label a process that runs inside Docker or Kubernetes, else None."""
    try:
        cgroup = Path(f"/proc/{pid}/cgroup").read_text()
    except OSError:
        return None
    kind = "K8s" if "kubepods" in cgroup else "Docker" if re.search(r"docker|containerd", cgroup) else None
    if not kind:
        return None
    # Inside a container HOSTNAME is the pod / container name.
    try:
        env = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
        for item in env:
            if item.startswith(b"HOSTNAME="):
                return f"{kind} · {item[9:].decode(errors='replace')}"
    except OSError:
        pass
    return kind


def gpu_processes() -> list[dict[str, Any]]:
    if run(["bash", "-lc", "command -v nvidia-smi >/dev/null 2>&1"]).returncode != 0:
        return []
    ids = run(["nvidia-smi", "--query-gpu=index,uuid", "--format=csv,noheader"], timeout=8)
    index_of = {}
    for line in ids.stdout.splitlines():
        parts = [x.strip() for x in line.split(",")]
        if len(parts) == 2 and parts[0].isdigit():
            index_of[parts[1]] = int(parts[0])
    apps = run(
        ["nvidia-smi", "--query-compute-apps=gpu_uuid,pid,used_memory,process_name", "--format=csv,noheader,nounits"],
        timeout=8,
    )
    rows = []
    for line in apps.stdout.splitlines():
        parts = [x.strip() for x in line.split(",", 3)]
        if len(parts) != 4 or not parts[1].isdigit():
            continue
        uuid, pid_s, mem, name = parts
        pid = int(pid_s)
        row: dict[str, Any] = {
            "gpu": index_of.get(uuid),
            "pid": pid,
            "vram_mb": float(mem) if mem.replace(".", "", 1).isdigit() else None,
            "name": name,
            "user": None,
            "started": None,
            "command": name,
            "container": container_of(pid),
        }
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                row["user"] = proc.username()
                row["started"] = proc.create_time()
                row["command"] = " ".join(proc.cmdline())[:400] or name
        except (psutil.Error, KeyError):
            pass
        rows.append(row)
    return sorted(rows, key=lambda r: (r["gpu"] if r["gpu"] is not None else 99, -(r["vram_mb"] or 0)))


@app.get("/api/gpu/processes")
async def api_gpu_processes(request: Request):
    require_auth(request)
    gpus, procs = await asyncio.gather(in_thread(gpu_metrics), in_thread(gpu_processes))
    return {"gpus": gpus, "processes": procs, "now": time.time()}


@app.post("/api/gpu/processes/{pid}/kill")
async def api_gpu_process_kill(pid: int, request: Request):
    require_auth(request)
    require_root()
    body = await request.json()
    force = bool(body.get("force"))
    # Only processes currently on a GPU may be signalled from here.
    target = next((p for p in await in_thread(gpu_processes) if p["pid"] == pid), None)
    if target is None:
        raise HTTPException(status_code=404, detail=f"PID {pid} 目前沒有使用 GPU")
    try:
        os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
    except ProcessLookupError:
        raise HTTPException(status_code=404, detail=f"PID {pid} 已結束")
    who = target["user"] or "?"
    return {"ok": True, "message": f"已送出 {'SIGKILL' if force else 'SIGTERM'} 給 PID {pid}（{who} · GPU {target['gpu']}）"}


# ---------------------------------------------------------------------------
# Metric history (sampled every minute into SQLite, kept 7 days)
# ---------------------------------------------------------------------------

DATA_DIR = Path(os.environ.get("PORTAL_DATA_DIR", "/var/lib/server-admin-portal"))
HISTORY_DB = DATA_DIR / "metrics.db"
HISTORY_INTERVAL = 60
HISTORY_KEEP = 7 * 24 * 3600
HISTORY_RANGES = {"1h": 3600, "6h": 6 * 3600, "24h": 24 * 3600, "7d": 7 * 24 * 3600}
HISTORY_METRICS = {"temperature": "temp", "utilization": "util", "fan": "fan", "vram": "vram"}


def history_db() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(HISTORY_DB), timeout=10)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE IF NOT EXISTS gpu (ts INTEGER, gpu INTEGER, temp REAL, util REAL, fan REAL, vram REAL)")
    db.execute("CREATE INDEX IF NOT EXISTS gpu_ts ON gpu (ts)")
    db.execute("CREATE TABLE IF NOT EXISTS sys (ts INTEGER PRIMARY KEY, cpu REAL, mem REAL)")
    return db


def record_sample(db: sqlite3.Connection) -> None:
    now = int(time.time())
    db.executemany(
        "INSERT INTO gpu VALUES (?, ?, ?, ?, ?, ?)",
        [(now, g["index"], g["temperature"], g["utilization"], g["fan"], g["vram_percent"]) for g in gpu_metrics()],
    )
    db.execute("INSERT OR REPLACE INTO sys VALUES (?, ?, ?)", (now, psutil.cpu_percent(interval=1), psutil.virtual_memory().percent))
    db.execute("DELETE FROM gpu WHERE ts < ?", (now - HISTORY_KEEP,))
    db.execute("DELETE FROM sys WHERE ts < ?", (now - HISTORY_KEEP,))
    db.commit()


def background_loop() -> None:
    db = None
    while True:
        started = time.time()
        try:
            db = db or history_db()
            record_sample(db)
        except Exception as exc:  # keep sampling even if one round fails
            print(f"[history] sample failed: {exc}", flush=True)
            db = None
        try:
            maybe_nightly_disk_scan()
        except Exception as exc:
            print(f"[disk-scan] scheduler failed: {exc}", flush=True)
        time.sleep(max(1.0, HISTORY_INTERVAL - (time.time() - started)))


@app.on_event("startup")
async def start_background_loop() -> None:
    threading.Thread(target=background_loop, name="portal-background", daemon=True).start()


@app.get("/api/history")
async def api_history(request: Request, metric: str = "temperature", range: str = "24h"):
    require_auth(request)
    span = HISTORY_RANGES.get(range)
    if span is None or (metric not in HISTORY_METRICS and metric != "system"):
        raise HTTPException(status_code=400, detail="不支援的圖表參數")
    # About 300 points per chart regardless of the range.
    bucket = max(HISTORY_INTERVAL, span // 300)
    since = int(time.time()) - span

    def query() -> dict[str, Any]:
        db = history_db()
        try:
            if metric == "system":
                rows = db.execute(
                    "SELECT ts / ? * ? AS b, AVG(cpu), AVG(mem) FROM sys WHERE ts >= ? GROUP BY b ORDER BY b",
                    (bucket, bucket, since),
                ).fetchall()
                return {"series": [
                    {"key": "cpu", "label": "CPU", "points": [[r[0], r[1]] for r in rows]},
                    {"key": "mem", "label": "Memory", "points": [[r[0], r[2]] for r in rows]},
                ]}
            col = HISTORY_METRICS[metric]
            rows = db.execute(
                f"SELECT gpu, ts / ? * ? AS b, AVG({col}) FROM gpu WHERE ts >= ? GROUP BY gpu, b ORDER BY gpu, b",
                (bucket, bucket, since),
            ).fetchall()
            series: dict[int, list[list[float]]] = {}
            for gpu, b, value in rows:
                series.setdefault(gpu, []).append([b, value])
            return {"series": [{"key": f"gpu{g}", "label": f"GPU {g}", "points": pts} for g, pts in sorted(series.items())]}
        finally:
            db.close()

    data = await in_thread(query)
    return {**data, "metric": metric, "range": range, "since": since, "until": int(time.time()), "bucket": bucket}


# ---------------------------------------------------------------------------
# Disks: free space per disk, and space used per user on each disk
# ---------------------------------------------------------------------------

DISK_SKIP_FS = {"tmpfs", "devtmpfs", "squashfs", "overlay", "ramfs", "efivarfs", "iso9660", "nsfs", "autofs"}
DISK_SKIP_PREFIX = ("/snap", "/boot/efi", "/var/lib/docker", "/var/lib/kubelet", "/var/lib/containers", "/run")
DISK_USAGE_FILE = DATA_DIR / "disk-usage.json"
disk_scan_state: dict[str, Any] = {"running": False, "current": None, "files": 0, "started": None, "error": None}
disk_scan_lock = threading.Lock()


def list_disks() -> list[dict[str, Any]]:
    by_device: dict[str, Any] = {}
    for part in psutil.disk_partitions(all=False):
        if part.fstype in DISK_SKIP_FS or part.mountpoint.startswith(DISK_SKIP_PREFIX):
            continue
        # Bind mounts repeat a device; keep its shortest mount point.
        prev = by_device.get(part.device)
        if prev is None or len(part.mountpoint) < len(prev.mountpoint):
            by_device[part.device] = part
    disks = []
    for part in sorted(by_device.values(), key=lambda p: p.mountpoint):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except OSError:
            continue
        disks.append({
            "mount": part.mountpoint, "device": part.device, "fstype": part.fstype,
            "total": usage.total, "used": usage.used, "free": usage.free, "percent": usage.percent,
        })
    return disks


def uid_name(uid: int) -> str:
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid {uid}"


def run_disk_scan() -> None:
    started = time.time()
    result: dict[str, Any] = {"scanned_at": None, "duration": None, "disks": {}}
    try:
        for disk in list_disks():
            mount = disk["mount"]
            disk_scan_state["current"] = mount
            usage: dict[int, int] = {}
            # -xdev: stay on this filesystem; idle I/O priority so users are not slowed down.
            proc = subprocess.Popen(
                ["ionice", "-c3", "nice", "-n19", "find", mount, "-xdev", "-printf", "%U %b\n"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                uid, _, blocks = line.partition(" ")
                try:
                    usage[int(uid)] = usage.get(int(uid), 0) + int(blocks) * 512
                except ValueError:
                    continue
                disk_scan_state["files"] += 1
            proc.wait()
            result["disks"][mount] = {
                "users": sorted(
                    ({"uid": uid, "user": uid_name(uid), "bytes": size} for uid, size in usage.items()),
                    key=lambda u: -u["bytes"],
                ),
            }
        result["scanned_at"] = time.time()
        result["duration"] = round(time.time() - started)
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = DISK_USAGE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(result))
        tmp.replace(DISK_USAGE_FILE)
    except Exception as exc:
        disk_scan_state["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        disk_scan_state.update(running=False, current=None)
        disk_scan_lock.release()


def start_disk_scan() -> bool:
    if not disk_scan_lock.acquire(blocking=False):
        return False
    disk_scan_state.update(running=True, current=None, files=0, started=time.time(), error=None)
    threading.Thread(target=run_disk_scan, name="disk-scan", daemon=True).start()
    return True


def load_disk_usage() -> dict[str, Any] | None:
    try:
        return json.loads(DISK_USAGE_FILE.read_text())
    except (OSError, ValueError):
        return None


def maybe_nightly_disk_scan() -> None:
    # Refresh per-user usage once a night (03:00-03:59 server time).
    if time.localtime().tm_hour != 3 or os.geteuid() != 0:
        return
    last = (load_disk_usage() or {}).get("scanned_at") or 0
    if time.time() - last > 20 * 3600:
        start_disk_scan()


@app.get("/api/disks")
async def api_disks(request: Request):
    require_auth(request)
    return {
        "disks": await in_thread(list_disks),
        "usage": load_disk_usage(),
        "scan": {**disk_scan_state},
    }


@app.post("/api/disks/scan")
async def api_disks_scan(request: Request):
    require_auth(request)
    require_root()
    if not start_disk_scan():
        raise HTTPException(status_code=409, detail="硬碟掃描已在進行中")
    return {"ok": True, "message": "已開始掃描各使用者的硬碟用量（低優先權，不影響其他工作）"}


@app.get("/api/users")
async def api_users(request: Request):
    require_auth(request)
    return {"users": list_normal_users()}


@app.post("/api/users")
async def api_create_user(request: Request):
    require_auth(request)
    require_root()
    body = await request.json()
    username = str(body.get("username", "")).strip()
    password = str(body.get("password", ""))
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", username):
        raise HTTPException(status_code=400, detail="使用者名稱格式不合法")
    if not password:
        raise HTTPException(status_code=400, detail="請輸入密碼")
    if run(["id", username]).returncode == 0:
        raise HTTPException(status_code=409, detail="使用者已存在")
    result = run(["useradd", "-m", "-s", "/bin/bash", username], timeout=20)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout.strip())
    passwd_result = run(["chpasswd"], timeout=20, input_text=f"{username}:{password}\n")
    if passwd_result.returncode != 0:
        run(["userdel", "-r", username], timeout=20)
        raise HTTPException(status_code=500, detail=passwd_result.stdout.strip())
    return {"ok": True, "message": f"使用者 {username} 建立完成"}


@app.post("/api/users/{username}/reset-password")
async def api_reset_user_password(username: str, request: Request):
    require_auth(request)
    require_root()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", username) or run(["id", username]).returncode != 0:
        raise HTTPException(status_code=404, detail="找不到使用者")
    if not DEFAULT_LINUX_PASSWORD:
        raise HTTPException(status_code=400, detail="尚未設定預設 Linux 使用者密碼")
    result = run(["chpasswd"], timeout=20, input_text=f"{username}:{DEFAULT_LINUX_PASSWORD}\n")
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout.strip())
    return {"ok": True, "message": f"{username} 密碼已還原為預設值"}


@app.post("/api/admin/change-password")
async def api_change_admin_password(request: Request):
    global ADMIN_PASSWORD
    require_auth(request)
    require_root()
    body = await request.json()
    current = str(body.get("current_password", ""))
    new = str(body.get("new_password", ""))
    confirm = str(body.get("confirm_password", ""))
    if not secrets.compare_digest(current, ADMIN_PASSWORD):
        raise HTTPException(status_code=400, detail="目前管理密碼錯誤")
    if not new:
        raise HTTPException(status_code=400, detail="新密碼不可空白")
    if new != confirm:
        raise HTTPException(status_code=400, detail="兩次新密碼不一致")
    env_path = Path("/etc/server-admin-portal.env")
    text = env_path.read_text() if env_path.exists() else ""
    line = f"ADMIN_PASSWORD={new}"
    if re.search(r"^ADMIN_PASSWORD=.*$", text, flags=re.M):
        text = re.sub(r"^ADMIN_PASSWORD=.*$", line, text, flags=re.M)
    else:
        text = line + "\n" + text
    env_path.write_text(text)
    os.chmod(env_path, 0o600)
    ADMIN_PASSWORD = new
    return {"ok": True, "message": "管理介面登入密碼已更新"}


@app.delete("/api/users/{username}")
async def api_delete_user(username: str, request: Request):
    require_auth(request)
    require_root()
    if not re.fullmatch(r"[a-z_][a-z0-9_-]*", username):
        raise HTTPException(status_code=400, detail="使用者名稱格式不合法")
    if username in {"root", os.environ.get("SUDO_USER", "")}: 
        raise HTTPException(status_code=400, detail="禁止刪除此管理帳號")
    result = run(["userdel", "-r", username], timeout=30)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout.strip())
    return {"ok": True, "message": f"使用者 {username} 已刪除"}


@app.get("/healthz")
async def healthz():
    return JSONResponse({"ok": True})
