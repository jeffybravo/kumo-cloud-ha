"""Find hits, notes, phrases and beats in separated stems.

All analysis runs at 44.1 kHz with a 512-sample hop (about 11.6 ms per frame),
finer than any xLights frame rate, so snapping to the sequence frame is the
only rounding the marks see.

Two gates keep Demucs bleed (a faint ghost of the drums in the vocal stem, a
piano stem in a song that has no piano) out of the tracks:

* a hit must be within ``rel_floor_db`` of the loud end of its own stem or
  band, so quiet ghosts under a loud part are dropped, and
* it must be within ``abs_floor_db`` of the loud end of the whole mix, so a
  stem that is nothing but bleed produces nothing at all.
"""

from __future__ import annotations

from dataclasses import dataclass

import librosa
import numpy as np

from .xtiming import Mark

SR = 44100
HOP = 512
N_FFT = 2048
FRAME_S = HOP / SR
EPS = 1e-10

# Frequency bands used to split the drum stem (Hz), and how far below the loud
# end of the band a hit may be (dB). Each band also hears the other drums: a
# snare's noise reaches the kick band, and hi-hats and cymbals reach well down
# into the snare's. Those land well below the band's own drum, so the kick and
# snare floors are tight (and see the kick check in `drum_hits`). Hi-hats
# share their band with cymbals and the top of the snare, so a hard snare can
# show up there too. The snare band sits below where hats have energy
# (they have very little under 2-3 kHz) but above the kick's fundamental.
DRUM_BANDS = {
    "Kick": (30.0, 150.0, 12.0),
    "Snare": (200.0, 2500.0, 12.0),
    "Hi-Hat": (7000.0, 16000.0, 18.0),
}

# Pitch range for note names, per stem.
PITCH_RANGES = {
    "bass": ("B0", "C4"),
    "vocals": ("C2", "C6"),
    "guitar": ("E2", "E6"),
    "piano": ("A0", "C7"),
}


@dataclass(frozen=True)
class Settings:
    """Tuning knobs. ``sensitivity`` (0..1) scales the peak-picking threshold."""

    sensitivity: float = 0.5
    rel_floor_db: float = 30.0
    abs_floor_db: float = 45.0
    max_hit_s: float = 1.0
    min_note_s: float = 0.06
    phrase_gap_s: float = 0.4
    min_phrase_s: float = 0.3

    @property
    def delta(self) -> float:
        # Peak height above the local average needed to count as an onset,
        # as a fraction of the loudest onset in the track.
        return float(np.interp(self.sensitivity, [0.0, 0.5, 1.0], [0.3, 0.1, 0.03]))


@dataclass
class Spectrum:
    mag: np.ndarray  # (bins, frames)
    freqs: np.ndarray

    @classmethod
    def of(cls, y: np.ndarray) -> Spectrum:
        mag = np.abs(librosa.stft(y, n_fft=N_FFT, hop_length=HOP))
        return cls(mag, librosa.fft_frequencies(sr=SR, n_fft=N_FFT))

    @property
    def frames(self) -> int:
        return self.mag.shape[1]

    def band(self, lo: float, hi: float) -> np.ndarray:
        rows = (self.freqs >= lo) & (self.freqs < hi)
        return self.mag[rows]

    def power_db(self, lo: float = 0.0, hi: float = SR / 2) -> np.ndarray:
        band = self.band(lo, hi)
        return 10 * np.log10((band**2).sum(axis=0) + EPS)

    def flux(self, lo: float = 0.0, hi: float = SR / 2) -> np.ndarray:
        """Rectified spectral flux of log magnitude within a band."""
        log = np.log1p(self.band(lo, hi))
        diff = np.diff(log, axis=1, prepend=log[:, :1])
        return np.maximum(diff, 0).mean(axis=0)


def loud_level(power_db: np.ndarray) -> float:
    """The level of the loud parts: 99th percentile, ignoring one-off spikes."""
    return float(np.percentile(power_db, 99)) if power_db.size else -100.0


