"""
storage.py - persistent storage for the MagicalFairy star game.

Uses SQLite from the standard library (no extra requirements needed).
Scores are numeric; the star emoji is only used when displaying them.

Identity: users are keyed by lower-cased nickname (the project has no
IRC account tracking). A user who changes nick starts a new record.

Set FAIRY_DB in the environment to point the database at a persistent
disk path (e.g. /var/data/magicalfairy.db on Render with a disk attached).
"""
import os
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime, timezone

DB_PATH = os.environ.get("FAIRY_DB", "magicalfairy.db")

_lock = threading.Lock()
_initialized = False

_SCHEMA = """
CREATE TABLE IF NOT EXISTS star_users (
    user_id    TEXT PRIMARY KEY,
    nick       TEXT NOT NULL,
    stars      INTEGER NOT NULL DEFAULT 0,
    catches    INTEGER NOT NULL DEFAULT 0,
    failed     INTEGER NOT NULL DEFAULT 0,
    last_catch TEXT,
    updated    TEXT
);
CREATE TABLE IF NOT EXISTS star_meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _connect():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init_locked():
    """Create tables. If the database file is corrupted, move it aside and start fresh."""
    global _initialized
    if _initialized:
        return
    try:
        with closing(_connect()) as conn:
            with conn:
                conn.executescript(_SCHEMA)
    except sqlite3.DatabaseError as exc:
        backup = f"{DB_PATH}.corrupt-{int(time.time())}"
        print(f"[storage] Database unreadable ({exc}); moving it to {backup} and starting fresh.")
        try:
            os.replace(DB_PATH, backup)
        except OSError:
            pass
        with closing(_connect()) as conn:
            with conn:
                conn.executescript(_SCHEMA)
    _initialized = True


def init_storage():
    with _lock:
        _init_locked()


def _uid(user_id):
    return str(user_id).strip().lower()


# ---------------------------------------------------------------
# Scores
# ---------------------------------------------------------------
def get_user_stars(user_id):
    """Total stars for a user (0 if they have never caught one)."""
    with _lock:
        _init_locked()
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT stars FROM star_users WHERE user_id = ?", (_uid(user_id),)
            ).fetchone()
    return int(row["stars"]) if row else 0


def add_user_star(user_id, nick=None, amount=1):
    """
    Atomically add `amount` stars (and one successful catch) to a user.
    Returns the user's new total. Single UPSERT inside one transaction,
    so concurrent updates cannot lose a reward.
    """
    uid = _uid(user_id)
    nick = nick or uid
    now = _now()
    with _lock:
        _init_locked()
        with closing(_connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO star_users (user_id, nick, stars, catches, last_catch, updated)
                    VALUES (?, ?, ?, 1, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        nick       = excluded.nick,
                        stars      = star_users.stars + excluded.stars,
                        catches    = star_users.catches + 1,
                        last_catch = excluded.last_catch,
                        updated    = excluded.updated
                    """,
                    (uid, nick, int(amount), now, now),
                )
                row = conn.execute(
                    "SELECT stars FROM star_users WHERE user_id = ?", (uid,)
                ).fetchone()
    return int(row["stars"])


def record_failed_attempt(user_id, nick=None):
    """Count a failed catch attempt (never changes the star total)."""
    uid = _uid(user_id)
    nick = nick or uid
    now = _now()
    with _lock:
        _init_locked()
        with closing(_connect()) as conn:
            with conn:
                conn.execute(
                    """
                    INSERT INTO star_users (user_id, nick, stars, catches, failed, updated)
                    VALUES (?, ?, 0, 0, 1, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        nick    = excluded.nick,
                        failed  = star_users.failed + 1,
                        updated = excluded.updated
                    """,
                    (uid, nick, now),
                )


def get_user_star_stats(user_id):
    """Stats dict for a user. Unknown users get a valid all-zero record."""
    uid = _uid(user_id)
    with _lock:
        _init_locked()
        with closing(_connect()) as conn:
            row = conn.execute(
                "SELECT user_id, nick, stars, catches, failed, last_catch "
                "FROM star_users WHERE user_id = ?",
                (uid,),
            ).fetchone()
            if row is None:
                return {"user_id": uid, "nick": None, "stars": 0, "catches": 0,
                        "failed": 0, "last_catch": None, "rank": None}
            stats = dict(row)
            if stats["stars"] > 0:
                better = conn.execute(
                    "SELECT COUNT(*) AS c FROM star_users WHERE stars > ?", (stats["stars"],)
                ).fetchone()["c"]
                stats["rank"] = int(better) + 1
            else:
                stats["rank"] = None
    return stats


def get_top_star_collectors(limit=10):
    """
    Top collectors by stars (only users with at least one star).
    Ties: whoever reached the score first (earlier last_catch) ranks higher,
    then user_id, so the order is deterministic.
    """
    with _lock:
        _init_locked()
        with closing(_connect()) as conn:
            rows = conn.execute(
                """
                SELECT user_id, nick, stars, catches
                FROM star_users
                WHERE stars > 0
                ORDER BY stars DESC, COALESCE(last_catch, '') ASC, user_id ASC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------
# Small key/value store (used to remember the next spawn time
# across restarts)
# ---------------------------------------------------------------
def get_meta(key, default=None):
    with _lock:
        _init_locked()
        with closing(_connect()) as conn:
            row = conn.execute("SELECT value FROM star_meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_meta(key, value):
    with _lock:
        _init_locked()
        with closing(_connect()) as conn:
            with conn:
                conn.execute(
                    "INSERT INTO star_meta (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, str(value)),
                )
