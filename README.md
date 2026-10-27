# Audio Lab Pro

A self-hosted audio workbench built with Python 3.12, FastAPI, Jinja2, locally compiled Tailwind CSS, WaveSurfer.js, PostgreSQL, Redis, Celery, FFmpeg and Chromaprint. All runtime components run in Docker. The default deployment is a local, single-user workspace without authentication.

## Start

Install Docker Desktop (Windows/macOS) or Docker Engine with the Compose plugin (Linux). Start Docker, then from this directory:

```bash
cp .env.example .env
docker compose up --build -d
```

On Windows PowerShell use `Copy-Item .env.example .env` instead of `cp` if needed. Open **http://localhost:8000**. Interactive API documentation is at **http://localhost:8000/docs**. Database migrations run automatically in the one-shot `migrate` service before the application services start. The first build downloads dependencies and audio tools.

```bash
docker compose ps
docker compose logs -f
docker compose down
```

`down` preserves recordings, presets, history and database data. Adding `--volumes` permanently deletes persistent data; use it only when you intend to erase the workspace.

## Screenshots

[Desktop studio](docs/screenshots/studio-desktop.png) · [Mobile studio](docs/screenshots/studio-mobile.png)

## Architecture

Browser requests go to FastAPI (`web`), which renders Jinja2 HTML and exposes validated REST endpoints. Upload validation uses FFprobe in a request thread; expensive acoustic analysis, fingerprinting, previews, DSP and exports run in the separate `worker` service. PostgreSQL stores metadata, measurements, fingerprints, presets, jobs and version references. Redis brokers tasks, coordinates cancellation, guards duplicate work and serializes storage quota reservations. Celery Beat (`scheduler`) dispatches persisted pending jobs and schedules cleanup. Audio is stored in the shared `audio_storage` named volume, outside the database. PostgreSQL and Redis have no published ports. Web binds to host loopback only.

Jobs use pending → queued → processing → completed / failed / cancelled states. Job rows act as a durable dispatch outbox: a broker outage leaves jobs pending for redispatch. Duplicate deliveries are guarded by per-job Redis leases and terminal-state checks. Worker heartbeats renew leases; interrupted processing jobs become failed after the lease expires and the database heartbeat has been stale for two minutes. Reuse their settings to retry. The default worker has two prefork processes and prefetch one, limiting simultaneous audio operations. Progress comes from FFmpeg output timestamps for the current decoding, DSP or encoding pass; it resets for a new pass. Fingerprinting and measurement phases do not display fabricated percentages.

## Workflow

1. Drop an MP3, WAV, FLAC, OGG or M4A/AAC file, or select **Browse files**. The actual container, codec, duration, channels and sample rate are validated; filename extensions and MIME claims are not trusted. Mono/stereo inputs from 8–192 kHz are supported, up to six hours by default. Embedded cover art is accepted; video tracks and multiple audio tracks are rejected.
2. Wait for automatic analysis. Original waveforms use precomputed peaks (up to 4,000 per channel), keeping long recordings out of browser decoding memory. Seek by clicking the waveform; zoom with the slider. Drag to select a preview region. The available preview durations are 10, 20, 30 and 60 seconds, with a 20-second default. The region's start and selected duration define rendering and looping; dragging arbitrary region boundaries snaps the duration selector to the nearest supported length.
3. Choose Preserve for restrained mastering or Creative for broader processing ranges. Start from one of 13 presets and adjust individual effects. Save, rename and delete custom presets; they persist in PostgreSQL. Reset restores neutral settings with the protective output limiter enabled.
4. Select the output encoding, sample rate and MP3 bitrate. Click **Generate preview**. A real Celery job renders the selected source region, clamped to the end of the file. Identical source/parameters/region/encoding requests reuse completed previews while they remain available. **Regenerate preview** uses the current settings. Changing controls does not alter previously rendered audio.
5. Use **Play preview** below the processed waveform to play or pause generated audio and see its playback time. You can also play/pause, seek, set volume and loop the selection from the main transport. A/B switches map positions using preview offset and tempo. A requested position outside the preview is clamped to the available audio. Browser codec support varies: use MP3 or WAV 16-bit for widely supported previews; all formats can be downloaded. WAV 24-bit and FLAC may require an external player in some browsers.
6. **Process full recording** creates a separate final version, analyzes it, calculates a local fingerprint and stores comparison metrics. Download the result, listen to a previous version or reuse its settings. Processing history lists upload and processing dates, mode, preset, encoding and status. Cancel active jobs before deleting a recording. Deleting a recording removes its versions, measurements and associated files. Individual versions may also be deleted.

## Audio measurements

