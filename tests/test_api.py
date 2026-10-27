from datetime import timedelta
from uuid import uuid4
import pytest
from app.core.database import SessionLocal
from app.core.config import settings
from app.models import AudioFile, ProcessingJob, ProcessedVersion, ProcessingPreset, now
from app.services.storage import resolve
from app.tasks.audio_tasks import execute
from app.tasks.maintenance import cleanup


def test_health_and_errors(client):
    assert client.get("/api/health").json()["status"] == "ok"
    assert client.get("/api/ready").status_code == 200
    assert client.get("/api/audio/not-a-uuid").status_code == 422
    assert client.get("/api/audio/" + str(uuid4())).status_code == 404
    assert (
        client.post(
            "/api/audio/upload",
            headers={"Origin": "https://untrusted.example"},
            files={"file": ("a.wav", b"test")},
        ).status_code
        == 403
    )
    assert client.get("/api/audio", headers={"Host": "untrusted.example"}).status_code == 400
    assert client.get("/").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_reject_non_audio_and_oversized(client, monkeypatch):
    response = client.post("/api/audio/upload", files={"file": ("renamed.mp3", b"not audio", "audio/mpeg")})
    assert response.status_code == 422
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    assert (
        client.post("/api/audio/upload", files={"file": ("a.wav", b"x" * (1024**2 + 1))}).status_code == 413
    )


def test_upload_analysis_download_and_delete(client, recording):
    info = client.get("/api/audio/" + recording).json()
    assert info["jobs"][0]["state"] == "completed"
    assert client.get(f"/api/audio/{recording}/analysis").json()["duration"] == pytest.approx(4, abs=0.05)
    assert "status" in client.get(f"/api/audio/{recording}/fingerprint").json()
    assert client.get(f"/api/audio/{recording}/waveform").json()["peaks"]
    ranged = client.get(f"/api/audio/{recording}/stream", headers={"Range": "bytes=0-100"})
    assert ranged.status_code == 206
    assert len(ranged.content) == 101
    with SessionLocal() as db:
        key = db.get(AudioFile, recording).storage_key
    assert client.delete("/api/audio/" + recording).status_code == 200
    assert not resolve(key).exists()
    assert client.get("/api/audio/" + recording).status_code == 404


def test_real_processing_preview_cache_and_comparison(client, recording):
    req = {
        "effects": {"mode": "creative", "pitch": 3, "tempo": 1.1},
        "export": {"format": "mp3", "sample_rate": 44100, "bitrate": 192},
    }
    first = client.post(f"/api/audio/{recording}/preview", json={**req, "start": 1, "duration": 10})
    assert first.status_code == 202, first.text
    job_id = first.json()["job_id"]
    assert (
        client.post(f"/api/audio/{recording}/preview", json={**req, "start": 1, "duration": 10}).json()[
            "job_id"
        ]
        == job_id
    )
    execute.run(job_id)
    job = client.get("/api/jobs/" + job_id).json()
    assert job["state"] == "completed", job
    assert client.post(f"/api/audio/{recording}/preview", json={**req, "start": 1, "duration": 10}).json()[
        "cached"
    ]
    version = job["version_id"]
    download = client.get(f"/api/audio/{recording}/download/{version}")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]
    full = client.post(f"/api/audio/{recording}/process", json=req).json()["job_id"]
    execute.run(full)
    full_job = client.get("/api/jobs/" + full).json()
    assert full_job["state"] == "completed", full_job
    comparison = client.get(f"/api/audio/{recording}/comparison/" + full_job["version_id"]).json()
    assert comparison["waveform_correlation"]["value"] is None
    assert isinstance(comparison["duration_difference_seconds"], float)
    assert client.delete(f"/api/audio/{recording}/versions/{version}").status_code == 200
    assert client.get(f"/api/audio/{recording}/download/{version}").status_code == 404


def test_cancellation_and_active_delete(client, recording):
    job = client.post(f"/api/audio/{recording}/process", json={}).json()["job_id"]
    assert client.delete("/api/audio/" + recording).status_code == 409
    assert client.post(f"/api/jobs/{job}/cancel").json()["state"] == "cancelled"
    execute.run(job)
    assert client.get("/api/jobs/" + job).json()["state"] == "cancelled"
    assert client.delete("/api/audio/" + recording).status_code == 200


