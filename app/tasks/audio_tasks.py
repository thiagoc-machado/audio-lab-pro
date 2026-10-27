import logging
import time
from sqlalchemy import select
from redis.exceptions import LockNotOwnedError
from app.core.database import SessionLocal
from app.core.exceptions import AudioError, Cancelled
from app.models import AudioFile, AudioAnalysis, AudioFingerprint, ProcessingJob, ProcessedVersion, now
from app.schemas.processing import ProcessRequest, PreviewRequest
from app.services.storage import redis, initialize, reservation, resolve, allocate, delete
from app.services.analyzer import analyze
from app.services.fingerprints import fingerprint
from app.services.processor import process, EXTENSIONS
from app.services.comparison import compare
from app.tasks.celery_app import celery

logger = logging.getLogger(__name__)
TERMINAL = {"completed", "failed", "cancelled"}


def dispatch_one(job_id):
    with SessionLocal() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if not job or job.state in TERMINAL or job.state == "processing":
            return
        job.state = "queued"
        job.updated_at = now()
        db.commit()
    try:
        execute.apply_async(args=[job_id], task_id=job_id)
    except Exception:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if job and job.state == "queued":
                job.state = "pending"
                db.commit()
        logger.exception("Broker unavailable; persisted job will be dispatched by scheduler")


def update(job_id, **values):
    with SessionLocal() as db:
        job = db.get(ProcessingJob, job_id)
        if job:
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = now()
            db.commit()


@celery.task(name="audio.execute")
def execute(job_id):
    initialize()
    lock = redis.lock(f"job-lock:{job_id}", timeout=60, blocking=False)
    if not lock.acquire(blocking=False):
        return
    output = None
    analysis_data = None
    keep_output = False
    try:
        with SessionLocal() as db:
            job = db.get(ProcessingJob, job_id)
            if not job or job.state in TERMINAL:
                return
            audio = db.get(AudioFile, job.audio_id)
            if not audio:
                return
            source = resolve(audio.storage_key)
            audio_id, kind, params, metadata = audio.id, job.kind, job.params, audio.metadata_json
        last_heartbeat = 0

        def cancelled():
            nonlocal last_heartbeat
            if redis.exists(f"cancel:{job_id}"):
                return True
            if time.monotonic() - last_heartbeat > 5:
                lock.extend(60, replace_ttl=True)
                with SessionLocal() as db:
                    current = db.get(ProcessingJob, job_id)
                    if not current or current.cancel_requested or current.state in {"cancelled", "failed"}:
                        return True
                    current.updated_at = now()
                    db.commit()
                last_heartbeat = time.monotonic()
            return False

        def phase(message):
            if cancelled():
                raise Cancelled()
            update(job_id, phase=message, progress=0)

        def progress(value):
            update(job_id, progress=round(value, 2))

        update(job_id, state="processing", phase="Starting", progress=0)
        if cancelled():
            raise Cancelled()
        seconds = (
            metadata["duration"]
            if kind != "preview"
            else min(params["duration"], metadata["duration"] - params["start"])
        )
        rate = (
            metadata["sample_rate"]
            if kind == "analysis"
            else max(params["export"]["sample_rate"], metadata["sample_rate"])
        )
        # Float intermediates + decoded analysis + encoded export, including worst-case tempo expansion.
        estimate = int(seconds * rate * metadata["channels"] * 4 * (2 if kind == "analysis" else 8)) + 1024**2
        with reservation(estimate):
            if kind == "analysis":
                phase("Decoding original for acoustic analysis")
                analysis_data = analyze(source, cancelled=cancelled, progress=progress)
                phase("Calculating local fingerprint")
                fp = fingerprint(source, cancelled)
                with SessionLocal() as db:
                    current = db.scalar(
                        select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
                    )
                    if current.cancel_requested or redis.exists(f"cancel:{job_id}"):
                        raise Cancelled()
                    db.merge(AudioAnalysis(audio_id=audio_id, data=analysis_data))
                    db.merge(AudioFingerprint(audio_id=audio_id, data=fp))
                    current.state, current.progress, current.phase = "completed", 100, "Analysis complete"
                    db.commit()
                    keep_output = True
            else:
                request = (
                    PreviewRequest.model_validate(params)
                    if kind == "preview"
                    else ProcessRequest.model_validate(params)
                )
                output = allocate(
                    "previews" if kind == "preview" else "processed", EXTENSIONS[request.export.format]
                )
                process(
                    source,
                    resolve(output),
                    request,
                    metadata,
                    start=params.get("start"),
                    duration=seconds if kind == "preview" else None,
                    cancelled=cancelled,
                    progress=progress,
                    phase=phase,
                )
                phase("Analyzing processed audio")
                analysis_data = analyze(resolve(output), cancelled=cancelled, progress=progress)
                phase("Calculating processed fingerprint")
                fp = fingerprint(resolve(output), cancelled)
                comparison = {}
                if kind == "process":
                    with SessionLocal() as db:
                        original_analysis = db.get(AudioAnalysis, audio_id)
                        original_fp = db.get(AudioFingerprint, audio_id)
                        if not original_analysis or not original_fp:
                            raise AudioError("Original analysis must complete before processing")
                        a, fa = original_analysis.data, original_fp.data
                    phase("Comparing original and processed recording")
                    comparison = compare(
                        source, resolve(output), a, analysis_data, fa, fp, request.effects.tempo, cancelled
                    )
                with SessionLocal() as db:
                    current = db.scalar(
                        select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update()
                    )
                    if current.cancel_requested or redis.exists(f"cancel:{job_id}"):
                        raise Cancelled()
                    version = ProcessedVersion(
                        audio_id=audio_id,
                        job_id=job_id,
                        kind=kind,
                        storage_key=output,
                        params=params,
                        analysis=analysis_data,
                        fingerprint=fp,
                        comparison=comparison,
                    )
                    db.add(version)
                    db.flush()
                    current.version_id = version.id
                    current.state, current.progress, current.phase = "completed", 100, "Ready"
                    db.commit()
                    keep_output = True
    except Cancelled:
        update(job_id, state="cancelled", phase="Cancelled", progress=0)
    except Exception as exc:
        logger.exception("Job %s failed", job_id)
        message = (
            exc.message
            if isinstance(exc, AudioError)
            else "Processing failed. See container logs for details."
        )
        update(job_id, state="failed", phase="Failed", error=message)
    finally:
        if not keep_output:
            delete(output)
            if analysis_data:
                delete(analysis_data.get("waveform_key"))
        redis.delete(f"cancel:{job_id}")
        try:
            lock.release()
        except LockNotOwnedError:
            pass
