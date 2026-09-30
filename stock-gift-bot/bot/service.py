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

STATUS_ZH = {"pending": "未回覆", "want": "要買(未購入)", "skip": "不買", "bought": "已購入", "expired": "已過期"}
VOTE_ZH = {"pending": "未投票", "voted": "已投票", "expired": "投票已截止"}

HELP = """📖 指令
今天 ─ 預覽今天的提醒
近期 ─ 未來 14 天最後買進日的紀念品
清單 ─ 我要買/已買的股票
查 2317 ─ 查單一股票並顯示按鈕
要買 2317 / 不買 2317
已購入 2317 ─ 自己先買了也可以登記
已投票 2317
投票 2317 4/25-5/24 ─ 手動設定投票期間（所有人共用）"""

# Events that can still produce a reminder (or just ended, so they get marked expired).
LIVE_EVENTS = "(e.last_buy >= ? OR e.vote_end >= ?)"
EXPIRE_GRACE_DAYS = 30


class Service:
    def __init__(self, cfg: Config, db: DB, line: LineClient):
        self.cfg, self.db, self.line = cfg, db, line
        self.tz = ZoneInfo(cfg.tz)
        self.manual_csv = os.path.join(os.path.dirname(cfg.db_path) or ".", "manual_gifts.csv")

    def today(self) -> date:
        return datetime.now(self.tz).date()

    def calendar(self) -> TradingCalendar:
        return TradingCalendar(self.db.holidays())

    # ------------------------------------------------------------ data
    def refresh(self) -> str:
        today = self.today()
        errors = []

        def run(source, fn):
            try:
                result = fn()
                self.db.add_run(source, len(result))
                return result
            except Exception as ex:
                log.exception(source)
                self.db.add_run(source, 0, str(ex)[:500])
                errors.append(f"{source}: {ex}")
                return []

        self.db.save_holidays(run("holidays", sources.fetch_holidays))
        prices = []
        for name, *args in sources.PRICE_SOURCES:
            prices += run(f"prices_{name}", lambda a=args: sources.fetch_prices_from(*a))
        self.db.save_prices(prices)

        gifts = [(g, "histock") for g in run("histock", lambda: sources.fetch_gifts(today))]
        gifts += [(g, "manual_csv") for g in sources.load_manual_gifts(self.manual_csv, today)]
        for g, src in gifts:
            self.save_gift(g, src)

        self.db.set_meta("refresh_date", today.isoformat())
        self.db.set_meta("refresh_errors", "\n".join(errors))
        return f"紀念品 {len(gifts)} 筆、收盤價 {len(prices)} 筆" + ("\n⚠️ " + "\n⚠️ ".join(errors) if errors else "")

    def save_gift(self, g: dict, source: str):
        if g.get("vote_start") and g.get("vote_end"):
            vs, ve, vsrc = g["vote_start"], g["vote_end"], "source"
        else:
            vs = g["meeting"] - timedelta(days=self.cfg.vote_start_days)
            ve = g["meeting"] - timedelta(days=self.cfg.vote_end_days)
            vsrc = "estimate"
        self.db.upsert_event(g["code"], g["name"], g["last_buy"], g["meeting"], g["gift"], vs, ve, vsrc, source)

    def scrape_preview(self) -> dict:
        """Fetch everything without saving, for the admin 'raw scrape' page."""
        today, out = self.today(), {"errors": []}
        try:
            html = sources.fetch_histock_html()
            out["histock_bytes"] = len(html)
            out["histock_tables"] = sources.table_headers(html)
            out["gifts"] = sources.parse_histock(html, today)
        except Exception as ex:
            out["errors"].append(f"HiStock: {ex}")
            out.setdefault("gifts", [])
        out["prices"] = {}
        for name, *args in sources.PRICE_SOURCES:
            try:
                out["prices"][name] = sources.fetch_prices_from(*args)
            except Exception as ex:
                out["errors"].append(f"{name}: {ex}")
                out["prices"][name] = []
        try:
            out["holidays"] = sources.fetch_holidays()
        except Exception as ex:
            out["errors"].append(f"holidays: {ex}")
            out["holidays"] = []
        return out

    # ------------------------------------------------------------ reminders
    def plan(self, user_id: str, today: date | None = None, apply_updates: bool = False):
        today = today or self.today()
        since = (today - timedelta(days=EXPIRE_GRACE_DAYS)).isoformat()
        events = self.db.user_events(user_id, LIVE_EVENTS, (since, since))
        events = [e for e in events if e["buy_status"] in ("pending", "want")
                  or (e["buy_status"] == "bought" and e["vote_status"] == "pending")]
        prices = self.db.prices()
        items, updates = plan_day(events, today, self.calendar(), self.cfg,
                                  {c: p["close"] for c, p in prices.items()})
        if apply_updates:
            for event_id, buy, vote in updates:
                self.db.set_status(user_id, event_id, buy, vote, via="system")
        return items, prices

    def build_messages(self, items: list[Item], prices) -> list[dict]:
        order = {ASK_BUY: 0, ASK_BOUGHT: 1, ASK_VOTE: 2}
        items = sorted(items, key=lambda i: (order[i.kind], i.event["last_buy"]))
        n = {k: sum(1 for i in items if i.kind == k) for k in order}
        alt = f"股東紀念品提醒：要買嗎 {n[ASK_BUY]}、購入了嗎 {n[ASK_BOUGHT]}、投票了嗎 {n[ASK_VOTE]}"
        return carousels(items, prices, alt)

    def send_daily(self, force: bool = False) -> int:
        """Push today's reminder to every active user once. Returns users pushed."""
        today = self.today().isoformat()
        errors = self.db.get_meta("refresh_errors", "") if self.db.get_meta("refresh_date") == today else ""
        sent = 0
        for u in self.db.active_users():
            if not force and u["digest_date"] == today:
                continue
            try:
                items, prices = self.plan(u["user_id"], apply_updates=True)
                messages = self.build_messages(items, prices) if items else []
                if errors and u["is_admin"]:
                    messages.insert(0, text("⚠️ 今天資料更新有問題，提醒可能不完整：\n" + errors))
                if messages:
                    self.line.push(u["user_id"], messages)
                    sent += 1
                self.db.exec("UPDATE users SET digest_date = ? WHERE user_id = ?", (today, u["user_id"]))
            except Exception:
                log.exception("daily push to %s failed", u["user_id"])
        log.info("daily digest pushed to %d users", sent)
        return sent

    def send_preview(self, user_id: str, day: date) -> int:
        """Admin test: push what `user_id` would get on `day`, without changing any state."""
        items, prices = self.plan(user_id, today=day)
        messages = [text(f"🧪 測試訊息：模擬 {day} 的提醒（按鈕是真的，會記錄你的回答）")]
        messages += self.build_messages(items, prices) if items else [text("那天沒有要提醒的")]
        self.line.push(user_id, messages)
        return len(items)

    # ------------------------------------------------------------ users
    def register(self, user_id: str) -> list[dict]:
        """Called for any message/follow from a LINE user. Returns a reply, or None if active."""
        u = self.db.user(user_id)
        if not u:
            try:
                name = self.line.profile(user_id).get("displayName", "")
            except Exception:
                name = ""
            self.db.add_user(user_id, name, "active" if self.cfg.auto_approve else "pending")
            self.db.log(user_id, None, "joined", "line")
            u = self.db.user(user_id)
        if u["status"] == "active":
            return None
        if u["status"] == "pending":
            return [text("👋 已收到你的使用申請，等管理員在後台核准後就會開始提醒你")]
        return []  # blocked: stay silent

    # ------------------------------------------------------------ postback
    def on_postback(self, user_id: str, data: str, via: str = "line") -> list[dict]:
        q = {k: v[0] for k, v in parse_qs(data).items()}
        e = self.db.user_event(user_id, int(q.get("id", 0)))
        if not e:
            return [text("找不到這筆資料")]
        action, tag, eid = q.get("a"), f"{e['code']} {e['name']}", e["id"]
        today, t = self.today(), f"{self.cfg.remind_time:%H:%M}"
        past_last_buy = today > date.fromisoformat(e["last_buy"])

        if action in ("want", "skip", "later") and (e["buy_status"] == "expired" or past_last_buy):
            return [text(f"{tag} 已過最後買進日（{e['last_buy']}），不再提醒")]
        if action == "want":
            self.db.set_status(user_id, eid, "want", via=via)
            return [text(f"👍 {tag} 記下了。最後買進日 {e['last_buy']} 前，每個交易日 {t} 問你有沒有買到")]
        if action == "skip":
            self.db.set_status(user_id, eid, "skip", via=via)
            return [text(f"👌 {tag} 不買，不會再提醒")]
        if action == "bought":
            self.db.set_status(user_id, eid, "bought", via=via)
            vote = f"{e['vote_start']} ~ {e['vote_end']}" if e.get("vote_start") else "未知"
            est = "（推估，可用「投票 代號 起-迄」修正）" if e.get("vote_source") == "estimate" else ""
            return [text(f"🎉 {tag} 已購入！\n投票期間 {vote}{est}\n期間內每天 {t} 會問你投票了沒")]
        if action == "later":
            if e["buy_status"] == "pending":
                self.db.set_status(user_id, eid, "want", via=via)
            else:
                self.db.log(user_id, eid, "buy_later", via)
            return [text(f"⏰ {tag} 明天 {t} 再提醒")]
        if action == "voted":
            self.db.set_status(user_id, eid, vote_status="voted", via=via)
            return [text(f"✅ {tag} 已投票，不再提醒。記得去領紀念品 🎁")]
        if action == "vote_later":
            self.db.log(user_id, eid, "vote_later", via)
            if e.get("vote_end") and today > date.fromisoformat(e["vote_end"]):
                return [text(f"{tag} 投票已截止")]
            return [text(f"⏰ {tag} 明天 {t} 再問你")]
        return [text("看不懂這個按鈕")]

    # ------------------------------------------------------------ text commands
    def on_text(self, user_id: str, msg: str) -> list[dict]:
        msg = msg.strip()
        today = self.today()

        if msg in ("說明", "help", "?", "？"):
            return [text(HELP)]
        if msg == "今天":
            items, prices = self.plan(user_id)
            return self.build_messages(items, prices)[:5] if items else [text("今天沒有要提醒的")]
        if msg == "清單":
            rows = self.db.user_events(user_id, "COALESCE(ue.buy_status, 'pending') IN ('want', 'bought') "
                                                "AND e.meeting >= ?", (today.isoformat(),))
            if not rows:
                return [text("目前沒有追蹤中的股票")]
            lines = [f"{r['code']} {r['name']}｜{r['gift']}\n  {STATUS_ZH[r['buy_status']]}"
                     + (f"・{VOTE_ZH[r['vote_status']]}" if r["buy_status"] == "bought" else "")
                     + f"・最後買進 {r['last_buy'][5:]}" for r in rows]
            return [text("📋 追蹤清單\n" + "\n".join(lines))]
        if msg.startswith("近期"):
            end = today + timedelta(days=14)
            rows = self.db.user_events(user_id, "e.last_buy BETWEEN ? AND ?", (today.isoformat(), end.isoformat()))
            if not rows:
                return [text("未來 14 天沒有最後買進日")]
            prices, lines = self.db.prices(), []
            for r in rows[:60]:
                p = prices.get(r["code"])
                price = f" ${p['close']:g}" if p and p["close"] is not None else ""
                lines.append(f"{r['last_buy'][5:]} {r['code']} {r['name']}{price}｜{r['gift']}")
            return [text("📅 近期最後買進日\n" + "\n".join(lines))]

        m = re.fullmatch(r"投票\s*(\w+)\s+(.+)", msg)
        if m:
            e = self.db.find_user_event_by_code(user_id, m.group(1), today)
            start, end = sources.parse_date_range(m.group(2), today)
            if not e or not start:
                return [text("格式：投票 2317 4/25-5/24")]
            self.db.set_vote_period(e["id"], start, end)
            self.db.log(user_id, e["id"], f"vote_period={start}~{end}")
            return [text(f"✅ {e['code']} {e['name']} 投票期間設為 {start} ~ {end}")]

        m = re.fullmatch(r"(查|要買|不買|已購入|買了|已投票)\s*(\w+)", msg)
        if m:
            verb, code = m.groups()
            e = self.db.find_user_event_by_code(user_id, code, today)
            if not e:
                return [text(f"找不到 {code} 的股東會紀念品資料")]
            if verb == "查":
                kind = {"pending": ASK_BUY, "want": ASK_BOUGHT}.get(e["buy_status"], ASK_VOTE)
                if e["buy_status"] in ("skip", "expired") or (kind == ASK_VOTE and e["vote_status"] != "pending"):
                    status = STATUS_ZH[e["buy_status"]] + (
                        f"・{VOTE_ZH[e['vote_status']]}" if e["buy_status"] == "bought" else "")
                    return [text(f"{e['code']} {e['name']}｜{e['gift']}\n最後買進 {e['last_buy']}\n"
                                 f"股東會 {e['meeting']}\n狀態：{status}")]
                return carousels([Item(e, kind, [])], self.db.prices(), f"{e['code']} {e['name']}")
            action = {"要買": "want", "不買": "skip", "已購入": "bought", "買了": "bought", "已投票": "voted"}[verb]
            return self.on_postback(user_id, f"a={action}&id={e['id']}")

        return [text("輸入「說明」看可以用的指令")]
