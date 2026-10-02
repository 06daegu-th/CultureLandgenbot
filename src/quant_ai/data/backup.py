"""DB 자동 백업 — 하루 한 개, 7개 보관, 압축(gzip), 무결성 확인.

SQLite: 쓰는 중에도 안전한 온라인 백업 API(sqlite3.backup)로 복사 → PRAGMA integrity_check → gzip.
PostgreSQL: pg_dump 가 있으면 custom 형식으로, 없으면 안내만 (운영 DB 는 서버 쪽 백업을 권장).
"""

from __future__ import annotations

import gzip
import logging
import shutil
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)


def _sqlite_path(url: str) -> Path | None:
    if not url.startswith("sqlite:///"):
        return None
    return Path(url[len("sqlite:///"):])


def backup(database_url: str, artifacts_dir, keep: int = 7, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    out_dir = Path(artifacts_dir) / "backups"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = now.strftime("%Y%m%d-%H%M")
    src = _sqlite_path(database_url)
    if src is not None:
        if not src.exists():
            return {"skipped": f"DB 파일 없음: {src}"}
        tmp = out_dir / f"quant_ai-{stamp}.db"
        s = sqlite3.connect(str(src))
        d = sqlite3.connect(str(tmp))
        try:
            s.backup(d)
            ok = d.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            d.close()
            s.close()
        if ok != "ok":
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"백업 무결성 실패: {ok}")
        gz = tmp.with_suffix(".db.gz")
        with open(tmp, "rb") as fi, gzip.open(gz, "wb", compresslevel=6) as fo:
            shutil.copyfileobj(fi, fo)
        tmp.unlink()
        pattern = "quant_ai-*.db.gz"
    elif database_url.startswith("postgres"):
        if not shutil.which("pg_dump"):
            return {"skipped": "PostgreSQL: pg_dump 가 없어 건너뜀 (DB 서버 쪽 백업을 쓰세요)"}
        u = urlparse(database_url.replace("postgresql+psycopg", "postgresql"))
        gz = out_dir / f"quant_ai-{stamp}.dump"
        env = {"PGPASSWORD": u.password or ""}
        subprocess.run(["pg_dump", "-Fc", "-h", u.hostname or "localhost", "-p", str(u.port or 5432),  # noqa: S603, S607
                        "-U", u.username or "postgres", "-f", str(gz), (u.path or "/").lstrip("/")],
                       check=True, env={**env, "PATH": "/usr/bin:/bin:/usr/local/bin"}, timeout=1800)
        ok, pattern = "ok", "quant_ai-*.dump"
    else:
        return {"skipped": "지원하지 않는 DB"}
    olds = sorted(out_dir.glob(pattern))
    for old in olds[:-keep]:
        old.unlink(missing_ok=True)
    return {"file": gz.name, "size": gz.stat().st_size, "integrity": ok, "kept": min(len(olds), keep),
            "dir": str(out_dir)}


def restore_hint(artifacts_dir) -> str:
    return (f"복원: 대시보드를 끄고 {Path(artifacts_dir) / 'backups'} 의 최신 .db.gz 를 풀어 "
            "quant_ai.db 자리에 덮어쓰세요 (gunzip -c 파일.db.gz > quant_ai.db)")


__all__ = ["backup", "restore_hint"]
