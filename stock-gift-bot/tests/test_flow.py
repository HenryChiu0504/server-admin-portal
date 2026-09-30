from datetime import date

import pytest

from bot import sources
from bot.config import Config
from bot.db import DB
from bot.reminders import ASK_BOUGHT, ASK_BUY, ASK_VOTE, plan_day
from bot.service import Service
from bot.tradingcal import TradingCalendar

# 2026-04-06 (Mon) is a holiday in this test calendar.
CAL = TradingCalendar({date(2026, 4, 6)})


class FakeLine:
    def __init__(self):
        self.pushed = []

    def push(self, to, messages):
        self.pushed.append(messages)


class FrozenService(Service):
    day = date(2026, 4, 1)

    def today(self):
        return self.day

    def calendar(self):
        return CAL


@pytest.fixture
def svc(tmp_path):
    cfg = Config(db_path=str(tmp_path / "bot.db"), user_id="Uxxx", max_price=0)
    s = FrozenService(cfg, DB(cfg.db_path), FakeLine())
    # last buy Thu 4/9; meeting 6/10; vote 5/10 ~ 5/12
    s.db.upsert_event("2317", "鴻海", date(2026, 4, 9), date(2026, 6, 10), "環保袋",
                      date(2026, 5, 10), date(2026, 5, 12), "source")
    s.db.save_prices([{"code": "2317", "name": "鴻海", "close": 200.5, "trade_date": "2026-03-31"}])
    return s


def kinds(svc):
    items, _ = svc.plan(apply_updates=True)
    return [i.kind for i in items]


def eid(svc):
    return svc.db.events()[0]["id"]


def test_trading_calendar():
    # 3 trading days before Thu 4/9 skipping Mon 4/6 holiday and weekend -> Fri 4/3
    assert CAL.shift(date(2026, 4, 9), -3) == date(2026, 4, 3)
    assert not CAL.is_trading_day(date(2026, 4, 4))


def test_buy_window_and_skip(svc):
    svc.day = date(2026, 4, 2)
    assert kinds(svc) == []                       # too early
    svc.day = date(2026, 4, 3)
    assert kinds(svc) == [ASK_BUY]                # 3 trading days before
    svc.day = date(2026, 4, 4)
    assert kinds(svc) == []                       # Saturday
    svc.on_postback(f"a=skip&id={eid(svc)}")
    svc.day = date(2026, 4, 7)
    assert kinds(svc) == []                       # said no -> silent


def test_full_happy_path(svc):
    svc.day = date(2026, 4, 3)
    svc.on_postback(f"a=want&id={eid(svc)}")
    svc.day = date(2026, 4, 7)
    assert kinds(svc) == [ASK_BOUGHT]
    svc.on_postback(f"a=later&id={eid(svc)}")     # 明天再提醒
    svc.day = date(2026, 4, 9)
    items, _ = svc.plan(apply_updates=False)
    assert items[0].kind == ASK_BOUGHT and "最後買進日" in items[0].notes[0]
    svc.on_postback(f"a=bought&id={eid(svc)}")
    svc.day = date(2026, 5, 9)
    assert kinds(svc) == []                       # before voting
    svc.day = date(2026, 5, 10)                   # Sunday, weekends allowed
    assert kinds(svc) == [ASK_VOTE]
    svc.on_postback(f"a=vote_later&id={eid(svc)}")
    svc.day = date(2026, 5, 11)
    assert kinds(svc) == [ASK_VOTE]
    svc.on_postback(f"a=voted&id={eid(svc)}")
    svc.day = date(2026, 5, 12)
    assert kinds(svc) == []


def test_expiry(svc):
    svc.day = date(2026, 4, 3)
    svc.on_postback(f"a=want&id={eid(svc)}")
    svc.day = date(2026, 4, 10)
    assert kinds(svc) == []
    assert svc.db.events()[0]["buy_status"] == "expired"
    assert "過" in svc.on_postback(f"a=bought&id={eid(svc)}")[0]["text"]


def test_vote_expiry(svc):
    svc.db.set_status(eid(svc), "bought")
    svc.day = date(2026, 5, 13)
    assert kinds(svc) == []
    assert svc.db.events()[0]["vote_status"] == "expired"


def test_daily_push_once(svc):
    svc.day = date(2026, 4, 3)
    svc.send_daily()
    svc.send_daily()
    assert len(svc.line.pushed) == 1
    flex = svc.line.pushed[0][0]
    bubble = flex["contents"]["contents"][0]
    assert bubble["header"]["contents"][0]["text"] == "2317 鴻海"
    assert "200.5" in str(bubble["body"])


def test_filters(tmp_path):
    cfg = Config(db_path=str(tmp_path / "b.db"), max_price=100)
    ev = {"id": 1, "code": "2317", "name": "x", "last_buy": "2026-04-09", "meeting": "2026-06-10",
          "gift": "環保袋", "vote_start": None, "vote_end": None, "buy_status": "pending", "vote_status": "pending"}
    assert plan_day([ev], date(2026, 4, 9), CAL, cfg, {"2317": 200})[0] == []
    assert plan_day([ev], date(2026, 4, 9), CAL, cfg, {"2317": 50})[0][0].kind == ASK_BUY
    ev["gift"] = "不發放"
    assert plan_day([ev], date(2026, 4, 9), CAL, cfg, {"2317": 50})[0] == []


def test_text_commands(svc):
    svc.day = date(2026, 4, 2)
    assert svc.on_text("查 2317")[0]["type"] == "flex"
    assert "2026-04-25" in svc.on_text("投票 2317 4/25-5/24")[0]["text"]
    assert svc.db.events()[0]["vote_source"] == "manual"
    svc.on_text("已購入 2317")
    assert svc.db.events()[0]["buy_status"] == "bought"
    assert "2317" in svc.on_text("清單")[0]["text"]
    # a refresh must not overwrite manual vote period or answers
    svc.db.upsert_event("2317", "鴻海", date(2026, 4, 9), date(2026, 6, 10), "環保袋",
                        date(2026, 5, 1), date(2026, 5, 2), "estimate")
    e = svc.db.events()[0]
    assert (e["vote_start"], e["buy_status"]) == ("2026-04-25", "bought")


HTML = """<table><tr><th>代號</th><th>名稱</th><th>股價</th><th>最後買進日</th>
<th>股東會日期</th><th>性質</th><th>股東會紀念品</th></tr>
<tr><td>2317</td><td>鴻海</td><td>200</td><td>03/26</td><td>05/29</td><td>常會</td><td>環保袋</td></tr>
<tr><td>2883</td><td>凱基金</td><td>15</td><td>2026/04/09</td><td>115/06/12</td><td>常會</td><td>彩繪花鳥筷</td></tr>
</table>"""


def test_parse_histock():
    rows = sources.parse_histock(HTML, date(2026, 3, 1))
    assert [(r["code"], r["last_buy"], r["meeting"], r["gift"]) for r in rows] == [
        ("2317", date(2026, 3, 26), date(2026, 5, 29), "環保袋"),
        ("2883", date(2026, 4, 9), date(2026, 6, 12), "彩繪花鳥筷"),
    ]
    assert sources.roc_compact_to_date("1150930") == date(2026, 9, 30)
