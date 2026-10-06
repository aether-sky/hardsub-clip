"""End to end on a generated episode: the full English track beats a default signs-and-songs track, and its
lines are burned in at the right moments of the clip."""
import os, subprocess, sys, tempfile, unittest
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


def ts(seconds):
    return f"0:{int(seconds) // 60:02d}:{seconds % 60:05.2f}"


class RenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ffmpeg, _ = hardsub_clip.find_tools()
        cls.dir = tempfile.TemporaryDirectory()
        d = Path(cls.dir.name)
        # Full track: a line during the first three seconds of every six.
        (d / "full.ass").write_text(HEADER.format(style="Default") + "".join(
            f"Dialogue: 0,{ts(k * 6)},{ts(k * 6 + 3)},Default,,0,0,0,,Line number {k}\n" for k in range(20)))
        (d / "signs.ass").write_text(HEADER.format(style="Signs") + "".join(
            f"Dialogue: 0,{ts(k * 40)},{ts(k * 40 + 2)},Signs,,0,0,0,,{{\\pos(320,40)}}SIGN {k}\n" for k in range(3)))
        cls.video = d / "Episode 01.mkv"
        subprocess.run([cls.ffmpeg, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x202060:s=640x360:r=24:d=120",
                        "-f", "lavfi", "-i", "sine=f=440:d=120", "-i", str(d / "signs.ass"), "-i", str(d / "full.ass"),
                        "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-c:v", "libx264", "-preset", "ultrafast",
                        "-c:a", "aac", "-c:s", "copy",
                        "-metadata:s:a:0", "language=jpn",
                        "-metadata:s:s:0", "language=eng", "-metadata:s:s:0", "title=Signs & Songs",
                        "-disposition:s:0", "default",
                        "-metadata:s:s:1", "language=eng", "-metadata:s:s:1", "title=English", "-disposition:s:1", "0",
                        "-attach", r"C:\Windows\Fonts\arial.ttf", "-metadata:s:t", "mimetype=application/x-truetype-font",
                        str(cls.video)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.dir.cleanup()

    def run_clip(self, *args):
        result = subprocess.run([sys.executable, str(SCRIPT), str(self.video), *args], capture_output=True, text=True,
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
        report = self.run_clip("--list")
        self.assertRegex(report, r"Signs & Songs.*-> signs-songs")
        self.assertRegex(report, r"\"English\".*-> full")

    def test_attached_font_named_by_absolute_path_is_not_written_there(self):
        # The fixture's font attachment is named C:\Windows\Fonts\arial.ttf; dumping by that name would overwrite it.
        clip = Path(self.dir.name) / "font-clip.mp4"
        self.run_clip("0:00", "0:02", "-o", str(clip))
        self.assertTrue(clip.is_file())

    def test_full_track_lines_land_at_their_clip_times(self):
        clip = Path(self.dir.name) / "clip.mp4"
        report = self.run_clip("0:33", "0:45", "-o", str(clip))
        self.assertIn("\"English\"", report.split("using", 1)[1].splitlines()[0])
        # Clip 4.5 s is episode 37.5 s, inside line 6; clip 1.5 s is episode 34.5 s, between lines.
        self.assertGreater(self.bright_pixels(clip, 4.5), 200)
        self.assertEqual(self.bright_pixels(clip, 1.5), 0)


if __name__ == "__main__":
    unittest.main()
