import hashlib
import json
from pathlib import Path
from uuid import UUID
from fastapi import APIRouter, Depends, UploadFile, File
from fastapi.responses import FileResponse
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from app.core.config import settings
from app.core.database import get_db
from app.core.exceptions import AudioError
from app.models import (
    AudioFile,
    AudioAnalysis,
    AudioFingerprint,
    ProcessingJob,
    ProcessedVersion,
    ProcessingPreset,
)
from app.schemas.processing import ProcessRequest, PreviewRequest, PresetCreate, PresetUpdate
from app.services import storage
from app.services.ffmpeg import probe, capabilities
from app.services.presets import builtins
from app.tasks.audio_tasks import dispatch_one, TERMINAL

router = APIRouter(prefix="/api")


def identifier(value):
    try:
        return str(UUID(value))
    except ValueError:
        raise AudioError("Invalid identifier", 422) from None


def audio_or_404(db, audio_id):
    audio = db.get(AudioFile, identifier(audio_id))
    if not audio:
        raise AudioError("Recording not found", 404)
    return audio


def version_or_404(db, audio_id, version_id):
    audio_or_404(db, audio_id)
    version = db.get(ProcessedVersion, identifier(version_id))
    if not version or version.audio_id != audio_id:
        raise AudioError("Version not found", 404)
    return version


def audio_json(audio):
    return {
        "id": audio.id,
        "name": audio.name,
        "size": audio.size,
        "metadata": audio.metadata_json,
        "created_at": audio.created_at.isoformat(),
    }


def job_json(job):
    return {
        k: getattr(job, k)
        for k in (
            "id",
            "audio_id",
            "kind",
            "state",
            "progress",
            "phase",
            "error",
            "cancel_requested",
            "version_id",
            "params",
            "created_at",
            "updated_at",
        )
    }


def version_json(v):
    return {
        "id": v.id,
        "audio_id": v.audio_id,
        "job_id": v.job_id,
        "kind": v.kind,
        "params": v.params,
        "analysis": {k: val for k, val in v.analysis.items() if k != "waveform_key"},
        "fingerprint": v.fingerprint,
        "comparison": v.comparison,
        "created_at": v.created_at.isoformat(),
    }


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1 FROM alembic_version"))
        storage.redis.ping()
        storage.initialize()
        return {"status": "ready"}
    except Exception:
        raise AudioError("Dependencies are not ready", 503) from None


@router.get("/capabilities")
def get_capabilities():
    return {
        **capabilities(),
        "max_upload_mb": settings.max_upload_mb,
        "max_duration_seconds": settings.max_duration_seconds,
        "worker_online": bool(storage.redis.exists("scheduler-heartbeat")),
    }


@router.post("/audio/upload", status_code=202)
def upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
    key = storage.allocate("originals", "audio")
    path = storage.resolve(key)
    saved = False
    try:
        with storage.reservation(settings.max_upload_mb * 1024**2):
            size = 0
            with path.open("wb") as dest:
                while chunk := file.file.read(1024**2):
                    size += len(chunk)
                    if size > settings.max_upload_mb * 1024**2:
                        raise AudioError(f"Upload exceeds {settings.max_upload_mb} MB", 413)
                    dest.write(chunk)
            metadata = probe(path)
            name = Path((file.filename or "recording").replace("\\", "/")).name
            name = "".join(c for c in name if c.isprintable())[:255] or "recording"
            audio = AudioFile(name=name, storage_key=key, size=size, metadata_json=metadata)
            db.add(audio)
            db.flush()
            job = ProcessingJob(audio_id=audio.id, kind="analysis")
            db.add(job)
            db.commit()
            saved = True
            dispatch_one(job.id)
            return {"audio_id": audio.id, "job_id": job.id}
    finally:
        file.file.close()
        if not saved:
            path.unlink(missing_ok=True)


@router.get("/audio")
def list_audio(db: Session = Depends(get_db)):
    return [audio_json(a) for a in db.scalars(select(AudioFile).order_by(AudioFile.created_at.desc()))]


@router.get("/audio/{audio_id}")
def get_audio(audio_id: str, db: Session = Depends(get_db)):
    audio = audio_or_404(db, audio_id)
    jobs = list(
        db.scalars(
            select(ProcessingJob)
            .where(ProcessingJob.audio_id == audio.id)
            .order_by(ProcessingJob.created_at.desc())
        )
    )
    return {**audio_json(audio), "jobs": [job_json(j) for j in jobs]}


