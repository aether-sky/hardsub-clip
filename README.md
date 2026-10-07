# hardsub-clip

A Claude Code skill that cuts a clip from a show, anime or movie with its English subtitles
burned in. Give Claude a video file and a start and end time; it finds the English dialogue
track, makes sure it isn't just a "signs and songs" track, and renders an mp4 you can share
anywhere.

## Notes from a Human

I'd always wanted a feature in a media player where you could make a clip of something, similar to the clip feature on youtube or twitch to share with friends. It's easy enough with ffmpeg and some fiddling, but then you don't have subtitles, and the process is tedious. This finds the subs automatically, burns them in, and does all the fiddling for you.

## Install

1. Copy this folder to `~/.claude/skills/hardsub-clip` (Claude Code picks it up next session).
2. Have an ffmpeg build with libass and ffprobe on your PATH. On Windows,
   `winget install Gyan.FFmpeg` does it. Or set `FFMPEG` to an ffmpeg.exe or its folder.
3. Python 3.11 or later. No packages to install.

## Use

Ask Claude for a clip, for example "clip episode 3 from 4:10 to 4:25 with subs", or run it
yourself:

    python scripts/hardsub_clip.py "Show - 03.mkv" 4:10 4:25
    python scripts/hardsub_clip.py "Show - 03.mkv" --list

The clip lands in `Videos/clips` in your home folder, named after the episode and the range.
Each end gets half a second of padding, and if the start would cut into a line, the clip starts
half a second before that line instead, so it never opens mid-sentence.

## How it picks the subtitles

It looks at every subtitle track in the file, plus subtitle files next to it named after the
episode (`Show - 03.en.ass`). A track counts as English from its language tag or title, or, for
the many fansubs that leave the tag empty, from its text. It then counts dialogue lines per
minute, leaving out signs, songs, karaoke and positioned text: a real dialogue track has
several a minute, a signs-and-songs track almost none. Forced tracks never count as dialogue.

Text subtitles (ASS, SRT, WebVTT) are burned in with libass, using the fonts attached to the
video so fansub typesetting looks right. Blu-ray PGS and DVD subtitles are overlaid as images,
scaled to fit when the subtitle canvas and the video differ in size.

If no English track has full dialogue, it stops and lists the tracks instead of guessing.
`--track` picks one by stream index or file name, and `--audio` picks the audio stream
(Japanese by default).

## What it reads and writes

Reads the video file and the subtitle files beside it. Writes the clip, and temporary files that
are deleted when it finishes. Nothing goes over the network.

## Tests

    python -m unittest discover -s tests/unit
    python -m unittest discover -s tests/integration

The integration tests generate short episodes with ffmpeg and fail on anything ffmpeg prints.
