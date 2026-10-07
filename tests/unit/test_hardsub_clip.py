"""Timestamp parsing, dialogue detection, signs-and-songs classification and track choice."""
import os, sys, unittest
from argparse import ArgumentTypeError
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "scripts"))
import hardsub_clip
from hardsub_clip import Line, Track

HEADER = "[Script Info]\nScriptType: v4.00+\n\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"


def line(style, raw, start=0.0, end=1.0):
    return Line(start, end, hardsub_clip.OVERRIDE.sub("", raw).strip(), style, raw)


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


class DurationTests(unittest.TestCase):
    def test_container_duration_wins(self):
        info = {"format": {"duration": "1440.5"}, "streams": [{"duration": "1500"}]}
        self.assertEqual(hardsub_clip.media_duration(info), 1440.5)

    def test_missing_container_duration_falls_back_to_stream_tags(self):
        info = {"format": {"duration": "0.000000"},
                "streams": [{"tags": {"DURATION": "00:23:40.500000000"}}, {"tags": {"DURATION-eng": "00:23:39.000000000"}}]}
        self.assertEqual(hardsub_clip.media_duration(info), 1420.5)

    def test_no_known_duration_stops_the_script(self):
        with self.assertRaises(SystemExit):
            hardsub_clip.media_duration({"format": {}, "streams": [{"codec_type": "video"}]})


class AssTests(unittest.TestCase):
    def test_text_with_commas_stays_whole(self):
        lines = hardsub_clip.parse_ass(HEADER + "Dialogue: 0,0:01:02.50,0:01:04.00,Default,,0,0,0,,{\\i1}Wait, what?\\NNo.\n")
        self.assertEqual(lines[0].start, 62.5)
        self.assertEqual(lines[0].text, "Wait, what? No.")

    def test_signs_songs_and_karaoke_are_not_dialogue(self):
        self.assertFalse(hardsub_clip.is_dialogue(line("Signs", "Shop")))
        self.assertFalse(hardsub_clip.is_dialogue(line("OP_Eng", "la la")))
        self.assertFalse(hardsub_clip.is_dialogue(line("Default", "{\\pos(320,40)}Station")))
        self.assertFalse(hardsub_clip.is_dialogue(line("Default", "{\\k20}la{\\k30}la")))
        self.assertTrue(hardsub_clip.is_dialogue(line("Default - Top", "{\\an8}Over here!")))
        self.assertTrue(hardsub_clip.is_dialogue(line("Italics", "{\\fad(200,0)}I remember.")))
        self.assertTrue(hardsub_clip.is_dialogue(Line(0, 1, image=True)))

    def test_track_rate_decides_full_or_signs_songs(self):
        self.assertEqual(hardsub_clip.classify(300, 24 * 60)[0], "full")
        self.assertEqual(hardsub_clip.classify(10, 24 * 60)[0], "signs-songs")
        self.assertEqual(hardsub_clip.classify(40, 24 * 60, "Signs & Songs")[0], "signs-songs")
        self.assertEqual(hardsub_clip.classify(40, 24 * 60)[0], "unclear")

    def test_forced_track_is_signs_songs_whatever_its_rate(self):
        self.assertEqual(hardsub_clip.classify(300, 24 * 60, forced=True)[0], "signs-songs")


class ClipRangeTests(unittest.TestCase):
    LINES = [line("Default", "Hello there.", 10.0, 13.0), line("Signs", "SHOP", 20.0, 22.0)]

    def test_both_ends_get_padding(self):
        self.assertEqual(hardsub_clip.clip_range(15, 18, self.LINES, 100), (14.5, 18.5))

    def test_start_inside_a_line_moves_before_it(self):
        self.assertEqual(hardsub_clip.clip_range(12, 18, self.LINES, 100), (9.5, 18.5))

    def test_start_inside_a_sign_is_not_moved(self):
        self.assertEqual(hardsub_clip.clip_range(21, 25, self.LINES, 100), (20.5, 25.5))

    def test_padding_stops_at_the_video_edges(self):
        self.assertEqual(hardsub_clip.clip_range(0.2, 99.8, [], 100), (0.0, 100))

    def test_moved_start_inside_the_previous_line_moves_again(self):
        lines = [line("Default", "One.", 8.0, 9.8)] + self.LINES
        self.assertEqual(hardsub_clip.clip_range(12, 18, lines, 100), (7.5, 18.5))


