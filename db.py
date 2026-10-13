"""База данных бота (SQLite). Хранится в постоянном хранилище /data."""
import os
import sqlite3
import time

DATA_DIR = os.getenv("DATA_DIR", "/data")
os.makedirs(DATA_DIR, exist_ok=True)
DB_PATH = os.path.join(DATA_DIR, "bot.db")

_conn = sqlite3.connect(DB_PATH, check_same_thread=False)
_conn.row_factory = sqlite3.Row


def now() -> int:
    return int(time.time())


def init():
    _conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY,
            name TEXT,
            username TEXT,
            status TEXT DEFAULT 'pending',      -- pending / active / blocked
            is_admin INTEGER DEFAULT 0,
            current_idx INTEGER DEFAULT 1,      -- номер урока, на котором ученик сейчас
            created_at INTEGER,
            approved_at INTEGER,
            last_activity INTEGER,
            reminded INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS lessons (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position INTEGER,
            title TEXT,
            video_id TEXT, video_kind TEXT,
            task_text TEXT, task_photo TEXT,
            review_id TEXT, review_kind TEXT,
            cover_id TEXT, review_cover_id TEXT,
            created_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, lesson_id INTEGER, submitted_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS submission_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            submission_id INTEGER, kind TEXT, content TEXT
        );
        CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT);
        """
    )
    cols = [r["name"] for r in _conn.execute("PRAGMA table_info(users)")]
    if "consent_at" not in cols:
        _conn.execute("ALTER TABLE users ADD COLUMN consent_at INTEGER")
    _conn.commit()


def q(sql, args=()):
    return _conn.execute(sql, args).fetchall()


def q1(sql, args=()):
    return _conn.execute(sql, args).fetchone()


def run(sql, args=()):
    cur = _conn.execute(sql, args)
    _conn.commit()
    return cur.lastrowid


# ---------- kv ----------
def kv_get(k):
    r = q1("SELECT v FROM kv WHERE k=?", (k,))
    return r["v"] if r else None


def kv_set(k, v):
    run("INSERT INTO kv(k,v) VALUES(?,?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, v))


# ---------- users ----------
def get_user(uid):
    return q1("SELECT * FROM users WHERE id=?", (uid,))


def add_user(uid, name, username, status, is_admin=0):
    run(
        "INSERT OR IGNORE INTO users(id,name,username,status,is_admin,created_at,last_activity,approved_at) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (uid, name, username, status, is_admin, now(), now(), now() if status == "active" else None),
    )


def update_user(uid, **fields):
    keys = ", ".join(f"{k}=?" for k in fields)
    run(f"UPDATE users SET {keys} WHERE id=?", (*fields.values(), uid))


def touch(uid, name=None, username=None):
    if name is not None:
        run("UPDATE users SET last_activity=?, reminded=0, name=?, username=? WHERE id=?",
            (now(), name, username, uid))
    else:
        run("UPDATE users SET last_activity=?, reminded=0 WHERE id=?", (now(), uid))


def delete_user(uid):
    run("DELETE FROM submission_items WHERE submission_id IN (SELECT id FROM submissions WHERE user_id=?)", (uid,))
    run("DELETE FROM submissions WHERE user_id=?", (uid,))
    run("DELETE FROM users WHERE id=?", (uid,))


def admins():
    return [r["id"] for r in q("SELECT id FROM users WHERE is_admin=1")]


def has_admins():
    return bool(q1("SELECT 1 FROM users WHERE is_admin=1"))


def students(status=None):
    if status:
        return q("SELECT * FROM users WHERE status=? AND is_admin=0 ORDER BY created_at", (status,))
    return q("SELECT * FROM users WHERE is_admin=0 ORDER BY status, created_at")


# ---------- lessons ----------
def lessons():
    return q("SELECT * FROM lessons ORDER BY position, id")


def lessons_count():
    return q1("SELECT COUNT(*) c FROM lessons")["c"]


def lesson_by_idx(idx):
    if idx < 1:
        return None
    return q1("SELECT * FROM lessons ORDER BY position, id LIMIT 1 OFFSET ?", (idx - 1,))


def lesson(lid):
    return q1("SELECT * FROM lessons WHERE id=?", (lid,))


def idx_of(lid):
    for i, l in enumerate(lessons(), start=1):
        if l["id"] == lid:
            return i
    return None


def add_lesson(title, video_id, video_kind, task_text, task_photo, review_id, review_kind):
    pos = (q1("SELECT MAX(position) m FROM lessons")["m"] or 0) + 1
    return run(
        "INSERT INTO lessons(position,title,video_id,video_kind,task_text,task_photo,review_id,review_kind,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (pos, title, video_id, video_kind, task_text, task_photo, review_id, review_kind, now()),
    )


def update_lesson(lid, **fields):
    keys = ", ".join(f"{k}=?" for k in fields)
    run(f"UPDATE lessons SET {keys} WHERE id=?", (*fields.values(), lid))


def delete_lesson(lid):
    run("DELETE FROM lessons WHERE id=?", (lid,))


# ---------- homework ----------
def save_submission(uid, lid, items):
    sid = run("INSERT INTO submissions(user_id,lesson_id,submitted_at) VALUES(?,?,?)", (uid, lid, now()))
    for kind, content in items:
        run("INSERT INTO submission_items(submission_id,kind,content) VALUES(?,?,?)", (sid, kind, content))
    return sid


def has_submitted(uid, lid):
    return bool(q1("SELECT 1 FROM submissions WHERE user_id=? AND lesson_id=?", (uid, lid)))


def submissions_of(uid):
    return q(
        "SELECT s.*, l.title FROM submissions s LEFT JOIN lessons l ON l.id=s.lesson_id "
        "WHERE s.user_id=? ORDER BY s.submitted_at",
        (uid,),
    )


def submission(sid):
    return q1("SELECT * FROM submissions WHERE id=?", (sid,))


def submission_items(sid):
    return q("SELECT * FROM submission_items WHERE submission_id=? ORDER BY id", (sid,))


def submitted_lessons_count(uid):
    return q1("SELECT COUNT(DISTINCT lesson_id) c FROM submissions WHERE user_id=?", (uid,))["c"]
