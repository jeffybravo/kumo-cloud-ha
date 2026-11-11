"""Turn a song and its stems into xLights timing tracks."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from . import audio, detect
from .xtiming import TimingTrack

DISPLAY = {
    "vocals": "Vocals",
    "bass": "Bass",
    "guitar": "Guitar",
    "piano": "Piano",
    "other": "Other",
}
# Order tracks show up in xLights, roughly most to least useful for lights.
MELODIC_ORDER = ["vocals", "bass", "guitar", "piano", "other"]


def analyze(
    song: Path,
    stems: dict[str, Path],
    *,
    settings: detect.Settings,
    beats_per_bar: int = 4,
    pitch: bool = True,
) -> tuple[list[TimingTrack], float]:
    """Return the timing tracks and the detected tempo (BPM)."""
    mix = audio.load(song, detect.SR)[0]
    duration = len(mix) / detect.SR
    mix_level = detect.loud_level(detect.Spectrum.of(mix).power_db())

    tracks: list[TimingTrack] = []
    melodic: list[TimingTrack] = []
    phrase_starts: list[float] = []
    drum_tracks: dict = {}
    accent = None

    if "drums" in stems:
        log("drums")
        drums = audio.load(stems["drums"], detect.SR)[0]
        drum_tracks = detect.drum_hits(drums, mix_level, settings)
        lo, hi, _ = detect.DRUM_BANDS["Kick"]
        accent = detect.Spectrum.of(drums).flux(lo, hi)

    others = [s for s in stems if s != "drums" and s not in MELODIC_ORDER]
    for stem in [s for s in MELODIC_ORDER if s in stems] + sorted(others):
        log(stem)
        y = audio.load(stems[stem], detect.SR)[0]
        title = DISPLAY.get(stem, stem.title())
        lines = detect.phrases(y, mix_level, settings)
        phrase_starts += [m.start for m in lines]
        melodic.append(TimingTrack(f"{title} Phrases", lines))
        melodic.append(
            TimingTrack(
                f"{title} Notes",
                detect.notes(y, stem, mix_level, settings, with_pitch=pitch),
            )
        )

    log("beats")
    grid = detect.beat_grid(mix, accent, beats_per_bar, np.array(phrase_starts))
    tracks.append(
        TimingTrack("Beats", detect.beat_marks(grid, beats_per_bar, duration))
    )
    tracks.append(TimingTrack("Bars", detect.bar_marks(grid, beats_per_bar, duration)))
    for name in ("Drums", "Kick", "Snare", "Hi-Hat"):
        if name in drum_tracks:
            tracks.append(TimingTrack(name, drum_tracks[name]))
    return tracks + melodic, grid.tempo


def log(stage: str) -> None:
    print(f"Analysing {stage}...", file=sys.stderr)
