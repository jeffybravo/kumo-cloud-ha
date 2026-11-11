"""Timing marks and the files xLights imports them from.

xLights reads ``.xtiming`` files with either a single ``<timing>`` root or a
``<timings>`` root holding several, each made of ``<EffectLayer>`` elements
whose ``<Effect>`` children carry ``label``, ``starttime`` and ``endtime`` in
milliseconds. On import it rounds times down to the sequence's frame length, so
marks are snapped to that frame here first: two marks that would land on the
same frame are merged rather than left for xLights to collapse into zero-length
marks.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import quoteattr


@dataclass(frozen=True)
class Mark:
    """One timing mark, in seconds."""

    start: float
    end: float
    label: str = ""


@dataclass
class TimingTrack:
    name: str
    marks: list[Mark]


def snap_marks(marks: Iterable[Mark], frame_ms: int) -> list[tuple[int, int, str]]:
    """Snap marks to whole frames and make them non-overlapping.

    Returns ``(start_ms, end_ms, label)`` tuples, sorted, each at least one
    frame long. A mark that starts on the same frame as the previous one is
    dropped; one that starts inside the previous mark cuts it short.
    """
    out: list[tuple[int, int, str]] = []
    for mark in sorted(marks, key=lambda m: m.start):
        start = max(0, round(mark.start * 1000 / frame_ms) * frame_ms)
        end = max(start + frame_ms, round(mark.end * 1000 / frame_ms) * frame_ms)
        if out:
            prev_start, prev_end, prev_label = out[-1]
            if start <= prev_start:
                continue
            if start < prev_end:
                out[-1] = (prev_start, start, prev_label)
        out.append((start, end, mark.label))
    return out


def _timing_xml(track: TimingTrack, frame_ms: int, indent: str) -> str:
    lines = [f"{indent}<timing name={quoteattr(track.name)}>"]
    lines.append(f"{indent}   <EffectLayer>")
    for start, end, label in snap_marks(track.marks, frame_ms):
        lines.append(
            f"{indent}      <Effect label={quoteattr(label)} "
            f'starttime="{start}" endtime="{end}" />'
        )
    lines.append(f"{indent}   </EffectLayer>")
    lines.append(f"{indent}</timing>")
    return "\n".join(lines)


def to_xtiming(tracks: Sequence[TimingTrack], frame_ms: int) -> str:
    """Render one track as ``<timing>``, or several under ``<timings>``."""
    header = '<?xml version="1.0" encoding="UTF-8"?>\n'
    if len(tracks) == 1:
        return header + _timing_xml(tracks[0], frame_ms, "") + "\n"
    body = "\n".join(_timing_xml(t, frame_ms, "   ") for t in tracks)
    return f"{header}<timings>\n{body}\n</timings>\n"


def to_audacity_labels(track: TimingTrack, frame_ms: int) -> str:
    """Audacity label text (start, end, label in seconds, tab separated).

    xLights imports these too (Import Timing Track, ``.txt``), which also makes
    the marks easy to inspect against the waveform in Audacity.

    Two quirks of xLights' reader shape this: it converts with
    ``int(seconds * 1000)``, which truncates 0.57 to 569 ms and then a whole
    frame on snapping, so times carry an extra half millisecond; and it treats
    ``#`` as the start of a comment, so sharps are written as ``♯``.
    """
    return "".join(
        f"{(start + 0.5) / 1000:.4f}\t{(end + 0.5) / 1000:.4f}\t"
        f"{label.replace('#', '♯')}\n"
        for start, end, label in snap_marks(track.marks, frame_ms)
    )


def safe_filename(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_" else "_" for c in name)
    return keep.strip().replace(" ", "_") or "track"


def write_tracks(
    tracks: Sequence[TimingTrack],
    out_dir: Path,
    *,
    frame_ms: int,
    song_name: str,
    audacity: bool = True,
) -> list[Path]:
    """Write every track to its own file plus one combined ``.xtiming``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    combined = out_dir / f"{safe_filename(song_name)}_all.xtiming"
    combined.write_text(to_xtiming(tracks, frame_ms), encoding="utf-8")
    written.append(combined)
    for track in tracks:
        base = out_dir / safe_filename(track.name)
        path = base.with_suffix(".xtiming")
        path.write_text(to_xtiming([track], frame_ms), encoding="utf-8")
        written.append(path)
        if audacity:
            txt = base.with_suffix(".txt")
            txt.write_text(to_audacity_labels(track, frame_ms), encoding="utf-8")
            written.append(txt)
    return written
