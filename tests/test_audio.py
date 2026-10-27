import numpy as np
import pytest
import soundfile as sf
from pydantic import ValidationError
from app.schemas.processing import Effects, ProcessRequest, Export
from app.services.ffmpeg import probe, capabilities, run
from app.services.processor import process
from app.services.analyzer import analyze
from app.services.fingerprints import fingerprint
from app.services.comparison import aligned_correlation
from app.services.presets import builtins
from app.core.exceptions import AudioError, Cancelled


def frequency(path):
    samples, rate = sf.read(path, always_2d=True)
    samples = samples[rate // 4 : -rate // 4, 0]
    return np.fft.rfftfreq(len(samples), 1 / rate)[
        np.argmax(abs(np.fft.rfft(samples * np.hanning(len(samples)))))
    ]


@pytest.mark.parametrize(
    "params",
    [
        {"pitch": 13},
        {"tempo": 0},
        {"bass": float("nan")},
        {"unknown": 3},
        {"mode": "preserve", "pitch": 3},
        {"mode": "preserve", "reverb": 0.3},
        {"chorus": 0.2},
    ],
)
def test_parameter_rejection(params):
    with pytest.raises(ValidationError):
        Effects(**params)


def test_presets_are_valid():
    assert len(builtins()) == 13
    for preset in builtins():
        Effects.model_validate(preset["effects"])


def test_probe_analysis_and_fingerprint(sine):
    metadata = probe(sine)
    assert metadata["sample_rate"] == 48000
    assert metadata["channels"] == 2
    assert metadata["duration"] == pytest.approx(4, abs=0.01)
    result = analyze(sine)
    assert result["integrated_lufs"] < -10
    assert result["true_peak_dbtp"] < -10
    assert result["rms_dbfs"] == pytest.approx(-17.85, abs=0.3)
    assert result["stereo_correlation"] == pytest.approx(1, abs=0.001)
    assert result["clipped_samples"] == 0
    assert sum(b["energy_fraction"] for b in result["frequency_distribution"]) == pytest.approx(1, abs=0.01)
    fp = fingerprint(sine)
    assert fp["status"] in {"completed", "unavailable"}
    if fp["status"] == "completed":
        assert len(fp["hash"]) == 64
        assert fp["hash"] == fingerprint(sine)["hash"]


@pytest.mark.parametrize(
    "pitch,tempo,expected_hz,duration", [(12, 1, 880, 4), (0, 2, 440, 2), (7, 0.8, 659.25, 5)]
)
def test_pitch_and_tempo_independent(sine, tmp_path, pitch, tempo, expected_hz, duration):
    assert capabilities()["rubberband"], "Docker must provide Rubber Band support"
    request = ProcessRequest(effects=Effects(mode="creative", pitch=pitch, tempo=tempo))
    destination = tmp_path / "out.wav"
    process(sine, destination, request, probe(sine))
    assert frequency(destination) == pytest.approx(expected_hz, abs=5)
    assert probe(destination)["duration"] == pytest.approx(duration, abs=0.15)


@pytest.mark.parametrize(
    "encoding,bitrate",
    [("mp3", 128), ("mp3", 192), ("mp3", 256), ("mp3", 320), ("wav16", 320), ("wav24", 320), ("flac", 320)],
)
def test_all_export_formats(sine, tmp_path, encoding, bitrate):
    from app.services.processor import EXTENSIONS

    out = tmp_path / f"export.{EXTENSIONS[encoding]}"
    process(sine, out, ProcessRequest(export=Export(format=encoding, bitrate=bitrate)), probe(sine))
    assert out.stat().st_size > 100
    assert probe(out)["duration"] == pytest.approx(4, abs=0.15)
    if encoding.startswith("wav"):
        assert sf.info(out).subtype == ("PCM_16" if encoding == "wav16" else "PCM_24")


@pytest.mark.parametrize("preset", builtins(), ids=lambda p: p["name"])
def test_all_preset_filter_graphs(sine, tmp_path, preset):
    out = tmp_path / "preset.wav"
    process(sine, out, ProcessRequest(effects=Effects.model_validate(preset["effects"])), probe(sine))
    samples, _ = sf.read(out)
    assert np.isfinite(samples).all()
    assert abs(samples).max() < 1


def test_preview_region(sine, tmp_path):
    out = tmp_path / "preview.wav"
    process(sine, out, ProcessRequest(), probe(sine), start=1, duration=2)
    assert probe(out)["duration"] == pytest.approx(2, abs=0.05)


def test_subprocess_cancellation():
    with pytest.raises(Cancelled):
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=20",
                "-f",
                "null",
                "-",
            ],
            cancelled=lambda: True,
        )


