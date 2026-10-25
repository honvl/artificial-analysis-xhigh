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
COLORS = {
    "Anthropic": "#cc785c",
    "OpenAI": "#1f1f1f",
    "Google": "#34A853",
    "Meta": "#0089f4",
    "SpaceXAI": "#736cd3",
    "Kimi": "#4466ee",
    "Z AI": "#1c7ff8",
}


def model(slug, name, score, lab="Anthropic", release=None, color="__lab__"):
    return {
        "slug": slug,
        "name": name,
        "intelligenceIndex": score,
        "modelCreatorName": lab,
        "modelCreatorColor": COLORS.get(lab) if color == "__lab__" else color,
        "_release": release or slug,
    }


def make_page(models, chunk_size=89):
    clean = [{k: v for k, v in m.items() if k != "_release"} for m in models]
    flight = '30:["$","x",null,{"initialModels":' + json.dumps(clean) + "}]\n"
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
    # Meta
    model("muse", "Muse Wonder (Max)", 52.4, "Meta", release="muse"),
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

# Full index run at API prices, by variant slug. Gemini, Meta, Kimi and Z AI have no measured plan multiplier.
RUNS = {
    "opus": 8708.0,
    "opus-xhigh": 4057.0,
    "opus-high": 2172.0,
    "opus-medium": 1627.0,
    "astra-xhigh": 3803.0,
    "astra-high": 2925.0,
    "luna": 122.0,
    "grok-xhigh": 4967.0,
    "grok-high": 2000.0,
    "gem-high": 999.0,  # a cost we have but no plan for: must be ignored
}


def slugs(boards, grade):
    return [e.slug for e in boards[grade]]


def bar_geometry(d):
    """(left x, right x, height) of a column path from `_column`."""
    m = re.fullmatch(
        r"M([\d.]+),([\d.]+) V[\d.]+ Q[\d.]+,([\d.]+) [\d.]+,[\d.]+ H[\d.]+ Q[\d.]+,[\d.]+ ([\d.]+),[\d.]+ V([\d.]+) Z", d
    )
    assert m, d
    x0, base, top, x1, base2 = (float(g) for g in m.groups())
    assert base == base2
    return x0, x1, base - top


class Leaderboard(unittest.TestCase):
    def test_parses_across_chunk_boundaries(self):
        self.assertEqual(len(ch.leaderboard_models(make_page(MODELS, chunk_size=7))), len(MODELS))

    def test_missing_data_is_a_parse_error(self):
        with self.assertRaises(common.ParseError):
            ch.leaderboard_models("<html></html>")
        with self.assertRaises(common.ParseError):
            ch.leaderboard_models(make_page([]))


class Colors(unittest.TestCase):
    def test_clean_color(self):
        self.assertEqual(ch.clean_color("#cc785c"), "#cc785c")
        self.assertEqual(ch.clean_color("#34A853"), "#34a853")
        self.assertEqual(ch.clean_color(" #0AF "), "#00aaff")  # short form is expanded
        for bad in (None, 12, "", "red", "#12", "#gggggg", "#cc785c;fill:red", '"/><script>x</script>', "url(#x)"):
            self.assertEqual(ch.clean_color(bad), ch.NEUTRAL, bad)

    def test_every_entry_carries_its_labs_color(self):
        boards = ch.leaderboards(MODELS, VARIANTS, n=30)
        by = {e.slug: e.color for e in boards["max"]}
        self.assertEqual(by["opus"], "#cc785c")
        self.assertEqual(by["luna"], "#1f1f1f")
        self.assertEqual(by["gem-high"], "#34a853")
        self.assertEqual(by["muse"], "#0089f4")
        self.assertEqual(by["kimi"], "#4466ee")

    def test_a_lab_without_a_usable_color_is_neutral(self):
        models = [model("a", "A (Xhigh)", 50.0, "Nobody", color=None), model("b", "B (Xhigh)", 40.0, "Evil", color="red;x")]
        variants = [{"slug": m["slug"], "name": m["name"], "releaseSlug": m["slug"]} for m in models]
        self.assertEqual({e.color for e in ch.leaderboards(models, variants, grades=("xhigh",))["xhigh"]}, {ch.NEUTRAL})


