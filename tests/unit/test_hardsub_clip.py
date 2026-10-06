"""Timestamp parsing, dialogue detection, signs-and-songs classification and track choice."""
import os, sys, unittest
from argparse import ArgumentTypeError
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import hardsub_clip

HEADER = "[Script Info]\nScriptType: v4.00+\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"


def event(style, raw):
    return {"style": style, "raw": raw, "text": hardsub_clip.OVERRIDE.sub("", raw).strip()}


class TimeTests(unittest.TestCase):
    def test_accepts_seconds_minutes_and_hours(self):
        self.assertEqual(hardsub_clip.parse_time("83"), 83)
        self.assertEqual(hardsub_clip.parse_time("1:23.5"), 83.5)
        self.assertEqual(hardsub_clip.parse_time("1:02:03"), 3723)

    def test_rejects_malformed_timestamps(self):
        for bad in ("1:2:3:4", "1m23s", "", "-5"):
            with self.assertRaises(ArgumentTypeError):
                hardsub_clip.parse_time(bad)

    def test_filename_labels_have_no_colons(self):
        self.assertEqual(hardsub_clip.label_time(754), "12m34s")
        self.assertEqual(hardsub_clip.label_time(3723.5), "1h02m03.5s")


class AssTests(unittest.TestCase):
    def test_text_with_commas_stays_whole(self):
        ev = hardsub_clip.parse_ass(HEADER + "Dialogue: 0,0:01:02.50,0:01:04.00,Default,,0,0,0,,{\\i1}Wait, what?\\NNo.\n")
        self.assertEqual(ev[0]["start"], 62.5)
        self.assertEqual(ev[0]["text"], "Wait, what? No.")

    def test_signs_songs_and_karaoke_are_not_dialogue(self):
        self.assertFalse(hardsub_clip.is_dialogue(event("Signs", "Shop")))
        self.assertFalse(hardsub_clip.is_dialogue(event("OP_Eng", "la la")))
        self.assertFalse(hardsub_clip.is_dialogue(event("Default", "{\\pos(320,40)}Station")))
        self.assertFalse(hardsub_clip.is_dialogue(event("Default", "{\\k20}la{\\k30}la")))
        self.assertTrue(hardsub_clip.is_dialogue(event("Default - Top", "{\\an8}Over here!")))
        self.assertTrue(hardsub_clip.is_dialogue(event("Italics", "{\\fad(200,0)}I remember.")))

    def test_track_rate_decides_full_or_signs_songs(self):
        self.assertEqual(hardsub_clip.classify(300, 24 * 60)[0], "full")
        self.assertEqual(hardsub_clip.classify(10, 24 * 60)[0], "signs-songs")
        self.assertEqual(hardsub_clip.classify(40, 24 * 60, "Signs & Songs")[0], "signs-songs")
        self.assertEqual(hardsub_clip.classify(40, 24 * 60)[0], "unclear")

    def test_forced_track_is_signs_songs_even_without_a_duration(self):
        self.assertEqual(hardsub_clip.classify(300, 24 * 60, forced=True)[0], "signs-songs")
        self.assertEqual(hardsub_clip.classify(5, 0, forced=True), ("signs-songs", 0.0))


class ClipRangeTests(unittest.TestCase):
    LINES = [{"start": 10.0, "end": 13.0, "style": "Default", "raw": "Hello there.", "text": "Hello there."},
             {"start": 20.0, "end": 22.0, "style": "Signs", "raw": "SHOP", "text": "SHOP"}]

    def test_both_ends_get_padding(self):
        self.assertEqual(hardsub_clip.clip_range(15, 18, self.LINES, 100), (14.5, 18.5))

    def test_start_inside_a_line_moves_before_it(self):
        self.assertEqual(hardsub_clip.clip_range(12, 18, self.LINES, 100), (9.5, 18.5))

    def test_start_inside_a_sign_is_not_moved(self):
        self.assertEqual(hardsub_clip.clip_range(21, 25, self.LINES, 100), (20.5, 25.5))

    def test_padding_stops_at_the_video_edges(self):
        self.assertEqual(hardsub_clip.clip_range(0.2, 99.8, [], 100), (0.0, 100))

    def test_moved_start_inside_the_previous_line_moves_again(self):
        lines = [{"start": 8.0, "end": 9.8, "style": "Default", "raw": "One.", "text": "One."}] + self.LINES
        self.assertEqual(hardsub_clip.clip_range(12, 18, lines, 100), (7.5, 18.5))


class TrackTests(unittest.TestCase):
    def test_sidecar_of_a_longer_episode_number_is_not_matched(self):
        video = Path("Show - 1.mkv")
        self.assertTrue(hardsub_clip.is_sidecar(Path("Show - 1.en.ass"), video))
        self.assertTrue(hardsub_clip.is_sidecar(Path("Show - 1 [English].srt"), video))
        self.assertFalse(hardsub_clip.is_sidecar(Path("Show - 12.en.ass"), video))
        self.assertFalse(hardsub_clip.is_sidecar(Path("Show - 1.mkv"), video))

    def test_unsupported_codec_is_listed_without_being_read(self):
        caption = {"kind": "embedded", "index": 3, "codec": "eia_608", "lang": "eng", "title": "", "forced": False,
                   "default": True, "frames": None, "english": True}
        hardsub_clip.assess(None, None, Path("x.mkv"), [caption], 24 * 60, 0.0, Path("."))
        self.assertEqual(caption["verdict"], "unsupported")
        self.assertIsNone(hardsub_clip.choose([caption]))

    def test_image_track_not_tagged_english_is_not_read(self):
        # ffprobe is None, so reading the track would fail.
        pgs = {"kind": "embedded", "index": 4, "codec": "hdmv_pgs_subtitle", "lang": "spa", "title": "",
               "forced": False, "default": False, "frames": None, "english": False}
        hardsub_clip.assess(None, None, Path("x.mkv"), [pgs], 24 * 60, 0.0, Path("."))
        self.assertEqual(pgs["verdict"], "not read")

    def test_sidecar_language_from_name(self):
        video = Path("Show - 01.mkv")
        self.assertEqual(hardsub_clip.sidecar_lang(Path("Show - 01.en.ass"), video), "eng")
        self.assertEqual(hardsub_clip.sidecar_lang(Path("Show - 01 [English].srt"), video), "eng")
        self.assertEqual(hardsub_clip.sidecar_lang(Path("Show - 01.ja.ass"), video), "ja")

    def test_untagged_track_language_read_from_its_text(self):
        english = [event("Default", "What do you think you're doing?"), event("Default", "I told you it was mine.")]
        spanish = [event("Default", "¿Qué estás haciendo aquí?"), event("Default", "Te dije que era mío.")]
        japanese = [event("Default", "何をしているの？"), event("Default", "それは私のだよ。")]
        self.assertTrue(hardsub_clip.text_looks_english(english))
        self.assertFalse(hardsub_clip.text_looks_english(spanish))
        self.assertFalse(hardsub_clip.text_looks_english(japanese))

    def test_default_flag_on_signs_track_does_not_win(self):
        signs = {"english": True, "verdict": "signs-songs", "default": True, "dialogue_lines": 5}
        full = {"english": True, "verdict": "full", "default": False, "dialogue_lines": 300}
        self.assertIs(hardsub_clip.choose([signs, full]), full)

    def test_no_choice_without_english_full_track(self):
        japanese = {"english": False, "verdict": "full", "default": True, "dialogue_lines": 300}
        self.assertIsNone(hardsub_clip.choose([japanese]))


if __name__ == "__main__":
    unittest.main()
