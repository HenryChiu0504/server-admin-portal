"""Minimal LINE Messaging API client + Flex message builders.

Push messages count toward the LINE Official Account monthly quota (the free
plan is small), so the daily reminder is sent as ONE push containing all
stocks. Replies (answering button taps / commands) are free.
"""
import base64
import hashlib
import hmac
import logging

import requests

from .reminders import ASK_BOUGHT, ASK_BUY, ASK_VOTE, Item

log = logging.getLogger(__name__)
API = "https://api.line.me/v2/bot/message"


class LineClient:
    def __init__(self, access_token: str, channel_secret: str):
        self.token = access_token
        self.secret = channel_secret

    def verify(self, body: bytes, signature: str) -> bool:
        digest = hmac.new(self.secret.encode(), body, hashlib.sha256).digest()
        return hmac.compare_digest(base64.b64encode(digest).decode(), signature or "")

    def _post(self, path: str, payload: dict):
        r = requests.post(f"{API}/{path}", json=payload, timeout=30,
                          headers={"Authorization": f"Bearer {self.token}"})
        if r.status_code >= 300:
            log.error("LINE %s failed: %s %s", path, r.status_code, r.text)
        r.raise_for_status()

    def push(self, to: str, messages: list[dict]):
        for i in range(0, len(messages), 5):
            self._post("push", {"to": to, "messages": messages[i:i + 5]})

    def reply(self, token: str, messages: list[dict]):
        self._post("reply", {"replyToken": token, "messages": messages[:5]})


def text(msg: str) -> dict:
    return {"type": "text", "text": msg[:5000]}


# ---------------------------------------------------------------- flex
TITLES = {ASK_BUY: "要買嗎？", ASK_BOUGHT: "有購入了嗎？", ASK_VOTE: "投票了嗎？"}
COLORS = {ASK_BUY: "#1E88E5", ASK_BOUGHT: "#F57C00", ASK_VOTE: "#43A047"}
BUTTONS = {
    ASK_BUY: [("要買", "want"), ("不買", "skip")],
    ASK_BOUGHT: [("已購入！", "bought"), ("明天再提醒", "later"), ("不買了", "skip")],
    ASK_VOTE: [("已投票", "voted"), ("未投票", "vote_later")],
}


def _row(label: str, value: str) -> dict:
    return {"type": "box", "layout": "baseline", "spacing": "sm", "contents": [
        {"type": "text", "text": label, "size": "sm", "color": "#888888", "flex": 3},
        {"type": "text", "text": value or "-", "size": "sm", "wrap": True, "flex": 7},
    ]}


def bubble(item: Item, price: dict | None) -> dict:
    e = item.event
    close = f"{price['close']:g} 元" if price and price.get("close") is not None else "查無"
    if price and price.get("trade_date"):
        close += f"（{price['trade_date'][5:].replace('-', '/')}）"
    vote = "-"
    if e.get("vote_start") and e.get("vote_end"):
        vote = f"{e['vote_start'][5:].replace('-', '/')} ~ {e['vote_end'][5:].replace('-', '/')}"
        if e.get("vote_source") == "estimate":
            vote += "（推估）"
    body = [
        _row("紀念品", e["gift"]),
        _row("昨收", close),
        _row("最後買進", e["last_buy"][5:].replace("-", "/")),
        _row("股東會", e["meeting"][5:].replace("-", "/")),
        _row("投票期間", vote),
    ]
    body += [{"type": "text", "text": n, "size": "sm", "wrap": True, "color": "#D32F2F"} for n in item.notes]
    buttons = [{
        "type": "button", "style": "primary" if i == 0 else "secondary", "height": "sm",
        "color": COLORS[item.kind] if i == 0 else None,
        "action": {"type": "postback", "label": label,
                   "data": f"a={action}&id={e['id']}", "displayText": f"{label} {e['code']}"},
    } for i, (label, action) in enumerate(BUTTONS[item.kind])]
    for b in buttons:
        if b["color"] is None:
            del b["color"]
    return {
        "type": "bubble", "size": "kilo",
        "header": {"type": "box", "layout": "vertical", "backgroundColor": COLORS[item.kind], "contents": [
            {"type": "text", "text": f"{e['code']} {e['name']}", "color": "#FFFFFF", "weight": "bold", "size": "lg"},
            {"type": "text", "text": TITLES[item.kind], "color": "#FFFFFF", "size": "sm"},
        ]},
        "body": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": body},
        "footer": {"type": "box", "layout": "vertical", "spacing": "sm", "contents": buttons},
    }


def carousels(items: list[Item], prices: dict[str, dict], alt: str) -> list[dict]:
    bubbles = [bubble(i, prices.get(i.event["code"])) for i in items]
    return [{"type": "flex", "altText": alt[:400],
             "contents": {"type": "carousel", "contents": bubbles[i:i + 12]}}
            for i in range(0, len(bubbles), 12)]
