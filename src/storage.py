"""Single-instance SQLite storage, including reading history and per-chapter reader progress."""
import base64
import hashlib
import json
import os
import re
import sqlite3
import time
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
                -- jm_/bika_ sync and local status tables belong to the removed favorites sync; only the
                -- temporary legacy migration reads them. Drop them together with that migration.
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
                CREATE TABLE IF NOT EXISTS bika_sync_runs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS bika_sync_files (
                    scope TEXT NOT NULL, album_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    page INTEGER NOT NULL, image_key TEXT NOT NULL, path TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0, digest TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(scope, album_id, chapter_id, page)
                );
                CREATE TABLE IF NOT EXISTS bika_sync_local_files (
                    root TEXT NOT NULL, album_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    page INTEGER NOT NULL, image_key TEXT NOT NULL, path TEXT NOT NULL,
                    size INTEGER NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(root, album_id, chapter_id, page), UNIQUE(root, path)
                );
                CREATE TABLE IF NOT EXISTS jm_favorites (
                    owner TEXT NOT NULL, comic_id TEXT NOT NULL,
                    favorited INTEGER CHECK(favorited IN (0,1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    item TEXT, position INTEGER,
                    PRIMARY KEY(owner, comic_id)
                );
                CREATE TABLE IF NOT EXISTS jm_favorite_catalog (
                    owner TEXT PRIMARY KEY, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sort TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS bika_favorites (
                    owner TEXT NOT NULL, comic_id TEXT NOT NULL,
                    favorited INTEGER CHECK(favorited IN (0,1)),
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    item TEXT, position INTEGER,
                    PRIMARY KEY(owner, comic_id)
                );
                CREATE TABLE IF NOT EXISTS bika_favorite_catalog (
                    owner TEXT PRIMARY KEY, updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    sort TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS reading_comics (
                    source TEXT NOT NULL, comic_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '', cover TEXT NOT NULL DEFAULT '', author TEXT NOT NULL DEFAULT '',
                    chapter_id TEXT NOT NULL, chapter_name TEXT NOT NULL DEFAULT '',
                    page INTEGER NOT NULL DEFAULT 0, total INTEGER NOT NULL DEFAULT 0,
                    in_history INTEGER NOT NULL DEFAULT 1 CHECK(in_history IN (0,1)),
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY(source, comic_id)
                );
                CREATE TABLE IF NOT EXISTS reading_chapters (
                    source TEXT NOT NULL, comic_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    page INTEGER NOT NULL DEFAULT 0, updated_at INTEGER NOT NULL,
                    PRIMARY KEY(source, comic_id, chapter_id)
                );
                CREATE TABLE IF NOT EXISTS jm_local_status (
                    root TEXT NOT NULL, album_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('downloaded','missing','error')),
                    title TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                    images INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    checked_at REAL NOT NULL,
                    PRIMARY KEY(root, album_id)
                );
                CREATE TABLE IF NOT EXISTS bika_local_status (
                    root TEXT NOT NULL, album_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('downloaded','missing','error')),
                    title TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                    images INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    checked_at REAL NOT NULL,
                    PRIMARY KEY(root, album_id)
                );
                CREATE INDEX IF NOT EXISTS jm_sync_files_path ON jm_sync_files(path);
                CREATE INDEX IF NOT EXISTS jm_sync_local_files_path ON jm_sync_local_files(path);
                CREATE INDEX IF NOT EXISTS bika_sync_files_path ON bika_sync_files(path);
                CREATE INDEX IF NOT EXISTS bika_sync_local_files_path ON bika_sync_local_files(path);
                CREATE INDEX IF NOT EXISTS reading_comics_updated ON reading_comics(updated_at);
                PRAGMA user_version=9;
            """)
            # v9: favorites rows keep the listing item and snapshot order so the whole collection can be filtered locally.
            for table, columns in (("jm_favorites", ("item TEXT", "position INTEGER")),
                                   ("bika_favorites", ("item TEXT", "position INTEGER")),
                                   ("jm_favorite_catalog", ("sort TEXT NOT NULL DEFAULT ''",)),
                                   ("bika_favorite_catalog", ("sort TEXT NOT NULL DEFAULT ''",))):
                existing = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
                for column in columns:
                    if column.split()[0] not in existing:
                        db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")

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

    def delete_settings(self, name):
        with self.connect() as db:
            db.execute("DELETE FROM app_settings WHERE name=?", (name,))

    def ensure_sync_tables(self, prefix):
        """Checkpoint tables for one library image-sync source."""
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,80}", prefix):
            raise ValueError(f"Invalid sync table prefix: {prefix}")
        with self.connect() as db:
            db.executescript(f"""
                CREATE TABLE IF NOT EXISTS {prefix}_sync_runs (
                    id TEXT PRIMARY KEY, state TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS {prefix}_sync_files (
                    scope TEXT NOT NULL, album_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    page INTEGER NOT NULL, image_key TEXT NOT NULL, path TEXT NOT NULL,
                    size INTEGER NOT NULL DEFAULT 0, digest TEXT NOT NULL DEFAULT '',
                    PRIMARY KEY(scope, album_id, chapter_id, page)
                );
                CREATE TABLE IF NOT EXISTS {prefix}_sync_local_files (
                    root TEXT NOT NULL, album_id TEXT NOT NULL, chapter_id TEXT NOT NULL,
                    page INTEGER NOT NULL, image_key TEXT NOT NULL, path TEXT NOT NULL,
                    size INTEGER NOT NULL, digest TEXT NOT NULL,
                    PRIMARY KEY(root, album_id, chapter_id, page), UNIQUE(root, path)
                );
                CREATE TABLE IF NOT EXISTS {prefix}_local_status (
                    root TEXT NOT NULL, album_id TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('downloaded','missing','error')),
                    title TEXT NOT NULL DEFAULT '', path TEXT NOT NULL DEFAULT '',
                    images INTEGER NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    checked_at REAL NOT NULL,
                    PRIMARY KEY(root, album_id)
                );
                CREATE INDEX IF NOT EXISTS {prefix}_sync_files_path ON {prefix}_sync_files(path);
                CREATE INDEX IF NOT EXISTS {prefix}_sync_local_files_path ON {prefix}_sync_local_files(path);
            """)

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

    def record_jm_favorites(self, owner, comic_ids, complete=False, items=None, sort=""):
        self._record_favorites("jm", self._jm_comic_id, owner, comic_ids, complete, items, sort)

    def _record_favorites(self, source, normalize, owner, comic_ids, complete, items, sort):
        """Upsert favorited rows; items (raw ID -> listing item) refresh stored card data.

        A complete snapshot drops rows missing from it. With ``sort`` the IDs are in that listing
        order and replace the stored positions; rows first seen later keep a NULL position.
        """
        if not owner:
            return
        ids = list(dict.fromkeys(normalize(value) for value in comic_ids))
        stored = {normalize(key): json.dumps(value, ensure_ascii=False) for key, value in (items or {}).items()}
        ordered = complete and bool(sort)
        table, catalog = f"{source}_favorites", f"{source}_favorite_catalog"
        with self.connect() as db:
            if complete:
                # Mark, upsert, sweep: rows outside the snapshot go away while kept rows retain their stored item.
                db.execute(f"UPDATE {table} SET favorited=0{', position=NULL' if ordered else ''} WHERE owner=?", (owner,))
            db.executemany(f"""INSERT INTO {table}(owner,comic_id,favorited,item,position) VALUES (?,?,1,?,?)
                ON CONFLICT(owner,comic_id) DO UPDATE SET favorited=1,item=COALESCE(excluded.item,item),
                position=COALESCE(excluded.position,position),updated_at=CURRENT_TIMESTAMP""",
                ((owner, comic_id, stored.get(comic_id), index if ordered else None) for index, comic_id in enumerate(ids)))
            if complete:
                db.execute(f"DELETE FROM {table} WHERE owner=? AND favorited=0", (owner,))
                db.execute(f"""INSERT INTO {catalog}(owner,sort) VALUES (?,?)
                    ON CONFLICT(owner) DO UPDATE SET updated_at=CURRENT_TIMESTAMP,
                    sort=CASE WHEN excluded.sort<>'' THEN excluded.sort ELSE sort END""", (owner, sort or ""))

    def favorite_catalog(self, source, owner, root, status_table):
        """Favorited rows for one account joined with the library local state in ``status_table`` under ``root``.

        Rows favorited after the last ordered snapshot have no position and come first, newest first.
        """
        if source not in ("jm", "bika") or not re.fullmatch(r"library_[a-z0-9_]+_local_status", status_table):
            raise ValueError("Unsupported favorites source")
        with self.connect() as db:
            meta = db.execute(f"SELECT updated_at, sort FROM {source}_favorite_catalog WHERE owner=?", (owner,)).fetchone()
            rows = db.execute(f"""SELECT f.comic_id, f.item, f.position, s.status, s.title, s.path, s.images, s.message, s.checked_at
                FROM {source}_favorites f LEFT JOIN {status_table} s ON s.root=? AND s.album_id=f.comic_id
                WHERE f.owner=? AND f.favorited=1
                ORDER BY f.position IS NOT NULL, f.position, f.updated_at DESC, f.comic_id""", (root, owner)).fetchall()
        return {"complete": meta is not None, "updated_at": meta["updated_at"] if meta else None,
                "sort": meta["sort"] if meta else "", "rows": [dict(row) for row in rows]}

    def favorited_ids(self, source, owner, comic_ids):
        """The given raw IDs the local favorites table records as favorited; invalid IDs are skipped."""
        normalize = {"jm": self._jm_comic_id, "bika": self._bika_comic_id}.get(source)
        if not normalize:
            raise ValueError("Unsupported favorites source")
        wanted = {}
        for value in comic_ids:
            try:
                wanted.setdefault(normalize(value), []).append(value)
            except ValueError:
                continue
        if not owner or not wanted:
            return []
        with self.connect() as db:
            rows = db.execute(f"""SELECT comic_id FROM {source}_favorites WHERE owner=? AND favorited=1
                AND comic_id IN ({','.join('?' * len(wanted))})""", (owner, *wanted)).fetchall()
        return [value for row in rows for value in wanted[row["comic_id"]]]

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

    def record_bika_favorites(self, owner, comic_ids, complete=False, items=None, sort=""):
        self._record_favorites("bika", self._bika_comic_id, owner, comic_ids, complete, items, sort)

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

    def refresh_book_metadata(self, source, comic_id, metadata):
        """Refresh an existing book without changing categories or re-adding a removed book."""
        payload = json.dumps(metadata, ensure_ascii=False)
        with self.connect() as db:
            db.execute("""UPDATE library SET metadata=?, updated_at=CURRENT_TIMESTAMP
                WHERE source=? AND comic_id=? AND metadata<>?""", (payload, source, comic_id, payload))

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

    # Reading history: one row per comic (last read position), one row per chapter (page index).
    # updated_at is Unix milliseconds so merges from older clients compare against the same unit.
    _READING_COLUMNS = ("source", "comic_id", "title", "cover", "author", "chapter_id",
                        "chapter_name", "page", "total", "in_history", "updated_at")

    @staticmethod
    def _reading_row(row):
        return {"source": row["source"], "id": row["comic_id"], "title": row["title"], "cover": row["cover"],
                "author": row["author"], "chapter_id": row["chapter_id"], "chapter_name": row["chapter_name"],
                "page": row["page"], "total": row["total"], "in_history": bool(row["in_history"]),
                "updated_at": row["updated_at"]}

    def reading_comics(self, limit=None):
        sql = "SELECT * FROM reading_comics ORDER BY updated_at DESC, rowid DESC"
        params = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
        with self.connect() as db:
            return [self._reading_row(row) for row in db.execute(sql, params)]

    def reading_comic(self, source, comic_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM reading_comics WHERE source=? AND comic_id=?",
                             (source, comic_id)).fetchone()
            chapters = db.execute("""SELECT chapter_id,page,updated_at FROM reading_chapters
                WHERE source=? AND comic_id=?""", (source, comic_id)).fetchall()
        return {"comic": self._reading_row(row) if row else None,
                "chapters": {item["chapter_id"]: {"page": item["page"], "updated_at": item["updated_at"]}
                             for item in chapters}}

    def save_reading(self, source, comic_id, values, updated_at=None):
        """Record the current reader position and put the comic back on the continue-reading list."""
        now = int(updated_at if updated_at is not None else time.time() * 1000)
        row = dict(values, source=source, comic_id=comic_id, in_history=1, updated_at=now)
        with self.connect() as db:
            db.execute(f"""INSERT INTO reading_comics({",".join(self._READING_COLUMNS)})
                VALUES ({",".join("?" * len(self._READING_COLUMNS))})
                ON CONFLICT(source,comic_id) DO UPDATE SET title=excluded.title, cover=excluded.cover,
                author=excluded.author, chapter_id=excluded.chapter_id, chapter_name=excluded.chapter_name,
                page=excluded.page, total=excluded.total, in_history=1, updated_at=excluded.updated_at""",
                       tuple(row[name] for name in self._READING_COLUMNS))
            db.execute("""INSERT INTO reading_chapters VALUES (?,?,?,?,?)
                ON CONFLICT(source,comic_id,chapter_id) DO UPDATE SET page=excluded.page,
                updated_at=excluded.updated_at""", (source, comic_id, values["chapter_id"], values["page"], now))
        return now

    def import_reading(self, comics, chapters):
        """Merge records exported from older browser storage; newer server rows always win."""
        with self.connect() as db:
            db.executemany(f"""INSERT INTO reading_comics({",".join(self._READING_COLUMNS)})
                VALUES ({",".join("?" * len(self._READING_COLUMNS))})
                ON CONFLICT(source,comic_id) DO UPDATE SET title=excluded.title, cover=excluded.cover,
                author=excluded.author, chapter_id=excluded.chapter_id, chapter_name=excluded.chapter_name,
                page=excluded.page, total=excluded.total, in_history=excluded.in_history,
                updated_at=excluded.updated_at WHERE excluded.updated_at > reading_comics.updated_at""",
                           (tuple(int(item[name]) if name == "in_history" else item[name]
                                  for name in self._READING_COLUMNS) for item in comics))
            db.executemany("""INSERT INTO reading_chapters VALUES (?,?,?,?,?)
                ON CONFLICT(source,comic_id,chapter_id) DO UPDATE SET page=excluded.page,
                updated_at=excluded.updated_at WHERE excluded.updated_at > reading_chapters.updated_at""",
                           ((item["source"], item["comic_id"], item["chapter_id"], item["page"], item["updated_at"])
                            for item in chapters))

    def clear_reading_history(self):
        """Empty the continue-reading list while keeping every chapter's saved page."""
        with self.connect() as db:
            return db.execute("UPDATE reading_comics SET in_history=0 WHERE in_history=1").rowcount