def test_presets_crud_and_validation(client):
    assert len(client.get("/api/presets").json()) == 13
    created = client.post("/api/presets", json={"name": "Custom warm", "effects": {"bass": 2}})
    assert created.status_code == 201, created.text
    pid = created.json()["id"]
    assert client.patch("/api/presets/" + pid, json={"name": "Renamed"}).json()["name"] == "Renamed"
    with SessionLocal() as db:
        assert db.get(ProcessingPreset, pid).params["bass"] == 2
    assert client.post("/api/presets", json={"name": " ", "effects": {}}).status_code == 422
    assert client.delete("/api/presets/" + pid).status_code == 200
    assert client.delete("/api/presets/" + pid).status_code == 404


def test_cleanup_only_expires_preview(client, recording):
    full = client.post(f"/api/audio/{recording}/process", json={}).json()["job_id"]
    execute.run(full)
    preview = client.post(f"/api/audio/{recording}/preview", json={}).json()["job_id"]
    execute.run(preview)
    with SessionLocal() as db:
        job = db.get(ProcessingJob, preview)
        v = db.get(ProcessedVersion, job.version_id)
        assert v is not None
        preview_key = v.storage_key
        v.created_at = now() - timedelta(hours=25)
        original_key = db.get(AudioFile, recording).storage_key
        full_key = db.get(ProcessedVersion, db.get(ProcessingJob, full).version_id).storage_key
        db.commit()
    cleanup.run()
    assert not resolve(preview_key).exists()
    assert resolve(original_key).exists()
    assert resolve(full_key).exists()
    assert client.get("/api/jobs/" + preview).json()["version_id"] is None


def test_storage_and_foreign_version_security(client, recording):
    from app.core.exceptions import AudioError

    with pytest.raises(AudioError):
        resolve("../../etc/passwd")
    assert client.get(f"/api/audio/{recording}/download/{uuid4()}").status_code == 404
    assert client.post(f"/api/audio/{recording}/process", json={"effects": {"pitch": 99}}).status_code == 422
    assert (
        client.post(
            f"/api/audio/{recording}/process", json={"export": {"format": "mp3", "sample_rate": 96000}}
        ).status_code
        == 422
    )
    assert client.post(f"/api/audio/{recording}/preview", json={"start": 10}).status_code == 422


def test_queued_job_dispatch_and_interrupted_recovery(client, recording, monkeypatch):
    from app.tasks.maintenance import dispatch

    dispatched = []
    monkeypatch.setattr("app.tasks.maintenance.dispatch_one", lambda job_id: dispatched.append(job_id))
    pending = client.post(f"/api/audio/{recording}/process", json={}).json()["job_id"]
    interrupted = client.post(f"/api/audio/{recording}/process", json={}).json()["job_id"]
    with SessionLocal() as db:
        job = db.get(ProcessingJob, interrupted)
        job.state = "processing"
        job.updated_at = now() - timedelta(minutes=5)
        db.commit()
    dispatch.run()
    assert pending in dispatched
    assert client.get("/api/jobs/" + interrupted).json()["state"] == "failed"


def test_filename_and_content_validation(client, sine, monkeypatch):
    response = client.post(
        "/api/audio/upload",
        files={"file": ("../../malicious.exe", sine.read_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 202
    info = client.get("/api/audio/" + response.json()["audio_id"]).json()
    assert info["name"] == "malicious.exe"
    with SessionLocal() as db:
        stored = db.get(AudioFile, info["id"])
        assert stored.storage_key.startswith("originals/") and ".." not in stored.storage_key
    # Size limits are enforced for streamed requests too, before the multipart parser.
    monkeypatch.setattr(settings, "max_upload_mb", 1)
    header = b'--test\r\nContent-Disposition: form-data; name="file"; filename="huge.wav"\r\nContent-Type: audio/wav\r\n\r\n'
    huge = client.post(
        "/api/audio/upload",
        content=iter([header] + [b"x" * (1024**2)] * 3 + [b"\r\n--test--\r\n"]),
        headers={"Content-Type": "multipart/form-data; boundary=test"},
    )
    assert huge.status_code == 413


def test_quota_and_duration_validation(client, sine, monkeypatch):
    monkeypatch.setattr(settings, "storage_quota_gb", 0.000001)
    response = client.post("/api/audio/upload", files={"file": ("tone.wav", sine.read_bytes())})
    assert response.status_code == 507
    monkeypatch.setattr(settings, "storage_quota_gb", 20)
    monkeypatch.setattr(settings, "max_duration_seconds", 1)
    assert (
        client.post("/api/audio/upload", files={"file": ("tone.wav", sine.read_bytes())}).status_code == 422
    )
