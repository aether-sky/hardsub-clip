"""Burn a video's English dialogue subtitles into a clip between two timestamps."""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
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


def ass_time(text):
    h, m, s = text.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_ass(text):
    """Events of an ASS script as dicts with start, end, style, raw (with override tags) and plain text."""
    events, fields, in_events = [], None, False
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("["):
            in_events = line.lower() == "[events]"
        elif in_events and line.lower().startswith("format:"):
            fields = [f.strip().lower() for f in line.split(":", 1)[1].split(",")]
        elif in_events and fields and line.startswith("Dialogue:"):
            values = line.split(":", 1)[1].split(",", len(fields) - 1)
            row = dict(zip(fields, (v.strip() for v in values)))
            raw = row.get("text", "")
            plain = OVERRIDE.sub("", raw).replace("\\N", " ").replace("\\n", " ").replace("\\h", " ").strip()
            events.append({"start": ass_time(row["start"]), "end": ass_time(row["end"]),
                           "style": row.get("style", ""), "raw": raw, "text": plain})
    return events


def is_dialogue(event):
    """A spoken line: plain text in a non-sign style, not positioned or karaoke-timed like a sign or song."""
    return bool(event["text"]) and not SIGN_STYLE.search(event["style"]) and not POSITIONED.search(event["raw"])


def classify(dialogue_lines, duration, title="", forced=False):
    """'full', 'signs-songs' or 'unclear' from dialogue lines per minute of runtime, plus the rate. A forced track
    is always 'signs-songs'."""
    per_min = dialogue_lines / (duration / 60) if duration else 0.0
    if forced:
        verdict = "signs-songs"
    elif per_min >= FULL_PER_MIN:
        verdict = "full"
    elif per_min < SIGNS_PER_MIN or SIGN_TITLE.search(title or ""):
        verdict = "signs-songs"
    else:
        verdict = "unclear"
    return verdict, per_min


def is_english(lang, title):
    return (lang or "").lower() in ENGLISH_LANGS or bool(re.search(r"\benglish\b|\beng\b", title or "", re.I))


def text_looks_english(events):
    """True when dialogue is almost all ASCII letters and common English words make up a sixth of it."""
    text = " ".join(e["text"] for e in events if is_dialogue(e)).lower()
    letters = [c for c in text if c.isalpha()]
    words = re.findall(r"[a-z']+", text)
    if not letters or not words or sum(c.isascii() for c in letters) / len(letters) < 0.95:
        return False
    return sum(w in ENGLISH_WORDS for w in words) / len(words) >= 1 / 6


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
    """Embedded subtitle streams and sidecar subtitle files, each as a dict describing the track."""
    tracks = []
    for s in info["streams"]:
        if s.get("codec_type") != "subtitle":
            continue
        tags = s.get("tags", {})
        tracks.append({"kind": "embedded", "index": s["index"], "codec": s.get("codec_name", "?"),
                       "lang": tags.get("language", ""), "title": tags.get("title", ""),
                       "forced": bool(s.get("disposition", {}).get("forced")),
                       "default": bool(s.get("disposition", {}).get("default")),
                       "frames": next((int(v) for k, v in tags.items() if k.upper().startswith("NUMBER_OF_FRAMES")), None)})
    for path in sorted(video.parent.iterdir()):
        if is_sidecar(path, video):
            tracks.append({"kind": "file", "path": path, "codec": path.suffix[1:].lower(),
                           "lang": sidecar_lang(path, video), "title": path.name, "forced": False, "default": False,
                           "frames": None})
    return tracks


def describe(track):
    where = f"stream {track['index']}" if track["kind"] == "embedded" else f"file {track['path'].name}"
    flags = "".join(f" [{f}]" for f in ("default", "forced") if track[f])
    title = f" \"{track['title']}\"" if track["title"] and track["kind"] == "embedded" else ""
    sniffed = " (untagged, text reads as English)" if track.get("sniffed") else ""
    return f"{where}: {track['codec']} lang={track['lang'] or '?'}{sniffed}{title}{flags}"


