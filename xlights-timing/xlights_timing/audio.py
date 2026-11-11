"""Reading and writing audio files."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf


def load(path: Path, sr: int, channels: int = 1) -> np.ndarray:
    """Decode any audio file to float32 at ``sr``, shaped (channels, samples).

    ffmpeg handles every format xLights users tend to have (mp3, m4a, ogg,
    wav...) and resamples in one pass; soundfile is the fallback for wav/flac
    when ffmpeg isn't installed.
    """
    if shutil.which("ffmpeg"):
        cmd = [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "f32le",
            "-acodec",
            "pcm_f32le",
            "-ac",
            str(channels),
            "-ar",
            str(sr),
            "-",
        ]
        raw = subprocess.run(cmd, check=True, capture_output=True).stdout
        data = np.frombuffer(raw, dtype=np.float32)
        return data.reshape(-1, channels).T.copy()

    import librosa

    data, file_sr = sf.read(str(path), dtype="float32", always_2d=True)
    data = data.T
    if file_sr != sr:
        data = librosa.resample(data, orig_sr=file_sr, target_sr=sr)
    if data.shape[0] != channels:
        mono = data.mean(axis=0, keepdims=True)
        data = np.repeat(mono, channels, axis=0)
    return data.astype(np.float32)


def save(path: Path, audio: np.ndarray, sr: int) -> None:
    """Write (channels, samples) audio as 16-bit WAV."""
    path.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(path), np.clip(audio.T, -1.0, 1.0), sr, subtype="PCM_16")
