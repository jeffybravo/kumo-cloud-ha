"""Command line: ``xlights-timing song.mp3``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from . import detect, pipeline, separate, xtiming


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="xlights-timing",
        description=(
            "Split a song into instruments and write a timing track per "
            "instrument (kick, snare, hi-hat, bass, vocals...) for xLights."
        ),
    )
    p.add_argument("song", type=Path, help="audio file (mp3, wav, m4a, flac...)")
    p.add_argument(
        "-o",
        "--out",
        type=Path,
        help="output folder (default: <song name>_xlights next to the song)",
    )
    p.add_argument(
        "--model",
        default="htdemucs",
        choices=separate.MODELS,
        help=(
            "Demucs model: htdemucs (default; drums, bass, vocals, other), "
            "htdemucs_6s (adds guitar and piano), htdemucs_ft (best, slowest)"
        ),
    )
    p.add_argument(
        "--stems-dir",
        type=Path,
        help=(
            "skip separation and use stems already in this folder "
            "(drums.wav, bass.wav, vocals.wav, ... from any separator)"
        ),
    )
    rate = p.add_mutually_exclusive_group()
    rate.add_argument(
        "--fps",
        type=int,
        default=20,
        help="frame rate of your xLights sequence (default: 20, i.e. 50 ms)",
    )
    rate.add_argument(
        "--frame-ms", type=int, help="sequence frame length in ms (e.g. 25)"
    )
    p.add_argument(
        "--sensitivity",
        type=float,
        default=0.5,
        help="0 (only strong hits) to 1 (every little hit); default 0.5",
    )
    p.add_argument("--beats-per-bar", type=int, default=4)
    p.add_argument(
        "--no-pitch",
        action="store_true",
        help="don't label notes with their pitch (faster)",
    )
    p.add_argument(
        "--no-audacity",
        action="store_true",
        help="don't also write Audacity label (.txt) files",
    )
    p.add_argument(
        "--shifts",
        type=int,
        default=1,
        help="Demucs passes to average; 2-5 is a little cleaner and slower",
    )
    p.add_argument("--device", help="cpu, cuda or mps (default: best available)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    song: Path = args.song
    if not song.is_file():
        print(f"error: {song} not found", file=sys.stderr)
        return 2
    if not 0.0 <= args.sensitivity <= 1.0:
        print("error: --sensitivity must be between 0 and 1", file=sys.stderr)
        return 2
    frame_ms = args.frame_ms or round(1000 / args.fps)
    out = args.out or song.with_name(f"{song.stem}_xlights")

    if args.stems_dir:
        stems = separate.find_stems(args.stems_dir)
        if not stems:
            print(f"error: no audio files in {args.stems_dir}", file=sys.stderr)
            return 2
    else:
        stems = separate.separate(
            song,
            out / "stems",
            model_name=args.model,
            device=args.device,
            shifts=args.shifts,
        )

    settings = detect.Settings(sensitivity=args.sensitivity)
    tracks, tempo = pipeline.analyze(
        song,
        stems,
        settings=settings,
        beats_per_bar=args.beats_per_bar,
        pitch=not args.no_pitch,
    )
    xtiming.write_tracks(
        tracks,
        out / "timing",
        frame_ms=frame_ms,
        song_name=song.stem,
        audacity=not args.no_audacity,
    )

    print(f"\nTempo: about {tempo:.0f} BPM. Frame: {frame_ms} ms.")
    width = max(len(t.name) for t in tracks)
    for t in tracks:
        n = len(xtiming.snap_marks(t.marks, frame_ms))
        print(f"  {t.name:<{width}}  {n:>5} marks")
    print(f"\nTiming files: {out / 'timing'}")
    if not args.stems_dir:
        print(f"Stems:        {out / 'stems'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