@router.get("/audio/{audio_id}/analysis")
def get_analysis(audio_id: str, db: Session = Depends(get_db)):
    audio_or_404(db, audio_id)
    record = db.get(AudioAnalysis, audio_id)
    if not record:
        raise AudioError("Analysis is not ready", 409)
    return {k: v for k, v in record.data.items() if k != "waveform_key"}


@router.get("/audio/{audio_id}/fingerprint")
def get_fingerprint(audio_id: str, db: Session = Depends(get_db)):
    audio_or_404(db, audio_id)
    record = db.get(AudioFingerprint, audio_id)
    if not record:
        raise AudioError("Fingerprint is not ready", 409)
    return record.data


@router.get("/audio/{audio_id}/waveform")
def waveform(audio_id: str, version_id: str | None = None, db: Session = Depends(get_db)):
    audio_or_404(db, audio_id)
    if version_id:
        data = version_or_404(db, audio_id, version_id).analysis
    else:
        record = db.get(AudioAnalysis, audio_id)
        if not record:
            raise AudioError("Waveform is not ready", 409)
        data = record.data
    path = storage.resolve(data["waveform_key"])
    if not path.is_file():
        raise AudioError("Waveform has expired", 404)
    return FileResponse(path, media_type="application/json")


@router.get("/audio/{audio_id}/stream")
def stream(audio_id: str, db: Session = Depends(get_db)):
    audio = audio_or_404(db, audio_id)
    path = storage.resolve(audio.storage_key)
    if not path.is_file():
        raise AudioError("Audio file is missing", 404)
    codec = audio.metadata_json["codec"]
    media = {
        "mp3": "audio/mpeg",
        "flac": "audio/flac",
        "vorbis": "audio/ogg",
        "opus": "audio/ogg",
        "aac": "audio/mp4",
        "alac": "audio/mp4",
    }.get(codec, "audio/wav")
    if audio.metadata_json["container"] == "aac":
        media = "audio/aac"
    return FileResponse(path, media_type=media, content_disposition_type="inline")


def create_job(db, audio_id, payload, kind):
    audio = audio_or_404(db, audio_id)
    if not db.get(AudioAnalysis, audio.id):
        raise AudioError("Wait for original analysis to complete", 409)
    if (payload.effects.pitch or payload.effects.tempo != 1) and not capabilities()["rubberband"]:
        raise AudioError("Independent pitch and tempo processing requires FFmpeg Rubber Band support")
    if kind == "preview" and payload.start >= audio.metadata_json["duration"]:
        raise AudioError("Preview start must be before the end of the recording")
    params = payload.model_dump()
    cache_key = None
    with storage.redis.lock(f"submit:{audio.id}", timeout=15, blocking_timeout=10):
        if kind == "preview":
            cache_key = hashlib.sha256(
                json.dumps(
                    {"source": audio.id, "params": {k: v for k, v in params.items() if k != "preset_name"}},
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode()
            ).hexdigest()
            existing = list(
                db.scalars(
                    select(ProcessingJob)
                    .where(
                        ProcessingJob.cache_key == cache_key,
                        ProcessingJob.state.in_(["pending", "queued", "processing", "completed"]),
                    )
                    .order_by(ProcessingJob.created_at.desc())
                )
            )
            for previous in existing:
                version = db.get(ProcessedVersion, previous.version_id) if previous.version_id else None
                if previous.state != "completed" or (
                    version and storage.resolve(version.storage_key).is_file()
                ):
                    return {"job_id": previous.id, "cached": previous.state == "completed"}
        active = db.scalar(
            select(__import__("sqlalchemy").func.count())
            .select_from(ProcessingJob)
            .where(ProcessingJob.state.in_(["pending", "queued", "processing"]))
        )
        if active >= 32:
            raise AudioError("Job queue is full. Wait for active jobs to finish.", 429)
        job = ProcessingJob(audio_id=audio.id, kind=kind, params=params, cache_key=cache_key)
        db.add(job)
        db.commit()
    dispatch_one(job.id)
    return {"job_id": job.id, "cached": False}


@router.post("/audio/{audio_id}/preview", status_code=202)
def preview(audio_id: str, payload: PreviewRequest, db: Session = Depends(get_db)):
    return create_job(db, audio_id, payload, "preview")


@router.post("/audio/{audio_id}/process", status_code=202)
def process(audio_id: str, payload: ProcessRequest, db: Session = Depends(get_db)):
    return create_job(db, audio_id, payload, "process")


@router.get("/jobs/{job_id}")
def get_job(job_id: str, db: Session = Depends(get_db)):
    job = db.get(ProcessingJob, identifier(job_id))
    if not job:
        raise AudioError("Job not found", 404)
    return job_json(job)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, db: Session = Depends(get_db)):
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == identifier(job_id)).with_for_update())
    if not job:
        raise AudioError("Job not found", 404)
    if job.state not in TERMINAL:
        job.cancel_requested = True
        if job.state in {"pending", "queued"}:
            job.state, job.phase = "cancelled", "Cancelled"
        db.commit()
        storage.redis.set(f"cancel:{job.id}", "1", ex=settings.process_timeout_seconds * 8)
    return job_json(job)


