import json
import logging
import subprocess
import tempfile
import time
from functools import lru_cache
from pathlib import Path
from app.core.config import settings
from app.core.exceptions import AudioError, Cancelled

logger = logging.getLogger(__name__)


def run(args, *, timeout=None, cancelled=None, progress=None, duration=None, allowed_returncodes=()):
    """Disk-backed subprocess output avoids pipe deadlocks and unbounded process output in RAM."""
    timeout = timeout or settings.process_timeout_seconds
    with tempfile.TemporaryDirectory(dir=settings.storage_root / "temporary") as tmp:
        out, err, status = [Path(tmp) / x for x in ("stdout", "stderr", "progress")]
        command = list(map(str, args))
        if command[0] == "ffmpeg" and progress:
            command[1:1] = ["-progress", str(status), "-stats_period", "0.5"]
        with out.open("wb") as stdout, err.open("wb") as stderr:
            process = subprocess.Popen(
                command, stdout=stdout, stderr=stderr, stdin=subprocess.DEVNULL, start_new_session=True
            )
            started = time.monotonic()
            last = 0.0
            try:
                while process.poll() is None:
                    if cancelled and cancelled():
                        raise Cancelled()
                    if time.monotonic() - started > timeout:
                        raise AudioError("Audio command exceeded its configured timeout")
                    if progress and time.monotonic() - last > 0.8:
                        seconds = 0
                        if status.exists():
                            for line in status.read_text(errors="replace").splitlines():
                                if line.startswith("out_time_us="):
                                    try:
                                        seconds = int(line.split("=", 1)[1]) / 1e6
                                    except ValueError:
                                        pass
                        progress(min(99.9, seconds / duration * 100) if duration else 0)
                        last = time.monotonic()
                    time.sleep(0.1)
                if cancelled and cancelled():
                    raise Cancelled()
                if process.returncode and process.returncode not in allowed_returncodes:
                    detail = err.read_text(errors="replace")[-3000:]
                    logger.warning("Audio command failed: %s", detail)
                    raise AudioError(
                        "Audio tool could not process this recording. Check format and effect settings."
                    )
            finally:
                if process.poll() is None:
                    process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
        return out.read_text(errors="replace"), err.read_text(errors="replace")


def command(source):
    return [
        "ffmpeg",
        "-hide_banner",
        "-nostdin",
        "-y",
        "-threads",
        "2",
        "-filter_threads",
        "2",
        "-protocol_whitelist",
        "file,pipe",
        "-i",
        str(source),
        "-map",
        "0:a:0",
        "-vn",
    ]


def probe(source):
    output, _ = run(
        [
            "ffprobe",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-show_format",
            "-show_streams",
            "-print_format",
            "json",
            str(source),
        ],
        timeout=30,
    )
    data = json.loads(output)
    streams = data.get("streams", [])
    audio = [s for s in streams if s.get("codec_type") == "audio"]
    if len(audio) != 1 or any(
        s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic") for s in streams
    ):
        raise AudioError("Upload a single audio track without video")
    s = audio[0]
    f = data.get("format", {})
    formats = set(f.get("format_name", "").split(","))
    supported = {"mp3", "wav", "flac", "ogg", "mov", "mp4", "m4a", "3gp", "3g2", "mj2", "aac"}
    codec = s.get("codec_name", "")
    if not formats & supported or not (
        codec.startswith("pcm_") or codec in {"mp3", "flac", "vorbis", "opus", "aac", "alac"}
    ):
        raise AudioError("Unsupported audio content. Use MP3, WAV, FLAC, OGG or M4A/AAC.")
    duration = float(f.get("duration") or s.get("duration") or 0)
    rate, channels = int(s.get("sample_rate", 0)), int(s.get("channels", 0))
    if not 0 < duration <= settings.max_duration_seconds:
        raise AudioError("Recording duration is missing or exceeds the configured maximum")
    if channels not in (1, 2) or not 8000 <= rate <= 192000:
        raise AudioError("Only mono/stereo audio from 8 kHz to 192 kHz is supported")
    tags = {
        k: str(v)[:200]
        for k, v in f.get("tags", {}).items()
        if k.lower() in {"title", "artist", "album", "date", "genre", "track"}
    }
    return {
        "duration": duration,
        "codec": codec,
        "container": f.get("format_name"),
        "sample_rate": rate,
        "channels": channels,
        "bit_depth": int(s.get("bits_per_raw_sample") or s.get("bits_per_sample") or 0) or None,
        "bitrate": int(f.get("bit_rate") or s.get("bit_rate") or 0),
        "tags": tags,
    }


@lru_cache
def capabilities():
    filters, _ = run(["ffmpeg", "-hide_banner", "-filters"], timeout=20)
    return {
        "rubberband": "rubberband" in filters,
        "denoise": "afftdn" in filters,
        "fingerprint": True,
        "stereo_width": True,
    }
