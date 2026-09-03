from __future__ import annotations

import asyncio
import json
import os
import pwd
import re
import secrets
import subprocess
from pathlib import Path
from typing import Any

import psutil
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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


def run(cmd: list[str], timeout: int = 30, input_text: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        text=True,
        input=input_text,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=timeout,
        check=False,
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



def fan_mode() -> str:
    if not Path("/usr/local/libexec/nvidia-fanctl").exists():
        return "unavailable"
    result = run(["nvidia-settings", "-c", os.environ.get("NVIDIA_FAN_DISPLAY", ":99"), "-q", "[gpu:0]/GPUFanControlState", "-t"], timeout=5)
    if result.returncode != 0:
        return "unknown"
    return "manual" if result.stdout.strip().splitlines()[-1:] == ["1"] else "auto"


@app.get("/api/fan/status")
async def api_fan_status(request: Request):
    require_auth(request)
    installed = Path("/usr/local/libexec/nvidia-fanctl").exists()
    return {"installed": installed, "mode": fan_mode(), "gpus": gpu_metrics()}


@app.post("/api/fan/install")
async def api_fan_install(request: Request):
    require_auth(request)
    require_root()
    result = run([str(TOOLS_DIR / "fan_install.sh")], timeout=240)
    if result.returncode != 0:
        raise HTTPException(status_code=500, detail=result.stdout[-4000:])
    return {"ok": True, "message": "風扇控制軟體安裝完成", "log": result.stdout[-4000:]}


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
