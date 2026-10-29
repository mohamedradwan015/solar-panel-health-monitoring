"""Per-upload run directories. Tokens are unguessable; everything for one upload lives in one directory."""
from __future__ import annotations
import json, re, secrets, shutil, time, threading
from pathlib import Path
from . import settings

_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_last_cleanup = 0.0
_lock = threading.Lock()


class RunNotFound(KeyError):
    pass


def new_run() -> tuple[str, Path]:
    settings.DATA_DIR.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(18)
    d = settings.DATA_DIR / token
    d.mkdir(mode=0o700)
    return token, d


def run_dir(token: str) -> Path:
    if not isinstance(token, str) or not _TOKEN_RE.match(token):
        raise RunNotFound(token)
    d = settings.DATA_DIR / token
    if not d.is_dir():
        raise RunNotFound(token)
    d.touch()
    return d


def write_json(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, allow_nan=False, default=str), encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def cleanup_expired(force: bool = False) -> int:
    """Delete run directories untouched for RUN_TTL_SECONDS. Cheap; throttled to once per minute."""
    global _last_cleanup
    now = time.time()
    with _lock:
        if not force and now - _last_cleanup < 60:
            return 0
        _last_cleanup = now
    n = 0
    if not settings.DATA_DIR.is_dir():
        return 0
    for d in settings.DATA_DIR.iterdir():
        try:
            if d.is_dir() and _TOKEN_RE.match(d.name) and now - d.stat().st_mtime > settings.RUN_TTL_SECONDS:
                shutil.rmtree(d, ignore_errors=True); n += 1
        except OSError:
            pass
    return n