| Measurement | Calculation and limits |
|---|---|
| Duration, codec, container, size, bitrate, sample rate, channels, bit depth | FFprobe metadata. Unknown bit depth is `null`, common for compressed formats. |
| Integrated LUFS, true peak dBTP, loudness range LU | FFmpeg `loudnorm` input measurements using EBU R128 / ITU BS.1770. True peak uses the filter's oversampled measurement. Non-finite measurements for silence are `null`. |
| RMS dBFS | `20 log10(sqrt(mean(x²)))`, using every decoded sample across channels. |
| Sample peak / peak dBFS | Maximum absolute decoded amplitude and `20 log10(peak)`. Distinct from true peak. |
| Approximate dynamic range | 95th-to-10th percentile ratio of active block RMS, excluding blocks below −100 dBFS. Depends on block length; **not** a standardized DR score or loudness range. |
| Stereo balance | `10 log10(right energy / left energy)`; positive means right-heavy. Mono is unavailable. |
| Clipping indicator | Count of decoded sample values reaching full scale; near-full-scale integer PCM also counts. This is an indicator, not proof of audible clipping. |
| Silence segments | FFmpeg `silencedetect`, below −50 dB for at least 0.5 seconds. |
| Spectral centroid / band energy | Approximate power-weighted centroid and normalized energy in six bands; 2,048-sample Hann FFTs from the start of evenly spaced waveform blocks, not a full-resolution spectrogram. |
| Stereo correlation | Full-recording, mean-centered Pearson correlation of left/right samples. Mono and zero-variance signals are unavailable. |
| Aligned waveform correlation | First 120 seconds, mono at 8 kHz, FFT alignment search within ±2 seconds followed by Pearson correlation on overlapping samples. Unavailable when tempo differs. Repetitive tones can have ambiguous alignment. No universal recognition similarity is inferred. |

Analysis decodes to temporary floating-point PCM and streams through bounded-size blocks rather than loading entire recordings into memory. Analysis may take multiple passes. Large/high-rate/slow-tempo inputs need substantial temporary disk space; a conservative reservation may refuse them before reaching the configured storage quota. Temporary PCM is removed after use.

## Fingerprint interpretation

Chromaprint's `fpcalc` produces a **local acoustic fingerprint** for up to the first 120 seconds. The UI exposes the algorithm, fingerprint duration, generation timestamp, actual encoded representation and a SHA-256 digest of that representation. Very short or silent recordings may not produce a fingerprint and show an explicit unavailable status. A fingerprint failure does not invent data or discard an otherwise valid export.

Comparisons report exact representation equality and hash equality as booleans, plus duration, loudness, centroid, frequency-band and aligned waveform differences. Cryptographic hash differences are never converted to similarity percentages. Chromaprint does **not** extract YouTube Content ID fingerprints, verify rights, predict claims or bypass copyright recognition. Local measurements do not establish recognition behavior of proprietary services.

## DSP chain and encodings

Independent pitch/tempo uses FFmpeg's Rubber Band filter. The Docker image includes this filter; capability detection disables those controls if it is unavailable. There is no playback-rate substitution. The processing chain is: pitch/tempo → optional FFT denoising → high/low-pass → shelving and parametric EQ → compression → saturation → chorus/flanger/phaser → short multi-tap reverb approximation → echo/delay → stereo width → protective limiter → optional two-pass loudness normalization → final limiter → encoding. All numeric parameters are finite, range-validated Pydantic values and are never inserted into a shell command. Stereo width is a no-op for mono recordings. Noise reduction uses generic FFT denoising, without an acquired noise profile. Reverb is a deterministic multi-tap echo approximation, not room convolution. Effect tails may extend duration.

A lossless floating-point PCM intermediate avoids repeated lossy encoding. Loudness normalization first measures the effected audio and then applies measured settings; silent input stays silent. The optional final limiter targets −1 dBFS sample peak. Lossy encoding can create intersample overshoots, so inspect exported true peak; no guarantee of a particular delivered true peak is made. Disabling the limiter permits overload. Available outputs: MP3 128/192/256/320 kbps, WAV PCM 16/24-bit and FLAC lossless. Supported output sample rates: 32, 44.1, 48, 88.2 and 96 kHz (MP3 up to 48 kHz). Transcoding or upsampling does not restore lost source quality. Only bounded, allowlisted title/artist/album/date/genre/track metadata is copied.

## Configuration and storage

Copy `.env.example` before starting. Configuration is validated at startup.

| Variable | Default |
|---|---|
| `MAX_UPLOAD_MB` | 250 |
| `MAX_DURATION_SECONDS` | 21600 |
| `STORAGE_QUOTA_GB` | 20 |
| `PROCESS_TIMEOUT_SECONDS` | 3600, per external audio command |
| `PREVIEW_TTL_HOURS` | 24 |
| `DEBUG` | false |
| `POSTGRES_PASSWORD` | local-audio-password (change for deployment) |
| `ALLOWED_HOSTS` / `ALLOWED_ORIGINS` | Loopback application URLs |

