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

SVG_NS = {"s": "http://www.w3.org/2000/svg"}


def model(slug, name, score, lab="Anthropic", release=None):
    return {"slug": slug, "name": name, "intelligenceIndex": score, "creator": {"name": lab}, "_release": release or slug}


def make_page(models, chunk_size=89):
    clean = [{k: v for k, v in m.items() if k != "_release"} for m in models]
    flight = '30:["$","x",null,{"pickerModels":' + json.dumps(clean) + "}]\n"
    chunks = [flight[i : i + chunk_size] for i in range(0, len(flight), chunk_size)]
    pushes = "".join(f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks)
    return "<html>" + pushes + "</html>"


MODELS = [
    # Claude Opus 5.5: every level
    model("opus", "Claude Opus 5.5 (Max, Default Fallback)", 57.6, release="opus"),
    model("opus-xhigh", "Claude Opus 5.5 (Xhigh, Default Fallback)", 56.04, release="opus"),
    model("opus-high", "Claude Opus 5.5 (High, Default Fallback)", 53.6, release="opus"),
    model("opus-medium", "Claude Opus 5.5 (Medium, Default Fallback)", 51.2, release="opus"),
    # GPT-6 Astra: xhigh and high only
    model("astra-xhigh", "GPT-6 Astra (Xhigh)", 52.4, "OpenAI", release="astra"),
    model("astra-high", "GPT-6 Astra (High)", 50.9, "OpenAI", release="astra"),
    # Gemini 4 Argon only has High, so High is the best it can do
    model("gem-high", "Gemini 4 Argon (High)", 52.6, "Google", release="gem"),
    # Grok 4.7: no Max and no Medium
    model("grok-xhigh", "Grok 4.7 (Xhigh)", 46.4, "SpaceXAI", release="grok"),
    model("grok-high", "Grok 4.7 (High)", 46.3, "SpaceXAI", release="grok"),
    model("grok-low", "Grok 4.7 (Low)", 42.2, "SpaceXAI", release="grok"),
    # GLM-5.3: Max and Low only
    model("glm", "GLM-5.3 (Max)", 44.8, "Z AI", release="glm"),
    model("glm-low", "GLM-5.3 (Low)", 30.0, "Z AI", release="glm"),
    # No effort label at all
    model("kimi", "Kimi K3.1 Pro", 55.0, "Kimi", release="kimi"),
    # A labelled release with a non-reasoning sibling that must be ignored
    model("luna", "GPT-6 Luna (Max)", 38.1, "OpenAI", release="luna"),
    model("luna-nr", "GPT-6 Luna (Non-reasoning)", 60.0, "OpenAI", release="luna"),
    # Unlabelled and non-reasoning: nowhere
    model("old-nr", "Old Model (Non-reasoning)", 59.0, "Other", release="old"),
    # Unusable scores
    model("bad", "Broken (Xhigh)", None, release="bad"),
    model("worse", "Boolean score (Xhigh)", True, release="worse"),
]
VARIANTS = [{"slug": m["slug"], "name": m["name"], "releaseSlug": m["_release"]} for m in MODELS]


def slugs(boards, grade):
    return [e.slug for e in boards[grade]]