class TrackTests(unittest.TestCase):
    def test_sidecar_of_a_longer_episode_number_is_not_matched(self):
        video = Path("Show - 1.mkv")
        self.assertTrue(hardsub_clip.is_sidecar(Path("Show - 1.en.ass"), video))
        self.assertTrue(hardsub_clip.is_sidecar(Path("Show - 1 [English].srt"), video))
        self.assertFalse(hardsub_clip.is_sidecar(Path("Show - 12.en.ass"), video))
        self.assertFalse(hardsub_clip.is_sidecar(Path("Show - 1.mkv"), video))

    def test_sidecar_language_from_name(self):
        video = Path("Show - 01.mkv")
        self.assertEqual(hardsub_clip.sidecar_lang(Path("Show - 01.en.ass"), video), "eng")
        self.assertEqual(hardsub_clip.sidecar_lang(Path("Show - 01 [English].srt"), video), "eng")
        self.assertEqual(hardsub_clip.sidecar_lang(Path("Show - 01.ja.ass"), video), "ja")

    def test_untagged_track_language_read_from_its_text(self):
        english = [line("Default", "What do you think you're doing?"), line("Default", "I told you it was mine.")]
        spanish = [line("Default", "¿Qué estás haciendo aquí?"), line("Default", "Te dije que era mío.")]
        japanese = [line("Default", "何をしているの？"), line("Default", "それは私のだよ。")]
        self.assertTrue(hardsub_clip.text_looks_english(english))
        self.assertFalse(hardsub_clip.text_looks_english(spanish))
        self.assertFalse(hardsub_clip.text_looks_english(japanese))

    def test_tagged_language_is_not_overridden_by_the_text(self):
        track = Track("ass", "spa", "", index=2, lines=[line("Default", "What do you think you're doing?")])
        hardsub_clip.decide_language(track)
        self.assertFalse(track.english)

    def test_unsupported_codec_is_never_picked(self):
        caption = Track("eia_608", "eng", "", index=3, default=True, english=True)
        hardsub_clip.assess(caption, 24 * 60)
        self.assertEqual(caption.verdict, "unsupported")
        self.assertIsNone(hardsub_clip.choose([caption]))

    def test_unread_image_track_without_a_frame_count_is_not_read(self):
        pgs = Track("hdmv_pgs_subtitle", "spa", "", index=4)
        hardsub_clip.assess(pgs, 24 * 60)
        self.assertEqual(pgs.verdict, "not read")

    def test_image_track_is_counted_from_its_frame_count(self):
        pgs = Track("hdmv_pgs_subtitle", "eng", "", index=4, frames=800, english=True)
        hardsub_clip.assess(pgs, 24 * 60)
        self.assertEqual((pgs.dialogue_lines, pgs.verdict), (400, "full"))

    def test_default_flag_on_signs_track_does_not_win(self):
        signs = Track("ass", "eng", "", index=2, english=True, verdict="signs-songs", default=True, dialogue_lines=5)
        full = Track("ass", "eng", "", index=3, english=True, verdict="full", dialogue_lines=300)
        self.assertIs(hardsub_clip.choose([signs, full]), full)

    def test_no_choice_without_english_full_track(self):
        japanese = Track("ass", "jpn", "", index=2, verdict="full", default=True, dialogue_lines=300)
        self.assertIsNone(hardsub_clip.choose([japanese]))

    def test_track_option_matches_stream_index_or_file_name_only(self):
        embedded = Track("ass", "eng", "", index=2)
        sidecar = Track("ass", "eng", "Show.en.ass", path=Path("Show.en.ass"))
        tracks = [sidecar, embedded]
        self.assertIs(hardsub_clip.find_track(tracks, "2"), embedded)
        self.assertIs(hardsub_clip.find_track(tracks, "Show.en.ass"), sidecar)
        self.assertIsNone(hardsub_clip.find_track(tracks, "None"))


if __name__ == "__main__":
    unittest.main()
