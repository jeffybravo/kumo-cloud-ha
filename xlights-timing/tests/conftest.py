"""A synthetic song whose hit and note times are known exactly.

120 BPM, 4/4, 8 bars, starting with one bar of silence-free groove:
kick on beats 1 and 3, snare on 2 and 4, closed hi-hat on every eighth note,
a bass note on every beat (A1 / E2), and a vocal line (C4 E4 G4 E4, half a
second each) in bars 3-4 and 7-8 only.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pytest

from xlights_timing import audio

SR = 44100
BPM = 120
BEAT = 60 / BPM
BARS = 8
LEAD_IN = 0.5  # seconds of silence before bar 1
DURATION = LEAD_IN + BARS * 4 * BEAT + 1.0


@dataclass
class Song:
    dir: Path
    mix: Path
    stems: dict[str, Path]
    kicks: list[float]
    snares: list[float]
    hats: list[float]
    bass: list[tuple[float, float, str]]
    vocals: list[tuple[float, float, str]]
    downbeats: list[float]


def _env(n: int, decay_s: float) -> np.ndarray:
    return np.exp(-np.arange(n) / (decay_s * SR))


def _place(buf: np.ndarray, t: float, sound: np.ndarray) -> None:
    i = int(round(t * SR))
    j = min(len(buf), i + len(sound))
    buf[i:j] += sound[: j - i]


def _tone(freq: float, length: float, rng: np.random.Generator) -> np.ndarray:
    n = int(length * SR)
    t = np.arange(n) / SR
    wave = sum(np.sin(2 * np.pi * freq * k * t) / k for k in (1, 2, 3, 4))
    attack = np.minimum(1, np.arange(n) / (0.01 * SR))
    release = np.minimum(1, (n - np.arange(n)) / (0.03 * SR))
    return (wave * attack * release).astype(np.float32)


def make_song(root: Path) -> Song:
    rng = np.random.default_rng(1)
    n = int(DURATION * SR)
    drums, bass, vocals = (np.zeros(n, np.float32) for _ in range(3))

    # Kick: 150 -> 45 Hz pitch drop.
    k_n = int(0.35 * SR)
    t = np.arange(k_n) / SR
    freq = 45 + 105 * np.exp(-t / 0.03)
    kick = np.sin(2 * np.pi * np.cumsum(freq) / SR) * _env(k_n, 0.12)
    # Snare: 190 Hz body plus noise.
    s_n = int(0.25 * SR)
    noise = rng.standard_normal(s_n)
    snare = 0.5 * np.sin(2 * np.pi * 190 * np.arange(s_n) / SR) * _env(
        s_n, 0.05
    ) + 0.6 * noise * _env(s_n, 0.07)
    # Hi-hat: high-passed noise, very short.
    h_n = int(0.06 * SR)
    hat_noise = np.diff(rng.standard_normal(h_n + 1))
    hat = 0.25 * hat_noise * _env(h_n, 0.015)

    kicks, snares, hats, downbeats = [], [], [], []
    bass_notes, vocal_notes = [], []
    for bar in range(BARS):
        bar_t = LEAD_IN + bar * 4 * BEAT
        downbeats.append(bar_t)
        for beat in range(4):
            bt = bar_t + beat * BEAT
            if beat in (0, 2):
                _place(drums, bt, kick)
                kicks.append(bt)
            else:
                _place(drums, bt, snare)
                snares.append(bt)
            for eighth in (0, 0.5):
                _place(drums, bt + eighth * BEAT, hat)
                hats.append(bt + eighth * BEAT)
            note, f = ("A1", 55.0) if beat % 2 == 0 else ("E2", 82.41)
            _place(bass, bt, 0.5 * _tone(f, 0.3, rng))
            bass_notes.append((bt, bt + 0.3, note))
        if bar in (2, 3, 6, 7):
            for i, (note, f) in enumerate(
                [("C4", 261.63), ("E4", 329.63), ("G4", 392.0), ("E4", 329.63)]
            ):
                st = bar_t + i * BEAT
                _place(vocals, st, 0.3 * _tone(f, 0.45, rng))
                vocal_notes.append((st, st + 0.45, note))

    root.mkdir(parents=True, exist_ok=True)
    stems = {}
    for name, sig in (("drums", drums), ("bass", bass), ("vocals", vocals)):
        stems[name] = root / "stems" / f"{name}.wav"
        audio.save(stems[name], sig[None], SR)
    mix_path = root / "song.wav"
    audio.save(mix_path, (drums + bass + vocals)[None] * 0.7, SR)
    return Song(
        root / "stems",
        mix_path,
        stems,
        kicks,
        snares,
        hats,
        bass_notes,
        vocal_notes,
        downbeats,
    )


@pytest.fixture(scope="session")
def song(tmp_path_factory) -> Song:
    return make_song(tmp_path_factory.mktemp("song"))
