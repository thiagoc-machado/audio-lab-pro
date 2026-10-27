import os
from uuid import uuid4
import numpy as np
import pytest
import soundfile as sf
from sqlalchemy import create_engine, text

# Tests run against an isolated PostgreSQL database and isolated audio storage.
original_url = os.environ.get("DATABASE_URL", "postgresql+psycopg://audio:local-audio-password@db:5432/audio")
test_name = "audio_test_" + uuid4().hex
os.environ["DATABASE_URL"] = original_url.rsplit("/", 1)[0] + "/" + test_name
os.environ["STORAGE_ROOT"] = "/tmp/" + test_name
os.environ["ALLOWED_HOSTS"] = "localhost,127.0.0.1,testserver"


@pytest.fixture(scope="session", autouse=True)
def database():
    admin = create_engine(original_url, isolation_level="AUTOCOMMIT")
    with admin.connect() as connection:
        connection.execute(text(f'CREATE DATABASE "{test_name}"'))
    from alembic.config import Config
    from alembic import command
    from app.services.storage import initialize

    command.upgrade(Config("alembic.ini"), "head")
    initialize()
    yield
    from app.core.database import engine

    engine.dispose()
    with admin.connect() as connection:
        connection.execute(text(f'DROP DATABASE "{test_name}" WITH (FORCE)'))
    admin.dispose()
    import shutil

    shutil.rmtree(os.environ["STORAGE_ROOT"], ignore_errors=True)


@pytest.fixture(autouse=True)
def clean_database(database):
    from app.core.database import engine

    with engine.begin() as connection:
        connection.execute(text("TRUNCATE audio_files, processing_presets CASCADE"))


@pytest.fixture
def client(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    monkeypatch.setattr("app.api.routes.dispatch_one", lambda job_id: None)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def sine(tmp_path):
    rate = 48000
    t = np.arange(rate * 4) / rate
    signal = 0.2 * np.sin(2 * np.pi * 440 * t)
    path = tmp_path / "tone.wav"
    sf.write(path, np.column_stack([signal, signal * 0.8]), rate, subtype="PCM_24")
    return path


@pytest.fixture
def recording(client, sine):
    with sine.open("rb") as handle:
        response = client.post("/api/audio/upload", files={"file": ("tone.wav", handle, "audio/wav")})
    assert response.status_code == 202, response.text
    from app.tasks.audio_tasks import execute

    execute.run(response.json()["job_id"])
    return response.json()["audio_id"]
