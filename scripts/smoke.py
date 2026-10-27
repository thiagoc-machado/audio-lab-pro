"""End-to-end exercise against live FastAPI and the separate Celery worker."""

import io
import time
from uuid import uuid4
import httpx
import numpy as np
import soundfile as sf

client = httpx.Client(base_url="http://web:8000", timeout=180, headers={"Host": "localhost:8000"})


def checked(response):
    response.raise_for_status()
    return response.json()


def wait(job_id):
    started = time.monotonic()
    while time.monotonic() - started < 240:
        job = checked(client.get("/api/jobs/" + job_id))
        if job["state"] in {"failed", "cancelled"}:
            raise AssertionError(job)
        if job["state"] == "completed":
            return job
        time.sleep(0.5)
    raise AssertionError("Live worker job timed out")


def main():
    for _ in range(60):
        try:
            checked(client.get("/api/ready"))
            break
        except httpx.HTTPError:
            time.sleep(1)
    else:
        raise AssertionError("Application did not become ready")
    rate = 48000
    t = np.arange(rate * 16) / rate
    # A changing harmonic signal produces a meaningful Chromaprint representation.
    x = 0.12 * np.sin(2 * np.pi * (220 * t + 4 * t * t)) + 0.05 * np.sin(2 * np.pi * 660 * t)
    buffer = io.BytesIO()
    sf.write(buffer, np.column_stack([x, x * 0.9]), rate, format="WAV", subtype="PCM_24")
    uploaded = checked(
        client.post(
            "/api/audio/upload",
            files={"file": ("smoke-" + uuid4().hex + ".wav", buffer.getvalue(), "audio/wav")},
        )
    )
    audio_id = uploaded["audio_id"]
    jobs = [uploaded["job_id"]]
    try:
        wait(jobs[0])
        analysis = checked(client.get(f"/api/audio/{audio_id}/analysis"))
        assert abs(analysis["duration"] - 16) < 0.05
        fp = checked(client.get(f"/api/audio/{audio_id}/fingerprint"))
        assert fp["status"] == "completed", fp
        request = {
            "effects": {"mode": "creative", "pitch": 3, "tempo": 1.1, "normalize": True},
            "export": {"format": "mp3", "bitrate": 192, "sample_rate": 48000},
        }
        preview = checked(
            client.post(f"/api/audio/{audio_id}/preview", json={**request, "start": 2, "duration": 10})
        )
        jobs.append(preview["job_id"])
        wait(jobs[-1])
        cached = checked(
            client.post(f"/api/audio/{audio_id}/preview", json={**request, "start": 2, "duration": 10})
        )
        assert cached["cached"] and cached["job_id"] == jobs[-1]
        full = checked(client.post(f"/api/audio/{audio_id}/process", json=request))
        jobs.append(full["job_id"])
        finished = wait(jobs[-1])
        version = finished["version_id"]
        data = client.get(f"/api/audio/{audio_id}/download/{version}")
        data.raise_for_status()
        assert len(data.content) > 1000
        comparison = checked(client.get(f"/api/audio/{audio_id}/comparison/{version}"))
        assert comparison["duration_difference_seconds"] < -0.5
        # Exercise cancellation through the API and the real worker.
        cancelled = checked(client.post(f"/api/audio/{audio_id}/process", json=request))
        jobs.append(cancelled["job_id"])
        checked(client.post("/api/jobs/" + jobs[-1] + "/cancel"))
        for _ in range(40):
            if checked(client.get("/api/jobs/" + jobs[-1]))["state"] == "cancelled":
                break
            time.sleep(0.25)
        else:
            raise AssertionError("Cancellation did not finish")
        print(
            "PASS: live upload, worker analysis, real fingerprint, preview, cache, full DSP, comparison, download and cancellation"
        )
    finally:
        for job_id in jobs:
            client.post("/api/jobs/" + job_id + "/cancel")
        for _ in range(40):
            response = client.delete("/api/audio/" + audio_id)
            if response.status_code == 200:
                break
            time.sleep(0.25)
        else:
            raise AssertionError("Smoke recording cleanup did not complete")
        client.close()


if __name__ == "__main__":
    main()
