import xml.etree.ElementTree as ET

from xlights_timing.xtiming import (
    Mark,
    TimingTrack,
    snap_marks,
    to_audacity_labels,
    to_xtiming,
    write_tracks,
)


def test_snap_rounds_to_frames_and_keeps_one_frame_minimum():
    marks = [Mark(0.012, 0.013, "a"), Mark(0.26, 0.74, "b")]
    assert snap_marks(marks, 50) == [(0, 50, "a"), (250, 750, "b")]


def test_snap_drops_same_frame_and_cuts_overlaps():
    marks = [
        Mark(1.00, 2.00, "long"),
        Mark(1.01, 1.20, "same frame"),
        Mark(1.50, 1.70, "inside"),
    ]
    assert snap_marks(marks, 50) == [(1000, 1500, "long"), (1500, 1700, "inside")]


def test_snap_sorts_and_clamps_negative():
    marks = [Mark(0.5, 0.6), Mark(-0.01, 0.1)]
    assert snap_marks(marks, 25) == [(0, 100, ""), (500, 600, "")]


def test_single_track_uses_timing_root():
    xml = to_xtiming([TimingTrack("Kick", [Mark(0.5, 1.0, "K & <1>")])], 50)
    root = ET.fromstring(xml)
    assert root.tag == "timing"
    assert root.get("name") == "Kick"
    (layer,) = root
    assert layer.tag == "EffectLayer"
    (effect,) = layer
    assert effect.attrib == {"label": "K & <1>", "starttime": "500", "endtime": "1000"}


def test_several_tracks_use_timings_root():
    tracks = [TimingTrack("A", [Mark(0, 1)]), TimingTrack('B "quoted"', [])]
    root = ET.fromstring(to_xtiming(tracks, 50))
    assert root.tag == "timings"
    assert [t.get("name") for t in root] == ["A", 'B "quoted"']
    assert all(t.tag == "timing" for t in root)


def test_audacity_labels():
    text = to_audacity_labels(TimingTrack("x", [Mark(1.0, 1.5, "C4")]), 50)
    assert text == "1.0005\t1.5005\tC4\n"


def test_audacity_labels_survive_xlights_parsing():
    """Mimic xLights: strip from '#', then int(strtod(field) * 1000)."""
    marks = [Mark(i * 0.01, i * 0.01 + 0.2, "C#4") for i in range(0, 6000, 7)]
    track = TimingTrack("x", marks)
    expected = snap_marks(marks, 25)
    lines = to_audacity_labels(track, 25).splitlines()
    assert len(lines) == len(expected)
    for line, (start, end, _) in zip(lines, expected, strict=True):
        line = line.split("#")[0]
        s, e, label = line.split("\t")
        assert (int(float(s) * 1000), int(float(e) * 1000)) == (start, end)
        assert label == "C♯4"


def test_write_tracks(tmp_path):
    tracks = [TimingTrack("Hi-Hat", [Mark(0, 1)]), TimingTrack("Bass Notes", [])]
    paths = write_tracks(tracks, tmp_path, frame_ms=50, song_name="My Song!")
    names = sorted(p.name for p in paths)
    assert names == [
        "Bass_Notes.txt",
        "Bass_Notes.xtiming",
        "Hi-Hat.txt",
        "Hi-Hat.xtiming",
        "My_Song__all.xtiming",
    ]
    for p in paths:
        if p.suffix == ".xtiming":
            ET.parse(p)