@router.get("/audio/{audio_id}/versions")
def versions(audio_id: str, db: Session = Depends(get_db)):
    audio_or_404(db, audio_id)
    return [
        version_json(v)
        for v in db.scalars(
            select(ProcessedVersion)
            .where(ProcessedVersion.audio_id == audio_id)
            .order_by(ProcessedVersion.created_at.desc())
        )
    ]


@router.get("/audio/{audio_id}/comparison/{version_id}")
def comparison(audio_id: str, version_id: str, db: Session = Depends(get_db)):
    return version_or_404(db, audio_id, version_id).comparison


@router.get("/audio/{audio_id}/download/{version_id}")
def download(audio_id: str, version_id: str, inline: bool = False, db: Session = Depends(get_db)):
    version = version_or_404(db, audio_id, version_id)
    path = storage.resolve(version.storage_key)
    if not path.is_file():
        raise AudioError("Export is no longer available", 404)
    name = Path(audio_or_404(db, audio_id).name).stem + "-" + version.id[:8] + path.suffix
    media = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".flac": "audio/flac"}[path.suffix]
    return FileResponse(
        path, filename=name, media_type=media, content_disposition_type="inline" if inline else "attachment"
    )


@router.delete("/audio/{audio_id}/versions/{version_id}")
def delete_version(audio_id: str, version_id: str, db: Session = Depends(get_db)):
    version = version_or_404(db, audio_id, version_id)
    keys = [version.storage_key, version.analysis.get("waveform_key")]
    job = db.get(ProcessingJob, version.job_id)
    if job:
        job.version_id = None
        job.phase = "Version deleted"
    db.delete(version)
    db.commit()
    for key in keys:
        storage.delete(key)
    return {"deleted": True}


@router.delete("/audio/{audio_id}")
def delete_audio(audio_id: str, db: Session = Depends(get_db)):
    with storage.redis.lock(f"submit:{identifier(audio_id)}", timeout=20, blocking_timeout=10):
        audio = audio_or_404(db, audio_id)
        jobs = list(db.scalars(select(ProcessingJob).where(ProcessingJob.audio_id == audio.id)))
        if any(j.state not in TERMINAL for j in jobs):
            raise AudioError(
                "Cancel active jobs and wait for them to stop before deleting this recording", 409
            )
        keys = [audio.storage_key]
        record = db.get(AudioAnalysis, audio.id)
        if record:
            keys.append(record.data.get("waveform_key"))
        for version in db.scalars(select(ProcessedVersion).where(ProcessedVersion.audio_id == audio.id)):
            keys += [version.storage_key, version.analysis.get("waveform_key")]
        db.delete(audio)
        db.commit()
        for key in keys:
            storage.delete(key)
    return {"deleted": True}


@router.get("/presets")
def presets(db: Session = Depends(get_db)):
    return builtins() + [
        {"id": p.id, "name": p.name, "builtin": False, "effects": p.params}
        for p in db.scalars(select(ProcessingPreset).order_by(ProcessingPreset.created_at))
    ]


@router.post("/presets", status_code=201)
def save_preset(payload: PresetCreate, db: Session = Depends(get_db)):
    p = ProcessingPreset(name=payload.name.strip(), params=payload.effects.model_dump())
    db.add(p)
    db.commit()
    return {"id": p.id, "name": p.name, "effects": p.params, "builtin": False}


@router.patch("/presets/{preset_id}")
def update_preset(preset_id: str, payload: PresetUpdate, db: Session = Depends(get_db)):
    p = db.get(ProcessingPreset, identifier(preset_id))
    if not p:
        raise AudioError("Custom preset not found", 404)
    if payload.name is not None:
        p.name = payload.name.strip()
    if payload.effects is not None:
        p.params = payload.effects.model_dump()
    db.commit()
    return {"id": p.id, "name": p.name, "effects": p.params, "builtin": False}


@router.delete("/presets/{preset_id}")
def delete_preset(preset_id: str, db: Session = Depends(get_db)):
    p = db.get(ProcessingPreset, identifier(preset_id))
    if not p:
        raise AudioError("Custom preset not found", 404)
    db.delete(p)
    db.commit()
    return {"deleted": True}
