"""Single-instance SQLite storage. Reader progress deliberately does not live here."""
import base64
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from Crypto.Cipher import AES


class Store:
    def __init__(self, root=None):
        self.root = Path(root or os.environ.get("DATA_DIR") or Path(__file__).resolve().parents[1] / "data")
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "comic.sqlite"
        key_path = self.root / "credentials.key"
        if self.path.exists() and not key_path.exists():
            raise RuntimeError("Missing credentials.key; restore it with the database backup")
        try:
            fd = os.open(key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            pass
        else:
            with os.fdopen(fd, "wb") as stream:
                stream.write(os.urandom(32))
        self.key = key_path.read_bytes()
        if len(self.key) != 32:
            raise RuntimeError("Invalid credentials.key; restore it with the database backup")
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript("""
                CREATE TABLE IF NOT EXISTS accounts (
                    source TEXT PRIMARY KEY, secret TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS source_settings (
                    source TEXT PRIMARY KEY, settings TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS library (
                    source TEXT NOT NULL, comic_id TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '', metadata TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(source, comic_id)
                );
                CREATE TABLE IF NOT EXISTS library_categories (
                    name TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS library_category_members (
                    category TEXT NOT NULL, source TEXT NOT NULL, comic_id TEXT NOT NULL,
                    PRIMARY KEY(category, source, comic_id)
                );
                CREATE TABLE IF NOT EXISTS downloads (
                    id TEXT PRIMARY KEY, source TEXT NOT NULL, comic_id TEXT NOT NULL,
                    chapter_id TEXT NOT NULL, title TEXT NOT NULL, chapter TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued', stage TEXT NOT NULL DEFAULT 'queued',
                    completed INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    error TEXT NOT NULL DEFAULT '', password TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    name TEXT PRIMARY KEY, settings TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jm_sync_runs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS jm_sync_files (
                    scope TEXT NOT NULL, album_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    page INTEGER NOT NULL, image_key TEXT NOT NULL, path TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0, digest TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(scope, album_id, chapter_id, page)
                );
                CREATE TABLE IF NOT EXISTS jm_sync_local_files (
                    root TEXT NOT NULL, album_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    page INTEGER NOT NULL, image_key TEXT NOT NULL, path TEXT NOT NULL,
                    size INTEGER NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(root, album_id, chapter_id, page), UNIQUE(root, path)
                );
                CREATE TABLE IF NOT EXISTS jm_favorites (
                    owner TEXT NOT NULL, comic_id TEXT NOT NULL,
                    favorited INTEGER CHECK(favorited IN (0,1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(owner, comic_id)
                );
                CREATE TABLE IF NOT EXISTS jm_favorite_catalog (
                    owner TEXT PRIMARY KEY, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS bika_favorites (
                    owner TEXT NOT NULL, comic_id TEXT NOT NULL,
                    favorited INTEGER CHECK(favorited IN (0,1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY(owner, comic_id)
                );
                CREATE TABLE IF NOT EXISTS bika_favorite_catalog (
                    owner TEXT PRIMARY KEY, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS jm_sync_files_path ON jm_sync_files(path);
                CREATE INDEX IF NOT EXISTS jm_sync_local_files_path ON jm_sync_local_files(path);
                PRAGMA user_version=6;
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def account(self, source):
        with self.connect() as db:
            row = db.execute("SELECT secret FROM accounts WHERE source=?", (source,)).fetchone()
        if row is None:
            return None
        payload = base64.b64decode(row["secret"])
        cipher = AES.new(self.key, AES.MODE_GCM, nonce=payload[:16])
        cipher.update(source.encode())
        return json.loads(cipher.decrypt_and_verify(payload[32:], payload[16:32]))

    def source_settings(self, source):
        with self.connect() as db:
            row = db.execute("SELECT settings FROM source_settings WHERE source=?", (source,)).fetchone()
        return json.loads(row[0]) if row else {}

    def save_source_settings(self, source, values):
        with self.connect() as db:
            db.execute("INSERT INTO source_settings VALUES (?,?) ON CONFLICT(source) DO UPDATE SET settings=excluded.settings",
                       (source, json.dumps(values)))

    def settings(self, name, default=None):
        with self.connect() as db:
            row = db.execute("SELECT settings FROM app_settings WHERE name=?", (name,)).fetchone()
        return json.loads(row[0]) if row else ({} if default is None else default)

    def save_settings(self, name, values):
        with self.connect() as db:
            db.execute("INSERT INTO app_settings VALUES (?,?) ON CONFLICT(name) DO UPDATE SET settings=excluded.settings",
                       (name, json.dumps(values, ensure_ascii=False)))

    def save_account(self, source, values):
        cipher = AES.new(self.key, AES.MODE_GCM)
        cipher.update(source.encode())
        data, tag = cipher.encrypt_and_digest(json.dumps(values).encode())
        secret = base64.b64encode(cipher.nonce + tag + data).decode()
        with self.connect() as db:
            db.execute("INSERT INTO accounts VALUES (?,?) ON CONFLICT(source) DO UPDATE SET secret=excluded.secret", (source, secret))

    @staticmethod
    def account_owner(account):
        return hashlib.sha256(str(account).encode()).hexdigest() if account else None

    @staticmethod
    def _jm_comic_id(value):
        value = str(value)
        if not value.isascii() or not value.isdigit() or len(value) > 20 or int(value) < 1:
            raise ValueError("Invalid JM comic ID")
        return str(int(value))

    def record_jm_favorites(self, owner, comic_ids, complete=False):
        if not owner:
            return
        ids = list(dict.fromkeys(self._jm_comic_id(value) for value in comic_ids))
        with self.connect() as db:
            if complete:
                db.execute("DELETE FROM jm_favorites WHERE owner=?", (owner,))
            db.executemany("""INSERT INTO jm_favorites(owner,comic_id,favorited) VALUES (?,?,1)
                ON CONFLICT(owner,comic_id) DO UPDATE SET favorited=1,updated_at=CURRENT_TIMESTAMP""",
                ((owner, comic_id) for comic_id in ids))
            if complete:
                db.execute("""INSERT INTO jm_favorite_catalog(owner) VALUES (?)
                    ON CONFLICT(owner) DO UPDATE SET updated_at=CURRENT_TIMESTAMP""", (owner,))

    def set_jm_favorite(self, owner, comic_id, favorited):
        if not owner:
            return
        if favorited is not None and not isinstance(favorited, bool):
            raise ValueError("Invalid JM favorite state")
        comic_id = self._jm_comic_id(comic_id)
        with self.connect() as db:
            db.execute("""INSERT INTO jm_favorites(owner,comic_id,favorited) VALUES (?,?,?)
                ON CONFLICT(owner,comic_id) DO UPDATE SET favorited=excluded.favorited,
                updated_at=CURRENT_TIMESTAMP""", (owner, comic_id, favorited))

    def jm_favorite_status(self, owner, comic_id):
        comic_id = self._jm_comic_id(comic_id)
        with self.connect() as db:
            row = db.execute("""SELECT f.comic_id,f.favorited,c.owner AS complete
                FROM (SELECT ? AS owner) wanted
                LEFT JOIN jm_favorites f ON f.owner=wanted.owner AND f.comic_id=?
                LEFT JOIN jm_favorite_catalog c ON c.owner=wanted.owner""", (owner, comic_id)).fetchone()
        if row["comic_id"] is not None:
            return None if row["favorited"] is None else bool(row["favorited"])
        return False if row["complete"] is not None else None

    @staticmethod
    def _bika_comic_id(value):
        value = str(value).lower()
        if len(value) != 24 or any(char not in "0123456789abcdef" for char in value):
            raise ValueError("Invalid Bika comic ID")
        return value

    def record_bika_favorites(self, owner, comic_ids, complete=False):
        if not owner:
            return
        ids = list(dict.fromkeys(self._bika_comic_id(value) for value in comic_ids))
        with self.connect() as db:
            if complete:
                db.execute("DELETE FROM bika_favorites WHERE owner=?", (owner,))
            db.executemany("""INSERT INTO bika_favorites(owner,comic_id,favorited) VALUES (?,?,1)
                ON CONFLICT(owner,comic_id) DO UPDATE SET favorited=1,updated_at=CURRENT_TIMESTAMP""",
                ((owner, comic_id) for comic_id in ids))
            if complete:
                db.execute("""INSERT INTO bika_favorite_catalog(owner) VALUES (?)
                    ON CONFLICT(owner) DO UPDATE SET updated_at=CURRENT_TIMESTAMP""", (owner,))

    def set_bika_favorite(self, owner, comic_id, favorited):
        if not owner:
            return
        if favorited is not None and not isinstance(favorited, bool):
            raise ValueError("Invalid Bika favorite state")
        comic_id = self._bika_comic_id(comic_id)
        with self.connect() as db:
            db.execute("""INSERT INTO bika_favorites(owner,comic_id,favorited) VALUES (?,?,?)
                ON CONFLICT(owner,comic_id) DO UPDATE SET favorited=excluded.favorited,
                updated_at=CURRENT_TIMESTAMP""", (owner, comic_id, favorited))

    def bika_favorite_status(self, owner, comic_id):
        comic_id = self._bika_comic_id(comic_id)
        with self.connect() as db:
            row = db.execute("""SELECT f.comic_id,f.favorited,c.owner AS complete
                FROM (SELECT ? AS owner) wanted
                LEFT JOIN bika_favorites f ON f.owner=wanted.owner AND f.comic_id=?
                LEFT JOIN bika_favorite_catalog c ON c.owner=wanted.owner""", (owner, comic_id)).fetchone()
        if row["comic_id"] is not None:
            return None if row["favorited"] is None else bool(row["favorited"])
        return False if row["complete"] is not None else None

    def books(self):
        with self.connect() as db:
            rows = db.execute("SELECT * FROM library ORDER BY updated_at DESC").fetchall()
            memberships = db.execute("SELECT * FROM library_category_members").fetchall()
        categories = {}
        for member in memberships:
            categories.setdefault((member["source"], member["comic_id"]), set()).add(member["category"])
        result = []
        for row in rows:
            names = categories.get((row["source"], row["comic_id"]), set())
            if row["category"]:
                names.add(row["category"])
            result.append(dict(json.loads(row["metadata"]), source=row["source"], id=row["comic_id"],
                               library_category=row["category"], library_categories=sorted(names),
                               updated_at=row["updated_at"]))
        return result

    def library_categories(self):
        with self.connect() as db:
            return [row[0] for row in db.execute("""SELECT name FROM library_categories
                UNION SELECT category FROM library WHERE category != '' ORDER BY 1""")]

    def create_library_category(self, name):
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO library_categories(name) VALUES (?)", (name,))

    def import_favorite_book(self, source, comic_id, metadata, category):
        """Add a cloud collection membership without replacing manual metadata/categories."""
        with self.connect() as db:
            added = db.execute("""INSERT OR IGNORE INTO library(source,comic_id,metadata) VALUES (?,?,?)""",
                               (source, comic_id, json.dumps(metadata, ensure_ascii=False))).rowcount
            db.execute("INSERT OR IGNORE INTO library_categories(name) VALUES (?)", (category,))
            db.execute("INSERT OR IGNORE INTO library_category_members VALUES (?,?,?)", (category, source, comic_id))
        return bool(added)

    def save_book(self, source, comic_id, metadata, category=""):
        with self.connect() as db:
            db.execute("""INSERT INTO library(source,comic_id,metadata,category) VALUES (?,?,?,?)
                ON CONFLICT(source,comic_id) DO UPDATE SET metadata=excluded.metadata,
                category=excluded.category, updated_at=CURRENT_TIMESTAMP""",
                       (source, comic_id, json.dumps(metadata, ensure_ascii=False), category))

    def set_category(self, source, comic_id, category):
        with self.connect() as db:
            return db.execute("UPDATE library SET category=? WHERE source=? AND comic_id=?",
                              (category, source, comic_id)).rowcount

    def remove_book(self, source, comic_id):
        with self.connect() as db:
            db.execute("DELETE FROM library_category_members WHERE source=? AND comic_id=?", (source, comic_id))
            db.execute("DELETE FROM library WHERE source=? AND comic_id=?", (source, comic_id))

    def tasks(self):
        with self.connect() as db:
            return [dict(r) for r in db.execute("SELECT * FROM downloads ORDER BY created_at DESC, rowid DESC")]

    def task(self, task_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM downloads WHERE id=?", (task_id,)).fetchone()
        return dict(row) if row else None

    def update_task(self, task_id, **values):
        allowed = {"status", "stage", "completed", "total", "error"}
        if not values or not values.keys() <= allowed:
            raise ValueError("Invalid task update")
        with self.connect() as db:
            db.execute("UPDATE downloads SET " + ",".join(f"{k}=?" for k in values) +
                       ",updated_at=CURRENT_TIMESTAMP WHERE id=?", (*values.values(), task_id))
