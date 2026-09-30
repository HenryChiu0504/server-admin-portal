"""Glue between data sources, the reminder planner, the DB and LINE."""
import logging
import os
import re
from datetime import date, datetime, timedelta
from urllib.parse import parse_qs
from zoneinfo import ZoneInfo

from . import sources
from .config import Config
from .db import DB
from .line_api import LineClient, carousels, text
from .reminders import ASK_BOUGHT, ASK_BUY, ASK_VOTE, Item, plan_day
from .tradingcal import TradingCalendar

log = logging.getLogger(__name__)

STATUS_ZH = {
    "pending": "未決定", "want": "要買(未購入)", "skip": "不買", "bought": "已購入", "expired": "已過期",
}
VOTE_ZH = {"pending": "未投票", "voted": "已投票", "expired": "投票已截止"}

HELP = """📖 指令
今天 ─ 預覽今天的提醒
近期 ─ 未來 14 天最後買進日的紀念品
清單 ─ 我要買/已買的股票
查 2317 ─ 查單一股票並顯示按鈕
要買 2317 / 不買 2317
已購入 2317 ─ 自己先買了也可以登記
已投票 2317
投票 2317 4/25-5/24 ─ 手動設定投票期間
更新 ─ 立刻重新抓資料"""


class Service:
    def __init__(self, cfg: Config, db: DB, line: LineClient):
        self.cfg, self.db, self.line = cfg, db, line
        self.tz = ZoneInfo(cfg.tz)
        self.manual_csv = os.path.join(os.path.dirname(cfg.db_path) or ".", "manual_gifts.csv")

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def calendar(self) -> TradingCalendar:
        return TradingCalendar(self.db.holidays())

    def prices(self) -> dict[str, dict]:
        return {r["code"]: r for r in self.db._all("SELECT * FROM prices")}

    # ------------------------------------------------------------ refresh
    def refresh(self) -> str:
        today = self.today()
        errors = []
        try:
            self.db.save_holidays(sources.fetch_holidays())
        except Exception as ex:
            log.exception("holidays")
            errors.append(f"休市日: {ex}")
        prices = sources.fetch_prices()
        if prices:
            self.db.save_prices(prices)
        else:
            errors.append("收盤價: 抓不到資料")

        gifts = []
        try:
            gifts = sources.fetch_gifts(today)
        except Exception as ex:
            log.exception("gifts")
            errors.append(f"紀念品(HiStock): {ex}")
        gifts += sources.load_manual_gifts(self.manual_csv, today)

        for g in gifts:
            if g["vote_start"] and g["vote_end"]:
                vs, ve, src = g["vote_start"], g["vote_end"], "source"
            else:
                vs = g["meeting"] - timedelta(days=self.cfg.vote_start_days)
                ve = g["meeting"] - timedelta(days=self.cfg.vote_end_days)
                src = "estimate"
            self.db.upsert_event(g["code"], g["name"], g["last_buy"], g["meeting"], g["gift"], vs, ve, src)

        self.db.set_meta("refresh_date", today.isoformat())
        self.db.set_meta("refresh_errors", "\n".join(errors))
        summary = f"紀念品 {len(gifts)} 筆、收盤價 {len(prices)} 筆"
        log.info("refresh done: %s %s", summary, errors)
        return summary + ("\n⚠️ " + "\n⚠️ ".join(errors) if errors else "")

    # ------------------------------------------------------------ daily
    def plan(self, apply_updates: bool):
        today = self.today()
        events = self.db.events(
            "buy_status IN ('pending', 'want') OR (buy_status = 'bought' AND vote_status = 'pending')")
        prices = self.prices()
        items, updates = plan_day(events, today, self.calendar(), self.cfg,
                                  {c: p["close"] for c, p in prices.items()})
        if apply_updates:
            for event_id, buy, vote in updates:
                self.db.set_status(event_id, buy, vote)
        return items, prices

    def build_messages(self, items: list[Item], prices) -> list[dict]:
        order = {ASK_BUY: 0, ASK_BOUGHT: 1, ASK_VOTE: 2}
        items = sorted(items, key=lambda i: (order[i.kind], i.event["last_buy"]))
        counts = {k: sum(1 for i in items if i.kind == k) for k in order}
        alt = f"股東紀念品提醒：要買嗎 {counts[ASK_BUY]}、購入了嗎 {counts[ASK_BOUGHT]}、投票了嗎 {counts[ASK_VOTE]}"
        return carousels(items, prices, alt)

    def send_daily(self, force: bool = False):
        today = self.today().isoformat()
        if not force and self.db.get_meta("digest_date") == today:
            return
        items, prices = self.plan(apply_updates=True)
        messages = self.build_messages(items, prices) if items else []
        errors = self.db.get_meta("refresh_errors", "")
        if errors and self.db.get_meta("refresh_date") == today:
            messages.insert(0, text("⚠️ 今天資料更新有問題，提醒可能不完整：\n" + errors))
        if messages and self.cfg.user_id:
            self.line.push(self.cfg.user_id, messages)
        self.db.set_meta("digest_date", today)
        log.info("daily digest: %d items", len(items))

    # ------------------------------------------------------------ postback
    def on_postback(self, data: str) -> list[dict]:
        q = {k: v[0] for k, v in parse_qs(data).items()}
        e = self.db.event(int(q.get("id", 0)))
        if not e:
            return [text("找不到這筆資料")]
        action, tag = q.get("a"), f"{e['code']} {e['name']}"
        today = self.today()

        if action in ("want", "skip", "bought", "later") and e["buy_status"] == "expired":
            return [text(f"{tag} 已過最後買進日（{e['last_buy']}），不再提醒")]
        if action == "want":
            self.db.set_status(e["id"], "want")
            return [text(f"👍 {tag} 記下了。最後買進日 {e['last_buy']} 前，每個交易日 "
                         f"{self.cfg.remind_time:%H:%M} 問你有沒有買到")]
        if action == "skip":
            self.db.set_status(e["id"], "skip")
            return [text(f"👌 {tag} 不買，不會再提醒")]
        if action == "bought":
            self.db.set_status(e["id"], "bought")
            vote = f"{e['vote_start']} ~ {e['vote_end']}" if e.get("vote_start") else "未知"
            est = "（推估，可用「投票 代號 起-迄」修正）" if e.get("vote_source") == "estimate" else ""
            return [text(f"🎉 {tag} 已購入！\n投票期間 {vote}{est}\n期間內每天 "
                         f"{self.cfg.remind_time:%H:%M} 會問你投票了沒")]
        if action == "later":
            if e["buy_status"] == "pending":
                self.db.set_status(e["id"], "want")
            return [text(f"⏰ {tag} 明天 {self.cfg.remind_time:%H:%M} 再提醒")]
        if action == "voted":
            self.db.set_status(e["id"], vote_status="voted")
            return [text(f"✅ {tag} 已投票，不再提醒。記得去領紀念品 🎁")]
        if action == "vote_later":
            if e.get("vote_end") and today > date.fromisoformat(e["vote_end"]):
                return [text(f"{tag} 投票已截止")]
            return [text(f"⏰ {tag} 明天 {self.cfg.remind_time:%H:%M} 再問你")]
        return [text("看不懂這個按鈕")]

    # ------------------------------------------------------------ text commands
    def on_text(self, msg: str) -> list[dict]:
        msg = msg.strip()
        today = self.today()

        if msg in ("說明", "help", "?", "？"):
            return [text(HELP)]
        if msg == "更新":
            return [text("🔄 " + self.refresh())]
        if msg == "今天":
            items, prices = self.plan(apply_updates=False)
            return self.build_messages(items, prices)[:5] if items else [text("今天沒有要提醒的")]
        if msg == "清單":
            rows = self.db.events("buy_status IN ('want', 'bought') AND meeting >= ?", (today.isoformat(),))
            if not rows:
                return [text("目前沒有追蹤中的股票")]
            lines = [f"{r['code']} {r['name']}｜{r['gift']}\n  {STATUS_ZH[r['buy_status']]}"
                     + (f"・{VOTE_ZH[r['vote_status']]}" if r["buy_status"] == "bought" else "")
                     + f"・最後買進 {r['last_buy'][5:]}" for r in rows]
            return [text("📋 追蹤清單\n" + "\n".join(lines))]
        if msg.startswith("近期"):
            end = today + timedelta(days=14)
            rows = self.db.events("last_buy BETWEEN ? AND ?", (today.isoformat(), end.isoformat()))
            if not rows:
                return [text("未來 14 天沒有最後買進日")]
            prices = self.prices()
            lines = []
            for r in rows[:60]:
                p = prices.get(r["code"])
                lines.append(f"{r['last_buy'][5:]} {r['code']} {r['name']} "
                             f"${p['close']:g}｜{r['gift']}" if p else
                             f"{r['last_buy'][5:]} {r['code']} {r['name']}｜{r['gift']}")
            return [text("📅 近期最後買進日\n" + "\n".join(lines))]

        m = re.fullmatch(r"投票\s*(\w+)\s+(.+)", msg)
        if m:
            e = self.db.find_event_by_code(m.group(1), today)
            start, end = sources.parse_date_range(m.group(2), today)
            if not e or not start:
                return [text("格式：投票 2317 4/25-5/24")]
            self.db.set_vote_period(e["id"], start, end)
            return [text(f"✅ {e['code']} {e['name']} 投票期間設為 {start} ~ {end}")]

        m = re.fullmatch(r"(查|要買|不買|已購入|買了|已投票)\s*(\w+)", msg)
        if m:
            verb, code = m.groups()
            e = self.db.find_event_by_code(code, today)
            if not e:
                return [text(f"找不到 {code} 的股東會紀念品資料（可以先「更新」）")]
            if verb == "查":
                kind = {"pending": ASK_BUY, "want": ASK_BOUGHT}.get(e["buy_status"], ASK_VOTE)
                if e["buy_status"] in ("skip", "expired") or (kind == ASK_VOTE and e["vote_status"] != "pending"):
                    status = STATUS_ZH[e["buy_status"]] + (
                        f"・{VOTE_ZH[e['vote_status']]}" if e["buy_status"] == "bought" else "")
                    return [text(f"{e['code']} {e['name']}｜{e['gift']}\n最後買進 {e['last_buy']}\n"
                                 f"股東會 {e['meeting']}\n狀態：{status}")]
                return carousels([Item(e, kind, [])], self.prices(), f"{e['code']} {e['name']}")
            action = {"要買": "want", "不買": "skip", "已購入": "bought", "買了": "bought", "已投票": "voted"}[verb]
            if action == "bought":  # manual registration also works after the reminder window
                self.db.set_status(e["id"], "bought")
            return self.on_postback(f"a={action}&id={e['id']}")

        return [text("輸入「說明」看可以用的指令")]
