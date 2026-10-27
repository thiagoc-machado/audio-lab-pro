import math
from app.core.exceptions import AudioError
from app.schemas.processing import ProcessRequest
from app.services.ffmpeg import capabilities, command, run
from app.services.analyzer import loudness
from app.services.storage import allocate, resolve, delete

ENCODINGS = {
    "mp3": ["-c:a", "libmp3lame"],
    "wav16": ["-c:a", "pcm_s16le"],
    "wav24": ["-c:a", "pcm_s24le"],
    "flac": ["-c:a", "flac", "-sample_fmt", "s32"],
}
EXTENSIONS = {"mp3": "mp3", "wav16": "wav", "wav24": "wav", "flac": "flac"}


def filters(e, channels):
    chain = []
    if e.pitch or e.tempo != 1:
        if not capabilities()["rubberband"]:
            raise AudioError(
                "This FFmpeg build lacks Rubber Band; independent pitch/tempo processing is unavailable"
            )
        chain.append(f"rubberband=tempo={e.tempo}:pitch={2 ** (e.pitch / 12)}:transients=crisp:phase=laminar")
    if e.denoise:
        if not capabilities()["denoise"]:
            raise AudioError("FFT noise reduction is unavailable in this FFmpeg build")
        chain.append(f"afftdn=nr={e.denoise * 20 + 1}:nf=-40")
    if e.highpass > 20:
        chain.append(f"highpass=f={e.highpass}")
    if e.lowpass < 20000:
        chain.append(f"lowpass=f={e.lowpass}")
    if e.bass:
        chain.append(f"bass=g={e.bass}:f=120")
    if e.treble:
        chain.append(f"treble=g={e.treble}:f=6000")
    if e.eq_gain:
        chain.append(f"equalizer=f={e.eq_frequency}:t=q:w={e.eq_q}:g={e.eq_gain}")
    if e.compression:
        chain.append(
            f"acompressor=threshold={10 ** (-18 / 20)}:ratio={1 + e.compression * 5}:attack=20:release=250:makeup=1"
        )
    if e.saturation:
        chain.append(f"acontrast=contrast={e.saturation * 80}")
    if e.chorus:
        chain.append(f"chorus=0.6:0.8:40:{e.chorus * 0.4}:0.3:2")
    if e.flanger:
        chain.append(f"flanger=delay=0:depth={e.flanger * 5}:regen=10:width=50:speed=0.3")
    if e.phaser:
        chain.append(f"aphaser=in_gain=0.6:out_gain=0.8:delay=3:decay={e.phaser * 0.5}:speed=0.5")
    if e.reverb:
        chain.append(
            f"aecho=0.6:0.8:31|61|97|137:{e.reverb * 0.5}|{e.reverb * 0.4}|{e.reverb * 0.3}|{e.reverb * 0.2}"
        )
    if e.echo:
        chain.append(f"aecho=0.7:0.8:250|500:{e.echo * 0.7}|{e.echo * 0.4}")
    if e.delay_ms:
        chain.append(f"aecho=0.7:0.8:{e.delay_ms}:0.35")
    if channels == 2 and e.width != 1:
        direct, cross = (1 + e.width) / 2, (1 - e.width) / 2
        chain.append(f"pan=stereo|c0={direct}*c0{cross:+g}*c1|c1={cross}*c0{direct:+g}*c1")
    # A final limiter always follows gain-changing processing unless explicitly disabled.
    if e.limiter:
        chain.append("alimiter=limit=0.891251:attack=5:release=80:level=false:latency=true")
    return chain or ["anull"]


def process(
    source,
    destination,
    request: ProcessRequest,
    metadata,
    *,
    start=None,
    duration=None,
    cancelled=None,
    progress=None,
    phase=None,
):
    e, export = request.effects, request.export
    expected = (duration or metadata["duration"]) / e.tempo
    intermediate = allocate("temporary", "wav")
    try:
        args = command(source)
        # Accurate trim occurs after input decoding, with timestamps reset before effects.
        dsp = [f"aresample={max(48000, metadata['sample_rate'], export.sample_rate)}"] + filters(
            e, metadata["channels"]
        )
        if start is not None:
            dsp = [f"atrim=start={start}:duration={duration}", "asetpts=PTS-STARTPTS"] + dsp
        if phase:
            phase("Applying effects")
        run(
            args
            + [
                "-af",
                ",".join(dsp),
                "-ar",
                str(export.sample_rate),
                "-c:a",
                "pcm_f32le",
                "-rf64",
                "auto",
                str(resolve(intermediate)),
            ],
            cancelled=cancelled,
            progress=progress,
            duration=expected,
        )
        final_filters = []
        if e.normalize:
            if phase:
                phase("Measuring loudness for normalization")
            measured = loudness(resolve(intermediate), cancelled).get("normalization_input", {})
            values = [
                measured.get(k) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
            ]
            if all(v is not None and math.isfinite(float(v)) for v in values):
                i, tp, lra, threshold, offset = values
                final_filters.append(
                    f"loudnorm=I={e.target_lufs}:TP=-1:LRA=11:measured_I={i}:measured_TP={tp}:measured_LRA={lra}:measured_thresh={threshold}:offset={offset}:linear=true"
                )
            # Silence has no finite LUFS and is left unchanged.
        if e.limiter:
            final_filters.append("alimiter=limit=0.891251:level=false:latency=true")
        args = command(resolve(intermediate))
        if final_filters:
            args += ["-af", ",".join(final_filters)]
        args += ENCODINGS[export.format]
        if export.format.startswith("wav"):
            args += ["-rf64", "auto"]
        if export.format == "mp3":
            args += ["-b:a", f"{export.bitrate}k"]
        args += ["-ar", str(export.sample_rate), "-map_metadata", "-1"]
        for key, value in metadata.get("tags", {}).items():
            args += ["-metadata", f"{key}={value}"]
        if phase:
            phase("Encoding export")
        run(args + [str(destination)], cancelled=cancelled, progress=progress, duration=expected)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        delete(intermediate)
