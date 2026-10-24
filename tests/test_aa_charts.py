import json
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aa_charts as ch  # noqa: E402
import aa_common as common  # noqa: E402


def model(slug, name, score, lab="Anthropic"):
    return {"slug": slug, "name": name, "intelligenceIndex": score, "creator": {"name": lab}}


def make_page(models, chunk_size=89):
    flight = '30:["$","x",null,{"pickerModels":' + json.dumps(models) + "}]\n"
    chunks = [flight[i : i + chunk_size] for i in range(0, len(flight), chunk_size)]
    pushes = "".join(f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks)
    return "<html>" + pushes + "</html>"


MODELS = [
    model("opus", "Claude Opus 5.5 (Max, Default Fallback)", 57.6),
    model("opus-xhigh", "Claude Opus 5.5 (Xhigh, Default Fallback)", 56.04),
    model("astra-xhigh", "GPT-6 Astra (Xhigh)", 52.4, "OpenAI"),
    model("grok-xhigh", "Grok 4.7 (Xhigh)", 46.4, "SpaceXAI"),
    model("gem-xhigh", "Gemini 4 Pro (Xhigh)", 50.0, "Google"),
    model("opus-high", "Claude Opus 5.5 (High, Default Fallback)", 53.6),
    model("opus-medium", "Claude Opus 5.5 (Medium, Default Fallback)", 51.2),
    model("plain", "Kimi K3.1 Pro", 55.0, "Kimi"),  # no effort tag: not at any level
    model("bad", "Broken (Xhigh)", None),  # no score: skipped
    model("worse", "Boolean score (Xhigh)", True),  # a bool is not a score
]


class Picker(unittest.TestCase):
    def test_parses_picker_across_chunk_boundaries(self):
        models = ch.picker_models(make_page(MODELS, chunk_size=7))
        self.assertEqual(len(models), len(MODELS))

    def test_missing_picker_is_a_parse_error(self):
        with self.assertRaises(common.ParseError):
            ch.picker_models("<html></html>")
        with self.assertRaises(common.ParseError):
            ch.picker_models(make_page([]))


class ShortName(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ch.short_name("GPT-6 Astra (Xhigh)"), "GPT-6 Astra")
        self.assertEqual(ch.short_name("Claude Opus 5.5 (Max, Default Fallback)"), "Claude Opus 5.5 (fallback)")
        self.assertEqual(ch.short_name("GPT-6 Sol (Daybreak Blue, max)"), "GPT-6 Sol (Daybreak Blue)")
        self.assertEqual(ch.short_name("Qwen3.8 Max (0902)"), "Qwen3.8 Max (0902)")
        self.assertEqual(ch.short_name("Kimi K3.1 Pro"), "Kimi K3.1 Pro")


class Boards(unittest.TestCase):
    def test_top_models_per_grade_sorted_by_score(self):
        boards = ch.leaderboards(MODELS, grades=("max", "xhigh", "high"), n=3)
        self.assertEqual([e.slug for e in boards["xhigh"]], ["opus-xhigh", "astra-xhigh", "gem-xhigh"])
        self.assertEqual([e.slug for e in boards["max"]], ["opus"])
        self.assertEqual([e.slug for e in boards["high"]], ["opus-high"])

    def test_untagged_and_unscored_models_are_excluded(self):
        slugs = {e.slug for rows in ch.leaderboards(MODELS, grades=("max", "xhigh", "high", "medium")).values() for e in rows}
        self.assertNotIn("plain", slugs)
        self.assertNotIn("bad", slugs)
        self.assertNotIn("worse", slugs)

    def test_scores_are_rounded_to_one_decimal(self):
        self.assertEqual(ch.leaderboards(MODELS, grades=("xhigh",))["xhigh"][0].score, 56.0)

    def test_empty_grade_is_a_parse_error(self):
        with self.assertRaises(common.ParseError):
            ch.leaderboards(MODELS, grades=("low",))

    def test_lab_colors_follow_the_entity(self):
        by = {e.slug: e.lab_class for e in ch.leaderboards(MODELS, grades=("xhigh",))["xhigh"]}
        self.assertEqual(by["opus-xhigh"], "anthropic")
        self.assertEqual(by["astra-xhigh"], "openai")
        self.assertEqual(by["gem-xhigh"], "google")
        self.assertEqual(by["grok-xhigh"], "other")


