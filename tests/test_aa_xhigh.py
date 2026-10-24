import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aa_common as common  # noqa: E402
import aa_xhigh as aa  # noqa: E402


def variant(slug, name, release):
    return {"slug": slug, "name": name, "releaseSlug": release}


def default(slug, name, release=None):
    return {"slug": slug, "name": name, "chartDefaultSelected": True, "release": {"slug": release or slug}}


def make_page(defaults, variants, chunk_size=97):
    """A page shaped like the real one: RSC text split into push([1,"..."]) chunks."""
    flight = (
        '28:["$","div",null,{"initialModels":' + json.dumps(defaults) + "}]\n"
        '29:["$","x",null,{"models":' + json.dumps(variants) + "}]\n"
    )
    chunks = [flight[i : i + chunk_size] for i in range(0, len(flight), chunk_size)]
    pushes = "".join(f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks)
    return "<html><script>self.__next_f.push([0])</script>" + pushes + "</html>"


VARIANTS = [
    variant("opus", "Claude Opus 5.5 (Max, Default Fallback)", "opus"),
    variant("opus-xhigh", "Claude Opus 5.5 (Xhigh, Default Fallback)", "opus"),
    variant("opus-high", "Claude Opus 5.5 (High, Default Fallback)", "opus"),
    variant("opus-medium", "Claude Opus 5.5 (Medium, Default Fallback)", "opus"),
    variant("fable", "Claude Fable 5 (Max, Opus 4.8 Fallback)", "fable"),
    variant("fable-xhigh", "Claude Fable 5 (Xhigh)", "fable"),  # different qualifier: not a match
    variant("glm", "GLM-5.3 (Max)", "glm"),
    variant("glm-low", "GLM-5.3 (Low)", "glm"),
    variant("grok", "Grok 4.7 (Xhigh)", "grok"),
    variant("grok-high", "Grok 4.7 (High)", "grok"),
    variant("gem", "Gemini 4 Argon (High)", "gem"),
    variant("plain", "MiMo-V2.6-Pro", "plain"),
    variant("qwen", "Qwen3.8 Max (0902)", "qwen-0902"),
]
DEFAULTS = [
    default("plain", "MiMo-V2.6-Pro"),
    default("opus", "Claude Opus 5.5 (Max, Default Fallback)"),
    default("fable", "Claude Fable 5 (Max, Opus 4.8 Fallback)"),
    default("glm", "GLM-5.3 (Max)"),
    default("grok", "Grok 4.7 (Xhigh)"),
    default("gem", "Gemini 4 Argon (High)"),
    default("qwen", "Qwen3.8 Max (0902)", "qwen-0902"),
]


class ParseLabel(unittest.TestCase):
    def test_labels(self):
        self.assertEqual(common.parse_label("Claude Opus 5.5 (Max, Default Fallback)"), common.Label("max", ("default fallback",)))
        self.assertEqual(common.parse_label("GPT-6 Sol (Daybreak Blue, max)"), common.Label("max", ("daybreak blue",)))
        self.assertEqual(common.parse_label("Grok 4.7 (Xhigh)"), common.Label("xhigh"))
        self.assertEqual(common.parse_label("Qwen3.8 Max (0902)"), common.Label(None))
        self.assertEqual(common.parse_label("MiMo-V2.6-Pro"), common.Label(None))


class Decode(unittest.TestCase):
    def test_chunks_split_mid_value_are_rejoined(self):
        payload = common.flight_payload(make_page(DEFAULTS, VARIANTS, chunk_size=11))
        self.assertEqual(aa.default_models(payload), DEFAULTS)
        self.assertEqual(aa.variant_index(payload), VARIANTS)

    def test_missing_data_is_a_parse_error(self):
        with self.assertRaises(common.ParseError):
            aa.build("<html>nothing here</html>")
        with self.assertRaises(common.ParseError):
            aa.build(make_page([], VARIANTS))


