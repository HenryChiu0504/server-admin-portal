"""Decides what to ask the user on a given day. Pure logic, no I/O.

Buy flow  (trading days only, from N trading days before the last buy date):
    pending --要買--> want --已購入--> bought
    pending --不買--> skip                    (never asked again)
    pending/want, past last buy date --> expired
Vote flow (every day in the voting period, only for bought):
    pending --已投票--> voted                 (never asked again)
    pending, past vote end --> expired
"""
from dataclasses import dataclass
from datetime import date

from .config import Config
from .tradingcal import TradingCalendar

ASK_BUY = "ask_buy"          # 要買嗎？
ASK_BOUGHT = "ask_bought"    # 有購入嗎？
ASK_VOTE = "ask_vote"        # 投票了嗎？


@dataclass
class Item:
    event: dict
    kind: str
    notes: list


def gift_skipped(gift: str, cfg: Config) -> bool:
    gift = (gift or "").strip()
    return not gift or any(k in gift for k in cfg.skip_gift_keywords)


def plan_day(events: list[dict], today: date, cal: TradingCalendar, cfg: Config,
             prices: dict[str, float | None]):
    """Returns (items to send, [(event_id, buy_status, vote_status)] status updates)."""
    items, updates = [], []
    trading = cal.is_trading_day(today)

    for e in events:
        last_buy = date.fromisoformat(e["last_buy"])
        vote_start = date.fromisoformat(e["vote_start"]) if e.get("vote_start") else None
        vote_end = date.fromisoformat(e["vote_end"]) if e.get("vote_end") else None
        buy, vote = e["buy_status"], e["vote_status"]

        # ---- expiry ------------------------------------------------------
        if buy in ("pending", "want") and today > last_buy:
            updates.append((e["id"], "expired", None))
            continue
        if buy == "bought" and vote == "pending" and vote_end and today > vote_end:
            updates.append((e["id"], None, "expired"))
            continue

        # ---- buy reminders -----------------------------------------------
        if buy in ("pending", "want") and trading:
            start = cal.shift(last_buy, -cfg.buy_remind_trading_days)
            if start <= today <= last_buy:
                if buy == "pending":
                    price = prices.get(e["code"])
                    if gift_skipped(e["gift"], cfg):
                        continue
                    if cfg.max_price and price is not None and price > cfg.max_price:
                        continue
                left = cal.trading_days_between(today, last_buy)
                notes = ["⚠️ 今天就是最後買進日，收盤前要買！"] if left == 0 else [f"還有 {left} 個交易日"]
                items.append(Item(e, ASK_BUY if buy == "pending" else ASK_BOUGHT, notes))
            continue

        # ---- vote reminders ----------------------------------------------
        if buy == "bought" and vote == "pending" and vote_start and vote_end:
            if vote_start <= today <= vote_end and (trading or cfg.vote_remind_weekends):
                notes = []
                if today == vote_start:
                    notes.append("🗳️ 今天開始電子投票，投完可在股東e服務領 eGift（若公司有提供）")
                if today == vote_end:
                    notes.append("⚠️ 今天是投票最後一天！")
                if e.get("vote_source") == "estimate":
                    notes.append("（投票期間為推估，請以股東e服務為準）")
                items.append(Item(e, ASK_VOTE, notes))

    return items, updates