def pick_peaks(
    envelope: np.ndarray, settings: Settings, min_gap_s: float
) -> np.ndarray:
    """Onset frames from an onset-strength envelope."""
    peak = envelope.max() if envelope.size else 0.0
    if peak <= 0:
        return np.array([], dtype=int)
    env = envelope / peak
    return librosa.util.peak_pick(
        env,
        pre_max=3,
        post_max=3,
        pre_avg=10,
        post_avg=5,
        delta=settings.delta,
        wait=max(1, round(min_gap_s / FRAME_S)),
    )


def gate(
    frames: np.ndarray,
    power_db: np.ndarray,
    mix_level: float,
    settings: Settings,
    rel_floor_db: float | None = None,
) -> np.ndarray:
    """Drop onsets too quiet relative to their own band or to the whole mix."""
    if frames.size == 0:
        return frames
    rel = settings.rel_floor_db if rel_floor_db is None else rel_floor_db
    threshold = max(loud_level(power_db) - rel, mix_level - settings.abs_floor_db)
    # Attack peaks land a few frames after the onset is detected.
    window = np.lib.stride_tricks.sliding_window_view(
        np.pad(power_db, (0, 5), mode="edge"), 6
    )
    peaks = window.max(axis=1)
    return frames[peaks[frames] >= threshold]


def frames_to_s(frames: np.ndarray) -> np.ndarray:
    return librosa.frames_to_time(frames, sr=SR, hop_length=HOP)


def hits_to_marks(
    frames: np.ndarray, duration_s: float, max_len_s: float, label: str = ""
) -> list[Mark]:
    """Hits last until the next hit, at most ``max_len_s``."""
    times = frames_to_s(frames)
    marks = []
    for i, start in enumerate(times):
        nxt = times[i + 1] if i + 1 < len(times) else duration_s
        marks.append(Mark(start, min(nxt, start + max_len_s), label))
    return marks


# --------------------------------------------------------------------- drums


def drum_hits(
    y: np.ndarray, mix_level: float, settings: Settings
) -> dict[str, list[Mark]]:
    """Kick, snare and hi-hat tracks from the drum stem, plus every hit."""
    spec = Spectrum.of(y)
    duration = len(y) / SR
    tracks: dict[str, list[Mark]] = {}
    all_frames: list[np.ndarray] = []

    for name, (lo, hi, floor_db) in DRUM_BANDS.items():
        power = spec.power_db(lo, hi)
        frames = pick_peaks(spec.flux(lo, hi), settings, min_gap_s=0.07)
        frames = gate(frames, power, mix_level, settings, rel_floor_db=floor_db)
        if name == "Snare" and all_frames:
            # A kick's attack (its "knock" and beater click) reaches into the
            # snare band. On a kick, only count a snare that is about as loud
            # as the loudest snares, as one really played with the kick is.
            kicks = all_frames[0]
            with_kick = np.array(
                [kicks.size > 0 and np.abs(kicks - f).min() <= 2 for f in frames],
                dtype=bool,
            )
            strong = gate(frames, power, mix_level, settings, rel_floor_db=4.0)
            frames = frames[~with_kick | np.isin(frames, strong)]
        all_frames.append(frames)
        tracks[name] = hits_to_marks(frames, duration, settings.max_hit_s, name)

    # "Drums": any hit, labelled with every drum that sounded on it.
    events: dict[int, list[str]] = {}
    for name, frames in zip(DRUM_BANDS, all_frames, strict=True):
        for f in frames:
            # Treat hits within two frames (~23 ms) as the same moment.
            key = next((k for k in events if abs(k - f) <= 2), int(f))
            events.setdefault(key, []).append(name)
    keys = np.array(sorted(events), dtype=int)
    combined = hits_to_marks(keys, duration, settings.max_hit_s)
    tracks["Drums"] = [
        Mark(m.start, m.end, "+".join(events[k]))
        for m, k in zip(combined, keys, strict=True)
    ]
    return tracks


# ------------------------------------------------------------ melodic stems