def test_subprocess_timeout():
    with pytest.raises(AudioError, match="timeout"):
        run(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostdin",
                "-re",
                "-f",
                "lavfi",
                "-i",
                "sine=duration=20",
                "-f",
                "null",
                "-",
            ],
            timeout=0.15,
        )


def test_correlation_alignment(sine, tmp_path):
    signal, rate = sf.read(sine)
    shifted = np.vstack([np.zeros((rate // 5, 2)), signal])
    out = tmp_path / "shifted.wav"
    sf.write(out, shifted, rate)
    correlation = aligned_correlation(sine, out)
    assert correlation["value"] > 0.99


def test_mono_and_silence(tmp_path):
    path = tmp_path / "silent.wav"
    sf.write(path, np.zeros(48000), 48000)
    a = analyze(path)
    assert a["stereo_balance_db"] is None
    assert a["stereo_correlation"] is None
    assert a["integrated_lufs"] is None
    assert a["peak_amplitude"] == 0
    assert a["silence_segments"]


@pytest.mark.parametrize(
    "extension,encoder",
    [("mp3", "libmp3lame"), ("flac", "flac"), ("ogg", "libvorbis"), ("m4a", "aac"), ("aac", "aac")],
)
def test_supported_input_formats(sine, tmp_path, extension, encoder):
    from app.services.ffmpeg import command

    out = tmp_path / f"input.{extension}"
    run(command(sine) + ["-c:a", encoder, str(out)])
    assert probe(out)["duration"] == pytest.approx(4, abs=0.2)


def test_stereo_and_mono_effects(sine, tmp_path):
    from app.services.ffmpeg import command

    mono = tmp_path / "mono.wav"
    run(command(sine) + ["-ac", "1", str(mono)])
    output = tmp_path / "mono-effects.wav"
    settings = Effects(mode="creative", delay_ms=200, eq_gain=2, eq_q=2, width=1.8, denoise=0.2, limiter=True)
    process(mono, output, ProcessRequest(effects=settings), probe(mono))
    assert probe(output)["channels"] == 1
    assert np.isfinite(sf.read(output)[0]).all()


def test_no_shell_execution(sine, tmp_path):
    # Argument-like filenames remain ordinary file paths.
    dangerous = tmp_path / "audio;touch marker.wav"
    dangerous.write_bytes(sine.read_bytes())
    assert probe(dangerous)["duration"] == pytest.approx(4, abs=0.05)
    assert not (tmp_path / "marker.wav").exists()


def test_antiphase_stereo_spectral_analysis(tmp_path):
    rate = 48000
    t = np.arange(rate * 2) / rate
    x = 0.2 * np.sin(2 * np.pi * 440 * t)
    file = tmp_path / "antiphase.wav"
    sf.write(file, np.column_stack([x, -x]), rate)
    result = analyze(file)
    assert result["stereo_correlation"] == pytest.approx(-1, abs=0.001)
    assert result["spectral_centroid_hz"] == pytest.approx(440, abs=25)


def test_low_sample_rate_input(tmp_path):
    rate = 8000
    t = np.arange(rate * 2) / rate
    source = tmp_path / "low-rate.wav"
    sf.write(source, 0.2 * np.sin(2 * np.pi * 440 * t), rate)
    destination = tmp_path / "processed.wav"
    process(source, destination, ProcessRequest(effects=Effects(treble=3, lowpass=18000)), probe(source))
    assert probe(destination)["sample_rate"] == 48000
