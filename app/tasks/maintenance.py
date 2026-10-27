from datetime import timedelta
import shutil
from sqlalchemy import select
from app.core.config import settings
from app.core.database import SessionLocal
from app.models import AudioFile, AudioAnalysis, ProcessedVersion, ProcessingJob, now
from app.services.storage import redis, delete, initialize, AREAS
from app.tasks.celery_app import celery
from app.tasks.audio_tasks import dispatch_one


@celery.task(name="audio.dispatch")
def dispatch():
    redis.set("scheduler-heartbeat", now().isoformat(), ex=120)
    with SessionLocal() as db:
        jobs = list(
            db.scalars(
                select(ProcessingJob).where(ProcessingJob.state.in_(["pending", "queued", "processing"]))
            )
        )
        dispatch_ids = []
        for job in jobs:
            age = (now() - job.updated_at).total_seconds()
            if job.state == "processing":
                if age > 120 and not redis.exists(f"job-lock:{job.id}"):
                    job.state, job.error, job.phase = (
                        "failed",
                        "Worker stopped unexpectedly; reuse settings to retry.",
                        "Worker interrupted",
                    )
            elif job.state == "pending" or age > 60:
                dispatch_ids.append(job.id)
        db.commit()
    for job_id in dispatch_ids:
        dispatch_one(job_id)


@celery.task(name="audio.cleanup")
def cleanup():
    initialize()
    cutoff = now() - timedelta(hours=settings.preview_ttl_hours)
    with SessionLocal() as db:
        expired = list(
            db.scalars(
                select(ProcessedVersion).where(
                    ProcessedVersion.kind == "preview", ProcessedVersion.created_at < cutoff
                )
            )
        )
        for version in expired:
            delete(version.storage_key)
            delete(version.analysis.get("waveform_key"))
            job = db.get(ProcessingJob, version.job_id)
            if job:
                job.version_id = None
                job.phase = "Preview expired; regenerate to listen"
            db.delete(version)
        db.commit()
        # Only remove unreferenced transient files. Originals and final exports are never aged out.
        referenced = {row.data.get("waveform_key") for row in db.scalars(select(AudioAnalysis))}
        referenced.update(row.storage_key for row in db.scalars(select(AudioFile)))
        for version in db.scalars(select(ProcessedVersion)):
            referenced.update([version.storage_key, version.analysis.get("waveform_key")])
    stale = now().timestamp() - settings.process_timeout_seconds * 8 - 600
    for area in AREAS:
        for item in (settings.storage_root / area).iterdir():
            if item.is_file() and item.stat().st_mtime < stale and f"{area}/{item.name}" not in referenced:
                item.unlink(missing_ok=True)

    # Crash-left subprocess log directories are confined to temporary storage.
    temporary = (settings.storage_root / "temporary").resolve()
    for item in temporary.iterdir():
        target = item.resolve()
        if (
            item.is_dir()
            and not item.is_symlink()
            and target.is_relative_to(temporary)
            and item.stat().st_mtime < stale
        ):
            shutil.rmtree(target)
    return {"expired_previews": len(expired)}