def extract_text_tracks(ffmpeg, video, tracks, work):
    """Write every text track to work/<n>.ass in one pass over the video, and set track['ass'] to it."""
    cmd = [ffmpeg, "-v", "error", "-y", "-i", str(video)]
    for n, t in enumerate(tracks):
        t["ass"] = work / f"{n}.ass"
        if t["kind"] == "file":
            if t["codec"] in ("ass", "ssa"):
                shutil.copyfile(t["path"], t["ass"])
            else:
                run([ffmpeg, "-v", "error", "-y", "-i", str(t["path"]), "-c:s", "ass", str(t["ass"])])
        else:
            cmd += ["-map", f"0:{t['index']}", "-c:s", "copy" if t["codec"] in ("ass", "ssa") else "ass", str(t["ass"])]
    if len(cmd) > 6:
        run(cmd)
    for t in tracks:
        t["events"] = parse_ass(t["ass"].read_text(encoding="utf-8-sig", errors="replace"))


def image_events(ffprobe, video, index, offset):
    """Displays of an image track, each shown until the next display set, timed from the start of the file."""
    frames = json.loads(run([ffprobe, "-v", "error", "-select_streams", str(index), "-show_frames",
                             "-show_entries", "frame=pts_time,num_rects", "-of", "json", str(video)]))["frames"]
    times = [float(f["pts_time"]) - offset for f in frames]
    return [{"start": times[n], "end": times[n + 1] if n + 1 < len(times) else times[n] + 5,
             "style": "", "raw": "", "text": "(image)"}
            for n, f in enumerate(frames) if f.get("num_rects")]


def assess(ffmpeg, ffprobe, video, tracks, duration, offset, work):
    """Fill in verdict, dialogue_lines and per_min on each track. Image tracks with a muxer frame count are
    counted from it, halved because each display is followed by a clearing packet. Other image tracks are read
    only when tagged English, since an untagged or other-language image track can never be picked."""
    text = [t for t in tracks if t["codec"] in TEXT_CODECS]
    extract_text_tracks(ffmpeg, video, text, work)
    for t in tracks:
        if t["codec"] in IMAGE_CODECS and t["frames"] is None and not t["english"]:
            t["verdict"], t["per_min"], t["dialogue_lines"] = "not read", 0.0, 0
            t["note"] = "image subtitles not tagged English"
            continue
        if t["codec"] in IMAGE_CODECS:
            if t["frames"] is None:
                t["events"] = image_events(ffprobe, video, t["index"], offset)
            t["dialogue_lines"] = t["frames"] // 2 if t["frames"] is not None else len(t["events"])
            t["note"] = "image subtitles, styles unknown"
        elif t["codec"] in TEXT_CODECS:
            t["dialogue_lines"] = sum(map(is_dialogue, t["events"]))
            t["note"] = f"{len(t['events'])} events"
        else:
            t["verdict"], t["per_min"], t["dialogue_lines"] = "unsupported", 0.0, 0
            t["note"] = "codec cannot be burned in"
            continue
        t["verdict"], t["per_min"] = classify(t["dialogue_lines"], duration,
                                              t["title"] if t["kind"] == "embedded" else "", t["forced"])


def choose(tracks):
    """The English full-subtitle track with the default flag, then the most dialogue; None if there is none."""
    full = [t for t in tracks if t["english"] and t["verdict"] == "full"]
    return max(full, key=lambda t: (t["default"], t["dialogue_lines"]), default=None)


def clip_range(start, end, events, duration):
    """The asked-for range padded on both ends, with the start moved back before any dialogue line it would cut,
    repeatedly, since the moved start can land in the line before."""
    start, end = max(0.0, start - PADDING), min(duration, end + PADDING)
    while cut := [e["start"] for e in events if e["start"] < start < e["end"] and is_dialogue(e)]:
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