class ShortName(unittest.TestCase):
    def test_names(self):
        self.assertEqual(ch.short_name("GPT-6 Astra (Xhigh)"), "GPT-6 Astra")
        self.assertEqual(ch.short_name("Claude Opus 5.5 (Max, Default Fallback)"), "Claude Opus 5.5")
        self.assertEqual(ch.short_name("Claude Fable 5 (Max, Opus 4.8 Fallback)"), "Claude Fable 5")
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


class Labels(unittest.TestCase):
    def entry(self, name, effort, label=None):
        return ch.Entry("x", name, label or ch.short_name(name), "Lab", "#123456", 50.0, effort)

    def test_at_the_level_is_plain(self):
        self.assertEqual(ch.axis_label(self.entry("GPT-6 Astra (Xhigh)", "xhigh"), "xhigh"), "GPT-6 Astra")

    def test_below_the_level_names_its_setting(self):
        self.assertEqual(ch.axis_label(self.entry("Gemini 4 Argon (High)", "high"), "max"), "Gemini 4 Argon (high)")
        e = self.entry("Claude Opus 5.5 (High, Default Fallback)", "high")
        self.assertEqual(ch.axis_label(e, "max"), "Claude Opus 5.5 (high)")

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


class SubscriptionCost(unittest.TestCase):
    def setUp(self):
        self.boards = ch.leaderboards(MODELS, VARIANTS, n=10)
        self.by_slug = {e.slug: e for rows in self.boards.values() for e in rows}

    def test_no_fallback_text_anywhere_people_look(self):
        boards = ch.leaderboards(MODELS, VARIANTS)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ch.write_charts(boards, out, runs=RUNS)
            for rel in ("summary.html", "charts/max.svg", "charts/xhigh.svg"):
                self.assertNotIn("allback", (out / rel).read_text(), rel)

    def test_usd(self):
        self.assertEqual(ch.usd(8708), "$8,708")
        self.assertEqual(ch.usd(192.23), "$192")
        self.assertEqual(ch.usd(89.55), "$90")
        self.assertEqual(ch.usd(7.294), "$7.29")

    def test_the_run_is_divided_by_the_labs_plan_multiplier(self):
        c = ch.subscription_cost(self.by_slug["opus"], RUNS)
        self.assertEqual((c.plan, c.api), ("Claude Max 20x", 8708.0))
        self.assertAlmostEqual(c.sub, 8708 / 45.3)
        self.assertAlmostEqual(ch.subscription_cost(self.by_slug["astra-xhigh"], RUNS).sub, 3803 / 10.25)
        grok = ch.subscription_cost(self.by_slug["grok-xhigh"], RUNS)
        self.assertEqual(grok.plan, "SuperGrok + X Premium+")
        self.assertAlmostEqual(grok.sub, 4967 / 80)

    def test_no_plan_or_no_cost_means_no_bar(self):
        self.assertIsNone(ch.subscription_cost(self.by_slug["gem-high"], RUNS))  # a cost, but no measured plan
        self.assertIsNone(ch.subscription_cost(self.by_slug["muse"], RUNS))  # Meta: no measured plan
        self.assertIsNone(ch.subscription_cost(self.by_slug["opus"], {}))  # a plan, but no cost yet

    def test_wanted_slugs_are_only_labs_with_a_plan_and_unique(self):
        wanted = ch.wanted_slugs(self.boards)
        self.assertEqual(len(wanted), len(set(wanted)))
        self.assertTrue({"opus", "opus-xhigh", "opus-high", "opus-medium", "astra-xhigh", "grok-xhigh"} <= set(wanted))
        for slug in wanted:
            self.assertIn(self.by_slug[slug].lab, ("Anthropic", "OpenAI", "SpaceXAI"))
        self.assertNotIn("gem-high", wanted)
        self.assertNotIn("muse", wanted)

    def test_the_axis_is_shared_and_has_at_most_four_steps(self):
        self.assertEqual(ch.cost_axis(self.boards, RUNS), (100, 400))  # the tallest bar is Astra xhigh at $371
        self.assertIsNone(ch.cost_axis(self.boards, {}))

        def axis_for(sub):
            e = ch.Entry("a", "A (Max)", "A", "Anthropic", "#111111", 50.0, "max")
            return ch.cost_axis({"max": [e]}, {"a": sub * 45.3})

        self.assertEqual(axis_for(519), (200, 600))
        self.assertEqual(axis_for(192), (50, 200))
        self.assertEqual(axis_for(36), (10, 40))
        self.assertEqual(axis_for(8), (2, 8))
        for sub in (0.5, 3, 77, 400, 1999, 12345):
            step, top = axis_for(sub)
            self.assertGreaterEqual(top, sub)
            self.assertLessEqual(top // step, 4)


class ThinBars(unittest.TestCase):
    def setUp(self):
        self.boards = ch.leaderboards(MODELS, VARIANTS, n=10)
        self.axis = ch.cost_axis(self.boards, RUNS)

    def parse(self, grade, runs=RUNS, axis="shared"):
        svg = ch.render_svg(grade, self.boards[grade], runs, self.axis if axis == "shared" else axis)
        root = ET.fromstring(svg)
        paths = root.findall(".//s:path", SVG_NS)
        thin = [p for p in paths if p.get("fill") == ch.THIN_COLOR]
        main = [p for p in paths if p.get("fill") != ch.THIN_COLOR]
        return svg, root, main, thin

    def test_a_thin_bar_sits_beside_each_column_of_a_lab_with_a_plan(self):
        for grade, rows in self.boards.items():
            _, _, main, thin = self.parse(grade)
            self.assertEqual(len(main), len(rows), grade)
            self.assertEqual(len(thin), sum(1 for e in rows if ch.subscription_cost(e, RUNS)), grade)
        _, _, main, thin = self.parse("xhigh")
        self.assertGreater(len(thin), 0)
        self.assertLess(len(thin), len(main))  # Gemini has no plan, so not every column has one

    def test_the_thin_bar_is_8px_next_to_the_main_bar_with_a_2px_gap(self):
        _, root, main, thin = self.parse("xhigh")
        entries = self.boards["xhigh"]
        priced = [i for i, e in enumerate(entries) if ch.subscription_cost(e, RUNS)]
        for path, i in zip(thin, priced):
            tx0, tx1, _ = bar_geometry(path.get("d"))
            mx0, mx1, _ = bar_geometry(main[i].get("d"))
            self.assertAlmostEqual(tx1 - tx0, ch.THIN_BAR, delta=0.2)
            self.assertAlmostEqual(tx0 - mx1, ch.BAR_GAP, delta=0.2)
            self.assertLessEqual(mx1 - mx0, ch.MAX_BAR + 0.1)

    def test_heights_follow_the_shared_dollar_axis(self):
        for grade in ("xhigh", "high", "max"):
            _, _, _, thin = self.parse(grade)
            priced = [ch.subscription_cost(e, RUNS) for e in self.boards[grade]]
            for path, c in zip(thin, [c for c in priced if c]):
                _, _, height = bar_geometry(path.get("d"))
                self.assertAlmostEqual(height, ch.PLOT_H * c.sub / self.axis[1], delta=0.2, msg=grade)

    def test_the_same_model_gets_a_shorter_bar_on_a_lower_effort_chart(self):
        def opus_bar(grade):
            _, _, main, thin = self.parse(grade)
            entries = self.boards[grade]
            priced = [i for i, e in enumerate(entries) if ch.subscription_cost(e, RUNS)]
            idx = next(k for k, i in enumerate(priced) if entries[i].slug.startswith("opus"))
            return bar_geometry(thin[idx].get("d"))[2]

        self.assertGreater(opus_bar("max"), opus_bar("xhigh"))
        self.assertGreater(opus_bar("xhigh"), opus_bar("high"))

    def test_main_bars_keep_their_slots_whether_or_not_a_column_has_a_thin_bar(self):
        _, _, main, _ = self.parse("xhigh")
        lefts = [bar_geometry(p.get("d"))[0] for p in main]
        gaps = {round(b - a, 1) for a, b in zip(lefts, lefts[1:])}
        self.assertEqual(len(gaps), 1)  # evenly spaced, so Gemini's bare column lines up with the rest

    def test_the_right_axis_is_labelled_and_the_legend_explains_the_thin_bar(self):
        svg, root, _, _ = self.parse("xhigh")
        ticks = [t.text for t in root.findall(".//s:text", SVG_NS) if t.get("class") == "tick" and t.text.startswith("$")]
        self.assertEqual(ticks, ["$0", "$100", "$200", "$300", "$400"])
        self.assertIn(ch.THIN_LABEL, svg)
        self.assertIn("(right axis)", ch.THIN_LABEL)

    def test_no_costs_means_no_thin_bars_axis_or_legend_key(self):
        for runs, axis in ((None, None), ({}, None), (RUNS, None)):
            svg = ch.render_svg("xhigh", self.boards["xhigh"], runs, axis)
            root = ET.fromstring(svg)
            self.assertEqual([p for p in root.findall(".//s:path", SVG_NS) if p.get("fill") == ch.THIN_COLOR], [])
            self.assertNotIn(ch.THIN_LABEL, svg)
            self.assertFalse([t for t in root.findall(".//s:text", SVG_NS) if (t.text or "").startswith("$")])

    def test_the_plot_narrows_to_make_room_for_the_axis(self):
        _, _, main_thin, _ = self.parse("xhigh")
        plain = ET.fromstring(ch.render_svg("xhigh", self.boards["xhigh"]))
        plain_main = plain.findall(".//s:path", SVG_NS)
        last_thin = bar_geometry(main_thin[-1].get("d"))[1]
        self.assertLess(last_thin, ch.W - ch.RIGHT_AXIS)  # the bars stop before the axis labels
        self.assertGreater(bar_geometry(plain_main[-1].get("d"))[1], last_thin)  # and the plain chart uses the room

    def test_the_tooltip_gives_the_exact_figures(self):
        svg, _, _, _ = self.parse("xhigh")
        self.assertIn("one full index run costs $90 on Claude Max 20x ($4,057 at API prices)", svg)
        self.assertIn("one full index run costs $371 on ChatGPT ($3,803 at API prices)", svg)
        self.assertNotIn("Gemini 4 Argon (High), Google: 52.6 · one full", svg)  # no plan, so no cost in its tooltip

    def test_well_formed_in_both_forms_and_the_legend_wraps_cleanly(self):
        for grade, rows in self.boards.items():
            ET.fromstring(ch.render_svg(grade, rows, RUNS, self.axis))
            ET.fromstring(ch.svg_file(grade, rows, RUNS, self.axis).encode())

    def test_the_thin_bar_is_one_neutral_not_a_lab_color(self):
        _, _, _, thin = self.parse("max")
        self.assertEqual({p.get("fill") for p in thin}, {ch.THIN_COLOR})
        self.assertNotIn(ch.THIN_COLOR, set(COLORS.values()))


class Svg(unittest.TestCase):
    def setUp(self):
        self.boards = ch.leaderboards(MODELS, VARIANTS, n=10)

    def test_is_well_formed_and_has_one_column_per_model(self):
        for grade, rows in self.boards.items():
            root = ET.fromstring(ch.render_svg(grade, rows))
            self.assertEqual(len(root.findall(".//s:path", SVG_NS)), len(rows), grade)
            self.assertEqual(len(root.findall(".//s:g", SVG_NS)), len(rows), grade)
            ET.fromstring(ch.svg_file(grade, rows).encode())  # the file form parses too

    def test_each_column_wears_its_labs_color(self):
        rows = self.boards["max"]
        root = ET.fromstring(ch.render_svg("max", rows))
        fills = [p.get("fill") for p in root.findall(".//s:path", SVG_NS)]
        self.assertEqual(fills, [e.color for e in rows])
        self.assertIn("#cc785c", fills)  # Anthropic's terracotta
        self.assertIn("#0089f4", fills)  # Meta's blue

    def test_legend_has_one_swatch_per_lab_in_rank_order(self):
        rows = self.boards["max"]
        root = ET.fromstring(ch.render_svg("max", rows))
        names = [t.text for t in root.findall(".//s:text", SVG_NS) if t.get("class") == "legend"]
        expected = list(dict.fromkeys(e.lab for e in rows))
        self.assertEqual(names, expected)
        swatches = [r.get("fill") for r in root.findall(".//s:rect", SVG_NS) if r.get("width") == "10"]
        self.assertEqual(swatches, [next(e.color for e in rows if e.lab == n) for n in expected])
        self.assertNotIn("Other labs", names)

    def test_legend_wraps_and_the_plot_moves_down_to_make_room(self):
        labs = [f"A Laboratory With A Rather Long Name Number {i}" for i in range(10)]
        rows = [ch.Entry(f"s{i}", f"M{i} (Xhigh)", f"M{i}", lab, "#336699", 50.0 - i, "xhigh") for i, lab in enumerate(labs)]
        items, count = ch.legend_items(rows)
        self.assertGreater(count, 1)
        self.assertEqual(len(items), 10)
        for x, _row, _fill, name, _swatch in items:
            self.assertLess(x + 16 + len(name) * 6.6, ch.W)  # nothing runs off the card
        tall = ET.fromstring(ch.render_svg("xhigh", rows))
        short = ET.fromstring(ch.render_svg("xhigh", rows[:1]))
        self.assertGreater(float(tall.get("height")), float(short.get("height")))
        ET.fromstring(ch.svg_file("xhigh", rows).encode())

    def test_hostile_color_and_names_cannot_break_out_of_the_svg(self):
        bad = [model("x", 'A&B <"x"> (Xhigh)', 40.0, 'Lab & "Co"', color='"/><script>alert(1)</script>')]
        variants = [{"slug": "x", "name": bad[0]["name"], "releaseSlug": "x"}]
        rows = ch.leaderboards(bad, variants, grades=("xhigh",))["xhigh"]
        svg = ch.render_svg("xhigh", rows)
        ET.fromstring(svg)
        self.assertNotIn("<script", svg)
        self.assertIn(ch.NEUTRAL, svg)

    def test_columns_are_capped_at_24px_with_square_baseline(self):
        root = ET.fromstring(ch.render_svg("max", self.boards["max"]))
        paths = root.findall(".//s:path", SVG_NS)
        self.assertTrue(paths)
        for p in paths:
            x0, x1, _ = bar_geometry(p.get("d"))
            self.assertLessEqual(x1 - x0, ch.MAX_BAR + 0.1)

    def test_value_on_the_cap(self):
        self.assertIn(">56.0<", ch.render_svg("xhigh", self.boards["xhigh"]))

    def test_max_chart_labels_the_unlabelled_and_the_below_level_models(self):
        svg = ch.render_svg("max", self.boards["max"])
        self.assertIn("Kimi K3.1 Pro †", svg)
        self.assertIn("Gemini 4 Argon (high)", svg)
        self.assertIn("† effort not labelled", svg)

    def test_tooltip_keeps_the_full_name_and_lab(self):
        rows = [ch.Entry("x", "A" * 60 + " (Xhigh)", "A" * 60, "OpenAI", "#1f1f1f", 40.0, "xhigh")]
        svg = ch.render_svg("xhigh", rows)
        self.assertIn("…", svg)
        self.assertIn("A" * 60 + " (Xhigh), OpenAI: 40.0", svg)

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
    def boards(self, models=MODELS):
        return ch.leaderboards(models, VARIANTS)

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
            self.assertEqual(json.loads((out / "charts.json").read_text())["grades"]["max"][0]["color"], "#cc785c")

    def test_cost_bars_reach_the_svgs_the_page_and_the_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ch.write_charts(self.boards(), out, runs=RUNS)
            svg = (out / "charts/xhigh.svg").read_text()
            self.assertIn(ch.THIN_LABEL, svg)
            self.assertIn(f'fill="{ch.THIN_COLOR}"', svg)
            page = (out / "summary.html").read_text()
            self.assertIn("thin bar beside a column", page)
            self.assertIn("Paweł Huryn", page)  # the multipliers are credited on the page
            self.assertIn("Claude Max 20x 45.3x", page)
            self.assertIn("SuperGrok + X Premium+ 80x", page)
            self.assertIn("$4,057 → $90 on Claude Max 20x", page)  # exact figures in the table view
            self.assertIn("<td>–</td>", page)  # a lab with no measured plan shows a dash
            self.assertNotIn('class="subline"', page)  # no text-line version of this
            self.assertNotIn("costline", page)
            state = json.loads((out / "charts.json").read_text())
            self.assertEqual(state["cost_axis"], [100, 400])
            first = state["grades"]["xhigh"][0]
            self.assertEqual(first["full_run_api"], 4057.0)
            self.assertAlmostEqual(first["full_run_subscription"], 4057 / 45.3, places=2)
            unpriced = next(e for e in state["grades"]["xhigh"] if e["lab"] == "Google")
            self.assertIsNone(unpriced["full_run_subscription"])

    def test_no_runs_means_plain_charts(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ch.write_charts(self.boards(), out)
            self.assertNotIn(ch.THIN_LABEL, (out / "charts/xhigh.svg").read_text())
            self.assertIsNone(json.loads((out / "charts.json").read_text())["cost_axis"])

    def test_idempotent_and_keeps_changed_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertTrue(ch.write_charts(self.boards(), out, now=datetime(2030, 1, 1, tzinfo=timezone.utc), runs=RUNS))
            self.assertFalse(ch.write_charts(self.boards(), out, now=datetime(2031, 1, 1, tzinfo=timezone.utc), runs=dict(RUNS)))
            self.assertEqual(json.loads((out / "charts.json").read_text())["changed_at"], "2030-01-01 00:00 UTC")

    def test_changed_scores_costs_or_colors_rewrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            t = [datetime(y, 1, 1, tzinfo=timezone.utc) for y in (2030, 2031, 2032, 2033)]
            ch.write_charts(self.boards(), out, now=t[0], runs=RUNS)
            scores = [dict(m) for m in MODELS]
            scores[1]["intelligenceIndex"] = 55.0
            self.assertTrue(ch.write_charts(self.boards(scores), out, now=t[1], runs=RUNS))
            self.assertTrue(ch.write_charts(self.boards(scores), out, now=t[2], runs={**RUNS, "opus": 9000.0}))
            self.assertEqual(json.loads((out / "charts.json").read_text())["changed_at"], "2032-01-01 00:00 UTC")
            recolored = [dict(m) for m in scores]
            for m in recolored:
                if m["modelCreatorName"] == "Anthropic":
                    m["modelCreatorColor"] = "#abcdef"
            self.assertTrue(ch.write_charts(self.boards(recolored), out, now=t[3], runs={**RUNS, "opus": 9000.0}))
            self.assertIn("#abcdef", (out / "charts/max.svg").read_text())


PAIR_MODELS = [
    model("opus-medium", "Claude Opus 5.5 (Medium, Default Fallback)", 51.2, release="opus"),
    model("opus-high", "Claude Opus 5.5 (High, Default Fallback)", 53.6, release="opus"),
    model("opus-xhigh", "Claude Opus 5.5 (Xhigh, Default Fallback)", 56.0, release="opus"),
    model("opus-max", "Claude Opus 5.5 (Max, Default Fallback)", 57.6, release="opus"),
    model("sonnet-high", "Claude Sonnet 5.5 (High, Default Fallback)", 46.8, release="sonnet"),
    model("sonnet-xhigh", "Claude Sonnet 5.5 (Xhigh, Default Fallback)", 51.9, release="sonnet"),
    model("sonnet-max", "Claude Sonnet 5.5 (Max, Default Fallback)", 56.0, release="sonnet"),
    model("haiku-max", "Claude Haiku 5.5 (Max)", 43.4, release="haiku"),  # third: never paired
    model("astra-high", "GPT-6 Astra (High)", 50.9, "OpenAI", release="astra"),
    model("astra-xhigh", "GPT-6 Astra (Xhigh)", 52.4, "OpenAI", release="astra"),
    model("sol-xhigh", "GPT-6.1 Sol (Xhigh)", 51.0, "OpenAI", release="sol"),  # no Max: no xhigh-vs-max pair
    model("gem-high", "Gemini 4 Argon (High)", 52.6, "Google", release="gem"),  # one model: no pair
    model("kimi", "Kimi K3.1 Pro", 55.0, "Kimi", release="kimi"),
    model("kimi-2", "Kimi K3 (Max)", 50.0, "Kimi", release="kimi2"),  # unlabelled best can't pair
]
PAIR_VARIANTS = [{"slug": m["slug"], "name": m["name"], "releaseSlug": m["_release"]} for m in PAIR_MODELS]
PAIR_RUNS = {"opus-high": 2172.0, "sonnet-xhigh": 2177.0, "astra-high": 2925.0, "sol-xhigh": 662.0}


class StepDown(unittest.TestCase):
    def setUp(self):
        self.pairs = ch.step_down_pairs(PAIR_MODELS, PAIR_VARIANTS)

    def test_best_model_one_level_below_the_next_model(self):
        got = [(p.lab, p.low, p.high, p.best.slug, p.nxt.slug) for p in self.pairs]
        self.assertEqual(
            got,
            [
                ("Anthropic", "medium", "high", "opus-medium", "sonnet-high"),
                ("Anthropic", "high", "xhigh", "opus-high", "sonnet-xhigh"),
                ("Anthropic", "xhigh", "max", "opus-xhigh", "sonnet-max"),
                ("OpenAI", "high", "xhigh", "astra-high", "sol-xhigh"),
            ],
        )

    def test_labs_without_two_labelled_models_are_left_out(self):
        labs = {p.lab for p in self.pairs}
        self.assertNotIn("Google", labs)  # one model
        self.assertNotIn("Kimi", labs)  # its best model has no effort label
        self.assertNotIn("haiku-max", {p.nxt.slug for p in self.pairs})  # only the top two models pair

    def test_tint(self):
        self.assertEqual(ch.tint("#000000"), "#808080")
        self.assertEqual(ch.tint("#ffffff"), "#ffffff")
        self.assertEqual(ch.tint("#cc785c", 0), "#cc785c")

    def test_chart_draws_each_pair_with_the_next_model_lighter(self):
        root = ET.fromstring(ch.render_pairs_svg(self.pairs))
        paths = root.findall(".//s:path", SVG_NS)
        self.assertEqual(len(paths), 2 * len(self.pairs))
        for i, p in enumerate(self.pairs):
            self.assertEqual(paths[2 * i].get("fill"), p.best.color)
            self.assertEqual(paths[2 * i + 1].get("fill"), ch.tint(p.nxt.color))
        labels = [t.text for t in root.findall(".//s:text", SVG_NS) if t.get("class") == "lbl"]
        self.assertEqual(labels[:2], ["Claude Opus 5.5 · medium", "Claude Sonnet 5.5 · high"])
        legend = [t.text for t in root.findall(".//s:text", SVG_NS) if t.get("class") == "legend"]
        self.assertIn(ch.NEXT_KEY, legend)

    def test_pairs_are_separated_by_a_wider_gap(self):
        root = ET.fromstring(ch.render_pairs_svg(self.pairs))
        lefts = [bar_geometry(p.get("d"))[0] for p in root.findall(".//s:path", SVG_NS)]
        within, between = lefts[1] - lefts[0], lefts[2] - lefts[1]
        self.assertGreater(between, within * 1.4)

    def test_cost_bars_and_shared_axis(self):
        axis = ch.cost_axis({"pairs": [e for p in self.pairs for e in (p.best, p.nxt)]}, PAIR_RUNS)
        svg = ch.render_pairs_svg(self.pairs, PAIR_RUNS, axis)
        root = ET.fromstring(svg)
        thin = [p for p in root.findall(".//s:path", SVG_NS) if p.get("fill") == ch.THIN_COLOR]
        self.assertEqual(len(thin), 4)  # the four priced variants
        self.assertIn("one full index run costs $48 on Claude Max 20x", svg)

    SPEEDS = {
        "opus-high": {"ttfat_s": 21.5, "tps": 80.6, "e2e_s": 27.7},
        "sonnet-xhigh": {"ttfat_s": 26.1, "tps": 111.1, "e2e_s": 30.6},
        "astra-high": {"ttfat_s": 10.0, "tps": 50.0, "e2e_s": 20.0},
    }

    def test_a_third_bar_shows_end_to_end_time_labelled_in_seconds(self):
        axis = ch.cost_axis({"pairs": [e for p in self.pairs for e in (p.best, p.nxt)]}, PAIR_RUNS)
        svg = ch.render_pairs_svg(self.pairs, PAIR_RUNS, axis, self.SPEEDS)
        root = ET.fromstring(svg)
        timed = [p for p in root.findall(".//s:path", SVG_NS) if p.get("fill") == ch.TIME_COLOR]
        self.assertEqual(len(timed), 3)
        labels = [t.text for t in root.findall(".//s:text", SVG_NS) if t.get("class") == "tval"]
        self.assertEqual(labels, ["28s", "31s", "20s"])
        self.assertIn(ch.TIME_LABEL, [t.text for t in root.findall(".//s:text", SVG_NS) if t.get("class") == "legend"])
        self.assertIn("first answer token 21.5 s, 81 tokens/s, end-to-end 27.7 s", svg)
        # heights share one scale: 27.7 s and 30.6 s on a 40 s axis
        h = {round(bar_geometry(p.get("d"))[2], 1) for p in timed}
        self.assertIn(round(ch.PLOT_H * 27.7 / 40, 1), h)

    def test_three_bars_sit_side_by_side_without_overlap(self):
        axis = ch.cost_axis({"pairs": [e for p in self.pairs for e in (p.best, p.nxt)]}, PAIR_RUNS)
        root = ET.fromstring(ch.render_pairs_svg(self.pairs, PAIR_RUNS, axis, self.SPEEDS))
        spans = sorted(bar_geometry(p.get("d"))[:2] for p in root.findall(".//s:path", SVG_NS))
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            self.assertGreaterEqual(b0 - a1, ch.BAR_GAP - 0.2)

    def test_no_speed_data_means_no_third_bar(self):
        svg = ch.render_pairs_svg(self.pairs, PAIR_RUNS, None, {})
        self.assertNotIn(ch.TIME_COLOR, svg)
        self.assertNotIn("tval", svg.split("</style>")[1])

    def test_no_pairs_is_an_error(self):
        with self.assertRaises(ValueError):
            ch.render_pairs_svg([])

    def test_written_to_the_page_the_svg_and_the_state(self):
        boards = ch.leaderboards(PAIR_MODELS, PAIR_VARIANTS)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ch.write_charts(boards, out, runs=PAIR_RUNS, pairs=self.pairs)
            ET.fromstring((out / "charts/step-down.svg").read_bytes())
            page = (out / "summary.html").read_text()
            self.assertIn('<section id="step-down">', page)
            self.assertIn('href="#step-down"', page)
            self.assertIn("<td>high vs xhigh</td>", page)
            self.assertIn('<td class="num">+1.7</td>', page)  # Opus high 53.6 vs Sonnet xhigh 51.9
            state = json.loads((out / "charts.json").read_text())
            self.assertEqual(state["step_down"][1]["levels"], ["high", "xhigh"])
            # without pairs there is no section and no file
            other = Path(tmp) / "other"
            ch.write_charts(boards, other)
            self.assertFalse((other / "charts/step-down.svg").exists())
            self.assertNotIn("step-down", (other / "summary.html").read_text())


@unittest.skipUnless(os.environ.get("AA_LIVE"), "set AA_LIVE=1 to hit the real site")
class Live(unittest.TestCase):
    def test_real_leaderboard(self):
        from aa_xhigh import variant_index

        variants = variant_index(common.flight_payload(common.fetch()))
        boards = ch.leaderboards(ch.leaderboard_models(common.fetch(ch.LEADERBOARD_URL)), variants)
        for grade in ch.GRADES:
            self.assertGreater(len(boards[grade]), 3)
            ET.fromstring(ch.render_svg(grade, boards[grade]))
        self.assertNotIn(ch.NEUTRAL, {e.color for e in boards["max"]})  # the top labs all have real colors


if __name__ == "__main__":
    unittest.main()
