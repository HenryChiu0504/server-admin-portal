"""SQLite storage: gift events (with the user's buy/vote answers), prices, holidays."""
import sqlite3
import threading
from datetime import date

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    code          TEXT NOT NULL,
    name          TEXT NOT NULL,
    last_buy      TEXT NOT NULL,           -- YYYY-MM-DD
    meeting       TEXT NOT NULL,           -- YYYY-MM-DD
    gift          TEXT NOT NULL DEFAULT '',
    vote_start    TEXT,
    vote_end      TEXT,
    vote_source   TEXT NOT NULL DEFAULT 'estimate',  -- source | estimate | manual
    buy_status    TEXT NOT NULL DEFAULT 'pending',   -- pending | want | skip | bought | expired
    vote_status   TEXT NOT NULL DEFAULT 'pending',   -- pending | voted | expired
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (code, meeting)
);
CREATE TABLE IF NOT EXISTS prices (
    code       TEXT PRIMARY KEY,
    name       TEXT,
    close      REAL,
    trade_date TEXT
);
CREATE TABLE IF NOT EXISTS holidays (
    day  TEXT PRIMARY KEY,
    name TEXT
);
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


class DB:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def _exec(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def _all(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    # ---- events -------------------------------------------------------
    def upsert_event(self, code, name, last_buy: date, meeting: date, gift,
                     vote_start: date | None, vote_end: date | None, vote_source: str):
        """Insert or refresh an event. The user's answers are never overwritten,
        and a manually entered vote period wins over scraped/estimated ones."""
        self._exec(
            """
            INSERT INTO events (code, name, last_buy, meeting, gift, vote_start, vote_end, vote_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (code, meeting) DO UPDATE SET
                name = excluded.name,
                last_buy = excluded.last_buy,
                gift = CASE WHEN excluded.gift != '' THEN excluded.gift ELSE events.gift END,
                vote_start = CASE WHEN events.vote_source = 'manual' THEN events.vote_start ELSE excluded.vote_start END,
                vote_end   = CASE WHEN events.vote_source = 'manual' THEN events.vote_end   ELSE excluded.vote_end END,
                vote_source = CASE WHEN events.vote_source = 'manual' THEN 'manual' ELSE excluded.vote_source END,
                updated_at = datetime('now', 'localtime')
            """,
            (code, name, last_buy.isoformat(), meeting.isoformat(), gift,
             vote_start and vote_start.isoformat(), vote_end and vote_end.isoformat(), vote_source),
        )

    def events(self, where: str = "1=1", params=()):
        return self._all(f"SELECT * FROM events WHERE {where} ORDER BY last_buy, code", params)

    def event(self, event_id: int):
        rows = self._all("SELECT * FROM events WHERE id = ?", (event_id,))
        return rows[0] if rows else None

    def find_event_by_code(self, code: str, today: date):
        """Nearest event of this stock whose meeting has not passed yet (else the latest one)."""
        rows = self._all(
            "SELECT * FROM events WHERE code = ? ORDER BY (meeting < ?), meeting",
            (code, today.isoformat()),
        )
        return rows[0] if rows else None

    def set_status(self, event_id: int, buy_status: str | None = None, vote_status: str | None = None):
        if buy_status:
            self._exec("UPDATE events SET buy_status = ? WHERE id = ?", (buy_status, event_id))
        if vote_status:
            self._exec("UPDATE events SET vote_status = ? WHERE id = ?", (vote_status, event_id))

    def set_vote_period(self, event_id: int, start: date, end: date):
        self._exec(
            "UPDATE events SET vote_start = ?, vote_end = ?, vote_source = 'manual' WHERE id = ?",
            (start.isoformat(), end.isoformat(), event_id),
        )

    # ---- prices -------------------------------------------------------
    def save_prices(self, rows):
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO prices (code, name, close, trade_date) VALUES (?, ?, ?, ?)",
                [(r["code"], r["name"], r["close"], r["trade_date"]) for r in rows],
            )
            self._conn.commit()

    def price(self, code: str):
        rows = self._all("SELECT * FROM prices WHERE code = ?", (code,))
        return rows[0] if rows else None

    # ---- holidays -----------------------------------------------------
    def save_holidays(self, rows):
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO holidays (day, name) VALUES (?, ?)",
                [(d.isoformat(), n) for d, n in rows],
            )
            self._conn.commit()

    def holidays(self) -> set[date]:
        return {date.fromisoformat(r["day"]) for r in self._all("SELECT day FROM holidays")}

    # ---- meta ---------------------------------------------------------
    def get_meta(self, key: str, default=None):
        rows = self._all("SELECT value FROM meta WHERE key = ?", (key,))
        return rows[0]["value"] if rows else default

    def set_meta(self, key: str, value: str):
        self._exec("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
