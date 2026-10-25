import json
import os
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aa_common as common  # noqa: E402
import aa_costs as costs  # noqa: E402

TODAY = date(2026, 10, 24)


def fresh(usd, fetched, **speed):
    """A cache entry with every field present (absent fields make an entry stale)."""
    return {"usd": usd, "ttfat_s": None, "tps": None, "e2e_s": None, **speed, "fetched": fetched}


def compare_page(models, chunk_size=97, speeds=None):
    """A comparison page: a small summary list that mentions each slug, then the full records.
    ``speeds`` maps a slug to (time to first answer token, output speed, end-to-end time)."""
    summary = [{"slug": s, "name": s, "intelligenceIndex": 50} for s in models]
    records = []
    for slug, total in models.items():
        rec = {"slug": slug, "name": slug, **{f"field{i}": i for i in range(25)}}
        if total != "missing":
            rec["intelligenceIndexCost"] = {"total": total, "input": 1.0}
        if speeds and slug in speeds:
            ttfat, tps, e2e = speeds[slug]
            rec["timeToFirstAnswerToken"] = {"input": ttfat, "reasoning": 0, "total": ttfat}
            rec["timescaleData"] = {"medianOutputSpeed": tps, "medianTimeToFirstChunk": ttfat}
            rec["endToEndResponseTime"] = {"input": ttfat, "reasoning": 0, "answer": e2e - ttfat, "total": e2e}
        records.append(rec)
    compact = {"separators": (",", ":")}  # the site's payload is compact JSON
    flight = '40:["$","x",null,{"pickerModels":' + json.dumps(summary, **compact) + ',"models":' + json.dumps(records, **compact) + "}]\n"
    chunks = [flight[i : i + chunk_size] for i in range(0, len(flight), chunk_size)]
    pushes = "".join(f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks)
    return "<html>" + pushes + "</html>"


class FakeSite:
    """Serves comparison pages for known costs and records every request."""

    def __init__(self, known, fail=()):
        self.known, self.fail, self.urls = known, set(fail), []

    def __call__(self, url):
        self.urls.append(url)
        a, b = url.removeprefix("https://artificialanalysis.ai/models/comparisons/").split("-vs-")
        if a in self.fail or b in self.fail:
            raise OSError("site is down")
        return compare_page({s: self.known.get(s, "missing") for s in dict.fromkeys((a, b))})


class Parse(unittest.TestCase):
    def test_finds_the_full_record_not_the_summary_mention(self):
        found = costs.full_run_costs(compare_page({"a": 8708.0, "b": 5324.5}), ["a", "b"])
        self.assertEqual(found, {"a": 8708.0, "b": 5324.5})

    def test_unusable_costs_are_skipped(self):
        page = compare_page({"ok": 10.0, "none": "missing", "neg": -3.0, "zero": 0, "flag": True, "text": "9"})
        self.assertEqual(costs.full_run_costs(page, ["ok", "none", "neg", "zero", "flag", "text", "absent"]), {"ok": 10.0})

    def test_speed_facts_are_read_and_a_variant_without_cost_is_still_kept(self):
        page = compare_page({"a": 4057.0, "b": "missing"}, speeds={"a": (21.5, 80.6, 27.7), "b": (26.1, 111.1, 30.6)})
        facts = costs.variant_facts(page, ["a", "b"])
        self.assertEqual(facts["a"], {"usd": 4057.0, "ttfat_s": 21.5, "tps": 80.6, "e2e_s": 27.7})
        self.assertEqual(facts["b"], {"usd": None, "ttfat_s": 26.1, "tps": 111.1, "e2e_s": 30.6})
        self.assertEqual(costs.full_run_costs(page, ["a", "b"]), {"a": 4057.0})

    def test_speed_by_slug_needs_an_end_to_end_time(self):
        cache = {"a": fresh(1.0, "2026-10-24", ttfat_s=21.5, tps=80.6, e2e_s=27.7), "b": fresh(2.0, "2026-10-24")}
        self.assertEqual(costs.speed_by_slug(cache), {"a": {"ttfat_s": 21.5, "tps": 80.6, "e2e_s": 27.7}})
        self.assertEqual(costs.usd_by_slug({"x": fresh(None, "2026-10-24")}), {})

    def test_a_page_without_data_is_a_parse_error(self):
        with self.assertRaises(common.ParseError):
            costs.full_run_costs("<html></html>", ["a"])


