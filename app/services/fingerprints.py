import hashlib
import json
from app.models import now
from app.services.ffmpeg import run


def fingerprint(source, cancelled=None):
    # Chromaprint intentionally fingerprints up to the first 120 seconds.
    try:
        output, stderr = run(
            ["fpcalc", "-json", "-length", "120", str(source)],
            timeout=180,
            cancelled=cancelled,
            allowed_returncodes=(3,),
        )
        # Debian fpcalc 1.5.1 returns 3 on clean decoder EOF even after producing valid JSON.
        # Accept only this specific EOF diagnostic; other decoder errors remain unavailable.
        if "ERROR:" in stderr and stderr.strip() != "ERROR: Error decoding audio frame (End of file)":
            raise ValueError("Chromaprint reported a decoder failure")
        data = json.loads(output)
        representation = data.get("fingerprint", "")
        if not representation:
            return {"status": "unavailable", "reason": "Recording is too short for a Chromaprint fingerprint"}
        return {
            "status": "completed",
            "algorithm": "Chromaprint default (fpcalc)",
            "duration": min(float(data["duration"]), 120),
            "analyzed_seconds_limit": 120,
            "representation": representation,
            "hash": hashlib.sha256(representation.encode()).hexdigest(),
            "generated_at": now().isoformat(),
        }
    except Exception as exc:
        from app.core.exceptions import Cancelled

        if isinstance(exc, Cancelled):
            raise
        return {
            "status": "unavailable",
            "reason": "Chromaprint could not fingerprint this recording (short or silent input may be unsupported)",
        }
