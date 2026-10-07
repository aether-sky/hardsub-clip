"""Burn a video's English dialogue subtitles into a clip between two timestamps."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

TEXT_CODECS = {"ass", "ssa", "subrip", "srt", "mov_text", "webvtt", "vtt", "text"}
IMAGE_CODECS = {"hdmv_pgs_subtitle", "dvd_subtitle", "dvb_subtitle"}
SIDECAR_EXTS = {".ass", ".ssa", ".srt", ".vtt"}
ENGLISH_LANGS = {"en", "eng", "en-us", "en-gb"}
UNTAGGED_LANGS = {"", "und"}
ENGLISH_WORDS = set("the a an and to of i you it is that what this in my me we he she do don't not be are was "
                    "for on with your have no just i'm it's so can".split())
SIGN_STYLE = re.compile(r"sign|song|(?<![a-z])(op|ed|ts)(?![a-z])|opening|ending|kara|lyric|title|typeset|insert|eyecatch|note",
                        re.I)
SIGN_TITLE = re.compile(r"sign|song|s\s*&\s*s|forced", re.I)
POSITIONED = re.compile(r"\\(?:pos|move|org|i?clip)\(|\\(?:kf|ko|k|K)\d")
OVERRIDE = re.compile(r"\{[^}]*\}")
FULL_PER_MIN = 3.0   # dialogue lines per minute of runtime at or above which a track is full subtitles
SIGNS_PER_MIN = 1.0  # below this a track is signs and songs only
PADDING = 0.5        # seconds added to each end of the asked-for range, and kept before a line the start would cut
DEFAULT_OUT_DIR = Path.home() / "Videos" / "clips"


@dataclass
class Line:
    """A subtitle line, timed in seconds from the start of the file. Image lines have no text or style."""
    start: float
    end: float
    text: str = ""
    style: str = ""
    raw: str = ""
    image: bool = False


@dataclass
class Track:
    """A subtitle stream in the video (index is set) or a subtitle file next to it (path is set)."""
    codec: str
    lang: str
    title: str
    index: int | None = None
    path: Path | None = None
    forced: bool = False
    default: bool = False
    frames: int | None = None  # the muxer's frame count tag
    lines: list | None = None  # None until the track is read
    ass: Path | None = None
    english: bool = False
    sniffed: bool = False      # English judged from the text, not a tag
    verdict: str = ""
    dialogue_lines: int = 0
    per_min: float = 0.0
    note: str = ""


def find_tools():
    """(ffmpeg, ffprobe) from the FFMPEG environment variable (ffmpeg.exe or its folder), else PATH."""
    configured = os.environ.get("FFMPEG", "")
    if os.path.isdir(configured):
        configured = os.path.join(configured, "ffmpeg.exe" if os.name == "nt" else "ffmpeg")
    ffmpeg = configured if configured and os.path.isfile(configured) else shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg not found: run `winget install Gyan.FFmpeg`, or set FFMPEG to an ffmpeg.exe or its folder")
    ffprobe = os.path.join(os.path.dirname(ffmpeg), os.path.basename(ffmpeg).replace("ffmpeg", "ffprobe"))
    if not os.path.isfile(ffprobe):
        sys.exit(f"ffprobe not found next to {ffmpeg}")
    return ffmpeg, ffprobe


def parse_time(text):
    """Seconds from '83', '1:23', '1:23.5' or '1:02:03.25'."""
    parts = text.strip().split(":")
    if not 1 <= len(parts) <= 3 or not all(re.fullmatch(r"\d+(\.\d+)?", p) for p in parts):
        raise argparse.ArgumentTypeError(f"not a timestamp: {text!r} (use 83, 1:23 or 1:02:03.5)")
    seconds = 0.0
    for p in parts:
        seconds = seconds * 60 + float(p)
    return seconds


def label_time(seconds):
    """Filename-safe time such as 12m34s or 1h02m03.5s."""
    h, rest = divmod(seconds, 3600)
    m, s = divmod(rest, 60)
    s_text = f"{s:06.3f}".rstrip("0").rstrip(".")
    return f"{int(h)}h{int(m):02d}m{s_text}s" if h else f"{int(m)}m{s_text}s"


def clock_seconds(text):
    """Seconds from an H:MM:SS.fff clock time, as ASS events and Matroska DURATION tags write them."""
    h, m, s = text.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def media_duration(info):
    """Seconds the video runs: the container's duration, else its longest stream's, else its longest DURATION tag.
    Ends the script when none is known, since neither the clip range nor the line rates can be checked."""
    container = float(info["format"].get("duration") or 0)
    if container > 0:
        return container
    durations = [float(s["duration"]) for s in info["streams"] if s.get("duration")]
    durations += [clock_seconds(v) for s in info["streams"] for k, v in s.get("tags", {}).items()
                  if k.upper().startswith("DURATION")]
    durations = [d for d in durations if d > 0]
    if not durations:
        sys.exit("ffprobe reports no duration for this video, so the clip range and subtitle tracks can't be checked")
    return max(durations)


def parse_ass(text):
    """Lines of an ASS script, keeping each line's override tags in raw and its plain text in text."""
    lines, fields, in_events = [], None, False
    for row_text in text.splitlines():
        row_text = row_text.strip()
        if row_text.startswith("["):
            in_events = row_text.lower() == "[events]"
        elif in_events and row_text.lower().startswith("format:"):
            fields = [f.strip().lower() for f in row_text.split(":", 1)[1].split(",")]
        elif in_events and fields and row_text.startswith("Dialogue:"):
            values = row_text.split(":", 1)[1].split(",", len(fields) - 1)
            row = dict(zip(fields, (v.strip() for v in values)))
            raw = row.get("text", "")
            plain = OVERRIDE.sub("", raw).replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip()
            lines.append(Line(clock_seconds(row["start"]), clock_seconds(row["end"]), plain, row.get("style", ""), raw))
    return lines


