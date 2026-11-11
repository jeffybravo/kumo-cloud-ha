# xlights-timing

Split a song into instruments and get an **xLights timing track per
instrument**: kick, snare, hi-hat, bass notes, vocal lines and notes, beats and
bars. Then drop effects on the snare, the kick or each sung word instead of
tapping out timing marks by hand.

```
xlights-timing "Carol of the Bells.mp3"
```

```
Tempo: about 142 BPM. Frame: 50 ms.
  Beats              612 marks
  Bars               153 marks
  Drums             1830 marks
  Kick               604 marks
  Snare              311 marks
  Hi-Hat            1204 marks
  Vocals Phrases      38 marks
  Vocals Notes       402 marks
  Bass Phrases        21 marks
  Bass Notes         577 marks
  Other Phrases       14 marks
  Other Notes        880 marks
```

(Illustrative numbers. Your song will differ.)

## How it works

1. **Separation.** [Demucs](https://github.com/facebookresearch/demucs) splits
   the song into stems: drums, bass, vocals and "other" (everything else). The
   `htdemucs_6s` model also splits out guitar and piano. The stems are saved
   as WAVs, which are handy for listening to what each track was built from.
2. **Drums.** The drum stem is split by frequency band into kick (30–150 Hz),
   snare (200–2500 Hz) and hi-hat (7–16 kHz), and each band gets its own
   hits. A **Drums** track has every hit, labelled with what played on it
   (`Kick+Hi-Hat`).
3. **Pitched stems.** Each one gets two tracks. **Notes** marks each note from
   its onset until it dies away, labelled with its pitch for bass, vocals,
   guitar and piano (`A1`, `E4`). **Phrases** marks each stretch where the
   instrument is playing, such as a whole vocal line, which suits on/off or
   fade effects.
4. **Beats and bars.** Beats are labelled `1 2 3 4`. Bars are numbered and
   start on the downbeat, picked from where the kick and the starts of
   vocal/instrument lines land.

Quiet ghosts of other instruments that Demucs leaves in a stem are filtered
out. A stem with nothing real in it, like the piano stem of a song with no
piano, gives an empty track.

Every mark is snapped to your sequence's frame length, and no two marks
overlap.

## Install

You need Python 3.10 or newer and [ffmpeg](https://ffmpeg.org/download.html)
on your PATH (for reading mp3/m4a). Then, from this folder:

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install .
```

That pulls in PyTorch, which is the big download. The first run also
downloads the Demucs model weights (cached afterwards). An NVIDIA GPU
(CUDA) or Apple Silicon (MPS) is used automatically if present. On CPU a
4-minute song takes a few minutes.

## Use

```
xlights-timing song.mp3                    # writes song_xlights/ next to the song
xlights-timing song.mp3 --fps 40           # match a 40 fps (25 ms) sequence
xlights-timing song.mp3 --model htdemucs_6s  # also guitar and piano tracks
xlights-timing song.mp3 --sensitivity 0.7  # catch softer hits
xlights-timing song.mp3 --stems-dir my_stems/  # skip Demucs, use your own stems
```

Output:

```
song_xlights/
  stems/    drums.wav bass.wav vocals.wav other.wav
  timing/   song_all.xtiming        every track in one file
            Kick.xtiming Snare.xtiming Vocals_Notes.xtiming ...   one per track
            Kick.txt Snare.txt ...  the same as Audacity labels
```

| Option | What it does |
| --- | --- |
| `--fps N` / `--frame-ms N` | Your sequence's timing (default 20 fps = 50 ms). Set this to match, or xLights rounds the marks again on import. |
| `--model` | `htdemucs` (default), `htdemucs_6s` (adds guitar and piano), `htdemucs_ft` (cleanest stems, about 4× slower). |
| `--sensitivity 0..1` | Lower keeps only strong hits; higher catches ghost notes and soft hits. Default 0.5. |
| `--beats-per-bar N` | For songs not in 4/4, e.g. `3` for a waltz. |
| `--stems-dir DIR` | Use stems you already have (from any separator: UVR, Moises, LALAL.AI...). Files are matched by name: `drums`, `bass`, `vocals`, `guitar`, `piano`, `other`. Any other name gets its own Phrases/Notes tracks. |
| `--no-pitch` | Skip pitch labels on notes (faster). |
| `--shifts N` | Average N Demucs passes for slightly cleaner stems (slower). |
| `--no-audacity` | Only write `.xtiming` files. |
| `-o DIR` | Output folder. |

## Import into xLights

1. Open your sequence, with the same song, in the **Sequencer** tab.
2. Right-click the name of any timing track and choose **Import Timing
   Track**. If the sequence has no timing track yet, use the **Import
   Timings** button in the sequence's settings dialog, which opens the same
   file picker.
3. Pick `song_all.xtiming` to bring in every track, or select just the ones
   you want, such as `Kick.xtiming` and `Vocals_Phrases.xtiming`. You can
   select several files at once, as long as they are all the same type.

Each track keeps its name (`Kick`, `Vocals Notes`...). If you already have a
track with that name, xLights adds `_1` to the new one.

The `.txt` files are Audacity label files. xLights imports those too, and you
can open them in Audacity next to the stem to check or tidy up the marks
before importing. Sharps are written as `♯` in them (`C♯4`), because xLights
treats `#` in a `.txt` timing file as the start of a comment.

## Limits and tips

* **Separation is good, not perfect.** Busy mixes leave some bleed. If a track
  has stray marks, lower `--sensitivity`. If it misses soft hits, raise it.
  `htdemucs_ft` gives cleaner stems when it matters.
* **Hi-hat includes cymbals**, and hard snare hits can show up there too,
  since they share the same high frequencies. Toms mostly land in Kick or
  Snare, depending on their pitch.
* **"Other" is polyphonic** (synths, pads, strings together), so its notes
  carry no pitch label. Use `htdemucs_6s` to pull guitar and piano out of it.
* **Downbeats** can come out a beat or two off in songs with unusual accents.
  The Beats track is still right; check where the Bars track starts and shift
  it in xLights if needed.
* Tempo changes are followed, but the bar grid assumes one time signature for
  the whole song.

## Development

```
pip install -e ".[test]"
pytest
```

The tests build a synthetic song with known kick, snare, hi-hat, bass and
vocal times and check that every hit and note is found with nothing extra,
along with pitch labels, phrases, beats and bars. Separation is tested with a
tiny untrained model, since the real weights are a download.
