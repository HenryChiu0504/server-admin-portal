"""FastAPI webhook for LINE + APScheduler jobs (data refresh, daily 09:10 reminder)."""
import json
import logging
import os
import threading
from datetime import datetime, time

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, HTTPException, Request

from .config import Config
from .db import DB
from .line_api import LineClient, text
from .service import Service

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("stock-gift-bot")

cfg = Config()
os.makedirs(os.path.dirname(cfg.db_path) or ".", exist_ok=True)
db = DB(cfg.db_path)
line = LineClient(cfg.access_token, cfg.channel_secret)
service = Service(cfg, db, line)
scheduler = BackgroundScheduler(timezone=cfg.tz)
app = FastAPI(title="stock-gift-bot")

# If the NAS was down at 09:10, still send when it comes back, up to this time.
LATE_SEND_UNTIL = time(13, 30)


def _safe(fn):
    def run():
        try:
            fn()
        except Exception:
            log.exception("job %s failed", fn.__name__)
    run.__name__ = run.__qualname__ = fn.__name__
    return run


def _startup_catch_up():
    service.refresh()
    now = datetime.now(service.tz)
    if cfg.remind_time <= now.time() <= LATE_SEND_UNTIL:
        service.send_daily()


@app.on_event("startup")
def startup():
    common = dict(misfire_grace_time=3600, coalesce=True, max_instances=1)
    scheduler.add_job(_safe(service.refresh), "cron", id="refresh",
                      hour=cfg.refresh_time.hour, minute=cfg.refresh_time.minute, **common)
    scheduler.add_job(_safe(service.send_daily), "cron", id="daily",
                      hour=cfg.remind_time.hour, minute=cfg.remind_time.minute, **common)
    scheduler.start()
    threading.Thread(target=_safe(_startup_catch_up), daemon=True).start()
    log.info("scheduler started: refresh %s, remind %s (%s)", cfg.refresh_time, cfg.remind_time, cfg.tz)


@app.on_event("shutdown")
def shutdown():
    scheduler.shutdown(wait=False)


@app.get("/health")
def health():
    return {"ok": True, "refresh_date": db.get_meta("refresh_date"),
            "digest_date": db.get_meta("digest_date"), "refresh_errors": db.get_meta("refresh_errors")}


@app.post("/callback")
async def callback(request: Request):
    body = await request.body()
    if not line.verify(body, request.headers.get("X-Line-Signature", "")):
        raise HTTPException(status_code=400, detail="bad signature")

    for ev in json.loads(body).get("events", []):
        token = ev.get("replyToken")
        user = ev.get("source", {}).get("userId", "")
        if not token:
            continue
        try:
            if not cfg.user_id:
                line.reply(token, [text(f"你的 LINE userId：\n{user}\n請填到 .env 的 LINE_USER_ID 後重啟")])
                continue
            if user != cfg.user_id:
                continue  # private bot: ignore everyone else
            if ev["type"] == "postback":
                line.reply(token, service.on_postback(ev["postback"]["data"]))
            elif ev["type"] == "message" and ev["message"].get("type") == "text":
                line.reply(token, service.on_text(ev["message"]["text"]))
            elif ev["type"] == "follow":
                line.reply(token, [text("嗨！我會在每個交易日 9:10 提醒你股東會紀念品 🎁\n輸入「說明」看指令")])
        except Exception:
            log.exception("handling event failed")
            try:
                line.reply(token, [text("處理失敗，請看 NAS 上的 log")])
            except Exception:
                pass
    return "OK"