def is_dialogue(line):
    """A spoken line: any image line, or plain text in a non-sign style, not positioned or karaoke-timed like a
    sign or song."""
    return line.image or (bool(line.text) and not SIGN_STYLE.search(line.style) and not POSITIONED.search(line.raw))


def classify(dialogue_lines, duration, title="", forced=False):
    """'full', 'signs-songs' or 'unclear' from dialogue lines per minute of runtime, plus the rate. A forced track
    is always 'signs-songs'."""
    per_min = dialogue_lines / (duration / 60)
    if forced:
        verdict = "signs-songs"
    elif per_min >= FULL_PER_MIN:
        verdict = "full"
    elif per_min < SIGNS_PER_MIN or SIGN_TITLE.search(title):
        verdict = "signs-songs"
    else:
        verdict = "unclear"
    return verdict, per_min


def is_english(lang, title):
    return lang.lower() in ENGLISH_LANGS or bool(re.search(r"\benglish\b|\beng\b", title, re.I))


def text_looks_english(lines):
    """True when dialogue is almost all ASCII letters and common English words make up a sixth of it."""
    text = " ".join(line.text for line in lines if is_dialogue(line)).lower()
    letters = [c for c in text if c.isalpha()]
    words = re.findall(r"[a-z']+", text)
    if not letters or not words or sum(c.isascii() for c in letters) / len(letters) < 0.95:
        return False
    return sum(w in ENGLISH_WORDS for w in words) / len(words) >= 1 / 6


def decide_language(track):
    """Set track.english from its language tag or title, or, for an untagged track that has been read, its text."""
    track.english = is_english(track.lang, track.title)
    if not track.english and track.lang.lower() in UNTAGGED_LANGS and track.lines is not None:
        track.english = track.sniffed = text_looks_english(track.lines)


def is_sidecar(path, video):
    """A subtitle file named after the video: its stem followed by a separator, so 'Show - 1' skips 'Show - 12'."""
    rest = path.name[len(video.stem):]
    return (path.suffix.lower() in SIDECAR_EXTS and path.name.startswith(video.stem)
            and rest[:1] in tuple(". _-[("))


def sidecar_lang(path, video):
    """Language marker in a sidecar name such as 'Show - 01.en.ass' or 'Show - 01 [English].srt'."""
    rest = path.stem[len(video.stem):].lower()
    if re.search(r"(^|[.\s_\-\[(])(en|eng|english)([.\s_\-\])]|$)", rest):
        return "eng"
    tokens = [t for t in re.split(r"[.\s_\-\[\]()]+", rest) if t]
    return tokens[-1] if tokens and len(tokens[-1]) in (2, 3) else ""


