#!/usr/bin/env python3
"""Build Artificial Analysis /models links at a fixed effort level (xhigh, high or medium).

The site renders its default chart selection (27 models) server-side and lets you
override it with ``?models=slug1,slug2,...``. Every effort level of a model is its
own slug (``claude-opus-5-5`` is Max, ``claude-opus-5-5-xhigh`` is Xhigh), so for a
target level the swap is: take the default selection, and for each model labelled
above that level substitute the sibling from the same release whose label is the
target level. Models at or below the target, and models with no effort label, are
left alone.

Standard library only.
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import re
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import aa_charts
import aa_coding
import aa_costs
from aa_common import (
    LEVELS,
    ORDER,
    SOURCE,
    Label,
    ParseError,
    display_name,
    fetch,
    flight_payload,
    level_href,
    parse_label,
    read_json,
    write_text,
)


# --- reading the page --------------------------------------------------------


def default_models(payload: str) -> list[dict]:
    """The models the site charts by default, in the site's own order."""
    key = '"initialModels":'
    i = payload.find(key + "[")
    if i == -1:
        raise ParseError('"initialModels" not found in page data')
    models, _ = json.JSONDecoder().raw_decode(payload, i + len(key))
    chosen = [m for m in models if m.get("chartDefaultSelected")]
    if not chosen:
        raise ParseError("no models flagged chartDefaultSelected")
    for m in chosen:
        if "slug" not in m or "name" not in m:
            raise ParseError(f"default model missing slug/name: {sorted(m)[:8]}")
    return chosen


def variant_index(payload: str) -> list[dict]:
    """Every selectable model/variant, as ``{slug, name, releaseSlug}``."""
    decoder = json.JSONDecoder()
    best: list[dict] = []
    for m in re.finditer(r'"models":(?=\[\{"slug":)', payload):
        try:
            arr, _ = decoder.raw_decode(payload, m.end())
        except json.JSONDecodeError:
            continue
        if all(isinstance(a, dict) and {"slug", "name", "releaseSlug"} <= a.keys() for a in arr):
            if len(arr) > len(best):
                best = arr
    if not best:
        raise ParseError("model selector list (slug/name/releaseSlug) not found in page data")
    return best


# --- the swap ----------------------------------------------------------------


@dataclass(frozen=True)
class Swap:
    slug: str
    name: str
    to_slug: str
    to_name: str


@dataclass(frozen=True)
class Kept:
    slug: str
    name: str


@dataclass
class Result:
    level: str
    slugs: list[str]
    swapped: list[Swap]
    unmatched: list[Kept]  # models above the level that have no sibling at it

    @property
    def url(self) -> str:
        return f"{SOURCE}?models={quote(','.join(self.slugs), safe='')}"


def above(level: str) -> list[str]:
    return list(ORDER[ORDER.index(level) + 1 :])


