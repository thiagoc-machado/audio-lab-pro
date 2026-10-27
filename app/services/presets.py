from app.schemas.processing import Effects

DEFINITIONS = [
    ("Clean & Balanced", "preserve", dict(normalize=True)),
    ("Warm & Smooth", "preserve", dict(bass=2, treble=-1, compression=0.2)),
    ("Subtle Mastering", "preserve", dict(normalize=True, target_lufs=-14, compression=0.25, width=1.1)),
    (
        "Podcast Clarity",
        "preserve",
        dict(highpass=80, eq_frequency=3000, eq_gain=2, compression=0.3, normalize=True),
    ),
    ("Light Restoration", "preserve", dict(denoise=0.35, highpass=40)),
    ("Cinematic Dark", "creative", dict(pitch=-3, lowpass=5000, reverb=0.4)),
    ("Dreamy Ambient", "creative", dict(tempo=0.85, reverb=0.6, chorus=0.35)),
    ("Lo-Fi Vintage", "creative", dict(highpass=180, lowpass=4500, saturation=0.35)),
    ("Deep & Slow", "creative", dict(pitch=-5, tempo=0.75, bass=3)),
    ("Bright & Energetic", "creative", dict(tempo=1.15, treble=3, compression=0.45)),
    ("Retro Radio", "creative", dict(highpass=400, lowpass=3500, saturation=0.5, width=0)),
    ("Spacious Reverb", "creative", dict(reverb=0.7, width=1.5)),
    ("Experimental Soundscape", "creative", dict(pitch=7, tempo=0.7, phaser=0.5, echo=0.4, flanger=0.3)),
]


def builtins():
    return [
        {
            "id": f"builtin-{i}",
            "name": name,
            "builtin": True,
            "effects": Effects(mode=mode, **params).model_dump(),
        }
        for i, (name, mode, params) in enumerate(DEFINITIONS)
    ]
