import os
from dataclasses import dataclass, field
from datetime import time


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _hhmm(value: str) -> time:
    h, m = value.split(":")
    return time(int(h), int(m))


@dataclass
class Config:
    channel_secret: str = field(default_factory=lambda: _env("LINE_CHANNEL_SECRET"))
    access_token: str = field(default_factory=lambda: _env("LINE_CHANNEL_ACCESS_TOKEN"))
    user_id: str = field(default_factory=lambda: _env("LINE_USER_ID"))
    remind_time: time = field(default_factory=lambda: _hhmm(_env("REMIND_TIME", "09:10")))
    refresh_time: time = field(default_factory=lambda: _hhmm(_env("REFRESH_TIME", "08:40")))
    buy_remind_trading_days: int = field(default_factory=lambda: int(_env("BUY_REMIND_TRADING_DAYS", "3")))
    vote_remind_weekends: bool = field(default_factory=lambda: _env("VOTE_REMIND_WEEKENDS", "true").lower() == "true")
    vote_start_days: int = field(default_factory=lambda: int(_env("VOTE_START_DAYS_BEFORE_MEETING", "30")))
    vote_end_days: int = field(default_factory=lambda: int(_env("VOTE_END_DAYS_BEFORE_MEETING", "3")))
    max_price: float = field(default_factory=lambda: float(_env("MAX_PRICE", "0") or 0))
    skip_gift_keywords: list = field(
        default_factory=lambda: [k for k in _env("SKIP_GIFT_KEYWORDS", "無,不發放,未定,未發放").split(",") if k]
    )
    db_path: str = field(default_factory=lambda: _env("DB_PATH", "/data/bot.db"))
    tz: str = field(default_factory=lambda: _env("TZ", "Asia/Taipei"))