def _or(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " or " + items[-1]


def select(defaults: list[dict], variants: list[dict], level: str = "xhigh", unmatched: str = "keep") -> Result:
    """Swap everything above ``level`` down to ``level`` across the default selection.

    ``unmatched`` decides what happens to a model above the level that has no
    sibling at it: "keep" leaves it as is, "drop" removes it from the chart.
    """
    if level not in ORDER[:-1]:
        raise ValueError(f"level must be one of {ORDER[:-1]}, not {level!r}")
    if unmatched not in ("keep", "drop"):
        raise ValueError(f"unmatched must be 'keep' or 'drop', not {unmatched!r}")
    rank = ORDER.index(level)
    by_slug = {v["slug"]: v for v in variants}
    by_release: dict[str, list[dict]] = collections.defaultdict(list)
    for v in variants:
        by_release[v["releaseSlug"]].append(v)

    slugs: list[str] = []
    swapped: list[Swap] = []
    kept: list[Kept] = []
    for m in defaults:
        slug, name = m["slug"], m["name"]
        label = parse_label(name)
        if label.effort is None or ORDER.index(label.effort) <= rank:
            slugs.append(slug)
            continue
        release = (by_slug.get(slug) or {}).get("releaseSlug") or (m.get("release") or {}).get("slug")
        want = Label(level, label.qualifier)
        target = next((v for v in by_release.get(release, []) if parse_label(v["name"]) == want), None)
        if target:
            slugs.append(target["slug"])
            swapped.append(Swap(slug, name, target["slug"], target["name"]))
        else:
            kept.append(Kept(slug, name))
            if unmatched == "keep":
                slugs.append(slug)

    slugs = list(dict.fromkeys(slugs))  # the default may already include both variants
    unknown = [s for s in slugs if s not in by_slug]
    if unknown:
        raise ParseError(f"slugs missing from the model selector list: {unknown}")
    if not slugs:
        raise ParseError("selection came out empty")
    return Result(level, slugs, swapped, kept)


def build(page: str, level: str = "xhigh", unmatched: str = "keep") -> Result:
    payload = flight_payload(page)
    return select(default_models(payload), variant_index(payload), level, unmatched)


# --- output ------------------------------------------------------------------


def explain(result: Result) -> str:
    lines = [f"{result.level}: {len(result.slugs)} models, {len(result.swapped)} swapped"]
    for s in result.swapped:
        lines.append(f"  swapped  {display_name(s.name)}  ->  {display_name(s.to_name)}")
    for k in result.unmatched:
        lines.append(f"  no {result.level}  {display_name(k.name)}  (left as is)")
    return "\n".join(lines)


def render_page(result: Result, changed_at: str, prefix: str = "") -> str:
    url = result.url
    level = result.level
    rows = "".join(
        f"<tr><td>{html.escape(display_name(s.name))}</td><td>{html.escape(display_name(s.to_name))}</td></tr>" for s in result.swapped
    )
    rows += "".join(
        f"<tr><td>{html.escape(display_name(k.name))}</td><td class=\"muted\">no {level} variant, unchanged</td></tr>"
        for k in result.unmatched
    )
    views = " · ".join(
        f"<strong>{lv}</strong>" if lv == level else f'<a href="{level_href(lv, prefix, "#info")}">{lv}</a>'
        for lv in LEVELS
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Artificial Analysis, {level} view</title>
<link rel="canonical" href="{html.escape(url)}">
<noscript><meta http-equiv="refresh" content="0; url={html.escape(url)}"></noscript>
<script>if (location.hash !== "#info") location.replace({json.dumps(url)});</script>
<style>
  :root {{ color-scheme: light; }}
  body {{ background: #fff; color: #0b0b0b; font: 16px/1.5 system-ui, sans-serif; max-width: 42rem; margin: 3rem auto; padding: 0 1rem; }}
  a {{ color: #2a78d6; }}
  td {{ padding: .25rem .75rem .25rem 0; vertical-align: top; }}
  .muted {{ color: #52514e; }}
  code {{ word-break: break-all; }}
</style>
</head>
<body>
<h1>Artificial Analysis, {level} view</h1>
<p><a href="{html.escape(url)}">Open the chart</a> with every {_or(above(level))} variant swapped for {level}.
Link last changed {html.escape(changed_at)}.</p>
<p class="muted">Views: {views} · <a href="{prefix}summary.html">summary charts</a> · <a href="{prefix}bookmark.html">bookmark</a></p>
<table>{rows}</table>
<p class="muted"><code>{html.escape(url)}</code></p>
</body>
</html>
"""


def level_dir(out: Path, level: str) -> Path:
    return out if level == LEVELS[0] else out / level


def write_level(result: Result, out: Path, now: datetime | None = None) -> bool:
    """Write index.html, link.txt and latest.json for one level. False if nothing changed.

    ``changed_at`` only moves when the link itself changes, so the output is stable
    between runs and the scheduled job commits only real changes.
    """
    d = level_dir(out, result.level)
    prefix = "" if d == out else "../"
    old = read_json(d / "latest.json")
    if old and old.get("url") == result.url and old.get("changed_at"):
        changed_at = old["changed_at"]
    else:
        changed_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    state = {
        "level": result.level,
        "url": result.url,
        "changed_at": changed_at,
        "source": SOURCE,
        "swapped": [{"from": s.name, "to": s.to_name} for s in result.swapped],
        "unmatched": [k.name for k in result.unmatched],
    }
    files = {
        "index.html": render_page(result, changed_at, prefix),
        "link.txt": result.url + "\n",
        "latest.json": json.dumps(state, indent=2) + "\n",
    }
    return any([write_text(d / name, text) for name, text in files.items()])


# --- cli ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument(
        "--level",
        choices=ORDER[:-1],
        default=LEVELS[0],
        help="effort level to print the link for (default: %(default)s)",
    )
    ap.add_argument(
        "--write",
        metavar="DIR",
        type=Path,
        help=f"regenerate the whole Pages site in DIR: links for {', '.join(LEVELS)} and the summary charts",
    )
    ap.add_argument("--explain", action="store_true", help="show which models were swapped")
    ap.add_argument("--open", action="store_true", help="open the link in your browser")
    ap.add_argument(
        "--unmatched",
        choices=("keep", "drop"),
        default="keep",
        help="models with no variant at the level: keep them as they are (default) or drop them from the chart",
    )
    ap.add_argument("--input", metavar="FILE", type=Path, help="parse a saved copy of the page instead of fetching")
    args = ap.parse_args(argv)

    try:
        page = args.input.read_text(encoding="utf-8") if args.input else fetch()
        payload = flight_payload(page)
        defaults, variants = default_models(payload), variant_index(payload)
        wanted = list(dict.fromkeys([*LEVELS, args.level])) if args.write else [args.level]
        results = {lv: select(defaults, variants, lv, args.unmatched) for lv in wanted}
    except (ParseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    status = 0
    if args.explain:
        for r in results.values():
            print(explain(r), file=sys.stderr)
    if args.write:
        for lv in LEVELS:
            changed = write_level(results[lv], args.write)
            print(f"{'updated' if changed else 'unchanged'}: {lv}", file=sys.stderr)
        # The charts come from a separate page, so a failure there must not hold back the links.
        try:
            models = aa_charts.leaderboard_models(fetch(aa_charts.LEADERBOARD_URL))
            boards = aa_charts.leaderboards(models, variants)
            pairs = aa_charts.step_down_pairs(models, variants)
            paired = [e.slug for p in pairs for e in (p.best, p.nxt)]  # need speed data, whatever the lab
            wanted = aa_charts.wanted_slugs({**boards, "pairs": [e for p in pairs for e in (p.best, p.nxt)]})
            costs_path = args.write / "costs.json"
            cache, problems = aa_costs.refresh(
                aa_costs.load(costs_path), list(dict.fromkeys(wanted + paired)), datetime.now(timezone.utc).date()
            )
            aa_costs.save(cache, costs_path)
            for problem in problems:
                print(f"warning: full-run cost: {problem}", file=sys.stderr)
            runs = aa_costs.usd_by_slug(cache)
            speeds = aa_costs.speed_by_slug(cache)
            # Coding agents come from a third page; a failure here must not hold back
            # the Intelligence charts above, and a missing coding section must not fail links.
            coding_state = None
            coding_changed_at = None
            coding_section = None
            try:
                all_coding = aa_coding.coding_agents(fetch(aa_coding.CODING_URL))
                picked = aa_coding.select_for_summary(all_coding)
                coding_state = aa_coding.coding_state(picked)
                old_charts = read_json(args.write / "charts.json") or {}
                if old_charts.get("coding_agents") == coding_state and old_charts.get("coding_changed_at"):
                    coding_changed_at = old_charts["coding_changed_at"]
                else:
                    coding_changed_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
                coding_section = aa_coding.render_coding_section(picked, coding_changed_at)
            except (ParseError, OSError) as exc:
                print(f"warning: coding agents: {exc}", file=sys.stderr)
            changed = aa_charts.write_charts(
                boards,
                args.write,
                runs=runs,
                pairs=pairs,
                speeds=speeds,
                coding_agents=coding_state,
                coding_changed_at=coding_changed_at,
                coding_section=coding_section,
            )
            print(f"{'updated' if changed else 'unchanged'}: summary charts", file=sys.stderr)
            if any(slug not in runs for slug in wanted):  # a cost line is missing, and nothing cached to fall back on
                print("error: summary charts: no full-run cost for some models, so a cost line is missing", file=sys.stderr)
                status = 1
        except (ParseError, OSError) as exc:
            print(f"error: summary charts: {exc}", file=sys.stderr)
            status = 1
    print(results[args.level].url)
    if args.open:
        webbrowser.open(results[args.level].url)
    return status


if __name__ == "__main__":
    sys.exit(main())