class Refresh(unittest.TestCase):
    def test_only_stale_or_missing_slugs_are_fetched_two_per_request(self):
        site = FakeSite({"a": 100.0, "b": 200.0, "c": 300.0, "d": 400.0})
        cache = {"d": fresh(111.0, "2026-10-23")}
        new, problems = costs.refresh(cache, ["a", "b", "c", "d"], TODAY, site)
        self.assertEqual(problems, [])
        self.assertEqual(len(site.urls), 2)  # a+b, then c on its own with a partner
        self.assertEqual({s: v["usd"] for s, v in new.items()}, {"a": 100.0, "b": 200.0, "c": 300.0, "d": 111.0})
        self.assertEqual(new["a"]["fetched"], "2026-10-24")
        self.assertEqual(new["d"], fresh(111.0, "2026-10-23"))  # untouched, and its date is not bumped
        requested = {s for u in site.urls for s in u.rsplit("/", 1)[1].split("-vs-")}
        self.assertNotIn("d", requested)  # the fresh entry is never requested, not even as a partner

    def test_nothing_to_do_makes_no_requests(self):
        site = FakeSite({})
        cache = {"a": fresh(1.0, "2026-10-24")}
        new, problems = costs.refresh(cache, ["a"], TODAY, site)
        self.assertEqual((new, problems, site.urls), (cache, [], []))

    def test_an_entry_from_before_the_speed_fields_is_fetched_again_once(self):
        site = FakeSite({"a": 5.0, "b": 6.0})
        new, _ = costs.refresh({"a": {"usd": 1.0, "fetched": "2026-10-24"}}, ["a"], TODAY, site)
        self.assertEqual(len(site.urls), 1)
        self.assertEqual(set(new["a"]), set(costs.FIELDS) | {"fetched"})
        costs.refresh(new, ["a"], TODAY, site)
        self.assertEqual(len(site.urls), 1)  # now complete, so not again

    def test_an_entry_is_stale_after_a_week(self):
        site = FakeSite({"a": 5.0, "b": 6.0})
        young = {"a": fresh(1.0, "2026-10-18")}  # 6 days
        old = {"b": fresh(1.0, "2026-10-17")}  # 7 days
        costs.refresh(young, ["a"], TODAY, site)
        self.assertEqual(site.urls, [])
        new, _ = costs.refresh(old, ["b"], TODAY, site)
        self.assertEqual(len(site.urls), 1)
        self.assertEqual(new["b"]["usd"], 6.0)

    def test_an_odd_one_out_is_paired_with_a_different_model(self):
        site = FakeSite({"a": 1.0, "b": 2.0, "c": 3.0})
        costs.refresh({}, ["a", "b", "c"], TODAY, site)
        last = site.urls[-1].removeprefix(costs.COMPARE_URL.split("{a}")[0])
        a, b = last.split("-vs-")
        self.assertEqual(a, "c")
        self.assertNotEqual(b, "c")
        # alone, it has nobody to pair with and goes against itself
        alone = FakeSite({"z": 9.0})
        new, _ = costs.refresh({}, ["z"], TODAY, alone)
        self.assertEqual(new["z"]["usd"], 9.0)

    def test_a_failed_request_keeps_the_old_value_and_reports_it(self):
        site = FakeSite({"a": 1.0, "b": 2.0}, fail={"a"})
        old = {"a": {"usd": 42.0, "fetched": "2026-10-01"}}
        new, problems = costs.refresh(old, ["a", "b"], TODAY, site)
        self.assertEqual(new["a"], old["a"])
        self.assertNotIn("b", new)  # b shared the failed request
        self.assertEqual(len(problems), 1)
        self.assertIn("site is down", problems[0])

    def test_a_slug_the_page_lacks_is_reported_not_stored(self):
        site = FakeSite({"a": 1.0})  # "ghost" has no cost on its page
        new, problems = costs.refresh({}, ["a", "ghost"], TODAY, site)
        self.assertIn("a", new)
        self.assertNotIn("ghost", new)
        self.assertTrue(any("ghost" in p for p in problems))

    def test_duplicates_in_the_wanted_list_are_fetched_once(self):
        site = FakeSite({"a": 1.0, "b": 2.0})
        costs.refresh({}, ["a", "b", "a", "b"], TODAY, site)
        self.assertEqual(len(site.urls), 1)

    def test_the_original_cache_is_not_mutated(self):
        cache = {"a": {"usd": 1.0, "fetched": "2026-10-01"}}
        snapshot = json.loads(json.dumps(cache))
        costs.refresh(cache, ["a"], TODAY, FakeSite({"a": 2.0}))
        self.assertEqual(cache, snapshot)


class Cache(unittest.TestCase):
    def test_round_trip_and_idempotent_save(self):
        cache = {"b": {"usd": 2.0, "fetched": "2026-10-24"}, "a": {"usd": 1.5, "fetched": "2026-10-23"}}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.json"
            self.assertTrue(costs.save(cache, path))
            self.assertFalse(costs.save(cache, path))
            self.assertEqual(costs.load(path), cache)
            self.assertEqual(list(json.loads(path.read_text())["runs"]), ["a", "b"])  # sorted, so diffs stay small
            self.assertEqual(costs.usd_by_slug(cache), {"b": 2.0, "a": 1.5})

    def test_bad_files_and_bad_entries_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "costs.json"
            self.assertEqual(costs.load(path), {})  # missing
            path.write_text("not json")
            self.assertEqual(costs.load(path), {})
            path.write_text(json.dumps({"runs": {"ok": {"usd": 1, "fetched": "2026-10-24"}, "bad": {"usd": "x", "fetched": "2026-10-24"},
                                                 "date": {"usd": 1, "fetched": "yesterday"}, "shape": 5}}))
            self.assertEqual(set(costs.load(path)), {"ok"})


class Plans(unittest.TestCase):
    def test_multipliers_are_the_measured_ones(self):
        self.assertEqual(costs.PLANS["Anthropic"], ("Claude Max 20x", 45.3))
        self.assertEqual(costs.PLANS["OpenAI"], ("ChatGPT", 10.25))
        self.assertEqual(costs.PLANS["SpaceXAI"], ("SuperGrok + X Premium+", 80.0))
        self.assertNotIn("Meta", costs.PLANS)  # Meta's multiplier was derived from allowances, not measured


@unittest.skipUnless(os.environ.get("AA_LIVE"), "set AA_LIVE=1 to hit the real site")
class Live(unittest.TestCase):
    def test_real_comparison_page(self):
        new, problems = costs.refresh({}, ["claude-opus-5-5-xhigh", "gpt-6-astra-xhigh"], TODAY)
        self.assertEqual(problems, [])
        self.assertGreater(new["claude-opus-5-5-xhigh"]["usd"], 100)


if __name__ == "__main__":
    unittest.main()