def render(ffmpeg, video, info, track, start, end, audio, out, work):
    duration = end - start
    cmd = [ffmpeg, "-v", "error", "-y", "-ss", f"{start:.3f}", "-i", str(video), "-t", f"{duration:.3f}"]
    if track["codec"] in IMAGE_CODECS:
        cmd += ["-filter_complex", f"[0:v:0][0:{track['index']}]overlay=eof_action=pass,format=yuv420p[v]", "-map", "[v]"]
    else:
        dump_fonts(ffmpeg, video, info, work / "fonts")
        shutil.copyfile(track["ass"], work / "clip.ass")
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
    a = ap.parse_args()

    video = a.video.resolve()
    if not video.is_file():
        sys.exit(f"no such file: {video}")
    if not a.list and (a.start is None or a.end is None):
        ap.error("start and end are required unless --list is given")
    ffmpeg, ffprobe = find_tools()
    info = probe(ffprobe, video)
    duration = float(info["format"].get("duration") or 0)
    offset = float(info["format"].get("start_time") or 0)
    if not a.list:
        if a.end <= a.start:
            sys.exit(f"end {a.end:.3f}s is not after start {a.start:.3f}s")
        if a.end > duration + 0.5:
            sys.exit(f"end {a.end:.3f}s is past the end of the video ({duration:.3f}s)")

    tracks = candidate_tracks(info, video)
    if not tracks:
        sys.exit("the video has no subtitle streams and no subtitle files sit next to it")
    with tempfile.TemporaryDirectory(prefix="hardsub-clip-") as tmp:
        work = Path(tmp)
        for t in tracks:
            t["english"] = is_english(t["lang"], t["title"])
        assess(ffmpeg, ffprobe, video, tracks, duration, offset, work)
        for t in tracks:
            if not t["english"] and t["lang"].lower() in UNTAGGED_LANGS and "events" in t:
                t["english"] = t["sniffed"] = text_looks_english(t["events"])
        print(f"{video.name}  ({duration / 60:.1f} min)")
        for t in tracks:
            print(f"  {describe(t)}  -> {t['verdict']}, {t['dialogue_lines']} dialogue lines "
                  f"({t['per_min']:.1f}/min, {t['note']}){'' if t['english'] else '  [not English]'}")
        if a.list:
            return

        if a.track:
            track = next((t for t in tracks if str(t.get("index")) == a.track or
                          (t["kind"] == "file" and t["path"].name == a.track)), None)
            if not track:
                sys.exit(f"--track {a.track}: no such subtitle stream or file in the list above")
            if track["verdict"] == "unsupported":
                sys.exit(f"--track {a.track}: {track['codec']} subtitles cannot be burned in")
        else:
            track = choose(tracks)
            if not track:
                sys.exit("no English track has full dialogue (see the list above). Pass --track to use one anyway.")
        print(f"using {describe(track)}")
        if track["codec"] in IMAGE_CODECS and "events" not in track:
            track["events"] = image_events(ffprobe, video, track["index"], offset)

        start, end = clip_range(a.start, a.end, track.get("events", []), duration)
        print(f"clip runs {start:.2f}s to {end:.2f}s (asked for {a.start:.2f}s to {a.end:.2f}s)")
        if "events" in track:
            in_clip = [e for e in track["events"] if e["end"] > start and e["start"] < end and is_dialogue(e)]
            print(f"{len(in_clip)} dialogue lines fall in the clip:")
            for e in in_clip:
                print(f"  {e['start'] - start:6.2f}s  {e['text']}")
            if not in_clip:
                print("  (none: the clip will have no dialogue subtitles)")

        audio = pick_audio(info, a.audio)
        out = (a.output or DEFAULT_OUT_DIR / f"{video.stem} [{label_time(start)}-{label_time(end)}].mp4").resolve()
        out.parent.mkdir(parents=True, exist_ok=True)
        render(ffmpeg, video, info, track, start, end, audio, out, work)
        print(f"wrote {out}")


if __name__ == "__main__":
    main()