Compose supplies internal `DATABASE_URL`, `REDIS_URL` and `STORAGE_ROOT`. If a password contains URL-special characters, encode it in the connection URL or supply an explicit URL via a Compose override. Changing the database password on an existing initialized volume requires updating the database role too.

The shared volume contains `originals/`, `previews/`, `processed/`, `waveforms/` and `temporary/`. Hourly cleanup expires previews after 24 hours and removes stale unreferenced transient files. Originals and final exports remain until manually deleted. Quota reservations are conservative and expire after the maximum processing budget if a worker crashes. Back up both `postgres_data` and `audio_storage` together while services are stopped. `redis_data` persists broker state. Restore the database and audio volume together to avoid broken file references.

## Development and tests

The host requires only Docker. Python and Node dependencies are locked in `uv.lock` and `package-lock.json`. Frontend assets are built locally in a Node build stage and copied into the non-root Python image; no CDN is used.

```bash
# All automated tests: isolated PostgreSQL database and temporary storage
docker compose exec web pytest
# Lint
docker compose exec web ruff check app tests
# Full live web + real Celery + download smoke test
docker compose exec web python scripts/smoke.py
# Browser workflow + desktop/mobile screenshots (optional test profile)
docker compose -f compose.yaml -f compose.test.yaml --profile test run --rm browser-tests
# Current migration / apply new migrations
docker compose exec web alembic current
docker compose exec web alembic upgrade head
# Generate a migration after intentional schema changes
docker compose exec web alembic revision --autogenerate -m "describe change"
# Development reload (restart worker after Python changes)
docker compose -f compose.yaml -f compose.dev.yaml up --build
```

The test suite creates generated audio fixtures and checks frequency changes, measured durations, independent pitch/tempo, all preset filter graphs, exports, analysis, fingerprints, safe cancellation/timeouts, validation, PostgreSQL persistence, preview reuse, range downloads, deletion and cleanup. It uses an isolated temporary database and does not delete application recordings. Tests require the provided database user to be able to create a test database (the default PostgreSQL image user can). The live smoke script uses uniquely named fixtures and deletes only its own recording afterward.

For frontend changes, rebuild the image to compile Tailwind and vendor WaveSurfer. The dev override bind-mounts application sources and uses dedicated named volumes to retain image-built CSS and vendor files. After CSS/dependency changes, rebuild and refresh those dev asset volumes. Production-style `docker compose up --build -d` is the simplest workflow for asset changes. Python changes reload the web process; restart the worker after changing task code. Task helpers are available in `scripts/tasks.ps1` and the Makefile.

## Optional reverse proxy and deployment

```bash
docker compose -f compose.yaml -f compose.prod.yaml --profile production up --build -d
```

Nginx is then available at http://localhost:8080. The production override explicitly disables debug and has no reload. Change Nginx's upload limit alongside `MAX_UPLOAD_MB`. The provided proxy is still loopback-only and uses HTTP. For public access, place authentication and per-user authorization in front of the entire app (including downloads/docs), enforce request and job rate limits, terminate HTTPS, restrict trusted proxy ranges, configure hosts/origins, set strong credentials, and define backup/retention policies. This shared local workspace has no tenant isolation and must not be exposed directly to the internet. Add TLS and authentication before changing host port bindings.

## Troubleshooting

- **Startup fails**: inspect `docker compose logs migrate web worker`; check `.env`, available disk space and Docker memory (at least 6 GB recommended). `docker compose up --build -d` is safe to repeat.
- **Pending jobs**: check worker and scheduler health, Redis availability and `docker compose logs worker scheduler`. Scheduler retries persisted dispatches. Completed/failed/cancelled jobs are never silently rerun.
- **Invalid upload**: test actual file content; renamed executables, playlists, video, multitrack and multichannel inputs are rejected. Files with missing/invalid duration are unsupported.
- **Job failed**: inspect its error and worker logs. Reuse settings to retry after correcting the cause. Per-command timeouts and storage reservations may reject large jobs.
- **Cannot delete recording**: cancel all active jobs and wait until cancellation finishes. Cancellation checks terminate the active subprocess and wait for it to exit.
- **Preview unavailable**: previews expire after the configured TTL. Regenerate with the same settings. Final exports do not expire.
- **Browser playback fails**: codec support differs across browsers; generate MP3 or WAV 16-bit, or download the file.
- **No finite LUFS/correlation/fingerprint**: silence or very short content can make these values undefined. The UI displays unavailable rather than invented values.

## References

The implementation follows the primary documentation for [FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html), [Chromaprint](https://acoustid.org/chromaprint), [Celery tasks](https://docs.celeryq.dev/en/stable/userguide/tasks.html), and [WaveSurfer](https://wavesurfer.xyz/docs/). See `docs/verification.md` for the actual checks run in this workspace.
