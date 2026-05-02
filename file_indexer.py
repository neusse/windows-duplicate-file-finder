import argparse
import datetime as dt
import fnmatch
import hashlib
import os
import platform
import sqlite3
import sys
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import psutil

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


SQLITE_I64_MIN = -(2**63)
SQLITE_I64_MAX = 2**63 - 1

DEFAULT_EXCLUDE_DIRNAMES = [
    "Windows",
    "Users\\georg\\AppData",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "Recovery",
    "PerfLogs",
    "Documents and Settings",
    "MSOCache",
    "Windows.old",
    "$RECYCLE.BIN",
    "System Volume Information",
]
DEFAULT_ONLY_USER = "georg"


@dataclass(frozen=True)
class AppConfig:
    exclude_dirnames: List[str]
    exclude_globs: List[str]
    only_user: Optional[str]
    follow_hidden_dirs: bool


@dataclass(frozen=True)
class Volume:
    label: str
    mountpoint: str


JOBS: Dict[str, Dict[str, Any]] = {}
JOBS_LOCK = threading.Lock()


def utc_now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def machine_name(default: Optional[str] = None) -> str:
    value = (default or os.environ.get("COMPUTERNAME") or platform.node() or "UNKNOWN").strip()
    return value.upper()


def sqlite_i64(value: int) -> int:
    v = int(value)
    if v < SQLITE_I64_MIN:
        return SQLITE_I64_MIN
    if v > SQLITE_I64_MAX:
        return SQLITE_I64_MAX
    return v


def norm_dir(path: str) -> str:
    p = path
    if os.name == "nt":
        if p.startswith("\\\\?\\UNC\\"):
            p = "\\\\" + p[8:]
        elif p.startswith("\\\\?\\"):
            p = p[4:]
    p = os.path.normcase(os.path.abspath(p))
    if not p.endswith(os.sep):
        p += os.sep
    return p


def strip_long_path_prefix(path: str) -> str:
    if os.name == "nt":
        if path.startswith("\\\\?\\UNC\\"):
            return "\\\\" + path[8:]
        if path.startswith("\\\\?\\"):
            return path[4:]
    return path


def to_long_path(path: str) -> str:
    if os.name != "nt":
        return path
    p = os.path.abspath(path)
    if p.startswith("\\\\?\\"):
        return p
    if p.startswith("\\\\"):
        return p
    return "\\\\?\\" + p


def is_dot_dir(path: str) -> bool:
    name = os.path.basename(path.rstrip(os.sep))
    return bool(name) and name.startswith(".")


def is_non_target_user_dir(path: str, only_user: Optional[str]) -> bool:
    if os.name != "nt" or not only_user:
        return False
    p = norm_dir(path).rstrip(os.sep)
    _, tail = os.path.splitdrive(p)
    parts = [seg for seg in tail.split(os.sep) if seg]
    if not parts or parts[0].lower() != "users":
        return False
    if len(parts) < 2:
        return False
    return parts[1].lower() != only_user.lower()


def load_config(path: Optional[str] = None) -> AppConfig:
    data: Dict[str, Any] = {}
    candidates = [Path("config.default.toml"), Path("config.local.toml")]
    if path:
        candidates.append(Path(path))
    for candidate in candidates:
        if candidate.exists():
            with candidate.open("rb") as f:
                loaded = tomllib.load(f)
            data.update(loaded.get("scan", {}))
    only_user = data.get("only_user", DEFAULT_ONLY_USER)
    if only_user == "":
        only_user = None
    return AppConfig(
        exclude_dirnames=list(data.get("exclude_dirnames", DEFAULT_EXCLUDE_DIRNAMES)),
        exclude_globs=list(data.get("exclude_globs", [])),
        only_user=only_user,
        follow_hidden_dirs=bool(data.get("follow_hidden_dirs", False)),
    )


