from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4
import shutil
from redis import Redis
from app.core.config import settings
from app.core.exceptions import AudioError

redis = Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=5, socket_connect_timeout=5)
AREAS = ("originals", "previews", "processed", "waveforms", "temporary")


def initialize():
    for area in AREAS:
        (settings.storage_root / area).mkdir(parents=True, exist_ok=True)


def resolve(key: str) -> Path:
    root = settings.storage_root.resolve()
    result = (root / key).resolve()
    if not result.is_relative_to(root) or result == root or result.parts[-2] not in AREAS:
        raise AudioError("Invalid storage identifier", 400)
    return result


def allocate(area: str, extension: str):
    if area not in AREAS or not extension.isalnum():
        raise AudioError("Invalid storage allocation", 400)
    return f"{area}/{uuid4()}.{extension}"


def used_bytes():
    return sum(p.stat().st_size for a in AREAS for p in (settings.storage_root / a).rglob("*") if p.is_file())


@contextmanager
def reservation(size: int):
    initialize()
    token = f"reservation:{uuid4()}"
    with redis.lock("storage-quota", timeout=30, blocking_timeout=10):
        reserved = sum(int(redis.get(k) or 0) for k in redis.scan_iter("reservation:*"))
        free = shutil.disk_usage(settings.storage_root).free
        if used_bytes() + reserved + size > settings.storage_quota_gb * 1024**3 or size > free - reserved:
            raise AudioError(
                "Storage quota or available disk space is insufficient. Delete unused recordings.", 507
            )
        redis.set(token, size, ex=settings.process_timeout_seconds * 8 + 600)
    try:
        yield
    finally:
        redis.delete(token)


def delete(key):
    if key:
        resolve(key).unlink(missing_ok=True)
