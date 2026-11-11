"""Separation plumbing, with a tiny untrained model.

The pretrained weights are a download, so this checks everything around the
model (decoding, normalisation, chunked inference, writing one WAV per stem)
rather than separation quality.
"""

import numpy as np
import pytest
import soundfile as sf

from xlights_timing import separate

htdemucs = pytest.importorskip("demucs.htdemucs")


def test_separate_writes_one_wav_per_stem(song, tmp_path):
    # Full depth but few channels: a shallower model would hand its
    # transformer tens of thousands of tokens and need gigabytes for attention.
    model = htdemucs.HTDemucs(
        sources=["drums", "bass", "other", "vocals"],
        channels=16,
        t_layers=1,
        segment=4,
    )
    paths = separate.separate(song.mix, tmp_path, device="cpu", model=model)
    assert set(paths) == {"drums", "bass", "other", "vocals"}
    mix_frames = sf.info(str(song.mix)).frames
    for path in paths.values():
        data, sr = sf.read(str(path))
        assert sr == 44100
        assert data.shape == (mix_frames, 2)
        assert np.isfinite(data).all()


def test_find_stems(tmp_path):
    for name in ("Drums.wav", "lead guitar.flac", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    assert set(separate.find_stems(tmp_path)) == {"drums", "lead guitar"}
