---
name: hardsub-clip
description: Cut a clip from a show, anime or movie file between two timestamps with its English subtitles burned in, after checking the English track is real dialogue and not a "signs and songs" / forced track. Use whenever the user gives a video file (mkv, mp4) plus a start and end time and wants a clip, a shareable snippet, a hardsubbed scene, or a moment "with subs", even if they don't say "burn in" or "subtitles".
---

# Hardsub clip

`scripts/hardsub_clip.py` does the whole job: it reads every subtitle track, picks the English
dialogue track, prints the lines that fall in the clip, and renders an H.264/AAC mp4.

```powershell
python scripts\hardsub_clip.py "<video>" <start> <end> [-o "<out.mp4>"] [--track N|file] [--audio N]
python scripts\hardsub_clip.py "<video>" --list
```

## Timestamps and output

Timestamps are times in the episode: `83`, `1:23`, `1:23.5` or `1:02:03`. Turn whatever the user
said ("12m34s", "at 12:34 until 12:50") into one of these forms and pass the range exactly as
asked. The script pads each end by half a second, and when the padded start falls inside a
dialogue line it moves back to half a second before that line, repeating if that lands inside the
line before, so the clip never opens mid-sentence.

The clip goes to `$env:USERPROFILE\Videos\clips\<video name> [<start>-<end>].mp4`, named by the
padded range. Pass `-o` when the user names a place.

## Tracks it considers

- Every subtitle stream in the video.
- Subtitle files (`.ass`, `.ssa`, `.srt`, `.vtt`) in the same folder whose name is the video's name
  followed by `.`, a space, `_`, `-`, `[` or `(`, such as `Show - 01.en.ass`. `Show - 1` does not
  match `Show - 12.en.ass`. The language comes from a marker in the rest of the name.

## How the track is chosen

A track is English when its language tag is `eng`/`en` or its title says English. Fansubs often
leave the tag empty, so an untagged text track counts as English when its text reads as English.

Each track then gets a verdict from its dialogue lines per minute. A dialogue line is text in a
style that isn't named like a sign, song, OP/ED, karaoke or title, without `\pos`, `\move`, `\clip`
or karaoke tags. 3 or more per minute is `full`. Under 1 per minute, a forced flag, or a title like
"Signs & Songs" is `signs-songs`. In between is `unclear`.

Image tracks (PGS, VobSub) have no text or styles, so every display counts as a line and the verdict
is rougher. They are counted from the muxer's frame count when the file has one. Otherwise only
English-tagged image tracks are read; the rest are listed as `not read`. Streams that can't be
burned in, such as broadcast captions, are listed as `unsupported`.

The pick is the English `full` track with the default flag, then the one with the most dialogue. A
default flag on a signs track never wins: that is the usual trap on dual-audio releases.

## When the script stops or warns

- **"no English track has full dialogue"**: show the user the track list and ask which to use, or
  whether the file is the wrong release. Don't pass `--track` on your own to push a `signs-songs`
  track through; that is exactly what the user wants to avoid. Worth offering with `--track`: an
  `unclear` track, with its lines-per-minute figure, and a `not read` image track, which is
  usually an untagged English track on a Blu-ray remux.
- **"0 dialogue lines fall in the clip"** is printed but the render still runs. Tell the user the
  range may be an OP/ED or a silent scene, or the timestamps may be off.
- **"ffprobe reports no duration"**: the file is damaged or still being written; say so.
- **Anything ffmpeg prints** is passed through on stderr. Read it to the user; an "Invalid UTF-8"
  message means a sidecar `.srt` is in a legacy encoding and some lines were dropped.
- **ffmpeg not found**: look for a bundled ffmpeg that has `ffprobe` beside it and `--enable-libass`
  in `ffmpeg -version`, and set `$env:FFMPEG` to its folder for the run. Tell the user
  `winget install Gyan.FFmpeg` puts a full build on PATH for good.

## Audio

The first Japanese audio stream, else the default one. `--audio N` picks the Nth audio stream
(0 = first) when the user wants the dub or the commentary.

## Tests

```powershell
python -m unittest discover -s tests\unit
python -m unittest discover -s tests\integration
```

The integration tests generate short episodes with ffmpeg, so they need it on PATH or `FFMPEG`.
Any output on stderr fails them.