class Swap(unittest.TestCase):
    def build(self, level="xhigh", **kw):
        return aa.build(make_page(DEFAULTS, VARIANTS), level, **kw)

    def test_xhigh_swaps_only_max(self):
        r = self.build("xhigh")
        self.assertEqual(r.slugs, ["plain", "opus-xhigh", "fable", "glm", "grok", "gem", "qwen"])
        self.assertEqual([s.slug for s in r.swapped], ["opus"])
        self.assertEqual({k.slug for k in r.unmatched}, {"fable", "glm"})

    def test_high_also_lowers_xhigh_models(self):
        r = self.build("high")
        self.assertEqual(r.slugs, ["plain", "opus-high", "fable", "glm", "grok-high", "gem", "qwen"])
        self.assertEqual([s.slug for s in r.swapped], ["opus", "grok"])

    def test_medium_keeps_models_with_no_medium_variant(self):
        r = self.build("medium")
        self.assertEqual(r.slugs, ["plain", "opus-medium", "fable", "glm", "grok", "gem", "qwen"])
        self.assertEqual({k.slug for k in r.unmatched}, {"fable", "glm", "grok", "gem"})

    def test_never_raises_a_model_above_its_level(self):
        # Gemini is High; asking for xhigh must not touch it, and low must lower it only if a low variant exists.
        self.assertIn("gem", self.build("xhigh").slugs)
        self.assertEqual(self.build("low").slugs.count("gem"), 1)
        self.assertIn("glm-low", self.build("low").slugs)

    def test_qualifier_must_match(self):
        r = self.build("xhigh")
        self.assertIn("fable", r.slugs)  # the Xhigh sibling has no fallback tag, so it is not the same thing
        self.assertNotIn("fable-xhigh", r.slugs)

    def test_drop_removes_models_with_no_variant(self):
        r = self.build("xhigh", unmatched="drop")
        self.assertNotIn("glm", r.slugs)
        self.assertNotIn("fable", r.slugs)
        self.assertIn("opus-xhigh", r.slugs)

    def test_already_present_variant_is_not_duplicated(self):
        defaults = DEFAULTS + [default("opus-xhigh", "Claude Opus 5.5 (Xhigh, Default Fallback)", "opus")]
        r = aa.build(make_page(defaults, VARIANTS))
        self.assertEqual(r.slugs.count("opus-xhigh"), 1)

    def test_unknown_slug_is_rejected(self):
        with self.assertRaises(common.ParseError):
            aa.build(make_page(DEFAULTS + [default("ghost", "Ghost")], VARIANTS))

    def test_bad_arguments(self):
        with self.assertRaises(ValueError):
            self.build("max")
        with self.assertRaises(ValueError):
            self.build("xhigh", unmatched="nope")

    def test_url_is_comma_encoded_and_ordered(self):
        self.assertEqual(
            self.build("xhigh").url,
            "https://artificialanalysis.ai/models?models=plain%2Copus-xhigh%2Cfable%2Cglm%2Cgrok%2Cgem%2Cqwen",
        )

    def test_above(self):
        self.assertEqual(aa.above("xhigh"), ["max"])
        self.assertEqual(aa.above("medium"), ["high", "xhigh", "max"])


class Output(unittest.TestCase):
    def results(self):
        page = make_page(DEFAULTS, VARIANTS)
        return {lv: aa.build(page, lv) for lv in common.LEVELS}

    def test_levels_land_in_their_own_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            for r in self.results().values():
                self.assertTrue(aa.write_level(r, out))
            self.assertTrue((out / "index.html").exists())  # xhigh at the root
            self.assertTrue((out / "high" / "link.txt").exists())
            self.assertTrue((out / "medium" / "latest.json").exists())
            self.assertIn("opus-high", (out / "high" / "link.txt").read_text())

    def test_write_is_idempotent_and_keeps_changed_at(self):
        r = self.results()["xhigh"]
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertTrue(aa.write_level(r, out, now=datetime(2030, 1, 1, tzinfo=timezone.utc)))
            self.assertFalse(aa.write_level(r, out, now=datetime(2031, 1, 1, tzinfo=timezone.utc)))
            self.assertEqual(json.loads((out / "latest.json").read_text())["changed_at"], "2030-01-01 00:00 UTC")

    def test_changed_link_rewrites_with_new_timestamp(self):
        page = make_page(DEFAULTS, VARIANTS)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            aa.write_level(aa.build(page, "xhigh"), out, now=datetime(2030, 1, 1, tzinfo=timezone.utc))
            changed = aa.write_level(aa.build(page, "xhigh", unmatched="drop"), out, now=datetime(2031, 1, 1, tzinfo=timezone.utc))
            self.assertTrue(changed)
            self.assertEqual(json.loads((out / "latest.json").read_text())["changed_at"], "2031-01-01 00:00 UTC")

    def test_page_text_and_navigation(self):
        res = self.results()
        root = aa.render_page(res["xhigh"], "T")
        high = aa.render_page(res["high"], "T", prefix="../")
        self.assertIn("every max variant swapped for xhigh", root)
        self.assertIn("every xhigh or max variant swapped for high", high)
        self.assertIn("location.replace", root)
        self.assertIn("color-scheme: light;", root)  # always a white page
        self.assertNotIn("dark", root)
        self.assertIn('href="high/#info"', root)  # root links down to the other levels, keeping the info view
        self.assertIn('href="../#info"', high)  # a level folder links back up to xhigh
        self.assertIn('href="../medium/#info"', high)
        self.assertIn('href="../summary.html"', high)
        self.assertIn("Claude Opus 5.5 (High, Default Fallback)", high)


class Cli(unittest.TestCase):
    def test_prints_the_requested_level(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "page.html"
            f.write_text(make_page(DEFAULTS, VARIANTS))
            for level, slug in (("xhigh", "opus-xhigh"), ("high", "opus-high"), ("medium", "opus-medium")):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    self.assertEqual(aa.main(["--input", str(f), "--level", level]), 0)
                self.assertIn(slug, buf.getvalue())

    def test_bad_input_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "page.html"
            f.write_text("<html></html>")
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(aa.main(["--input", str(f)]), 1)


@unittest.skipUnless(os.environ.get("AA_LIVE"), "set AA_LIVE=1 to hit the real site")
class Live(unittest.TestCase):
    def test_real_page(self):
        for level in common.LEVELS:
            r = aa.build(common.fetch(), level)
            self.assertGreater(len(r.slugs), 5)
            self.assertGreater(len(r.swapped), 0)


if __name__ == "__main__":
    unittest.main()
