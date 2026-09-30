"""Data sources.

- Gift list:   HiStock  https://histock.tw/stock/gift.aspx
- Close price: TWSE OpenAPI (listed) + TPEx OpenAPI (OTC), previous trading day
- Holidays:    TWSE OpenAPI holiday schedule
- Manual:      data/manual_gifts.csv (optional, adds/overrides rows)

The HiStock parser locates columns by header keywords instead of positions,
so a re-ordered table still parses; if the site changes its headers, adjust
HEADER_KEYWORDS.
"""
import csv
import logging
import os
import re
from datetime import date, timedelta

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
TIMEOUT = 30

HISTOCK_URL = "https://histock.tw/stock/gift.aspx"
TWSE_CLOSE_URL = "https://openapi.twse.com.tw/v1/exchangeReport/STOCK_DAY_ALL"
TPEX_CLOSE_URL = "https://www.tpex.org.tw/openapi/v1/tpex_mainboard_daily_close_quotes"
TWSE_HOLIDAY_URL = "https://openapi.twse.com.tw/v1/holidaySchedule/holidaySchedule"

HEADER_KEYWORDS = {
    "code": ["代號", "代碼"],
    "name": ["名稱", "公司"],
    "last_buy": ["最後買進"],
    "meeting": ["股東會日期", "開會日期", "會議日期"],
    "gift": ["紀念品"],
    "vote": ["投票"],
}

_DATE_RE = re.compile(r"(?:(\d{2,4})[/\-.])?(\d{1,2})[/\-.](\d{1,2})")


def parse_date(text: str, today: date) -> date | None:
    """Accept 2026/03/26, 115/03/26 (ROC), 03/26 (year inferred)."""
    m = _DATE_RE.search(text or "")
    if not m:
        return None
    y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
    try:
        if y:
            year = int(y)
            if year < 1911:
                year += 1911
            return date(year, mo, d)
        guess = date(today.year, mo, d)
        # A list published in Dec may already contain next year's dates.
        if guess < today - timedelta(days=200):
            guess = date(today.year + 1, mo, d)
        return guess
    except ValueError:
        return None


def parse_date_range(text: str, today: date):
    found = [parse_date(t.group(0), today) for t in _DATE_RE.finditer(text or "")]
    found = [d for d in found if d]
    if len(found) >= 2:
        return found[0], found[1]
    return None, None


def roc_compact_to_date(s: str) -> date | None:
    """'1150930' -> 2026-09-30."""
    s = (s or "").strip()
    if not re.fullmatch(r"\d{6,7}", s):
        return None
    return date(int(s[:-4]) + 1911, int(s[-4:-2]), int(s[-2:]))


# ---------------------------------------------------------------- gifts
def parse_histock(html: str, today: date) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [c.get_text(strip=True) for c in rows[0].find_all(["th", "td"])]
        col = {}
        for key, words in HEADER_KEYWORDS.items():
            for i, h in enumerate(headers):
                if any(w in h for w in words) and key not in col:
                    col[key] = i
        if not {"code", "last_buy", "meeting", "gift"} <= col.keys():
            continue

        events = []
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= max(col.values()):
                continue
            code_m = re.search(r"\b(\d{4,6}[A-Z]?)\b", cells[col["code"]])
            last_buy = parse_date(cells[col["last_buy"]], today)
            meeting = parse_date(cells[col["meeting"]], today)
            if not code_m or not last_buy or not meeting:
                continue
            name = cells[col["name"]] if "name" in col else ""
            name = name.replace(code_m.group(1), "").strip()
            vote_start = vote_end = None
            if "vote" in col:
                vote_start, vote_end = parse_date_range(cells[col["vote"]], today)
            events.append({
                "code": code_m.group(1), "name": name, "last_buy": last_buy, "meeting": meeting,
                "gift": cells[col["gift"]], "vote_start": vote_start, "vote_end": vote_end,
            })
        return events
    raise ValueError("HiStock: 找不到紀念品表格，網頁格式可能改了")


def fetch_gifts(today: date) -> list[dict]:
    r = requests.get(HISTOCK_URL, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return parse_histock(r.text, today)


def load_manual_gifts(path: str, today: date) -> list[dict]:
    """CSV columns: code,name,last_buy,meeting,gift[,vote_start,vote_end]"""
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            last_buy = parse_date(row.get("last_buy", ""), today)
            meeting = parse_date(row.get("meeting", ""), today)
            if not row.get("code") or not last_buy or not meeting:
                continue
            out.append({
                "code": row["code"].strip(), "name": (row.get("name") or "").strip(),
                "last_buy": last_buy, "meeting": meeting, "gift": (row.get("gift") or "").strip(),
                "vote_start": parse_date(row.get("vote_start") or "", today),
                "vote_end": parse_date(row.get("vote_end") or "", today),
            })
    return out


# ---------------------------------------------------------------- prices
def _num(v) -> float | None:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _pick(row: dict, *keys):
    for k in keys:
        if k in row and row[k] not in (None, ""):
            return row[k]
    return None


def fetch_prices() -> list[dict]:
    out = []
    for url, code_keys, name_keys, close_keys in (
        (TWSE_CLOSE_URL, ("Code",), ("Name",), ("ClosingPrice",)),
        (TPEX_CLOSE_URL, ("SecuritiesCompanyCode", "Code"), ("CompanyName", "Name"), ("Close", "ClosingPrice")),
    ):
        try:
            r = requests.get(url, headers=UA, timeout=TIMEOUT)
            r.raise_for_status()
            for row in r.json():
                code = _pick(row, *code_keys)
                close = _num(_pick(row, *close_keys))
                if not code or close is None:
                    continue
                d = roc_compact_to_date(str(_pick(row, "Date") or ""))
                out.append({"code": str(code).strip(), "name": _pick(row, *name_keys),
                            "close": close, "trade_date": d.isoformat() if d else None})
        except Exception:
            log.exception("抓收盤價失敗: %s", url)
    return out


# ---------------------------------------------------------------- holidays
def fetch_holidays() -> list[tuple[date, str]]:
    r = requests.get(TWSE_HOLIDAY_URL, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for row in r.json():
        name = str(row.get("Name", ""))
        # "開始交易日" / "最後交易日" entries are trading days, not holidays.
        if "開始交易" in name or "最後交易" in name:
            continue
        d = roc_compact_to_date(str(row.get("Date", "")))
        if d:
            out.append((d, name))
    return out
