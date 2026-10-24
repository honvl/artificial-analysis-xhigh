import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aa_xhigh as aa  # noqa: E402


def variant(slug, name, release=None):
    return {"slug": slug, "name": name, "releaseSlug": release or slug.removesuffix("-xhigh")}


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
    variant("fable", "Claude Fable 5 (Max, Opus 4.8 Fallback)", "fable"),
    variant("fable-xhigh", "Claude Fable 5 (Xhigh)", "fable"),  # different qualifier: not a match
    variant("glm", "GLM-5.3 (Max)", "glm"),
    variant("glm-low", "GLM-5.3 (Low)", "glm"),
    variant("grok", "Grok 4.7 (Xhigh)", "grok"),
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
        self.assertEqual(aa.parse_label("Claude Opus 5.5 (Max, Default Fallback)"), aa.Label("max", ("default fallback",)))
        self.assertEqual(aa.parse_label("GPT-6 Sol (Daybreak Blue, max)"), aa.Label("max", ("daybreak blue",)))
        self.assertEqual(aa.parse_label("Grok 4.7 (Xhigh)"), aa.Label("xhigh"))
        self.assertEqual(aa.parse_label("Qwen3.8 Max (0902)"), aa.Label(None))
        self.assertEqual(aa.parse_label("MiMo-V2.6-Pro"), aa.Label(None))


class Decode(unittest.TestCase):
    def test_chunks_split_mid_value_are_rejoined(self):
        page = make_page(DEFAULTS, VARIANTS, chunk_size=11)
        payload = aa.flight_payload(page)
        self.assertEqual(aa.default_models(payload), DEFAULTS)
        self.assertEqual(aa.variant_index(payload), VARIANTS)

    def test_missing_data_is_a_parse_error(self):
        with self.assertRaises(aa.ParseError):
            aa.build("<html>nothing here</html>")
        with self.assertRaises(aa.ParseError):
            aa.build(make_page([], VARIANTS))


class Swap(unittest.TestCase):
    def build(self, **kw):
        return aa.build(make_page(DEFAULTS, VARIANTS), **kw)

    def test_max_becomes_xhigh_and_nothing_else_moves(self):
        r = self.build()
        self.assertEqual(r.slugs, ["plain", "opus-xhigh", "fable", "glm", "grok", "gem", "qwen"])
        self.assertEqual([s.slug for s in r.swapped], ["opus"])

    def test_qualifier_must_match(self):
        r = self.build()
        self.assertIn("fable", r.slugs)  # Xhigh sibling has no fallback tag, so it is not the same thing
        self.assertNotIn("fable-xhigh", r.slugs)

    def test_unmatched_keep_and_drop(self):
        self.assertEqual({k.slug for k in self.build().no_xhigh}, {"fable", "glm"})
        dropped = self.build(unmatched="drop")
        self.assertNotIn("glm", dropped.slugs)
        self.assertNotIn("fable", dropped.slugs)
        self.assertIn("opus-xhigh", dropped.slugs)

    def test_already_present_xhigh_is_not_duplicated(self):
        defaults = DEFAULTS + [default("opus-xhigh", "Claude Opus 5.5 (Xhigh, Default Fallback)", "opus")]
        r = aa.build(make_page(defaults, VARIANTS))
        self.assertEqual(r.slugs.count("opus-xhigh"), 1)

    def test_unknown_slug_is_rejected(self):
        bad = DEFAULTS + [default("ghost", "Ghost")]
        with self.assertRaises(aa.ParseError):
            aa.build(make_page(bad, VARIANTS))

    def test_url_is_comma_encoded_and_ordered(self):
        r = self.build()
        self.assertEqual(
            r.url,
            "https://artificialanalysis.ai/models?models=plain%2Copus-xhigh%2Cfable%2Cglm%2Cgrok%2Cgem%2Cqwen",
        )


class Output(unittest.TestCase):
    def test_write_is_idempotent_and_keeps_changed_at(self):
        result = aa.build(make_page(DEFAULTS, VARIANTS))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            t1 = datetime(2030, 1, 1, tzinfo=timezone.utc)
            self.assertTrue(aa.write_outputs(result, out, now=t1))
            self.assertFalse(aa.write_outputs(result, out, now=datetime(2031, 1, 1, tzinfo=timezone.utc)))
            self.assertEqual((out / "link.txt").read_text().strip(), result.url)
            self.assertEqual(json.loads((out / "latest.json").read_text())["changed_at"], "2030-01-01 00:00 UTC")
            page = (out / "index.html").read_text()
            self.assertIn("location.replace", page)
            self.assertIn("Claude Opus 5.5 (Xhigh, Default Fallback)", page)

    def test_changed_link_rewrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            aa.write_outputs(aa.build(make_page(DEFAULTS, VARIANTS)), out)
            self.assertTrue(aa.write_outputs(aa.build(make_page(DEFAULTS, VARIANTS), "drop"), out))


@unittest.skipUnless(os.environ.get("AA_LIVE"), "set AA_LIVE=1 to hit the real site")
class Live(unittest.TestCase):
    def test_real_page(self):
        r = aa.build(aa.fetch())
        self.assertGreater(len(r.slugs), 5)
        self.assertGreater(len(r.swapped), 0)


if __name__ == "__main__":
    unittest.main()
