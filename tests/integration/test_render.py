"""End to end on generated episodes: the full English track beats a default signs-and-songs track, and its
lines are burned in at the right moments of the clip, for ASS and PGS tracks and for files that do not start at 0."""
import os, struct, subprocess, sys, tempfile, unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "hardsub_clip.py"
sys.path.insert(0, str(SCRIPT.parent))
import hardsub_clip

HEADER = ("[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n\n[V4+ Styles]\n"
          "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
          "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
          "MarginR, MarginV, Encoding\n"
          "Style: {style},Arial,40,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,0,2,10,10,20,1\n\n"
          "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
BACKGROUND = ["-f", "lavfi", "-i", "color=c=0x202060:s=640x360:r=24:d=120"]


def ts(seconds):
    return f"0:{int(seconds) // 60:02d}:{seconds % 60:05.2f}"


def pgs_segment(kind, pts, payload):
    ticks = round(pts * 90000)
    return b"PG" + struct.pack(">IIBH", ticks, ticks, kind, len(payload)) + payload


def pgs_display_set(pts, number, box):
    """A 640x360 PGS display set showing a white box (x, y, w, h), or clearing the screen when box is None."""
    if box is None:
        pcs = struct.pack(">HHBHBBBB", 640, 360, 0x10, number, 0x00, 0, 0, 0)
        return b"".join(pgs_segment(k, pts, p) for k, p in
                        ((0x16, pcs), (0x17, struct.pack(">BBHHHH", 1, 0, 0, 0, 1, 1)), (0x80, b"")))
    x, y, w, h = box
    pcs = struct.pack(">HHBHBBBB", 640, 360, 0x10, number, 0x80, 0, 0, 1) + struct.pack(">HBBHH", 0, 0, 0, x, y)
    wds = struct.pack(">BBHHHH", 1, 0, x, y, w, h)
    pds = struct.pack(">BBBBBBB", 0, 0, 1, 235, 128, 128, 255)
    rle = bytes([0, 0xC0 | (w >> 8), w & 0xFF, 1, 0, 0]) * h
    ods = struct.pack(">HBB", 0, 0, 0xC0) + struct.pack(">I", len(rle) + 4)[1:] + struct.pack(">HH", w, h) + rle
    return b"".join(pgs_segment(k, pts, p) for k, p in ((0x16, pcs), (0x17, wds), (0x14, pds), (0x15, ods), (0x80, b"")))


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg, _ = hardsub_clip.find_tools()
        cls.dir = tempfile.TemporaryDirectory()
        d = Path(cls.dir.name)
        # Every subtitle track below shows a line during the first three seconds of every six.
        (d / "full.ass").write_text(HEADER.format(style="Default") + "".join(
            f"Dialogue: 0,{ts(k * 6)},{ts(k * 6 + 3)},Default,,0,0,0,,Line number {k}\n" for k in range(20)))
        (d / "signs.ass").write_text(HEADER.format(style="Signs") + "".join(
            f"Dialogue: 0,{ts(k * 40)},{ts(k * 40 + 2)},Signs,,0,0,0,,{{\\pos(320,40)}}SIGN {k}\n" for k in range(3)))
        (d / "boxes.sup").write_bytes(b"".join(
            pgs_display_set(k * 6, 2 * k, (120, 300, 400, 40)) + pgs_display_set(k * 6 + 3, 2 * k + 1, None)
            for k in range(20)))
        encode = ["-c:v", "libx264", "-preset", "ultrafast", "-c:s", "copy"]

        cls.video = d / "Episode 01.mkv"
        cls.make([*BACKGROUND, "-f", "lavfi", "-i", "sine=f=440:d=120", "-i", str(d / "signs.ass"),
                  "-i", str(d / "full.ass"), "-map", "0", "-map", "1", "-map", "2", "-map", "3", *encode, "-c:a", "aac",
                  "-metadata:s:a:0", "language=jpn",
                  "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=Signs & Songs",
                  "-disposition:s:0", "default",
                  "-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "title=English", "-disposition:s:1", "0",
                  "-attach", r"C:\Windows\Fonts\arial.ttf", "-metadata:s:t", "mimetype=application/x-truetype-font",
                  str(cls.video)])
        cls.pgs_video = d / "Episode 02.mkv"
        cls.make([*BACKGROUND, "-i", str(d / "boxes.sup"), "-map", "0", "-map", "1", *encode,
                  "-metadata:s:s:0", "language=eng", str(cls.pgs_video)])
        cls.late_video = d / "Episode 03.mkv"
        cls.make([*BACKGROUND, "-i", str(d / "full.ass"), "-map", "0", "-map", "1", *encode,
                  "-metadata:s:s:0", "language=eng", "-output_ts_offset", "10", str(cls.late_video)])

    @classmethod
    def make(cls, args):
        subprocess.run([cls.ffmpeg, "-v", "error", "-y", *args], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def run_clip(self, video, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), str(video), *args], capture_output=True, text=True,
                                encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def bright_pixels(self, clip, at):
        """Near-white pixels in the bottom third of the frame at `at` seconds into the clip."""
        raw = subprocess.run([self.ffmpeg, "-v", "error", "-ss", str(at), "-i", str(clip), "-frames:v", "1",
                              "-vf", "crop=iw:ih/3:0:ih*2/3,format=gray", "-f", "rawvideo", "-"],
                             capture_output=True, check=True).stdout
        return sum(b > 200 for b in raw)

    def test_signs_track_is_recognised_even_when_default(self):
        report = self.run_clip(self.video, "--list")
        self.assertRegex(report, r"Signs & Songs.*-> signs-songs")
        self.assertRegex(report, r"\"English\".*-> full")

    def test_attached_font_named_by_absolute_path_is_not_written_there(self):
        # The fixture's font attachment is named C:\Windows\Fonts\arial.ttf; dumping by that name would overwrite it.
        clip = Path(self.dir.name) / "font-clip.mp4"
        self.run_clip(self.video, "0:00", "0:02", "-o", str(clip))
        self.assertTrue(clip.is_file())

    def test_full_track_lines_land_at_their_clip_times(self):
        clip = Path(self.dir.name) / "clip.mp4"
        report = self.run_clip(self.video, "0:34", "0:45", "-o", str(clip))
        self.assertIn("\"English\"", report.split("using", 1)[1].splitlines()[0])
        # Padding starts the clip at episode 33.5 s, so clip 4 s is 37.5 s, inside line 6, and clip 1 s is
        # 34.5 s, between lines.
        self.assertGreater(self.bright_pixels(clip, 4), 200)
        self.assertEqual(self.bright_pixels(clip, 1), 0)

    def test_lines_stay_in_place_when_the_file_does_not_start_at_zero(self):
        clip = Path(self.dir.name) / "late-clip.mp4"
        self.run_clip(self.late_video, "0:34", "0:45", "-o", str(clip))
        self.assertGreater(self.bright_pixels(clip, 4), 200)
        self.assertEqual(self.bright_pixels(clip, 1), 0)

    def test_pgs_line_cut_by_the_start_is_shown_from_its_beginning(self):
        clip = Path(self.dir.name) / "pgs-clip.mp4"
        self.run_clip(self.pgs_video, "0:31", "0:40", "-o", str(clip))
        # The padded start, 30.5 s, falls inside the 30-33 s box, so the clip starts at 29.5 s: clip 0.25 s is
        # 29.75 s, before the box appears, and clip 1 s is 30.5 s, inside it.
        self.assertEqual(self.bright_pixels(clip, 0.25), 0)
        self.assertGreater(self.bright_pixels(clip, 1), 200)


if __name__ == "__main__":
    unittest.main()
