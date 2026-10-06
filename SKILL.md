---
name: hardsub-clip
description: Cut a clip from a show, anime or movie file between two timestamps with its English subtitles burned in, after checking the English track is real dialogue and not a "signs and songs" / forced track. Use whenever the user gives a video file (mkv, mp4) plus a start and end time and wants a clip, a shareable snippet, a hardsubbed scene, or a moment "with subs", even if they don't say "burn in" or "subtitles".
---

# Hardsub clip

`scripts/hardsub_clip.py` does the whole job: it probes the file, scores every subtitle track (embedded,
plus sidecar `.ass/.srt/.ssa/.vtt` files that start with the video's name), picks the English
dialogue track, prints the lines that fall inside the clip, and renders an H.264/AAC mp4.

```powershell
python scripts\hardsub_clip.py "<video>" <start> <end> [-o "<out.mp4>"] [--track N|file] [--audio N]
python scripts\hardsub_clip.py "<video>" --list
```

Timestamps are times in the episode: `83`, `1:23`, `1:23.5` or `1:02:03`. Turn whatever the user
said ("12m34s", "at 12:34 until 12:50") into one of these forms. Pass the range exactly as asked:
the script pads each end by half a second, and when the padded start falls inside a dialogue line
it moves back to half a second before that line, so the clip never opens mid-sentence. Output defaults to
`~\Videos\clips\<video name> [12m34s-12m50s].mp4`; pass `-o` when the user names a place.

## How the track is chosen

Each track gets a verdict from its dialogue lines per minute of runtime. A dialogue line is text in
a style that isn't named like a sign, song, OP/ED, karaoke or title, without `\pos`, `\move`,
`\clip` or karaoke tags. 3 or more per minute is `full`. Under 1 per minute, a forced flag, or a
title like "Signs & Songs" is `signs-songs`. In between is `unclear`. Image tracks (PGS, VobSub) have
no styles to read, so every display counts as a line and their verdict is rougher; they are counted
from the muxer's frame count when it has one, else by reading the track. Streams that can't be
burned in, such as broadcast captions, are listed as `unsupported` and never picked.

English means a `eng`/`en` language tag or "English" in the title. Fansubs often leave the tag
empty, so an untagged text track counts as English when its text reads as English.

The pick is the English `full` track with the default flag, then the one with the most dialogue.
A default flag on a signs track never wins: that is the usual trap on dual-audio releases.

## When the script stops

- **"no English track has full dialogue"**: show the user the track list it printed and ask which to
  use, or whether the file is the wrong release. Don't pass `--track` on your own to force a
  `signs-songs` track through; that is exactly what the user wants to avoid. An `unclear` track is
  worth offering, with its lines-per-minute figure.
- **"0 dialogue lines fall in the clip"** is printed but the render still runs. Tell the user: the
  range may be an OP/ED or a silent scene, or the timestamps may be off.
- **ffmpeg not found**: look for a bundled ffmpeg that has `ffprobe` beside it and `--enable-libass`
  in `ffmpeg -version`, and set `$env:FFMPEG` to its folder for the run. Tell the user
  `winget install Gyan.FFmpeg` puts a full build on PATH for good.

## Audio

Defaults to the first Japanese audio stream, else the default one. `--audio N` picks the Nth audio
stream (0 = first) when the user wants the dub or the commentary.

## Tests

```powershell
python -m unittest discover -s tests\unit
python -m unittest discover -s tests\integration
```

The integration tests build a two-minute episode with ffmpeg, so they need it on PATH or `FFMPEG`.
