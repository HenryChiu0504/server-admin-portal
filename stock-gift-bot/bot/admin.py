"""Admin web backend at /admin (HTTP Basic auth: ADMIN_USERNAME / ADMIN_PASSWORD)."""
import secrets
from datetime import date
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates

from . import sources
from .reminders import ASK_BOUGHT, ASK_BUY, ASK_VOTE
from .service import STATUS_ZH, VOTE_ZH, Service

KIND_ZH = {ASK_BUY: "要買嗎？", ASK_BOUGHT: "有購入了嗎？", ASK_VOTE: "投票了嗎？"}
USER_STATUS_ZH = {"pending": "待核准", "active": "使用中", "blocked": "已停用"}

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.globals.update(STATUS_ZH=STATUS_ZH, VOTE_ZH=VOTE_ZH, KIND_ZH=KIND_ZH, USER_STATUS_ZH=USER_STATUS_ZH)
basic = HTTPBasic(realm="stock-gift-bot")


def build_router(service: Service, scheduler) -> APIRouter:
    cfg, db = service.cfg, service.db
    templates.env.globals["remind_time"] = f"{cfg.remind_time:%H:%M}"

    def auth(request: Request, cred: HTTPBasicCredentials = Depends(basic)):
        if not cfg.admin_password:
            raise HTTPException(503, "後台未啟用：請在 .env 設定 ADMIN_PASSWORD")
        ok = secrets.compare_digest(cred.username.encode(), cfg.admin_username.encode()) and \
            secrets.compare_digest(cred.password.encode(), cfg.admin_password.encode())
        if not ok:
            raise HTTPException(401, headers={"WWW-Authenticate": 'Basic realm="stock-gift-bot"'})
        if request.method == "POST":  # basic CSRF guard: form must come from this site
            origin = request.headers.get("origin") or request.headers.get("referer") or ""
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                raise HTTPException(403, "bad origin")

    r = APIRouter(prefix="/admin", dependencies=[Depends(auth)])

    def page(request, name, **ctx):
        ctx.update(request=request, msg=request.query_params.get("msg", ""), today=service.today())
        return templates.TemplateResponse(request, name, ctx)

    def back(url: str, msg: str = ""):
        sep = "&" if "?" in url else "?"
        return RedirectResponse(f"{url}{sep}msg={quote(msg)}" if msg else url, status_code=303)

    def users():
        return db.all("SELECT * FROM users ORDER BY status = 'active' DESC, created_at")

    # ------------------------------------------------------------ dashboard
    @r.get("", response_class=HTMLResponse)
    def dashboard(request: Request):
        today = service.today().isoformat()
        stats = {
            "users_active": db.one("SELECT COUNT(*) n FROM users WHERE status='active'")["n"],
            "users_pending": db.one("SELECT COUNT(*) n FROM users WHERE status='pending'")["n"],
            "events_upcoming": db.one("SELECT COUNT(*) n FROM events WHERE last_buy >= ?", (today,))["n"],
            "events_total": db.one("SELECT COUNT(*) n FROM events")["n"],
            "prices": db.one("SELECT COUNT(*) n, MAX(trade_date) d FROM prices"),
            "bought": db.one("SELECT COUNT(*) n FROM user_events WHERE buy_status='bought'")["n"],
            "voted": db.one("SELECT COUNT(*) n FROM user_events WHERE vote_status='voted'")["n"],
        }
        jobs = [{"id": j.id, "next": j.next_run_time} for j in scheduler.get_jobs()] if scheduler else []
        try:
            quota = service.line.quota()
        except Exception as ex:
            quota = {"error": str(ex)}
        runs = db.all("SELECT * FROM scrape_runs ORDER BY id DESC LIMIT 15")
        version_file = Path(cfg.db_path).parent / "version.txt"
        version = version_file.read_text(encoding="utf-8").strip() if version_file.exists() else "未知（尚未用 update.sh 更新過）"
        return page(request, "dashboard.html", stats=stats, jobs=jobs, quota=quota, runs=runs, version=version,
                    refresh_date=db.get_meta("refresh_date"), refresh_errors=db.get_meta("refresh_errors"))

    @r.post("/refresh")
    def refresh():
        return back("/admin", "🔄 " + service.refresh())

    @r.post("/send-daily")
    def send_daily(force: str = Form("")):
        n = service.send_daily(force=bool(force))
        return back("/admin", f"已推播給 {n} 位使用者")

    # ------------------------------------------------------------ gifts
    @r.get("/gifts", response_class=HTMLResponse)
    def gifts(request: Request, q: str = "", scope: str = "upcoming"):
        today = service.today().isoformat()
        where, params = ["1=1"], []
        if scope == "upcoming":
            where.append("(e.last_buy >= ? OR e.vote_end >= ?)")
            params += [today, today]
        if q:
            where.append("(e.code LIKE ? OR e.name LIKE ? OR e.gift LIKE ?)")
            params += [f"%{q}%"] * 3
        rows = db.all(f"""
            SELECT e.*,
              SUM(ue.buy_status = 'want') AS n_want, SUM(ue.buy_status = 'skip') AS n_skip,
              SUM(ue.buy_status = 'bought') AS n_bought, SUM(ue.vote_status = 'voted') AS n_voted
            FROM events e LEFT JOIN user_events ue ON ue.event_id = e.id
            WHERE {' AND '.join(where)} GROUP BY e.id ORDER BY e.last_buy, e.code LIMIT 1000""", params)
        return page(request, "gifts.html", rows=rows, prices=db.prices(), q=q, scope=scope)

    @r.post("/gifts/{event_id}/vote")
    def set_vote(event_id: int, start: str = Form(...), end: str = Form(...)):
        db.set_vote_period(event_id, date.fromisoformat(start), date.fromisoformat(end))
        db.log(None, event_id, f"vote_period={start}~{end}", "admin")
        return back("/admin/gifts", "已更新投票期間")

    @r.post("/gifts/add")
    def add_gift(code: str = Form(...), name: str = Form(""), last_buy: str = Form(...), meeting: str = Form(...),
                 gift: str = Form(""), vote_start: str = Form(""), vote_end: str = Form("")):
        service.save_gift({
            "code": code.strip(), "name": name.strip(), "gift": gift.strip(),
            "last_buy": date.fromisoformat(last_buy), "meeting": date.fromisoformat(meeting),
            "vote_start": date.fromisoformat(vote_start) if vote_start else None,
            "vote_end": date.fromisoformat(vote_end) if vote_end else None,
        }, "admin")
        return back("/admin/gifts", f"已新增/更新 {code}")

    @r.post("/gifts/{event_id}/delete")
    def delete_gift(event_id: int):
        db.exec("DELETE FROM user_events WHERE event_id = ?", (event_id,))
        db.exec("DELETE FROM events WHERE id = ?", (event_id,))
        return back("/admin/gifts", "已刪除")

    @r.get("/scrape", response_class=HTMLResponse)
    def scrape(request: Request):
        """Live fetch of every source without saving: see exactly what the crawler gets."""
        data = service.scrape_preview()
        return page(request, "scrape.html", data=data)

    # ------------------------------------------------------------ users
    @r.get("/users", response_class=HTMLResponse)
    def users_page(request: Request):
        rows = db.all("""
            SELECT u.*, SUM(ue.buy_status = 'bought') AS n_bought, SUM(ue.vote_status = 'voted') AS n_voted
            FROM users u LEFT JOIN user_events ue ON ue.user_id = u.user_id
            GROUP BY u.user_id ORDER BY u.status = 'pending' DESC, u.created_at""")
        return page(request, "users.html", rows=rows)

    @r.post("/users/{user_id}/{action}")
    def user_action(user_id: str, action: str, display_name: str = Form("")):
        if action in ("approve", "block"):
            db.exec("UPDATE users SET status = ? WHERE user_id = ?",
                    ("active" if action == "approve" else "blocked", user_id))
            if action == "approve":
                try:
                    service.line.push(user_id, [{"type": "text", "text":
                        f"✅ 已開通！每個交易日 {cfg.remind_time:%H:%M} 會提醒你股東會紀念品 🎁\n輸入「說明」看指令"}])
                except Exception:
                    pass
        elif action in ("admin", "unadmin"):
            db.exec("UPDATE users SET is_admin = ? WHERE user_id = ?", (int(action == "admin"), user_id))
        elif action == "rename":
            db.exec("UPDATE users SET display_name = ? WHERE user_id = ?", (display_name.strip(), user_id))
        elif action == "delete":
            db.exec("DELETE FROM user_events WHERE user_id = ?", (user_id,))
            db.exec("DELETE FROM users WHERE user_id = ?", (user_id,))
        else:
            raise HTTPException(404)
        db.log(user_id, None, f"user_{action}", "admin")
        return back("/admin/users", "已更新")

    # ------------------------------------------------------------ records
    @r.get("/records", response_class=HTMLResponse)
    def records(request: Request, user: str = "", event: str = "", buy: str = "", vote: str = ""):
        where, params = ["1=1"], []
        for col, val in (("ue.user_id", user), ("ue.event_id", event), ("ue.buy_status", buy),
                         ("ue.vote_status", vote)):
            if val:
                where.append(f"{col} = ?")
                params.append(val)
        rows = db.all(f"""
            SELECT ue.*, u.display_name, e.code, e.name, e.gift, e.last_buy, e.meeting, e.vote_start, e.vote_end
            FROM user_events ue JOIN events e ON e.id = ue.event_id
            LEFT JOIN users u ON u.user_id = ue.user_id
            WHERE {' AND '.join(where)} ORDER BY e.last_buy DESC, e.code, u.display_name LIMIT 2000""", params)
        return page(request, "records.html", rows=rows, users=users(),
                    f={"user": user, "event": event, "buy": buy, "vote": vote})

    @r.post("/records")
    def set_record(request: Request, user_id: str = Form(...), event_id: int = Form(...),
                   buy_status: str = Form(...), vote_status: str = Form(...)):
        if buy_status not in STATUS_ZH or vote_status not in VOTE_ZH:
            raise HTTPException(400)
        db.set_status(user_id, event_id, buy_status, vote_status, via="admin")
        return back(request.headers.get("referer") or "/admin/records", "已更新")

    @r.post("/records/add")
    def add_record(user_id: str = Form(...), code: str = Form(...), buy_status: str = Form("bought")):
        e = db.find_user_event_by_code(user_id, code.strip(), service.today())
        if not e:
            return back("/admin/records", f"找不到 {code}")
        db.set_status(user_id, e["id"], buy_status, via="admin")
        return back("/admin/records", f"已登記 {code}")

    @r.get("/logs", response_class=HTMLResponse)
    def logs(request: Request, user: str = ""):
        rows = db.all("""
            SELECT l.*, u.display_name, e.code, e.name FROM action_log l
            LEFT JOIN users u ON u.user_id = l.user_id LEFT JOIN events e ON e.id = l.event_id
            WHERE (? = '' OR l.user_id = ?) ORDER BY l.id DESC LIMIT 500""", (user, user))
        return page(request, "logs.html", rows=rows, users=users(), user=user)

    # ------------------------------------------------------------ test
    @r.get("/test", response_class=HTMLResponse)
    def test_page(request: Request, user: str = "", day: str = ""):
        preview = None
        if user and day:
            d = date.fromisoformat(day)
            items, prices = service.plan(user, today=d)
            cal = service.calendar()
            preview = {"day": d, "trading": cal.is_trading_day(d), "reminders": items, "prices": prices}
        return page(request, "test.html", users=users(), user=user, day=day or service.today().isoformat(),
                    preview=preview, checks=None)

    @r.post("/test/push")
    def test_push(user: str = Form(...), day: str = Form(...)):
        n = service.send_preview(user, date.fromisoformat(day))
        return back(f"/admin/test?user={quote(user)}&day={day}", f"已推播測試訊息（{n} 則提醒）")

    @r.post("/test/check", response_class=HTMLResponse)
    def test_check(request: Request):
        checks = []

        def check(name, fn):
            try:
                checks.append((name, True, fn()))
            except Exception as ex:
                checks.append((name, False, str(ex)))

        check("LINE Bot 連線", lambda: service.line.bot_info().get("displayName"))
        check("LINE 推播額度", lambda: service.line.quota())
        check("HiStock 紀念品", lambda: f"{len(sources.fetch_gifts(service.today()))} 筆")
        for name, *args in sources.PRICE_SOURCES:
            check(f"收盤價 {name}", lambda a=args: f"{len(sources.fetch_prices_from(*a))} 筆")
        check("休市日", lambda: f"{len(sources.fetch_holidays())} 筆")
        check("資料庫", lambda: f"{db.one('SELECT COUNT(*) n FROM events')['n']} 筆股東會")
        return page(request, "test.html", users=users(), user="", day=service.today().isoformat(),
                    preview=None, checks=checks)

    return r
