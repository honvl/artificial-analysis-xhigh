"""What one full run of the Intelligence Index costs, at API prices and on a subscription.

Artificial Analysis reports the cost of a full run of its index for every model variant. It
sits in the data of each model's comparison page, two models per page. Costs only move when
Artificial Analysis re-runs a model, so they are cached in ``costs.json`` and each entry is
refreshed once it is a week old. A failed refresh keeps the old value.

The subscription figure is Artificial Analysis's cost divided by how much more usage a plan
gives than API list prices. The multipliers below were measured by Paweł Huryn by burning real
plans (https://x.com/PawelHuryn/status/2107780775558979757). They are one person's estimates,
measured on one model per vendor: SemiAnalysis finds that what a $200 Claude plan buys depends
heavily on the model and workload (https://x.com/SemiAnalysis_/status/2107252022424531076).
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Callable

from aa_common import ParseError, fetch, flight_payload, read_json, write_text

COMPARE_URL = "https://artificialanalysis.ai/models/comparisons/{a}-vs-{b}"
MAX_AGE_DAYS = 7

# Lab (as Artificial Analysis names it) -> (plan, how many times more usage than API list prices it
# gives). Only labs with a multiplier measured by burning the plan are listed. Left out on purpose:
# Meta, whose only measurements are for Muse Spark 1.3, taken before Muse Wonder launched, disagree
# with each other (about 11x to 51x for the $50 plan) and do not carry over to Muse Wonder Full,
# which burns quota far faster and has no API price; and Google, for which nothing has been
# published. Adding a lab is one line here once someone measures it.
PLANS: dict[str, tuple[str, float]] = {
    "Anthropic": ("Claude Max 20x", 45.3),
    "OpenAI": ("ChatGPT", 10.25),
    "SpaceXAI": ("SuperGrok + X Premium+", 80.0),
}

Fetcher = Callable[[str], str]


def _model_object(payload: str, slug: str) -> dict | None:
    """The full record for ``slug`` in a comparison page: the dict that has it as its slug and
    many other fields (the page also mentions the slug in small summary lists)."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r'"slug":"%s"' % re.escape(slug), payload):
        for start in range(m.start(), max(0, m.start() - 3000), -1):
            if payload[start] != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(payload, start)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("slug") == slug and len(obj) > 20:
                return obj
    return None


# What the cache keeps per variant, each a positive number or None when the site has none:
# usd      one full index run at API prices
# ttfat_s  median time to the first answer token, seconds (includes thinking)
# tps      median output speed, tokens per second
# e2e_s    end-to-end response time: first answer token plus a 500-token answer, seconds
FIELDS = ("usd", "ttfat_s", "tps", "e2e_s")


def _num(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0:
        return float(value)
    return None


def _total(obj: dict, key: str) -> float | None:
    v = obj.get(key)
    return _num(v.get("total")) if isinstance(v, dict) else None


def variant_facts(page: str, slugs: list[str]) -> dict[str, dict[str, float | None]]:
    """Cost and speed facts for each of ``slugs`` that the page carries any of."""
    payload = flight_payload(page)
    found: dict[str, dict[str, float | None]] = {}
    for slug in slugs:
        obj = _model_object(payload, slug)
        if obj is None:
            continue
        facts = {
            "usd": _total(obj, "intelligenceIndexCost"),
            "ttfat_s": _total(obj, "timeToFirstAnswerToken"),
            "tps": _num((obj.get("timescaleData") or {}).get("medianOutputSpeed")),
            "e2e_s": _total(obj, "endToEndResponseTime"),
        }
        if any(v is not None for v in facts.values()):
            found[slug] = facts
    return found


def full_run_costs(page: str, slugs: list[str]) -> dict[str, float]:
    """The cost in USD of one full index run for each of ``slugs`` that the page carries."""
    return {s: f["usd"] for s, f in variant_facts(page, slugs).items() if f["usd"] is not None}


def load(path: Path) -> dict[str, dict]:
    """The cache: ``{slug: {"usd": ..., "ttfat_s": ..., "tps": ..., "e2e_s": ..., "fetched": "YYYY-MM-DD"}}``."""
    data = read_json(path) or {}
    runs = data.get("runs")
    return {k: v for k, v in runs.items() if _valid_entry(v)} if isinstance(runs, dict) else {}


def _valid_entry(v: object) -> bool:
    if not (isinstance(v, dict) and isinstance(v.get("fetched"), str)):
        return False
    if any(k in v and v[k] is not None and _num(v[k]) is None for k in FIELDS):
        return False
    try:
        date.fromisoformat(v["fetched"])
    except ValueError:
        return False
    return True


def save(cache: dict[str, dict], path: Path) -> bool:
    """Write the cache if it changed. True if the file changed."""
    return write_text(path, json.dumps({"runs": dict(sorted(cache.items()))}, indent=2) + "\n")


def usd_by_slug(cache: dict[str, dict]) -> dict[str, float]:
    return {slug: v["usd"] for slug, v in cache.items() if v.get("usd") is not None}


def speed_by_slug(cache: dict[str, dict]) -> dict[str, dict[str, float | None]]:
    """``{slug: {"ttfat_s", "tps", "e2e_s"}}`` for variants with an end-to-end time."""
    return {
        slug: {k: v.get(k) for k in ("ttfat_s", "tps", "e2e_s")}
        for slug, v in cache.items()
        if v.get("e2e_s") is not None
    }


def _is_stale(entry: dict | None, today: date) -> bool:
    """Missing, a week old, or written before a field existed (so it is fetched again once)."""
    if entry is None or any(k not in entry for k in FIELDS):
        return True
    return (today - date.fromisoformat(entry["fetched"])).days >= MAX_AGE_DAYS


def _pairs(slugs: list[str], anchors: list[str]) -> list[tuple[str, str]]:
    """Two slugs per request. An odd one out is paired with another known model, and only
    with itself when there is no other (a page compares two models, not one)."""
    pairs = []
    for i in range(0, len(slugs), 2):
        a = slugs[i]
        b = slugs[i + 1] if i + 1 < len(slugs) else next((s for s in anchors if s != a), a)
        pairs.append((a, b))
    return pairs


def refresh(
    cache: dict[str, dict], wanted: list[str], today: date, fetcher: Fetcher = fetch
) -> tuple[dict[str, dict], list[str]]:
    """Bring the cache up to date for ``wanted``.

    Returns the new cache and a list of problems, one per failed request or slug the page did
    not carry. Entries that could not be refreshed keep their old value.
    """
    new = {k: dict(v) for k, v in cache.items()}
    anchors = list(dict.fromkeys(wanted))
    stale = [s for s in anchors if _is_stale(cache.get(s), today)]
    problems: list[str] = []
    for a, b in _pairs(stale, anchors):
        try:
            found = variant_facts(fetcher(COMPARE_URL.format(a=a, b=b)), [a, b])
        except (ParseError, OSError) as exc:
            problems.append(f"{a} vs {b}: {exc}")
            continue
        for slug in dict.fromkeys((a, b)):
            if slug not in stale:
                continue  # only here as a partner
            if slug in found:
                facts = {k: (round(v, 2) if v is not None else None) for k, v in found[slug].items()}
                new[slug] = {**facts, "fetched": today.isoformat()}
            else:
                problems.append(f"{slug}: no cost or speed data on its comparison page")
    return new, problems