def run(cmd, cwd=None):
    """stdout of cmd; anything it prints to stderr is passed on, and a failure ends the script."""
    result = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode:
        sys.exit(f"command failed ({result.returncode}): {' '.join(map(str, cmd))}\n{result.stderr.strip()}")
    if result.stderr.strip():
        print(result.stderr.strip(), file=sys.stderr)
    return result.stdout


def probe(ffprobe, video):
    return json.loads(run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(video)]))


def candidate_tracks(info, video):
    """Embedded subtitle streams and the subtitle files named after the video."""
    tracks = []
    for s in info["streams"]:
        if s.get("codec_type") != "subtitle":
            continue
        tags = s.get("tags", {})
        tracks.append(Track(s.get("codec_name", "?"), tags.get("language", ""), tags.get("title", ""), index=s["index"],
                            forced=bool(s.get("disposition", {}).get("forced")),
                            default=bool(s.get("disposition", {}).get("default")),
                            frames=next((int(v) for k, v in tags.items() if k.upper().startswith("NUMBER_OF_FRAMES")),
                                        None)))
    for path in sorted(video.parent.iterdir()):
        if is_sidecar(path, video):
            tracks.append(Track(path.suffix[1:].lower(), sidecar_lang(path, video), path.name, path=path))
    return tracks


def describe(track):
    where = f"stream {track.index}" if track.path is None else f"file {track.path.name}"
    flags = "".join(f" [{name}]" for name, on in (("default", track.default), ("forced", track.forced)) if on)
    title = f" \"{track.title}\"" if track.title and track.path is None else ""
    sniffed = " (untagged, text reads as English)" if track.sniffed else ""
    return f"{where}: {track.codec} lang={track.lang or '?'}{sniffed}{title}{flags}"


def extract_text_tracks(ffmpeg, video, tracks, work):
    """Write every text track to work/<n>.ass in one ffmpeg run and read its lines. Sidecar files go through ffmpeg
    too, which turns UTF-16 scripts into the UTF-8 that libass and parse_ass expect."""
    inputs, outputs = ["-i", str(video)], []
    for n, t in enumerate(tracks):
        t.ass = work / f"{n}.ass"
        if t.path is None:
            source = f"0:{t.index}"
        else:
            source = f"{len(inputs) // 2}:0"
            inputs += ["-i", str(t.path)]
        outputs += ["-map", source, "-c:s", "copy" if t.codec in ("ass", "ssa") else "ass", str(t.ass)]
    if outputs:
        run([ffmpeg, "-v", "error", "-y", *inputs, *outputs])
    for t in tracks:
        t.lines = parse_ass(t.ass.read_text(encoding="utf-8-sig", errors="replace"))


def image_lines(ffprobe, video, index, start_time):
    """Displays of an image track, each shown until the next display set, timed from the start of the file."""
    frames = json.loads(run([ffprobe, "-v", "error", "-select_streams", str(index), "-show_frames",
                             "-show_entries", "frame=pts_time,num_rects", "-of", "json", str(video)]))["frames"]
    times = [float(f["pts_time"]) - start_time for f in frames]
    return [Line(times[n], times[n + 1] if n + 1 < len(times) else times[n] + 5, image=True)
            for n, f in enumerate(frames) if f.get("num_rects")]


def read_tracks(ffmpeg, ffprobe, video, tracks, start_time, work):
    """Read every text track, decide each track's language, then read the English image tracks that have no muxer
    frame count to count them by. Other image tracks are never picked, so reading them would be wasted work."""
    extract_text_tracks(ffmpeg, video, [t for t in tracks if t.codec in TEXT_CODECS], work)
    for t in tracks:
        decide_language(t)
        if t.codec in IMAGE_CODECS and t.english and t.frames is None:
            t.lines = image_lines(ffprobe, video, t.index, start_time)


