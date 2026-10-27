# Verification record

Verified on 2026-10-24 using Docker Desktop on Windows, Linux containers, Python 3.12.15, PostgreSQL 17, Redis 7, FFmpeg 5.1.9, Chromaprint 1.5.1 and Chromium in the Playwright 1.59.1 container.

| Check | Result |
|---|---|
| Docker image build, local frontend assets and dependency locks | Passed |
| Startup migrations and dependency readiness | Passed |
| `docker compose exec web pytest -q` | **57 passed**, no skipped tests, 56.42 seconds |
| `docker compose exec web ruff check app tests scripts alembic` | Passed |
| `docker compose exec web alembic check` | No new upgrade operations detected |
| `docker compose exec web python scripts/smoke.py` | Passed against the live web service and separate Celery worker |
| Docker Chromium workflow | Passed, no JavaScript exceptions or console errors |
| Desktop 1440 px and mobile 390 px | Passed; no mobile horizontal overflow; screenshots visually reviewed |
| Nginx configuration and proxied `/api/ready` | Passed |
| Production Compose override configuration | Validated |
| Persistent services | Web, worker, scheduler, database, Redis and optional Nginx healthy |
| Temporary test recordings | Removed; application library empty after verification |

The automated suite uses actual generated audio, actual FFmpeg/Rubber Band and Chromaprint, an isolated PostgreSQL database, temporary audio storage, and Redis. It covers upload validation, misleading MIME/filenames, streamed body limits, formats, quotas, duration limits, metadata, acoustic analysis, mono/stereo/silence, phase-inverted spectra, fingerprints, DSP parameter rejection, presets, every export encoding, previews, processing, preview cache reuse, comparisons, safe cancellation and timeouts, persistence, range downloads, deletion, cleanup and interrupted worker recovery. Pitch is checked using FFT frequency measurement; tempo is checked using measured output duration, including independent pitch and tempo changes.

The live smoke check exercises upload → worker analysis → a real fingerprint → preview → cached preview → full processing → acoustic comparison → download → cancellation → deletion. The Chromium test also exercises custom preset create/rename/delete, effects, original/processed playback, A/B switching, history, local Swagger assets, and preservation of both waveforms after a download.

## Artifacts

- [Desktop studio screenshot](screenshots/studio-desktop.png)
- [Mobile studio screenshot](screenshots/studio-mobile.png)
- [Reproducible live smoke script](../scripts/smoke.py)
- [Reproducible browser script](../scripts/browser.cjs)
- [Docker browser-test profile](../compose.test.yaml)

## Scope and remaining limits

All defined automated tests were executed. TestClient emits one upstream deprecation warning about its httpx integration; the suite still passes. The runtime API and real browser workflow are unaffected.

Audio fixtures are short synthetic signals. Six-hour endurance, maximum-size file stress tests and Firefox/Safari codec coverage were not performed; the verified browser is Chromium. This is a local, single-user deployment. Public authentication, per-user authorization, HTTPS and load/rate-limit deployment validation require an appropriately configured deployment environment as documented in the README.

Reverb is explicitly a multi-tap echo approximation, spectral/dynamic-range measurements are labeled approximate, fingerprints cover at most 120 seconds, and local fingerprints do not predict Content ID recognition. Undefined measurements are displayed as unavailable.

## Preview playback follow-up

Added a dedicated Play preview / Pause preview button and an independent playback timer below the processed waveform. Chromium verified starting playback, pausing, resuming, automatic selection of processed audio, timer updates, preservation after downloads and mobile layout. The complete browser workflow passed without console errors. The browser test now waits for its own upload identifier and validates fixture ownership before cleanup.
