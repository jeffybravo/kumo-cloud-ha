"""Split a song into instrument stems with Demucs."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

from . import audio

# htdemucs: drums, bass, other, vocals.
# htdemucs_6s adds guitar and piano (piano is the weaker of the two).
# htdemucs_ft is a fine-tuned htdemucs: better, about 4x slower.
MODELS = ("htdemucs", "htdemucs_ft", "htdemucs_6s")


def separate(
    song: Path,
    out_dir: Path,
    *,
    model_name: str = "htdemucs",
    device: str | None = None,
    shifts: int = 1,
    model=None,
) -> dict[str, Path]:
    """Separate ``song`` and write one WAV per stem into ``out_dir``.

    Returns ``{stem name: wav path}``. ``model`` lets callers pass an already
    loaded Demucs model (tests use this; the pretrained weights are a download).
    """
    import torch
    from demucs.apply import apply_model

    if model is None:
        from demucs.pretrained import get_model

        model = get_model(model_name)
    model.eval()
    if device is None:
        if torch.cuda.is_available():
            device = "cuda"
        elif torch.backends.mps.is_available():
            device = "mps"
        else:
            device = "cpu"

    mix = audio.load(song, model.samplerate, model.audio_channels)
    wav = torch.from_numpy(mix)
    # Same normalisation as `demucs.separate`: the models expect unit variance.
    ref = wav.mean(0)
    mean, std = ref.mean(), ref.std() + 1e-8
    wav = (wav - mean) / std

    print(f"Separating with {model_name} on {device}...", file=sys.stderr)
    with torch.no_grad():
        sources = apply_model(
            model,
            wav[None],
            device=device,
            shifts=shifts,
            split=True,
            overlap=0.25,
            progress=True,
        )[0]
    sources = sources * std + mean

    paths = {}
    for name, source in zip(model.sources, sources, strict=True):
        path = out_dir / f"{name}.wav"
        audio.save(path, source.cpu().numpy().astype(np.float32), model.samplerate)
        paths[name] = path
    return paths


def find_stems(stems_dir: Path) -> dict[str, Path]:
    """Pick up stems separated earlier (by this tool or any other).

    Any audio file in the folder counts; its name (without extension) is the
    stem name, so ``drums.wav``, ``Bass.mp3`` or ``lead guitar.flac`` all work.
    """
    exts = {".wav", ".flac", ".mp3", ".ogg", ".m4a", ".aiff", ".aif"}
    return {
        p.stem.lower(): p
        for p in sorted(stems_dir.iterdir())
        if p.suffix.lower() in exts
    }