class Picker(unittest.TestCase):
    def test_parses_picker_across_chunk_boundaries(self):
        self.assertEqual(len(ch.picker_models(make_page(MODELS, chunk_size=7))), len(MODELS))

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
    def setUp(self):
        self.boards = ch.leaderboards(MODELS, VARIANTS, n=20)

    def test_exact_variant_wins_on_its_own_chart(self):
        self.assertIn("opus", slugs(self.boards, "max"))
        self.assertIn("opus-xhigh", slugs(self.boards, "xhigh"))
        self.assertIn("opus-high", slugs(self.boards, "high"))
        self.assertIn("opus-medium", slugs(self.boards, "medium"))
        for grade in ch.GRADES:  # one entry per model, never two variants of the same release
            self.assertEqual(sum(s.startswith("opus") for s in slugs(self.boards, grade)), 1)

    def test_a_model_below_the_level_shows_its_best_setting(self):
        # Gemini only has High: it is the best it can do, so it is on max, xhigh and high but not medium.
        for grade in ("max", "xhigh", "high"):
            self.assertIn("gem-high", slugs(self.boards, grade), grade)
        self.assertNotIn("gem-high", slugs(self.boards, "medium"))

    def test_highest_setting_below_the_level(self):
        self.assertIn("grok-xhigh", slugs(self.boards, "max"))  # no Max, so Xhigh
        self.assertIn("grok-high", slugs(self.boards, "high"))
        self.assertIn("grok-low", slugs(self.boards, "medium"))  # no Medium, so Low
        self.assertIn("glm-low", slugs(self.boards, "xhigh"))  # Max is above the level, so Low
        self.assertIn("glm", slugs(self.boards, "max"))

    def test_a_model_whose_every_setting_is_above_the_level_is_left_off(self):
        only_max = [model("m", "Only Max Model (Max)", 50.0), model("o", "Other (Xhigh)", 40.0)]
        variants = [{"slug": m["slug"], "name": m["name"], "releaseSlug": m["slug"]} for m in only_max]
        boards = ch.leaderboards(only_max, variants, grades=("max", "xhigh"))
        self.assertEqual([e.slug for e in boards["max"]], ["m", "o"])
        self.assertEqual([e.slug for e in boards["xhigh"]], ["o"])

    def test_unlabelled_models_appear_only_on_the_max_chart(self):
        self.assertIn("kimi", slugs(self.boards, "max"))
        for grade in ("xhigh", "high", "medium"):
            self.assertNotIn("kimi", slugs(self.boards, grade))
        self.assertIsNone(next(e for e in self.boards["max"] if e.slug == "kimi").effort)

    def test_non_reasoning_variants_are_ignored(self):
        everything = {s for g in ch.GRADES for s in slugs(self.boards, g)}
        self.assertNotIn("luna-nr", everything)  # sibling of a labelled release
        self.assertNotIn("old-nr", everything)  # unlabelled and non-reasoning

    def test_unscored_models_are_excluded(self):
        everything = {s for g in ch.GRADES for s in slugs(self.boards, g)}
        self.assertFalse({"bad", "worse"} & everything)

    def test_sorted_by_score_and_truncated(self):
        top = ch.leaderboards(MODELS, VARIANTS, grades=("max",), n=3)["max"]
        self.assertEqual([e.slug for e in top], ["opus", "kimi", "gem-high"])
        self.assertEqual([e.score for e in top], sorted((e.score for e in top), reverse=True))

    def test_scores_are_rounded_to_one_decimal(self):
        self.assertEqual(next(e for e in self.boards["xhigh"] if e.slug == "opus-xhigh").score, 56.0)

    def test_empty_chart_is_a_parse_error(self):
        unlabelled = [model("k", "Kimi", 50.0)]
        with self.assertRaises(common.ParseError):
            ch.leaderboards(unlabelled, [{"slug": "k", "name": "Kimi", "releaseSlug": "k"}], grades=("xhigh",))

    def test_models_missing_from_the_variant_list_stand_alone(self):
        boards = ch.leaderboards(MODELS, [], grades=("max",), n=30)
        self.assertEqual(len([e for e in boards["max"] if e.slug.startswith("opus")]), 4)  # no grouping without releases

    def test_lab_colors_follow_the_entity(self):
        by = {e.slug: e.lab_class for e in self.boards["max"]}
        self.assertEqual(by["opus"], "anthropic")
        self.assertEqual(by["luna"], "openai")
        self.assertEqual(by["gem-high"], "google")
        self.assertEqual(by["kimi"], "other")


class Labels(unittest.TestCase):
    def entry(self, name, effort, label=None):
        return ch.Entry("x", name, label or ch.short_name(name), "Lab", 50.0, effort)

    def test_at_the_level_is_plain(self):
        e = self.entry("GPT-6 Astra (Xhigh)", "xhigh")
        self.assertEqual(ch.axis_label(e, "xhigh"), "GPT-6 Astra")

    def test_below_the_level_names_its_setting(self):
        self.assertEqual(ch.axis_label(self.entry("Gemini 4 Argon (High)", "high"), "max"), "Gemini 4 Argon (high)")
        e = self.entry("Claude Opus 5.5 (High, Default Fallback)", "high")
        self.assertEqual(ch.axis_label(e, "max"), "Claude Opus 5.5 (fallback, high)")

    def test_unlabelled_gets_a_dagger(self):
        self.assertEqual(ch.axis_label(self.entry("Kimi K3.1 Pro", None), "max"), "Kimi K3.1 Pro †")

    def test_long_names_are_clipped_but_keep_their_marker(self):
        long = "A" * 60
        for effort, grade, tail in ((None, "max", " †"), ("high", "max", " (high)")):
            label = ch.axis_label(self.entry(f"{long} (High)", effort, label=long), grade)
            self.assertLessEqual(len(label), ch.LABEL_CHARS)
            self.assertTrue(label.endswith(tail), label)
            self.assertIn("…", label)

    def test_footnote(self):
        at = self.entry("GPT-6 Astra (Xhigh)", "xhigh")
        below = self.entry("Gemini 4 Argon (High)", "high")
        none = self.entry("Kimi K3.1 Pro", None)
        self.assertEqual(ch.footnote("xhigh", [at]), "")
        self.assertIn("highest setting below max", ch.footnote("max", [at, below]))
        self.assertIn("†", ch.footnote("max", [none]))
        self.assertIn(" · ", ch.footnote("max", [below, none]))


