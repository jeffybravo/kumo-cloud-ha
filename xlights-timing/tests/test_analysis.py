"""End-to-end analysis of the synthetic song in conftest.py."""

import xml.etree.ElementTree as ET

import numpy as np
import pytest

from xlights_timing import cli, detect, pipeline

TOL = 0.03  # seconds; a little over two analysis frames


@pytest.fixture(scope="module")
def tracks(song):
    result, tempo = pipeline.analyze(song.mix, song.stems, settings=detect.Settings())
    return {t.name: t.marks for t in result}, tempo


def match(found, truth, tol=TOL):
    """(true times found, found times that match nothing)."""
    found = np.asarray(found)
    hits = sum(np.abs(found - t).min() <= tol for t in truth) if found.size else 0
    extra = sum(np.abs(np.asarray(truth) - f).min() > tol for f in found)
    return hits, extra


@pytest.mark.parametrize(
    ("name", "attr"), [("Kick", "kicks"), ("Snare", "snares"), ("Hi-Hat", "hats")]
)
def test_drum_hits(tracks, song, name, attr):
    truth = getattr(song, attr)
    starts = [m.start for m in tracks[0][name]]
    assert match(starts, truth) == (len(truth), 0)


def test_drums_track_labels_each_hit(tracks, song):
    first = tracks[0]["Drums"][0]
    assert first.start == pytest.approx(song.kicks[0], abs=TOL)
    assert set(first.label.split("+")) == {"Kick", "Hi-Hat"}
    # One mark per distinct moment: every eighth note.
    assert len(tracks[0]["Drums"]) == len(song.hats)


def test_bass_notes_with_pitch(tracks, song):
    marks = tracks[0]["Bass Notes"]
    assert match([m.start for m in marks], [b[0] for b in song.bass]) == (
        len(song.bass),
        0,
    )
    for mark, (_, end, note) in zip(marks, song.bass, strict=True):
        assert mark.label == note
        assert mark.end == pytest.approx(end, abs=0.06)


def test_vocal_notes_and_phrases(tracks, song):
    marks = tracks[0]["Vocals Notes"]
    assert [m.label for m in marks] == [v[2] for v in song.vocals]
    assert match([m.start for m in marks], [v[0] for v in song.vocals]) == (
        len(song.vocals),
        0,
    )
    phrases = tracks[0]["Vocals Phrases"]
    # Bars 3-4 and 7-8 (0-based 2-3 and 6-7).
    assert len(phrases) == 2
    assert phrases[0].start == pytest.approx(song.downbeats[2], abs=TOL)
    assert phrases[1].start == pytest.approx(song.downbeats[6], abs=TOL)


def test_tempo_beats_and_bars(tracks, song):
    named, tempo = tracks
    assert tempo == pytest.approx(120, abs=1)
    beats = named["Beats"]
    assert beats[0].start == pytest.approx(song.downbeats[0], abs=TOL)
    assert beats[0].label == "1"
    assert [m.label for m in beats[:5]] == ["1", "2", "3", "4", "1"]
    bars = named["Bars"]
    assert match([m.start for m in bars], song.downbeats) == (len(song.downbeats), 0)
    assert [m.label for m in bars] == [str(i + 1) for i in range(len(bars))]


def test_bleed_only_stem_produces_nothing(song):
    from xlights_timing import audio

    y = audio.load(song.stems["drums"], detect.SR)[0] * 10 ** (-60 / 20)
    mix = audio.load(song.mix, detect.SR)[0]
    mix_level = detect.loud_level(detect.Spectrum.of(mix).power_db())
    settings = detect.Settings()
    assert detect.notes(y, "piano", mix_level, settings, with_pitch=False) == []
    assert detect.phrases(y, mix_level, settings) == []


def test_cli_with_existing_stems(song, tmp_path, capsys):
    out = tmp_path / "out"
    code = cli.main(
        [
            str(song.mix),
            "--stems-dir",
            str(song.dir),
            "-o",
            str(out),
            "--fps",
            "40",
            "--no-pitch",
        ]
    )
    assert code == 0
    files = {p.name for p in (out / "timing").iterdir()}
    assert {"song_all.xtiming", "Kick.xtiming", "Vocals_Notes.xtiming"} <= files
    root = ET.parse(out / "timing" / "Kick.xtiming").getroot()
    starts = [int(e.get("starttime")) for e in root.iter("Effect")]
    assert all(s % 25 == 0 for s in starts)
    assert "Tempo: about 120 BPM" in capsys.readouterr().out
