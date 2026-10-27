from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Effects(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    mode: Literal["preserve", "creative"] = "preserve"
    pitch: float = Field(0, ge=-12, le=12)
    tempo: float = Field(1, ge=0.5, le=2)
    bass: float = Field(0, ge=-12, le=12)
    treble: float = Field(0, ge=-12, le=12)
    eq_frequency: float = Field(1000, ge=20, le=20000)
    eq_gain: float = Field(0, ge=-12, le=12)
    eq_q: float = Field(1, ge=0.1, le=10)
    compression: float = Field(0, ge=0, le=1)
    normalize: bool = False
    target_lufs: float = Field(-16, ge=-24, le=-9)
    width: float = Field(1, ge=0, le=2)
    reverb: float = Field(0, ge=0, le=0.8)
    echo: float = Field(0, ge=0, le=0.8)
    delay_ms: float = Field(0, ge=0, le=2000)
    chorus: float = Field(0, ge=0, le=1)
    flanger: float = Field(0, ge=0, le=1)
    phaser: float = Field(0, ge=0, le=1)
    saturation: float = Field(0, ge=0, le=1)
    lowpass: float = Field(20000, ge=100, le=20000)
    highpass: float = Field(20, ge=20, le=5000)
    limiter: bool = True
    denoise: float = Field(0, ge=0, le=1)

    @model_validator(mode="after")
    def preserve_limits(self):
        if self.mode == "preserve":
            if abs(self.pitch) > 2 or not 0.95 <= self.tempo <= 1.05:
                raise ValueError("Preserve mode requires pitch within +/-2 semitones and tempo 0.95-1.05")
            if self.reverb > 0.2 or self.compression > 0.4 or not 0.8 <= self.width <= 1.2:
                raise ValueError("Preserve mode requires subtle reverb, compression and stereo width")
            if any((self.echo, self.delay_ms, self.chorus, self.flanger, self.phaser, self.saturation)):
                raise ValueError("Creative effects require Creative mode")
        return self


class Export(BaseModel):
    model_config = ConfigDict(extra="forbid")
    format: Literal["mp3", "wav16", "wav24", "flac"] = "wav24"
    bitrate: Literal[128, 192, 256, 320] = 320
    sample_rate: Literal[32000, 44100, 48000, 88200, 96000] = 48000


class ProcessRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    effects: Effects = Field(default_factory=Effects)
    export: Export = Field(default_factory=Export)
    preset_name: str | None = Field(None, max_length=100)

    @model_validator(mode="after")
    def encoding_limits(self):
        if self.export.format == "mp3" and self.export.sample_rate > 48000:
            raise ValueError("MP3 supports output rates up to 48000 Hz")
        return self


class PreviewRequest(ProcessRequest):
    start: float = Field(0, ge=0, le=86400)
    duration: Literal[10, 20, 30, 60] = 20


class PresetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100, pattern=r".*\S.*")
    effects: Effects


class PresetUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(None, min_length=1, max_length=100, pattern=r".*\S.*")
    effects: Effects | None = None
