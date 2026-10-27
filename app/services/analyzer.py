import json
import math
import re
import numpy as np
import soundfile as sf
from app.services.ffmpeg import command, run, probe
from app.core.exceptions import AudioError
from app.services.storage import allocate, resolve, delete

BANDS = [(0, 60), (60, 250), (250, 500), (500, 2000), (2000, 6000), (6000, 24000)]


def finite(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (ValueError, TypeError):
        return None


def loudness(source, cancelled=None):
    _, stderr = run(
        command(source) + ["-af", "loudnorm=I=-16:TP=-1:LRA=11:print_format=json", "-f", "null", "-"],
        cancelled=cancelled,
    )
    match = re.findall(r'\{[^{}]*"input_i"[^{}]*\}', stderr, re.S)
    if not match:
        return {"integrated_lufs": None, "true_peak_dbtp": None, "loudness_range_lu": None}
    data = json.loads(match[-1])
    return {
        "integrated_lufs": finite(data.get("input_i")),
        "true_peak_dbtp": finite(data.get("input_tp")),
        "loudness_range_lu": finite(data.get("input_lra")),
        "normalization_input": data,
    }


def analyze(source, *, cancelled=None, progress=None):
    metadata = probe(source)
    key = allocate("temporary", "wav")
    waveform = allocate("waveforms", "json")
    duration = metadata["duration"]
    try:
        run(
            command(source) + ["-c:a", "pcm_f32le", "-rf64", "auto", str(resolve(key))],
            cancelled=cancelled,
            progress=progress,
            duration=duration,
        )
        loud = loudness(source, cancelled)
        _, silence_log = run(
            command(source) + ["-af", "silencedetect=noise=-50dB:d=0.5", "-f", "null", "-"],
            cancelled=cancelled,
        )
        silence = []
        start = None
        for line in silence_log.splitlines():
            if "silence_start:" in line:
                start = float(line.split("silence_start:")[1].split()[0])
            if "silence_end:" in line:
                end = float(line.split("silence_end:")[1].split()[0])
                silence.append({"start": start if start is not None else 0, "end": end})
                start = None
        if start is not None:
            silence.append({"start": start, "end": duration})
        sums = np.zeros(metadata["channels"])
        squared = np.zeros_like(sums)
        peak, clips, count, cross = 0.0, 0, 0, 0.0
        spectrum = np.zeros(1025)
        rms_windows = []
        with sf.SoundFile(resolve(key)) as audio:
            rate = audio.samplerate
            bins = min(4000, max(1, int(duration * 100)))
            bucket = max(1, math.ceil(len(audio) / bins))
            peaks = [[] for _ in range(audio.channels)]
            # Peaks and RMS windows share evenly sized, full-coverage blocks; no full-file array.
            for block in audio.blocks(blocksize=bucket, dtype="float64", always_2d=True):
                if cancelled and cancelled():
                    from app.core.exceptions import Cancelled

                    raise Cancelled()
                if not np.isfinite(block).all():
                    raise AudioError("Decoded audio contains non-finite sample values")
                for i in range(audio.channels):
                    peaks[i].append(float(np.max(np.abs(block[:, i]))))
                sums += block.sum(axis=0)
                squared += (block**2).sum(axis=0)
                peak = max(peak, float(np.max(np.abs(block))))
                threshold = (
                    1 - 2 ** (1 - metadata["bit_depth"])
                    if metadata["codec"].startswith("pcm_s") and metadata["bit_depth"]
                    else 1.0
                )
                clips += int(np.count_nonzero(np.abs(block) >= threshold))
                count += len(block)
                if audio.channels == 2:
                    cross += float(np.sum(block[:, 0] * block[:, 1]))
                rms_windows.append(float(np.sqrt(np.mean(block**2))))
                sample = np.zeros((2048, audio.channels))
                size = min(2048, len(block))
                sample[:size] = block[:size] * np.hanning(size)[:, None]
                spectrum += (np.abs(np.fft.rfft(sample, axis=0)) ** 2).mean(axis=1)
        frequency = np.fft.rfftfreq(2048, 1 / rate)
        total_energy = float(spectrum.sum())
        rms = float(np.sqrt(squared.sum() / (count * metadata["channels"])))
        centroid = float((frequency * spectrum).sum() / total_energy) if total_energy else 0
        active = [x for x in rms_windows if x > 1e-5]
        dynamic = (
            float(20 * np.log10(np.percentile(active, 95) / max(np.percentile(active, 10), 1e-12)))
            if active
            else None
        )
        denominator = float(np.sqrt(np.prod(squared - sums**2 / count))) if len(sums) == 2 else 0
        correlation = (
            float(np.clip((cross - np.prod(sums) / count) / denominator, -1, 1))
            if denominator > 1e-15
            else None
        )
        balance = (
            float(10 * np.log10(max(squared[-1], 1e-15) / max(squared[0], 1e-15)))
            if len(sums) == 2 and np.all(squared > 1e-20)
            else None
        )
        result = {
            **metadata,
            **{k: v for k, v in loud.items() if k != "normalization_input"},
            "rms_dbfs": 20 * math.log10(rms) if rms else None,
            "peak_amplitude": peak,
            "peak_dbfs": 20 * math.log10(peak) if peak else None,
            "dynamic_range_approx_db": dynamic,
            "stereo_balance_db": balance,
            "clipped_samples": clips,
            "sample_values": count * metadata["channels"],
            "stereo_correlation": correlation,
            "silence_segments": silence,
            "spectral_centroid_hz": centroid,
            "frequency_distribution": [
                {
                    "low_hz": lo,
                    "high_hz": hi,
                    "energy_fraction": float(
                        spectrum[(frequency >= lo) & (frequency < hi)].sum() / total_energy
                    )
                    if total_energy
                    else 0,
                }
                for lo, hi in BANDS
            ],
            "waveform_key": waveform,
        }
        resolve(waveform).write_text(json.dumps({"duration": duration, "peaks": peaks}))
        return result
    except BaseException:
        delete(waveform)
        raise
    finally:
        delete(key)
