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
# gives). Only labs with a multiplier that was measured by burning the plan are listed: Meta's
# figure was derived from its stated allowances rather than measured, so it is left out.
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


def full_run_costs(page: str, slugs: list[str]) -> dict[str, float]:
    """The cost in USD of one full index run for each of ``slugs`` that the page carries."""
    payload = flight_payload(page)
    found: dict[str, float] = {}
    for slug in slugs:
        obj = _model_object(payload, slug)
        cost = (obj or {}).get("intelligenceIndexCost")
        total = cost.get("total") if isinstance(cost, dict) else None
        if isinstance(total, (int, float)) and not isinstance(total, bool) and math.isfinite(total) and total > 0:
            found[slug] = float(total)
    return found


def load(path: Path) -> dict[str, dict]:
    """The cache: ``{slug: {"usd": float, "fetched": "YYYY-MM-DD"}}``."""
    data = read_json(path) or {}
    runs = data.get("runs")
    return {k: v for k, v in runs.items() if _valid_entry(v)} if isinstance(runs, dict) else {}


def _valid_entry(v: object) -> bool:
    if not (isinstance(v, dict) and isinstance(v.get("usd"), (int, float)) and isinstance(v.get("fetched"), str)):
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
    return {slug: v["usd"] for slug, v in cache.items()}


def _is_stale(entry: dict | None, today: date) -> bool:
    return entry is None or (today - date.fromisoformat(entry["fetched"])).days >= MAX_AGE_DAYS


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
            found = full_run_costs(fetcher(COMPARE_URL.format(a=a, b=b)), [a, b])
        except (ParseError, OSError) as exc:
            problems.append(f"{a} vs {b}: {exc}")
            continue
        for slug in dict.fromkeys((a, b)):
            if slug not in stale:
                continue  # only here as a partner
            if slug in found:
                new[slug] = {"usd": round(found[slug], 2), "fetched": today.isoformat()}
            else:
                problems.append(f"{slug}: no full-run cost on its comparison page")
    return new, problems