def note_onsets(
    y: np.ndarray, stem: str, mix_level: float, settings: Settings
) -> tuple[np.ndarray, np.ndarray]:
    """Onset frames and the per-frame power of a pitched stem."""
    fmax = 2000.0 if stem == "bass" else 11000.0
    # max_size=3 is SuperFlux-style smoothing across frequency, which stops
    # vibrato and slides from reading as a stream of new notes.
    env = librosa.onset.onset_strength(
        y=y, sr=SR, hop_length=HOP, n_fft=N_FFT, max_size=3, fmax=fmax
    )
    spec = Spectrum.of(y)
    power = spec.power_db()
    n = min(len(env), len(power))
    env, power = env[:n], power[:n]
    frames = pick_peaks(env, settings, min_gap_s=0.09)
    return gate(frames, power, mix_level, settings), power


def note_ends(
    onsets: np.ndarray, power: np.ndarray, mix_level: float, settings: Settings
) -> np.ndarray:
    """Where each note dies away: 15 dB below its peak, or the next onset."""
    floor = max(
        loud_level(power) - settings.rel_floor_db, mix_level - settings.abs_floor_db
    )
    ends = np.empty_like(onsets)
    for i, start in enumerate(onsets):
        stop = onsets[i + 1] if i + 1 < len(onsets) else len(power)
        seg = power[start:stop]
        peak_at = int(np.argmax(seg[:10])) if seg.size else 0
        cutoff = max(seg[peak_at] - 15.0, floor) if seg.size else floor
        quiet = np.nonzero(seg[peak_at:] < cutoff)[0]
        ends[i] = start + peak_at + quiet[0] if quiet.size else stop
    return ends