class Svg(unittest.TestCase):
    def setUp(self):
        self.boards = ch.leaderboards(MODELS, VARIANTS, n=10)

    def test_is_well_formed_and_has_one_column_per_model(self):
        for grade, rows in self.boards.items():
            root = ET.fromstring(ch.render_svg(grade, rows))
            self.assertEqual(len(root.findall(".//s:path", SVG_NS)), len(rows), grade)
            self.assertEqual(len(root.findall(".//s:g", SVG_NS)), len(rows), grade)
            ET.fromstring(ch.svg_file(grade, rows).encode())  # the file form parses too

    def test_names_are_escaped(self):
        rows = [ch.Entry("x", 'A&B <"x"> (Xhigh)', "A&B <x>", "Lab & Co", 40.0, "xhigh")]
        svg = ch.render_svg("xhigh", rows)
        ET.fromstring(svg)
        self.assertNotIn("A&B <x>", svg)

    def test_columns_are_capped_at_24px_with_square_baseline(self):
        root = ET.fromstring(ch.render_svg("max", self.boards["max"]))
        pattern = re.compile(r"M([\d.]+),([\d.]+) V[\d.]+ Q[\d.]+,[\d.]+ [\d.]+,[\d.]+ H[\d.]+ Q[\d.]+,[\d.]+ ([\d.]+),[\d.]+ V([\d.]+) Z")
        paths = root.findall(".//s:path", SVG_NS)
        self.assertTrue(paths)
        for p in paths:
            m = pattern.fullmatch(p.get("d"))
            self.assertIsNotNone(m, p.get("d"))
            x0, base0, x1, base1 = (float(g) for g in m.groups())
            self.assertLessEqual(x1 - x0, ch.MAX_BAR + 0.1)
            self.assertEqual(base0, base1)

    def test_value_on_the_cap_and_only_present_labs_in_legend(self):
        svg = ch.render_svg("xhigh", self.boards["xhigh"])
        self.assertIn(">56.0<", svg)
        self.assertIn(">Anthropic<", svg)
        self.assertNotIn(">Google<", ch.render_svg("medium", self.boards["medium"]))

    def test_max_chart_labels_the_unlabelled_and_the_below_level_models(self):
        svg = ch.render_svg("max", self.boards["max"])
        self.assertIn("Kimi K3.1 Pro †", svg)
        self.assertIn("Gemini 4 Argon (high)", svg)
        self.assertIn("† effort not labelled", svg)

    def test_tooltip_keeps_the_full_name(self):
        rows = [ch.Entry("x", "A" * 60 + " (Xhigh)", "A" * 60, "OpenAI", 40.0, "xhigh")]
        svg = ch.render_svg("xhigh", rows)
        self.assertIn("…", svg)
        self.assertIn("A" * 60 + " (Xhigh)", svg)

    def test_scale_leaves_headroom(self):
        self.assertEqual(ch._scale(57.6), (20, 60))
        self.assertEqual(ch._scale(60.0), (20, 80))
        self.assertEqual(ch._scale(35.0), (10, 40))

    def test_always_a_white_card_with_no_dark_variant(self):
        svg = ch.render_svg("max", self.boards["max"])
        self.assertIn("--surface:#ffffff", svg)
        self.assertNotIn("prefers-color-scheme", svg)

    def test_no_dashed_lines_and_no_serif(self):
        svg = ch.render_svg("max", self.boards["max"])
        self.assertNotIn("dasharray", svg)
        self.assertNotIn("serif", svg.replace("sans-serif", ""))


class Output(unittest.TestCase):
    def boards(self):
        return ch.leaderboards(MODELS, VARIANTS)

    def test_writes_every_file_and_a_white_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertTrue(ch.write_charts(self.boards(), out))
            for rel in ("summary.html", "charts.json", "charts/max.svg", "charts/xhigh.svg", "charts/high.svg", "charts/medium.svg"):
                self.assertTrue((out / rel).exists(), rel)
            page = (out / "summary.html").read_text()
            self.assertIn("<table>", page)  # a table view twin for every chart
            self.assertEqual(page.count("<details>"), 4)
            self.assertIn("<th>Setting</th>", page)
            self.assertIn("not labelled", page)
            self.assertIn("color-scheme: light;", page)
            self.assertIn("background: #fff", page)
            self.assertNotIn("prefers-color-scheme", page)
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
            changed = [dict(m) for m in MODELS]
            changed[1]["intelligenceIndex"] = 55.0
            boards = ch.leaderboards(changed, VARIANTS)
            self.assertTrue(ch.write_charts(boards, out, now=datetime(2031, 1, 1, tzinfo=timezone.utc)))
            self.assertEqual(json.loads((out / "charts.json").read_text())["changed_at"], "2031-01-01 00:00 UTC")


@unittest.skipUnless(os.environ.get("AA_LIVE"), "set AA_LIVE=1 to hit the real site")
class Live(unittest.TestCase):
    def test_real_picker(self):
        from aa_xhigh import variant_index

        variants = variant_index(common.flight_payload(common.fetch()))
        boards = ch.leaderboards(ch.picker_models(common.fetch(ch.PICKER_URL)), variants)
        for grade in ch.GRADES:
            self.assertGreater(len(boards[grade]), 3)
            ET.fromstring(ch.render_svg(grade, boards[grade]))


if __name__ == "__main__":
    unittest.main()
