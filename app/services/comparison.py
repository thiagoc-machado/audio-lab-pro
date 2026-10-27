import numpy as np
from app.services.ffmpeg import command, run
from app.services.storage import allocate, resolve, delete


def aligned_correlation(original, processed, cancelled=None):
    keys = [allocate("temporary", "raw") for _ in range(2)]
    try:
        for source, key in zip((original, processed), keys):
            run(
                command(source) + ["-t", "120", "-ac", "1", "-ar", "8000", "-f", "f32le", str(resolve(key))],
                cancelled=cancelled,
            )
        a, b = [np.fromfile(resolve(k), dtype="float32").astype("float64") for k in keys]
        n = min(len(a), len(b))
        if n < 8000:
            return {"value": None, "reason": "At least one second of overlapping audio is required"}
        a, b = a[:n], b[:n]
        a -= a.mean()
        b -= b.mean()
        size = 1 << (2 * n - 1).bit_length()
        corr = np.fft.irfft(np.fft.rfft(a, size) * np.conj(np.fft.rfft(b, size)), size)
        maxlag = min(16000, n // 4)
        lags = np.arange(-maxlag, maxlag + 1)
        lag = int(lags[np.argmax(np.abs(corr[lags % size]))])
        x, y = (a[lag:], b[: n - lag]) if lag >= 0 else (a[: n + lag], b[-lag:])
        denom = np.linalg.norm(x) * np.linalg.norm(y)
        return {
            "value": float(np.clip(np.dot(x, y) / denom, -1, 1)) if denom > 1e-12 else None,
            "alignment_lag_seconds": lag / 8000,
            "compared_seconds": len(x) / 8000,
            "method": "Mean-centered mono Pearson correlation after FFT lag search within +/-2 seconds; first 120 seconds",
        }
    finally:
        for key in keys:
            delete(key)


def compare(original, processed, a, b, fa, fb, tempo=1, cancelled=None):
    fingerprints_valid = fa.get("status") == fb.get("status") == "completed"

    def delta(key):
        return b[key] - a[key] if a.get(key) is not None and b.get(key) is not None else None

    correlation = (
        aligned_correlation(original, processed, cancelled)
        if tempo == 1
        else {
            "value": None,
            "reason": "Tempo differs; correlation requires time alignment without time stretching",
        }
    )
    return {
        "fingerprint_exact_match": fa["representation"] == fb["representation"]
        if fingerprints_valid
        else None,
        "fingerprint_hash_match": fa["hash"] == fb["hash"] if fingerprints_valid else None,
        "duration_difference_seconds": delta("duration"),
        "loudness_difference_lu": delta("integrated_lufs"),
        "spectral_centroid_difference_hz": delta("spectral_centroid_hz"),
        "frequency_distribution_l1": float(
            sum(
                abs(x["energy_fraction"] - y["energy_fraction"])
                for x, y in zip(a["frequency_distribution"], b["frequency_distribution"])
            )
        ),
        "waveform_correlation": correlation,
        "notice": "Local acoustic measurements do not predict recognition by external Content ID systems.",
    }