def assess(track, duration):
    """Set the track's verdict, dialogue_lines, per_min and note. An unread image track with a muxer frame count is
    counted from it, halved because each display is followed by a clearing packet."""
    if track.codec not in TEXT_CODECS | IMAGE_CODECS:
        track.verdict, track.note = "unsupported", "codec cannot be burned in"
    elif track.lines is None and track.frames is None:
        track.verdict, track.note = "not read", "image subtitles not tagged English"
    else:
        track.dialogue_lines = track.frames // 2 if track.lines is None else sum(map(is_dialogue, track.lines))
        track.note = ("image subtitles, styles unknown" if track.codec in IMAGE_CODECS
                      else f"{len(track.lines)} events")
        track.verdict, track.per_min = classify(track.dialogue_lines, duration,
                                                track.title if track.path is None else "", track.forced)


def choose(tracks):
    """The English full-subtitle track with the default flag, then the most dialogue; None if there is none."""
    full = [t for t in tracks if t.english and t.verdict == "full"]
    return max(full, key=lambda t: (t.default, t.dialogue_lines), default=None)


def find_track(tracks, name):
    """The track --track names: an embedded stream's index or a sidecar's file name."""
    return next((t for t in tracks if (t.path is None and str(t.index) == name) or
                 (t.path is not None and t.path.name == name)), None)


def clip_range(start, end, lines, duration):
    """The asked-for range padded on both ends, with the start moved back before any dialogue line it would cut,
    repeatedly, since the moved start can land in the line before."""
    start, end = max(0.0, start - PADDING), min(duration, end + PADDING)
    while cut := [line.start for line in lines if line.start < start < line.end and is_dialogue(line)]:
        start = max(0.0, min(cut) - PADDING)
    return start, end


def pick_audio(info, wanted):
    """Index among audio streams: the one asked for, else the first Japanese, else the default, else 0."""
    audio = [s for s in info["streams"] if s.get("codec_type") == "audio"]
    if not audio:
        return None
    if wanted is not None:
        if not 0 <= wanted < len(audio):
            sys.exit(f"--audio {wanted}: the video has {len(audio)} audio streams (0-{len(audio) - 1})")
        return wanted
    for n, s in enumerate(audio):
        if s.get("tags", {}).get("language", "").lower() in ("jpn", "ja"):
            return n
    return next((n for n, s in enumerate(audio) if s.get("disposition", {}).get("default")), 0)


def dump_fonts(ffmpeg, video, info, fonts):
    """Write the video's attached fonts into fonts/ so typeset subtitles render in their own faces. Each is named
    by stream index, because the filename stored in the video can be any path."""
    fonts.mkdir()
    dumps = []
    for s in info["streams"]:
        if s.get("codec_type") == "attachment":
            ext = Path(s.get("tags", {}).get("filename", "")).suffix.lower()
            dumps += [f"-dump_attachment:{s['index']}", str(fonts / f"{s['index']}{ext if ext.isascii() else ''}")]
    if dumps:
        run([ffmpeg, "-v", "error", "-y", *dumps, "-i", str(video), "-t", "0", "-f", "null", "-"])


def image_overlay(info, index):
    """Filter graph that lays an image track over the first video stream. A canvas of another size is scaled to fit
    inside the video, keeping its shape, and centred, as players do."""
    video = next(s for s in info["streams"] if s.get("codec_type") == "video")
    canvas = next(s for s in info["streams"] if s["index"] == index)
    vw, vh, cw, ch = video["width"], video["height"], canvas.get("width") or 0, canvas.get("height") or 0
    if not cw or not ch or (cw, ch) == (vw, vh):
        return f"[0:v:0][0:{index}]overlay=eof_action=pass,format=yuv420p[v]"
    scale = min(vw / cw, vh / ch)
    w, h = round(cw * scale / 2) * 2, round(ch * scale / 2) * 2
    return (f"[0:{index}]scale={w}:{h}[s];"
            f"[0:v:0][s]overlay={(vw - w) // 2}:{(vh - h) // 2}:eof_action=pass,format=yuv420p[v]")