def pitch_track(y: np.ndarray, stem: str) -> np.ndarray | None:
    """f0 per analysis frame (NaN when unvoiced), or None if not pitched."""
    if stem not in PITCH_RANGES:
        return None
    lo, hi = PITCH_RANGES[stem]
    # pYIN is slow; 11 kHz keeps the same 11.6 ms hop at a quarter the cost.
    ds = 4
    f0, _, _ = librosa.pyin(
        librosa.resample(y, orig_sr=SR, target_sr=SR // ds),
        fmin=librosa.note_to_hz(lo),
        fmax=librosa.note_to_hz(hi),
        sr=SR // ds,
        frame_length=1024,
        hop_length=HOP // ds,
    )
    return f0


def note_name(f0: np.ndarray | None, start: int, end: int) -> str:
    if f0 is None:
        return ""
    seg = f0[start:end]
    voiced = seg[~np.isnan(seg)]
    if voiced.size < max(2, 0.3 * seg.size):
        return ""
    return librosa.hz_to_note(float(np.median(voiced)), unicode=False)


def notes(
    y: np.ndarray,
    stem: str,
    mix_level: float,
    settings: Settings,
    with_pitch: bool = True,
) -> list[Mark]:
    onsets, power = note_onsets(y, stem, mix_level, settings)
    if onsets.size == 0:
        return []
    ends = note_ends(onsets, power, mix_level, settings)
    f0 = pitch_track(y, stem) if with_pitch else None
    marks = []
    for start, end in zip(onsets, ends, strict=True):
        s, e = frames_to_s(np.array([start, end]))
        if e - s >= settings.min_note_s:
            marks.append(Mark(s, e, note_name(f0, start, end)))
    return marks


def phrases(y: np.ndarray, mix_level: float, settings: Settings) -> list[Mark]:
    """Stretches where the stem is playing at all (e.g. each vocal line)."""
    power = Spectrum.of(y).power_db()
    floor = max(
        loud_level(power) - settings.rel_floor_db, mix_level - settings.abs_floor_db
    )
    active = power >= floor
    regions: list[list[float]] = []
    edges = np.diff(active.astype(int), prepend=0, append=0)
    starts = frames_to_s(np.nonzero(edges == 1)[0])
    stops = frames_to_s(np.nonzero(edges == -1)[0])
    for s, e in zip(starts, stops, strict=True):
        if regions and s - regions[-1][1] < settings.phrase_gap_s:
            regions[-1][1] = e
        else:
            regions.append([s, e])
    return [Mark(s, e) for s, e in regions if e - s >= settings.min_phrase_s]


# --------------------------------------------------------------------- beats


@dataclass
class BeatGrid:
    tempo: float
    beats: np.ndarray  # seconds
    downbeat_index: int  # index into beats of the first downbeat


def beat_grid(
    mix: np.ndarray,
    accent: np.ndarray | None,
    beats_per_bar: int,
    phrase_starts: np.ndarray | None = None,
) -> BeatGrid:
    """Beats from the mix, extended to cover the music, and the bar lines.

    The downbeat is chosen from three cues, weakest last:

    * ``accent``, an onset envelope that is strong on downbeats, usually the
      kick drum (one value per frame). Kicks often land on beats 1 and 3
      alike, so this alone can't tell those apart;
    * ``phrase_starts``, times where vocal and instrument lines begin, which
      mostly fall on (or just before) the one;
    * songs usually start on the one.
    """
    tempo, frames = librosa.beat.beat_track(y=mix, sr=SR, hop_length=HOP)
    tempo = float(np.atleast_1d(tempo)[0])
    beats = frames_to_s(np.asarray(frames, dtype=int))
    if beats.size < 2:
        return BeatGrid(tempo, beats, 0)

    # The beat tracker leaves out uncertain beats at the start and end; carry
    # the grid out to the first and last notes (onsets, not the tails of
    # notes ringing out, which would add a beat past the end).
    onsets = librosa.onset.onset_detect(
        y=mix, sr=SR, hop_length=HOP, units="time", backtrack=False
    )
    first, last = (onsets[0], onsets[-1]) if onsets.size else (beats[0], beats[-1])
    step = float(np.median(np.diff(beats)))
    tol = step / 4
    head = np.arange(beats[0] - step, first - tol, -step)[::-1]
    tail = np.arange(beats[-1] + step, last + tol, step)
    beats = np.concatenate([head, beats, tail])

    if accent is None:
        accent = librosa.onset.onset_strength(y=mix, sr=SR, hop_length=HOP, fmax=200.0)
    scores = np.zeros(beats_per_bar)
    if beats.size >= 2 * beats_per_bar:
        # Sample the envelope just around each beat to tolerate small offsets.
        frames = librosa.time_to_frames(beats, sr=SR, hop_length=HOP)
        padded = np.pad(accent, 2)
        at_beat = np.array(
            [padded[f : f + 5].max() if f < len(accent) else 0.0 for f in frames]
        )
        kick = np.array(
            [at_beat[k::beats_per_bar].mean() for k in range(beats_per_bar)]
        )
        if kick.max() > 0:
            scores += kick / kick.max()
    if phrase_starts is not None and len(phrase_starts):
        # Lines often start a little before the one (a pickup), so round to
        # the nearest beat.
        nearest = np.abs(beats[None, :] - np.asarray(phrase_starts)[:, None])
        idx = nearest.argmin(axis=1)
        votes = np.bincount(idx % beats_per_bar, minlength=beats_per_bar)
        scores += 0.5 * votes / votes.max()
    scores[0] += 0.1
    return BeatGrid(tempo, beats, int(np.argmax(scores)))


def beat_marks(grid: BeatGrid, beats_per_bar: int, duration_s: float) -> list[Mark]:
    beats = grid.beats
    if beats.size == 0:
        return []
    step = float(np.median(np.diff(beats))) if beats.size > 1 else 0.5
    ends = np.append(beats[1:], min(beats[-1] + step, duration_s))
    return [
        Mark(s, e, str((i - grid.downbeat_index) % beats_per_bar + 1))
        for i, (s, e) in enumerate(zip(beats, ends, strict=True))
    ]


def bar_marks(grid: BeatGrid, beats_per_bar: int, duration_s: float) -> list[Mark]:
    beats = grid.beats
    downbeats = beats[grid.downbeat_index :: beats_per_bar]
    if downbeats.size == 0:
        return []
    bar_len = (
        float(np.median(np.diff(downbeats)))
        if downbeats.size > 1
        else beats_per_bar * 0.5
    )
    ends = np.append(downbeats[1:], min(downbeats[-1] + bar_len, duration_s))
    marks = [
        Mark(s, e, str(i + 1))
        for i, (s, e) in enumerate(zip(downbeats, ends, strict=True))
    ]
    if grid.downbeat_index > 0:  # pickup beats before the first full bar
        marks.insert(0, Mark(beats[0], downbeats[0], "0"))
    return marks