def open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    if column not in column_names(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")


def init_schema(conn: sqlite3.Connection, default_machine: Optional[str] = None) -> None:
    current_machine = machine_name(default_machine)
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scans (
            id INTEGER PRIMARY KEY,
            started_utc TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS files (
            id INTEGER PRIMARY KEY,
            scan_id INTEGER NOT NULL,
            volume TEXT NOT NULL,
            dirpath TEXT NOT NULL,
            name TEXT NOT NULL,
            size INTEGER NOT NULL,
            ctime_ns INTEGER NOT NULL,
            atime_ns INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL,
            sha256 TEXT,
            FOREIGN KEY(scan_id) REFERENCES scans(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS scan_errors (
            id INTEGER PRIMARY KEY,
            scan_id INTEGER NOT NULL,
            path TEXT NOT NULL,
            error TEXT NOT NULL,
            created_utc TEXT NOT NULL,
            FOREIGN KEY(scan_id) REFERENCES scans(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS directory_summary (
            scan_id INTEGER NOT NULL,
            machine_name TEXT NOT NULL,
            volume TEXT NOT NULL,
            dirpath TEXT NOT NULL,
            files INTEGER NOT NULL,
            bytes INTEGER NOT NULL,
            PRIMARY KEY(scan_id, dirpath),
            FOREIGN KEY(scan_id) REFERENCES scans(id) ON DELETE CASCADE
        );
        """
    )
    add_column_if_missing(conn, "scans", "machine_name", "TEXT")
    add_column_if_missing(conn, "scans", "volume", "TEXT")
    add_column_if_missing(conn, "scans", "mountpoint", "TEXT")
    add_column_if_missing(conn, "scans", "status", "TEXT")
    add_column_if_missing(conn, "scans", "completed_utc", "TEXT")
    add_column_if_missing(conn, "scans", "files_inserted", "INTEGER DEFAULT 0")
    add_column_if_missing(conn, "scans", "errors_count", "INTEGER DEFAULT 0")
    add_column_if_missing(conn, "files", "machine_name", "TEXT")
    conn.execute("UPDATE scans SET machine_name = ? WHERE machine_name IS NULL", (current_machine,))
    conn.execute("UPDATE scans SET volume = '*' WHERE volume IS NULL")
    conn.execute("UPDATE scans SET mountpoint = volume WHERE mountpoint IS NULL")
    conn.execute("UPDATE scans SET status = 'completed' WHERE status IS NULL")
    conn.execute("UPDATE files SET machine_name = ? WHERE machine_name IS NULL", (current_machine,))
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_scans_machine_volume ON scans(machine_name, volume, id DESC);
        CREATE INDEX IF NOT EXISTS idx_files_machine_scan_size ON files(machine_name, scan_id, size);
        CREATE INDEX IF NOT EXISTS idx_files_machine_scan_name_size ON files(machine_name, scan_id, name, size);
        CREATE INDEX IF NOT EXISTS idx_files_machine_scan_sha ON files(machine_name, scan_id, sha256);
        CREATE INDEX IF NOT EXISTS idx_files_scan_volume ON files(scan_id, volume);
        CREATE INDEX IF NOT EXISTS idx_files_scan_dir ON files(scan_id, dirpath);
        CREATE INDEX IF NOT EXISTS idx_dir_summary_scan_bytes ON directory_summary(scan_id, bytes DESC);
        """
    )
    conn.commit()


def get_volumes() -> List[Volume]:
    vols: List[Volume] = []
    for part in psutil.disk_partitions(all=False):
        opts = (part.opts or "").lower()
        if "cdrom" in opts or not part.fstype:
            continue
        mount = part.mountpoint
        if not mount.endswith(os.sep):
            mount += os.sep
        vols.append(Volume(part.device, mount))
    seen = set()
    out = []
    for vol in vols:
        key = os.path.normcase(vol.mountpoint)
        if key not in seen:
            seen.add(key)
            out.append(vol)
    return out


def resolve_drive(drive: str) -> Volume:
    normalized = drive.strip()
    if len(normalized) == 2 and normalized[1] == ":":
        normalized += os.sep
    if not normalized.endswith(os.sep):
        normalized += os.sep
    for vol in get_volumes():
        if os.path.normcase(vol.mountpoint) == os.path.normcase(normalized):
            return vol
    return Volume(normalized, normalized)


def is_excluded_dir(path: str, mountpoint: str, config: AppConfig) -> bool:
    nd = norm_dir(path)
    norm_excludes = [norm_dir(os.path.join(mountpoint, d)) for d in config.exclude_dirnames]
    if any(nd.startswith(ex) for ex in norm_excludes):
        return True
    if not config.follow_hidden_dirs and is_dot_dir(path):
        return True
    if is_non_target_user_dir(path, config.only_user):
        return True
    raw = os.path.normcase(os.path.abspath(path))
    return any(fnmatch.fnmatch(raw, os.path.normcase(pattern)) for pattern in config.exclude_globs)


def start_scan(conn: sqlite3.Connection, machine: str, volume: str, mountpoint: str) -> int:
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO scans(started_utc, machine_name, volume, mountpoint, status, files_inserted, errors_count)
        VALUES (?, ?, ?, ?, 'running', 0, 0)
        """,
        (utc_now_iso(), machine, volume, mountpoint),
    )
    conn.commit()
    return int(cur.lastrowid)


def finish_scan(conn: sqlite3.Connection, scan_id: int, files: int, errors: int, status: str = "completed") -> None:
    conn.execute(
        """
        UPDATE scans
        SET completed_utc = ?, status = ?, files_inserted = ?, errors_count = ?
        WHERE id = ?
        """,
        (utc_now_iso(), status, files, errors, scan_id),
    )
    conn.commit()


def top_summary_dir(path: str, mountpoint: str) -> str:
    path = strip_long_path_prefix(path)
    mountpoint = strip_long_path_prefix(mountpoint)
    rel = os.path.relpath(path, mountpoint)
    if rel == ".":
        return mountpoint.rstrip(os.sep) + os.sep
    first = rel.split(os.sep, 1)[0]
    return os.path.join(mountpoint, first)


def summary_bucket_for_file(dirpath: str, mountpoint: Optional[str]) -> str:
    clean = strip_long_path_prefix(dirpath)
    if mountpoint and mountpoint != "*":
        try:
            return top_summary_dir(clean, mountpoint)
        except ValueError:
            pass
    drive, tail = os.path.splitdrive(clean)
    parts = [part for part in tail.split(os.sep) if part]
    if drive and parts:
        return os.path.join(drive + os.sep, parts[0])
    if drive:
        return drive + os.sep
    return parts[0] if parts else clean


def rebuild_directory_summary(conn: sqlite3.Connection, scan_id: int, machine: str) -> int:
    scan = conn.execute(
        "SELECT volume, mountpoint FROM scans WHERE id = ? AND machine_name = ?",
        (scan_id, machine),
    ).fetchone()
    if not scan:
        return 0
    buckets: Dict[str, List[int]] = {}
    for row in conn.execute(
        "SELECT dirpath, size FROM files WHERE scan_id = ? AND machine_name = ?",
        (scan_id, machine),
    ):
        bucket = summary_bucket_for_file(row["dirpath"], scan["mountpoint"])
        if bucket not in buckets:
            buckets[bucket] = [0, 0]
        buckets[bucket][0] += 1
        buckets[bucket][1] += int(row["size"])
    conn.executemany(
        """
        INSERT OR REPLACE INTO directory_summary(scan_id, machine_name, volume, dirpath, files, bytes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(scan_id, machine, scan["volume"], path, values[0], values[1]) for path, values in buckets.items()],
    )
    conn.commit()
    return len(buckets)


def scan_volume(
    volume: Volume,
    config: AppConfig,
    conn: sqlite3.Connection,
    scan_id: int,
    machine: str,
    batch_size: int = 2000,
) -> Tuple[int, int]:
    stack: List[str] = [volume.mountpoint]
    rows: List[Tuple[int, str, str, str, str, int, int, int, int, Optional[str]]] = []
    summary: Dict[str, List[int]] = {}
    files_inserted = 0
    errors = 0
    insert_sql = """
        INSERT INTO files(
            scan_id, machine_name, volume, dirpath, name, size, ctime_ns, atime_ns, mtime_ns, sha256
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    cur = conn.cursor()
    while stack:
        d = stack.pop()
        try:
            if is_excluded_dir(d, volume.mountpoint, config):
                continue
            with os.scandir(to_long_path(d)) as it:
                for entry in it:
                    try:
                        if entry.is_symlink():
                            continue
                        if entry.is_dir(follow_symlinks=False):
                            if not is_excluded_dir(entry.path, volume.mountpoint, config):
                                stack.append(entry.path)
                            continue
                        if entry.is_file(follow_symlinks=False):
                            st = entry.stat(follow_symlinks=False)
                            size = sqlite_i64(st.st_size)
                            dirpath = strip_long_path_prefix(os.path.dirname(entry.path))
                            rows.append(
                                (
                                    scan_id,
                                    machine,
                                    volume.label,
                                    dirpath,
                                    entry.name,
                                    size,
                                    sqlite_i64(getattr(st, "st_ctime_ns", int(st.st_ctime * 1e9))),
                                    sqlite_i64(getattr(st, "st_atime_ns", int(st.st_atime * 1e9))),
                                    sqlite_i64(getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))),
                                    None,
                                )
                            )
                            bucket = top_summary_dir(dirpath, volume.mountpoint)
                            if bucket not in summary:
                                summary[bucket] = [0, 0]
                            summary[bucket][0] += 1
                            summary[bucket][1] += size
                            if len(rows) >= batch_size:
                                cur.executemany(insert_sql, rows)
                                conn.commit()
                                files_inserted += len(rows)
                                rows.clear()
                    except (PermissionError, FileNotFoundError, OSError) as exc:
                        errors += 1
                        conn.execute(
                            "INSERT INTO scan_errors(scan_id, path, error, created_utc) VALUES (?, ?, ?, ?)",
                            (scan_id, getattr(entry, "path", d), str(exc), utc_now_iso()),
                        )
        except (PermissionError, FileNotFoundError, OSError) as exc:
            errors += 1
            conn.execute(
                "INSERT INTO scan_errors(scan_id, path, error, created_utc) VALUES (?, ?, ?, ?)",
                (scan_id, d, str(exc), utc_now_iso()),
            )
    if rows:
        cur.executemany(insert_sql, rows)
        conn.commit()
        files_inserted += len(rows)
    conn.executemany(
        """
        INSERT OR REPLACE INTO directory_summary(scan_id, machine_name, volume, dirpath, files, bytes)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        [(scan_id, machine, volume.label, path, values[0], values[1]) for path, values in summary.items()],
    )
    conn.commit()
    return files_inserted, errors


def compute_sha256(path: str, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(to_long_path(path), "rb", buffering=0) as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def latest_scan_id(conn: sqlite3.Connection, machine: str, volume: Optional[str] = None) -> Optional[int]:
    params: List[Any] = [machine]
    volume_sql = ""
    if volume:
        volume_sql = " AND volume = ?"
        params.append(volume)
    row = conn.execute(
        f"SELECT id FROM scans WHERE machine_name = ?{volume_sql} ORDER BY id DESC LIMIT 1",
        params,
    ).fetchone()
    return int(row["id"]) if row else None


def hash_candidates(conn: sqlite3.Connection, scan_id: int, machine: str, batch_size: int = 500) -> Tuple[int, int]:
    candidates_sql = """
        SELECT f.id, f.dirpath, f.name
        FROM files f
        JOIN (
            SELECT size
            FROM files
            WHERE scan_id = ? AND machine_name = ?
            GROUP BY size
            HAVING COUNT(*) > 1
        ) ds ON f.size = ds.size
        WHERE f.scan_id = ?
          AND f.machine_name = ?
          AND f.sha256 IS NULL
    """
    update_sql = "UPDATE files SET sha256 = ? WHERE id = ?"
    cur = conn.cursor()
    cur2 = conn.cursor()
    hashed = 0
    errors = 0
    updates: List[Tuple[str, int]] = []
    for file_id, dirpath, name in cur.execute(candidates_sql, (scan_id, machine, scan_id, machine)):
        full = os.path.join(dirpath, name)
        try:
            updates.append((compute_sha256(full), int(file_id)))
        except (PermissionError, FileNotFoundError, OSError):
            errors += 1
            continue
        if len(updates) >= batch_size:
            cur2.executemany(update_sql, updates)
            conn.commit()
            hashed += len(updates)
            updates.clear()
    if updates:
        cur2.executemany(update_sql, updates)
        conn.commit()
        hashed += len(updates)
    return hashed, errors


def duplicate_groups(
    conn: sqlite3.Connection,
    scan_id: int,
    machine: str,
    mode: str,
    limit: int,
    offset: int = 0,
) -> List[sqlite3.Row]:
    if mode == "sha256":
        return conn.execute(
            """
            SELECT size, sha256, COUNT(*) AS n, MIN(dirpath || ? || name) AS sample_path
            FROM files
            WHERE scan_id = ? AND machine_name = ? AND sha256 IS NOT NULL
            GROUP BY size, sha256
            HAVING n > 1
            ORDER BY size DESC, n DESC
            LIMIT ? OFFSET ?
            """,
            (os.sep, scan_id, machine, limit, offset),
        ).fetchall()
    return conn.execute(
        """
        SELECT size, name, COUNT(*) AS n, MIN(dirpath || ? || name) AS sample_path
        FROM files
        WHERE scan_id = ? AND machine_name = ?
        GROUP BY size, name
        HAVING n > 1
        ORDER BY size DESC, n DESC, name ASC
        LIMIT ? OFFSET ?
        """,
        (os.sep, scan_id, machine, limit, offset),
    ).fetchall()


def group_members(conn: sqlite3.Connection, scan_id: int, machine: str, mode: str, size: int, key: str) -> List[sqlite3.Row]:
    if mode == "sha256":
        return conn.execute(
            """
            SELECT id, dirpath, name, size, sha256, mtime_ns
            FROM files
            WHERE scan_id = ? AND machine_name = ? AND size = ? AND sha256 = ?
            ORDER BY dirpath, name
            LIMIT 1000
            """,
            (scan_id, machine, size, key),
        ).fetchall()
    return conn.execute(
        """
        SELECT id, dirpath, name, size, sha256, mtime_ns
        FROM files
        WHERE scan_id = ? AND machine_name = ? AND size = ? AND name = ?
        ORDER BY dirpath, name
        LIMIT 1000
        """,
        (scan_id, machine, size, key),
    ).fetchall()


def reset_scans(conn: sqlite3.Connection, machine: str, volume: Optional[str], scope: str) -> int:
    params: List[Any] = [machine]
    where = "machine_name = ?"
    if volume:
        where += " AND volume = ?"
        params.append(volume)
    if scope == "latest":
        row = conn.execute(f"SELECT id FROM scans WHERE {where} ORDER BY id DESC LIMIT 1", params).fetchone()
        if not row:
            return 0
        conn.execute("DELETE FROM scans WHERE id = ?", (row["id"],))
        conn.commit()
        return 1
    cur = conn.execute(f"DELETE FROM scans WHERE {where}", params)
    conn.commit()
    return int(cur.rowcount)


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def build_api(db_path: str, config_path: Optional[str] = None):
    from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
    from fastapi.responses import FileResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Windows Duplicate File Finder")
    static_dir = Path(__file__).with_name("web")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    def connect() -> sqlite3.Connection:
        conn = open_db(db_path)
        init_schema(conn)
        return conn

    @app.get("/")
    def index():
        return FileResponse(static_dir / "index.html")

    @app.get("/api/config")
    def api_config():
        cfg = load_config(config_path)
        return {**cfg.__dict__, "machine_name": machine_name()}

    @app.get("/api/volumes")
    def api_volumes():
        return [vol.__dict__ for vol in get_volumes()]

    @app.get("/api/machines")
    def api_machines():
        with connect() as conn:
            rows = conn.execute("SELECT DISTINCT machine_name FROM scans ORDER BY machine_name").fetchall()
        names = [row["machine_name"] for row in rows if row["machine_name"]]
        current = machine_name()
        if current not in names:
            names.insert(0, current)
        return names

    @app.get("/api/scans")
    def api_scans(machine: str = Query(default_factory=machine_name), volume: Optional[str] = None):
        with connect() as conn:
            params: List[Any] = [machine_name(machine)]
            where = "machine_name = ?"
            if volume:
                where += " AND volume = ?"
                params.append(volume)
            rows = conn.execute(
                f"""
                SELECT id, started_utc, completed_utc, machine_name, volume, mountpoint, status,
                       files_inserted, errors_count
                FROM scans
                WHERE {where}
                ORDER BY id DESC
                LIMIT 100
                """,
                params,
            ).fetchall()
        return rows_to_dicts(rows)

    @app.post("/api/scan")
    def api_scan(background: BackgroundTasks, drive: str, machine: str = Query(default_factory=machine_name)):
        job_id = str(uuid.uuid4())
        set_job(job_id, {"kind": "scan", "status": "queued", "machine": machine_name(machine), "drive": drive})
        background.add_task(run_scan_job, db_path, config_path, job_id, machine_name(machine), [drive])
        return {"job_id": job_id}

    @app.post("/api/hash")
    def api_hash(background: BackgroundTasks, scan_id: int, machine: str = Query(default_factory=machine_name)):
        job_id = str(uuid.uuid4())
        set_job(job_id, {"kind": "hash", "status": "queued", "machine": machine_name(machine), "scan_id": scan_id})
        background.add_task(run_hash_job, db_path, job_id, machine_name(machine), scan_id)
        return {"job_id": job_id}

    @app.get("/api/jobs/{job_id}")
    def api_job(job_id: str):
        with JOBS_LOCK:
            if job_id not in JOBS:
                raise HTTPException(status_code=404, detail="Unknown job")
            return JOBS[job_id]

    @app.get("/api/duplicates")
    def api_duplicates(
        scan_id: int,
        machine: str = Query(default_factory=machine_name),
        mode: str = "size_name",
        limit: int = 100,
        offset: int = 0,
    ):
        with connect() as conn:
            rows = duplicate_groups(conn, scan_id, machine_name(machine), mode, min(limit, 500), offset)
        return rows_to_dicts(rows)

    @app.get("/api/duplicates/members")
    def api_members(scan_id: int, size: int, key: str, machine: str = Query(default_factory=machine_name), mode: str = "size_name"):
        with connect() as conn:
            rows = group_members(conn, scan_id, machine_name(machine), mode, size, key)
        return rows_to_dicts(rows)

    @app.get("/api/treemap")
    def api_treemap(scan_id: int, machine: str = Query(default_factory=machine_name), limit: int = 80):
        active_machine = machine_name(machine)
        with connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) AS n FROM directory_summary WHERE scan_id = ? AND machine_name = ?",
                (scan_id, active_machine),
            ).fetchone()["n"]
            if count == 0:
                rebuild_directory_summary(conn, scan_id, active_machine)
            rows = conn.execute(
                """
                SELECT dirpath AS name, files, bytes AS value
                FROM directory_summary
                WHERE scan_id = ? AND machine_name = ?
                ORDER BY bytes DESC
                LIMIT ?
                """,
                (scan_id, active_machine, min(limit, 200)),
            ).fetchall()
        return rows_to_dicts(rows)

    @app.post("/api/reset")
    def api_reset(machine: str = Query(default_factory=machine_name), volume: Optional[str] = None, scope: str = "latest", vacuum: bool = False):
        with connect() as conn:
            deleted = reset_scans(conn, machine_name(machine), volume, scope)
            if vacuum:
                conn.execute("VACUUM")
        return {"deleted_scans": deleted}

    return app


def set_job(job_id: str, updates: Dict[str, Any]) -> None:
    with JOBS_LOCK:
        JOBS[job_id] = {**JOBS.get(job_id, {}), **updates, "updated_utc": utc_now_iso()}


def run_scan_job(db_path: str, config_path: Optional[str], job_id: str, machine: str, drives: List[str]) -> None:
    set_job(job_id, {"status": "running"})
    try:
        cfg = load_config(config_path)
        with open_db(db_path) as conn:
            init_schema(conn, machine)
            totals = []
            for drive in drives:
                vol = resolve_drive(drive)
                scan_id = start_scan(conn, machine, vol.label, vol.mountpoint)
                set_job(job_id, {"scan_id": scan_id, "volume": vol.label})
                files, errors = scan_volume(vol, cfg, conn, scan_id, machine)
                finish_scan(conn, scan_id, files, errors)
                totals.append({"scan_id": scan_id, "volume": vol.label, "files": files, "errors": errors})
        set_job(job_id, {"status": "completed", "results": totals})
    except Exception as exc:  # pragma: no cover
        set_job(job_id, {"status": "failed", "error": str(exc)})


def run_hash_job(db_path: str, job_id: str, machine: str, scan_id: int) -> None:
    set_job(job_id, {"status": "running"})
    try:
        with open_db(db_path) as conn:
            init_schema(conn, machine)
            hashed, errors = hash_candidates(conn, scan_id, machine)
        set_job(job_id, {"status": "completed", "hashed": hashed, "errors": errors})
    except Exception as exc:  # pragma: no cover
        set_job(job_id, {"status": "failed", "error": str(exc)})


def report_duplicates(conn: sqlite3.Connection, scan_id: int, machine: str, mode: str, limit: int = 50) -> None:
    rows = duplicate_groups(conn, scan_id, machine, mode, limit)
    print(f"Top {len(rows)} duplicate groups by {mode} for machine={machine} scan_id={scan_id}:")
    for row in rows:
        key = row["sha256"] if mode == "sha256" else row["name"]
        print(f"  n={row['n']} size={row['size']} key={key} sample={row['sample_path']}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Index Windows files into SQLite and browse duplicates.")
    parser.add_argument("--db", default="file_index.sqlite", help="SQLite database path")
    parser.add_argument("--config", default=None, help="Optional TOML config path")
    parser.add_argument("--machine", default=None, help="Override Windows machine name")
    sub = parser.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("scan", help="Scan selected drives and record file metadata.")
    ps.add_argument("--drive", action="append", help="Drive to scan, e.g. C:\\. Repeat for several drives.")
    ps.add_argument("--all-drives", action="store_true", help="Scan every detected attached volume.")
    ps.add_argument("--batch", type=int, default=2000, help="DB insert batch size")

    ph = sub.add_parser("hash", help="Hash candidate duplicates for one scan.")
    ph.add_argument("--scan-id", type=int, default=0, help="Scan id (0 = latest for machine/volume)")
    ph.add_argument("--volume", default=None, help="Volume label for latest scan lookup")
    ph.add_argument("--batch", type=int, default=500, help="DB update batch size")

    pr = sub.add_parser("report", help="Report duplicates.")
    pr.add_argument("--scan-id", type=int, default=0, help="Scan id (0 = latest for machine/volume)")
    pr.add_argument("--volume", default=None, help="Volume label for latest scan lookup")
    pr.add_argument("--mode", choices=["size_name", "sha256"], default="size_name")
    pr.add_argument("--limit", type=int, default=50)

    pc = sub.add_parser("config", help="Show active config.")
    pc.add_argument("action", choices=["show"])

    pv = sub.add_parser("volumes", help="List detected volumes.")

    preset = sub.add_parser("reset", help="Delete stale scan inventory.")
    preset.add_argument("--scope", choices=["latest", "all"], default="latest")
    preset.add_argument("--volume", default=None, help="Limit reset to a volume label such as C:\\")
    preset.add_argument("--vacuum", action="store_true", help="Run VACUUM after reset.")

    sub.add_parser("vacuum", help="Reclaim SQLite space.")

    pw = sub.add_parser("web", help="Run the local web app.")
    pw.add_argument("--host", default="127.0.0.1")
    pw.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    active_machine = machine_name(args.machine)

    if args.cmd == "web":
        import uvicorn

        uvicorn.run(build_api(args.db, args.config), host=args.host, port=args.port)
        return 0

    conn = open_db(args.db)
    init_schema(conn, active_machine)

    if args.cmd == "config":
        cfg = load_config(args.config)
        print(f"machine_name={active_machine}")
        print(f"only_user={cfg.only_user or ''}")
        print(f"follow_hidden_dirs={cfg.follow_hidden_dirs}")
        print("exclude_dirnames:")
        for item in cfg.exclude_dirnames:
            print(f"  {item}")
        print("exclude_globs:")
        for item in cfg.exclude_globs:
            print(f"  {item}")
        return 0

    if args.cmd == "volumes":
        for vol in get_volumes():
            print(f"{vol.label}\t{vol.mountpoint}")
        return 0

    if args.cmd == "scan":
        cfg = load_config(args.config)
        if args.all_drives:
            volumes = get_volumes()
        else:
            drives = args.drive or []
            if not drives:
                print("Specify --drive C:\\ or --all-drives")
                return 2
            volumes = [resolve_drive(drive) for drive in drives]
        for vol in volumes:
            scan_id = start_scan(conn, active_machine, vol.label, vol.mountpoint)
            print(f"scan_id={scan_id} machine={active_machine} volume={vol.label} mount={vol.mountpoint}")
            files, errs = scan_volume(vol, cfg, conn, scan_id, active_machine, batch_size=args.batch)
            finish_scan(conn, scan_id, files, errs)
            print(f"Done. scan_id={scan_id} files_inserted={files} errors={errs}")
        return 0

    if args.cmd == "hash":
        scan_id = args.scan_id or latest_scan_id(conn, active_machine, args.volume)
        if not scan_id:
            print("No scans found. Run: scan")
            return 2
        hashed, errs = hash_candidates(conn, scan_id, active_machine, batch_size=args.batch)
        print(f"Done. machine={active_machine} scan_id={scan_id} hashed={hashed} errors={errs}")
        return 0

    if args.cmd == "report":
        scan_id = args.scan_id or latest_scan_id(conn, active_machine, args.volume)
        if not scan_id:
            print("No scans found. Run: scan")
            return 2
        report_duplicates(conn, scan_id, active_machine, args.mode, limit=args.limit)
        return 0

    if args.cmd == "reset":
        deleted = reset_scans(conn, active_machine, args.volume, args.scope)
        if args.vacuum:
            conn.execute("VACUUM")
        print(f"deleted_scans={deleted}")
        return 0

    if args.cmd == "vacuum":
        conn.execute("VACUUM")
        print("vacuum=done")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
