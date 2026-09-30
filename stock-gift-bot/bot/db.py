"""SQLite storage.

events       one row per shareholder meeting (scraped, shared by everyone)
users        LINE users (pending until approved in the admin backend)
user_events  each user's answer per event (no row = pending/pending)
action_log   history of every answer, for the admin backend
"""
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
    source        TEXT NOT NULL DEFAULT 'histock',   -- histock | manual_csv
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    UNIQUE (code, meeting)
);
CREATE TABLE IF NOT EXISTS users (
    user_id       TEXT PRIMARY KEY,
    display_name  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',   -- pending | active | blocked
    is_admin      INTEGER NOT NULL DEFAULT 0,        -- gets data-error notices
    digest_date   TEXT,
    created_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
);
CREATE TABLE IF NOT EXISTS user_events (
    user_id       TEXT NOT NULL,
    event_id      INTEGER NOT NULL,
    buy_status    TEXT NOT NULL DEFAULT 'pending',   -- pending | want | skip | bought | expired
    vote_status   TEXT NOT NULL DEFAULT 'pending',   -- pending | voted | expired
    updated_at    TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    PRIMARY KEY (user_id, event_id)
);
CREATE TABLE IF NOT EXISTS action_log (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    at         TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    user_id    TEXT,
    event_id   INTEGER,
    action     TEXT NOT NULL,
    via        TEXT NOT NULL DEFAULT 'line'          -- line | admin | system
);
CREATE TABLE IF NOT EXISTS scrape_runs (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    source   TEXT NOT NULL,
    count    INTEGER NOT NULL DEFAULT 0,
    error    TEXT NOT NULL DEFAULT ''
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

# Events joined with one user's answers.
USER_EVENTS_SQL = """
SELECT e.*, COALESCE(ue.buy_status, 'pending') AS buy_status,
       COALESCE(ue.vote_status, 'pending') AS vote_status
FROM events e LEFT JOIN user_events ue ON ue.event_id = e.id AND ue.user_id = ?
"""


class DB:
    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def exec(self, sql, params=()):
        with self._lock:
            cur = self._conn.execute(sql, params)
            self._conn.commit()
            return cur

    def all(self, sql, params=()):
        with self._lock:
            return [dict(r) for r in self._conn.execute(sql, params).fetchall()]

    def one(self, sql, params=()):
        rows = self.all(sql, params)
        return rows[0] if rows else None

    # ---- events -------------------------------------------------------
    def upsert_event(self, code, name, last_buy: date, meeting: date, gift,
                     vote_start: date | None, vote_end: date | None, vote_source: str,
                     source: str = "histock"):
        """Insert or refresh an event; a manually entered vote period is kept."""
        self.exec(
            """
            INSERT INTO events (code, name, last_buy, meeting, gift, vote_start, vote_end, vote_source, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (code, meeting) DO UPDATE SET
                name = CASE WHEN excluded.name != '' THEN excluded.name ELSE events.name END,
                last_buy = excluded.last_buy,
                gift = CASE WHEN excluded.gift != '' THEN excluded.gift ELSE events.gift END,
                vote_start = CASE WHEN events.vote_source = 'manual' THEN events.vote_start ELSE excluded.vote_start END,
                vote_end   = CASE WHEN events.vote_source = 'manual' THEN events.vote_end   ELSE excluded.vote_end END,
                vote_source = CASE WHEN events.vote_source = 'manual' THEN 'manual' ELSE excluded.vote_source END,
                source = excluded.source,
                updated_at = datetime('now', 'localtime')
            """,
            (code, name, last_buy.isoformat(), meeting.isoformat(), gift,
             vote_start and vote_start.isoformat(), vote_end and vote_end.isoformat(), vote_source, source),
        )

    def event(self, event_id: int):
        return self.one("SELECT * FROM events WHERE id = ?", (event_id,))

    def user_events(self, user_id: str, where: str = "1=1", params=()):
        return self.all(f"{USER_EVENTS_SQL} WHERE {where} ORDER BY e.last_buy, e.code", (user_id, *params))

    def user_event(self, user_id: str, event_id: int):
        return self.one(f"{USER_EVENTS_SQL} WHERE e.id = ?", (user_id, event_id))

    def find_user_event_by_code(self, user_id: str, code: str, today: date):
        """Nearest event of this stock whose meeting has not passed yet (else the latest one)."""
        return self.one(f"{USER_EVENTS_SQL} WHERE e.code = ? ORDER BY (e.meeting < ?), e.meeting",
                        (user_id, code, today.isoformat()))

    def set_vote_period(self, event_id: int, start: date, end: date):
        self.exec("UPDATE events SET vote_start = ?, vote_end = ?, vote_source = 'manual' WHERE id = ?",
                  (start.isoformat(), end.isoformat(), event_id))

    # ---- per-user answers ---------------------------------------------
    def set_status(self, user_id: str, event_id: int, buy_status: str | None = None,
                   vote_status: str | None = None, via: str = "line", log: bool = True):
        self.exec(
            """
            INSERT INTO user_events (user_id, event_id, buy_status, vote_status)
            VALUES (?, ?, COALESCE(?, 'pending'), COALESCE(?, 'pending'))
            ON CONFLICT (user_id, event_id) DO UPDATE SET
                buy_status = COALESCE(?, user_events.buy_status),
                vote_status = COALESCE(?, user_events.vote_status),
                updated_at = datetime('now', 'localtime')
            """,
            (user_id, event_id, buy_status, vote_status, buy_status, vote_status),
        )
        if log:
            action = ", ".join(f"{k}={v}" for k, v in (("buy", buy_status), ("vote", vote_status)) if v)
            self.log(user_id, event_id, action, via)

    def log(self, user_id, event_id, action: str, via: str = "line"):
        self.exec("INSERT INTO action_log (user_id, event_id, action, via) VALUES (?, ?, ?, ?)",
                  (user_id, event_id, action, via))

    # ---- users --------------------------------------------------------
    def user(self, user_id: str):
        return self.one("SELECT * FROM users WHERE user_id = ?", (user_id,))

    def add_user(self, user_id: str, display_name: str, status: str):
        self.exec("INSERT OR IGNORE INTO users (user_id, display_name, status) VALUES (?, ?, ?)",
                  (user_id, display_name, status))

    def active_users(self):
        return self.all("SELECT * FROM users WHERE status = 'active' ORDER BY created_at")

    # ---- prices / holidays / runs -------------------------------------
    def save_prices(self, rows):
        with self._lock:
            self._conn.executemany(
                "INSERT OR REPLACE INTO prices (code, name, close, trade_date) VALUES (?, ?, ?, ?)",
                [(r["code"], r["name"], r["close"], r["trade_date"]) for r in rows],
            )
            self._conn.commit()

    def prices(self) -> dict[str, dict]:
        return {r["code"]: r for r in self.all("SELECT * FROM prices")}

    def save_holidays(self, rows):
        with self._lock:
            self._conn.executemany("INSERT OR REPLACE INTO holidays (day, name) VALUES (?, ?)",
                                   [(d.isoformat(), n) for d, n in rows])
            self._conn.commit()

    def holidays(self) -> set[date]:
        return {date.fromisoformat(r["day"]) for r in self.all("SELECT day FROM holidays")}

    def add_run(self, source: str, count: int, error: str = ""):
        self.exec("INSERT INTO scrape_runs (source, count, error) VALUES (?, ?, ?)", (source, count, error))

    # ---- meta ---------------------------------------------------------
    def get_meta(self, key: str, default=None):
        row = self.one("SELECT value FROM meta WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_meta(self, key: str, value: str):
        self.exec("INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))