class Svg(unittest.TestCase):
    def entries(self, grade="xhigh"):
        return ch.leaderboards(MODELS, grades=(grade,))[grade]

    def test_is_well_formed_and_has_one_column_per_model(self):
        rows = self.entries()
        root = ET.fromstring(ch.render_svg("xhigh", rows))
        ns = {"s": "http://www.w3.org/2000/svg"}
        self.assertEqual(len(root.findall(".//s:path", ns)), len(rows))
        self.assertEqual(len(root.findall(".//s:g", ns)), len(rows))
        ET.fromstring(ch.svg_file("xhigh", rows).encode())  # the file form parses too

    def test_names_are_escaped(self):
        rows = [ch.Entry("x", 'A&B <"x"> (Xhigh)', "A&B <x>", "Lab & Co", 40.0)]
        svg = ch.render_svg("xhigh", rows)
        ET.fromstring(svg)
        self.assertNotIn("A&B <x>", svg)

    def test_columns_are_capped_at_24px_with_square_baseline(self):
        root = ET.fromstring(ch.render_svg("xhigh", self.entries()))
        paths = root.findall(".//{http://www.w3.org/2000/svg}path")
        self.assertTrue(paths)
        pattern = re.compile(r"M([\d.]+),([\d.]+) V[\d.]+ Q[\d.]+,[\d.]+ [\d.]+,[\d.]+ H[\d.]+ Q[\d.]+,[\d.]+ ([\d.]+),[\d.]+ V([\d.]+) Z")
        for p in paths:
            m = pattern.fullmatch(p.get("d"))
            self.assertIsNotNone(m, p.get("d"))
            x0, base0, x1, base1 = (float(g) for g in m.groups())
            self.assertLessEqual(x1 - x0, ch.MAX_BAR + 0.1)
            self.assertEqual(base0, base1)  # both bottom corners sit on the baseline, unrounded

    def test_value_on_the_cap_and_only_present_labs_in_legend(self):
        svg = ch.render_svg("xhigh", self.entries())
        self.assertIn(">56.0<", svg)
        self.assertIn(">Anthropic<", svg)
        self.assertIn(">Other labs<", svg)
        svg_high = ch.render_svg("high", self.entries("high"))
        self.assertNotIn(">OpenAI<", svg_high)

    def test_long_labels_are_clipped_but_kept_in_the_tooltip(self):
        rows = [ch.Entry("x", "A" * 60 + " (Xhigh)", "A" * 60, "OpenAI", 40.0)]
        svg = ch.render_svg("xhigh", rows)
        self.assertIn("…", svg)
        self.assertIn("A" * 60 + " (Xhigh)", svg)  # full name stays in the <title> tooltip

    def test_scale_leaves_headroom(self):
        self.assertEqual(ch._scale(57.6), (20, 60))
        self.assertEqual(ch._scale(60.0), (20, 80))
        self.assertEqual(ch._scale(35.0), (10, 40))

    def test_no_dashed_lines_and_no_serif(self):
        svg = ch.render_svg("xhigh", self.entries())
        self.assertNotIn("dasharray", svg)
        self.assertNotIn("serif", svg.replace("sans-serif", ""))


class Output(unittest.TestCase):
    def boards(self):
        return ch.leaderboards(MODELS, grades=("max", "xhigh", "high", "medium"))

    def test_writes_every_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertTrue(ch.write_charts(self.boards(), out))
            for rel in ("summary.html", "charts.json", "charts/max.svg", "charts/xhigh.svg", "charts/high.svg", "charts/medium.svg"):
                self.assertTrue((out / rel).exists(), rel)
            page = (out / "summary.html").read_text()
            self.assertIn("<table>", page)  # a table view twin for every chart
            self.assertEqual(page.count("<details>"), 4)
            self.assertIn('href="high/#info"', page)

    def test_idempotent_and_keeps_changed_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertTrue(ch.write_charts(self.boards(), out, now=datetime(2030, 1, 1, tzinfo=timezone.utc)))
            self.assertFalse(ch.write_charts(self.boards(), out, now=datetime(2031, 1, 1, tzinfo=timezone.utc)))
            self.assertEqual(json.loads((out / "charts.json").read_text())["changed_at"], "2030-01-01 00:00 UTC")

    def test_changed_scores_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ch.write_charts(self.boards(), out, now=datetime(2030, 1, 1, tzinfo=timezone.utc))
            changed_models = [dict(m) for m in MODELS]
            changed_models[1]["intelligenceIndex"] = 55.0
            boards = ch.leaderboards(changed_models, grades=("max", "xhigh", "high", "medium"))
            self.assertTrue(ch.write_charts(boards, out, now=datetime(2031, 1, 1, tzinfo=timezone.utc)))
            self.assertEqual(json.loads((out / "charts.json").read_text())["changed_at"], "2031-01-01 00:00 UTC")


@unittest.skipUnless(os.environ.get("AA_LIVE"), "set AA_LIVE=1 to hit the real site")
class Live(unittest.TestCase):
    def test_real_picker(self):
        boards = ch.leaderboards(ch.picker_models(common.fetch(ch.PICKER_URL)))
        for grade in ch.GRADES:
            self.assertGreater(len(boards[grade]), 3)
            ET.fromstring(ch.render_svg(grade, boards[grade]))


if __name__ == "__main__":
    unittest.main()
