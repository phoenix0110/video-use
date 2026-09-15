import importlib.util
import tempfile
import unittest
from pathlib import Path


RENDER_PATH = Path(__file__).resolve().parents[1] / "helpers" / "render.py"
SPEC = importlib.util.spec_from_file_location("video_use_render", RENDER_PATH)
render = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(render)


class SubtitleTests(unittest.TestCase):
    def test_single_line_srt_splits_and_removes_overlap(self):
        source_text = """1
00:00:00,000 --> 00:00:04,000
这是一条非常长的中文字幕，需要被拆成多个不会换行的短句，而且不能丢失任何文字。

2
00:00:03,950 --> 00:00:06,000
第二条字幕
"""
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "master.srt"
            output = Path(tmp) / "master.single-line.srt"
            source.write_text(source_text, encoding="utf-8")
            render.prepare_single_line_srt(source, output, max_units=24)
            rendered = output.read_text(encoding="utf-8")
        self.assertNotIn("\n这是一条非常长的中文字幕，需要被拆成多个不会换行的短句，而且不能丢失任何文字。\n", rendered)
        self.assertIn("00:00:03,950", rendered)
        self.assertNotIn("\\N", rendered)

    def test_subtitle_style_has_no_text_sized_box(self):
        style = render._letterbox_sub_style(84, max_lines=1)
        self.assertIn("BorderStyle=1", style)
        self.assertIn("WrapStyle=2", style)
        self.assertNotIn("BorderStyle=3", style)

    def test_comma_clause_is_indivisible_and_prose_punctuation_is_removed(self):
        text = "落地第一件事不是看风景，是冲超市；一周食材一百二十五美元。"
        chunks = render._split_single_line_text(text, max_units=28)
        self.assertIn("落地第一件事不是看风景，", chunks)
        self.assertNotIn("。", "".join(chunks))
        self.assertNotIn("；", "".join(chunks))
        self.assertEqual(
            "".join(chunks),
            "落地第一件事不是看风景，是冲超市一周食材一百二十五美元",
        )


class AudioMixTests(unittest.TestCase):
    def test_configurable_source_levels(self):
        config = render._audio_mix_config({
            "source_under_narration": 0.08,
            "source_in_gaps": 0.30,
        })
        graph, _ = render._build_duck_filter("[0:a]", "[1:a]", [{
            "output_start": 1,
            "audio_duration": 2,
        }], config)
        self.assertIn("if(between(t,1.000,3.000),0.08,0.3)", graph)


class TimelineTests(unittest.TestCase):
    def test_fractional_ranges_quantize_without_total_drift(self):
        edl = {
            "ranges": [
                {"start": 0, "duration": 1.011},
                {"start": 2, "duration": 1.011},
                {"start": 4, "duration": 0.978},
            ],
            "total_duration_s": 3.0,
        }
        durations = render._render_durations(edl, fps=24)
        self.assertAlmostEqual(sum(durations), 3.0)
        self.assertTrue(all(round(duration * 24) == duration * 24 for duration in durations))


if __name__ == "__main__":
    unittest.main()