def render(ffmpeg, video, info, track, start, end, audio, out, work):
    duration = end - start
    cmd = [ffmpeg, "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}"]
    if track.codec in IMAGE_CODECS:
        cmd += ["-filter_complex", image_overlay(info, track.index), "-map", "[v]"]
    else:
        dump_fonts(ffmpeg, video, info, work / "fonts")
        shutil.copyfile(track.ass, work / "clip.ass")
        # Input seeking restarts timestamps at 0, so shift them back to the subtitle clock while burning.
        cmd += ["-map", "0:v:0", "-vf",
                f"setpts=PTS+{start:.3f}/TB,subtitles=clip.ass:fontsdir=fonts,setpts=PTS-STARTPTS,format=yuv420p"]
    if audio is not None:
        cmd += ["-map", f"0:a:{audio}", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-movflags", "+faststart", str(out)]
    run(cmd, cwd=work)


def main():
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("video", type=Path)
    ap.add_argument("start", type=parse_time, nargs="?", help="clip start: 83, 1:23 or 1:02:03.5")
    ap.add_argument("end", type=parse_time, nargs="?", help="clip end, same forms")
    ap.add_argument("--track", help="use this subtitle stream index or sidecar file name instead of the automatic pick")
    ap.add_argument("--audio", type=int, help="audio stream number among the video's audio streams (0 = first)")
    ap.add_argument("-o", "--output", type=Path, help=f"output .mp4 (default: {DEFAULT_OUT_DIR}\\<name> [start-end].mp4)")
    ap.add_argument("--list", action="store_true", help="report the subtitle tracks and stop")
    args = ap.parse_args()

    video = args.video.resolve()
    if not video.is_file():
        sys.exit(f"no such file: {video}")
    if not args.list and (args.start is None or args.end is None):
        ap.error("start and end are required unless --list is given")
    ffmpeg, ffprobe = find_tools()
    info = probe(ffprobe, video)
    duration = media_duration(info)
    start_time = float(info["format"].get("start_time") or 0)
    if not args.list:
        if args.end <= args.start:
            sys.exit(f"end {args.end:.3f}s is not after start {args.start:.3f}s")
        if args.end > duration + 0.5:
            sys.exit(f"end {args.end:.3f}s is past the end of the video ({duration:.3f}s)")

    tracks = candidate_tracks(info, video)
    if not tracks:
        sys.exit("the video has no subtitle streams and no subtitle files sit next to it")
    with tempfile.TemporaryDirectory(prefix="hardsub-clip-") as tmp:
        work = Path(tmp)
        read_tracks(ffmpeg, ffprobe, video, tracks, start_time, work)
        for t in tracks:
            assess(t, duration)
        print(f"{video.name}  ({duration / 60:.1f} min)")
        for t in tracks:
            print(f"  {describe(t)}  -> {t.verdict}, {t.dialogue_lines} dialogue lines "
                  f"({t.per_min:.1f}/min, {t.note}){'' if t.english else '  [not English]'}")
        if args.list:
            return

        if args.track:
            track = find_track(tracks, args.track)
            if not track:
                sys.exit(f"--track {args.track}: no such subtitle stream or file in the list above")
            if track.verdict == "unsupported":
                sys.exit(f"--track {args.track}: {track.codec} subtitles cannot be burned in")
        else:
            track = choose(tracks)
            if not track:
                sys.exit("no English track has full dialogue (see the list above). Pass --track to use one anyway.")
        print(f"using {describe(track)}")
        if track.codec in IMAGE_CODECS and track.lines is None:
            track.lines = image_lines(ffprobe, video, track.index, start_time)

        start, end = clip_range(args.start, args.end, track.lines, duration)
        print(f"clip runs {start:.2f}s to {end:.2f}s (asked for {args.start:.2f}s to {args.end:.2f}s)")
        in_clip = [line for line in track.lines if line.end > start and line.start < end and is_dialogue(line)]
        print(f"{len(in_clip)} dialogue lines fall in the clip:")
        for line in in_clip:
            print(f"  {line.start - start:6.2f}s  {'(image subtitle)' if line.image else line.text}")
        if not in_clip:
            print("  (none: the clip will have no dialogue subtitles)")

        audio = pick_audio(info, args.audio)
        out = (args.output or DEFAULT_OUT_DIR / f"{video.stem} [{label_time(start)}-{label_time(end)}].mp4").resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        render(ffmpeg, video, info, track, start, end, audio, out, work)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
