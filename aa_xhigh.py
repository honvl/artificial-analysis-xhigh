#!/usr/bin/env python3
"""Build an Artificial Analysis /models link with every "max" variant swapped for "xhigh".

The site renders its default chart selection (27 models) server-side and lets you
override it with ``?models=slug1,slug2,...``. Every effort level of a model is its
own slug (``claude-opus-5-5`` is Max, ``claude-opus-5-5-xhigh`` is Xhigh), so the
swap is: take the default selection, and for each model labelled "Max" substitute
the sibling from the same release whose label is "Xhigh".

Standard library only.
"""
from __future__ import annotations

import argparse
import collections
import html
import json
import re
import sys
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

SOURCE = "https://artificialanalysis.ai/models"
USER_AGENT = "Mozilla/5.0 (compatible; artificial-analysis-xhigh/1.0; +https://github.com)"
EFFORTS = {"max", "xhigh", "high", "medium", "low", "minimal"}


class ParseError(RuntimeError):
    """The page no longer looks the way this script expects."""


# --- fetching and decoding ---------------------------------------------------


def fetch(url: str = SOURCE, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8")


def flight_payload(page: str) -> str:
    """Join the Next.js flight chunks (``self.__next_f.push([1,"..."])``) into one string.

    Values can span chunk boundaries, so the chunks are concatenated before any
    searching is done.
    """
    decoder = json.JSONDecoder()
    marker = "self.__next_f.push("
    parts: list[str] = []
    pos = 0
    while (i := page.find(marker, pos)) != -1:
        start = i + len(marker)
        try:
            chunk, pos = decoder.raw_decode(page, start)
        except json.JSONDecodeError:
            pos = start
            continue
        if isinstance(chunk, list) and len(chunk) == 2 and chunk[0] == 1 and isinstance(chunk[1], str):
            parts.append(chunk[1])
    if not parts:
        raise ParseError("no Next.js flight data found in the page")
    return "".join(parts)


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


# --- effort labels -----------------------------------------------------------


@dataclass(frozen=True)
class Label:
    effort: str | None
    qualifier: tuple[str, ...] = ()


def parse_label(name: str) -> Label:
    """Pull the effort level out of a trailing parenthetical.

    "Claude Opus 5.5 (Max, Default Fallback)" -> Label("max", ("default fallback",))
    "GPT-6 Sol (Daybreak Blue, max)"          -> Label("max", ("daybreak blue",))
    "Qwen3.8 Max (0902)"                       -> Label(None)
    """
    m = re.search(r"\(([^()]*)\)\s*$", name)
    if not m:
        return Label(None)
    tokens = [t.strip().lower() for t in m.group(1).split(",")]
    effort = next((t for t in tokens if t in EFFORTS), None)
    if effort is None:
        return Label(None)
    return Label(effort, tuple(t for t in tokens if t != effort))


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
    slugs: list[str]
    swapped: list[Swap]
    no_xhigh: list[Kept]  # "max" models with no xhigh sibling

    @property
    def url(self) -> str:
        return f"{SOURCE}?models={quote(','.join(self.slugs), safe='')}"


def select(defaults: list[dict], variants: list[dict], unmatched: str = "keep") -> Result:
    """Swap max -> xhigh across the default selection.

    ``unmatched`` decides what happens to a max model that has no xhigh sibling:
    "keep" leaves it on max, "drop" removes it from the chart.
    """
    if unmatched not in ("keep", "drop"):
        raise ValueError(f"unmatched must be 'keep' or 'drop', not {unmatched!r}")
    by_slug = {v["slug"]: v for v in variants}
    by_release: dict[str, list[dict]] = collections.defaultdict(list)
    for v in variants:
        by_release[v["releaseSlug"]].append(v)

    slugs: list[str] = []
    swapped: list[Swap] = []
    no_xhigh: list[Kept] = []
    for m in defaults:
        slug, name = m["slug"], m["name"]
        label = parse_label(name)
        if label.effort != "max":
            slugs.append(slug)
            continue
        release = (by_slug.get(slug) or {}).get("releaseSlug") or (m.get("release") or {}).get("slug")
        want = Label("xhigh", label.qualifier)
        target = next((v for v in by_release.get(release, []) if parse_label(v["name"]) == want), None)
        if target:
            slugs.append(target["slug"])
            swapped.append(Swap(slug, name, target["slug"], target["name"]))
        else:
            no_xhigh.append(Kept(slug, name))
            if unmatched == "keep":
                slugs.append(slug)

    slugs = list(dict.fromkeys(slugs))  # the default may already include both variants
    unknown = [s for s in slugs if s not in by_slug]
    if unknown:
        raise ParseError(f"slugs missing from the model selector list: {unknown}")
    if not slugs:
        raise ParseError("selection came out empty")
    return Result(slugs, swapped, no_xhigh)


def build(page: str, unmatched: str = "keep") -> Result:
    payload = flight_payload(page)
    return select(default_models(payload), variant_index(payload), unmatched)


# --- output ------------------------------------------------------------------


def explain(result: Result) -> str:
    lines = [f"{len(result.slugs)} models, {len(result.swapped)} swapped to xhigh"]
    for s in result.swapped:
        lines.append(f"  swapped  {s.name}  ->  {s.to_name}")
    for k in result.no_xhigh:
        lines.append(f"  no xhigh {k.name}  (left as is)")
    return "\n".join(lines)


def render_page(result: Result, changed_at: str) -> str:
    url = result.url
    rows = "".join(
        f"<tr><td>{html.escape(s.name)}</td><td>{html.escape(s.to_name)}</td></tr>" for s in result.swapped
    )
    rows += "".join(
        f"<tr><td>{html.escape(k.name)}</td><td class=\"muted\">no xhigh variant, unchanged</td></tr>"
        for k in result.no_xhigh
    )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Artificial Analysis, xhigh view</title>
<link rel="canonical" href="{html.escape(url)}">
<noscript><meta http-equiv="refresh" content="0; url={html.escape(url)}"></noscript>
<script>if (location.hash !== "#info") location.replace({json.dumps(url)});</script>
<style>
  :root {{ color-scheme: light dark; }}
  body {{ font: 16px/1.5 system-ui, sans-serif; max-width: 42rem; margin: 3rem auto; padding: 0 1rem; }}
  td {{ padding: .25rem .75rem .25rem 0; vertical-align: top; }}
  .muted {{ opacity: .6; }}
  code {{ word-break: break-all; }}
</style>
</head>
<body>
<h1>Artificial Analysis, xhigh view</h1>
<p><a href="{html.escape(url)}">Open the chart</a> with every max variant swapped for xhigh.
Link last changed {html.escape(changed_at)}.</p>
<table>{rows}</table>
<p class="muted"><code>{html.escape(url)}</code></p>
</body>
</html>
"""


def write_outputs(result: Result, out: Path, now: datetime | None = None) -> bool:
    """Write index.html, link.txt and latest.json under ``out``. False if nothing changed."""
    out.mkdir(parents=True, exist_ok=True)
    latest = out / "latest.json"
    if latest.exists() and (out / "index.html").exists() and (out / "link.txt").exists():
        try:
            if json.loads(latest.read_text())["url"] == result.url:
                return False
        except (json.JSONDecodeError, KeyError):
            pass  # unreadable state: regenerate
    changed_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    (out / "index.html").write_text(render_page(result, changed_at))
    (out / "link.txt").write_text(result.url + "\n")
    latest.write_text(
        json.dumps(
            {
                "url": result.url,
                "changed_at": changed_at,
                "source": SOURCE,
                "swapped": [{"from": s.name, "to": s.to_name} for s in result.swapped],
                "no_xhigh": [k.name for k in result.no_xhigh],
            },
            indent=2,
        )
        + "\n"
    )
    return True


# --- cli ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--write", metavar="DIR", type=Path, help="regenerate index.html, link.txt, latest.json in DIR")
    ap.add_argument("--explain", action="store_true", help="show which models were swapped")
    ap.add_argument("--open", action="store_true", help="open the link in your browser")
    ap.add_argument(
        "--unmatched",
        choices=("keep", "drop"),
        default="keep",
        help="max models with no xhigh variant: keep them on max (default) or drop them from the chart",
    )
    ap.add_argument("--input", metavar="FILE", type=Path, help="parse a saved copy of the page instead of fetching")
    args = ap.parse_args(argv)

    try:
        page = args.input.read_text(encoding="utf-8") if args.input else fetch()
        result = build(page, args.unmatched)
    except (ParseError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.explain:
        print(explain(result), file=sys.stderr)
    if args.write:
        changed = write_outputs(result, args.write)
        print(f"{'updated' if changed else 'unchanged'}: {args.write}/", file=sys.stderr)
    print(result.url)
    if args.open:
        webbrowser.open(result.url)
    return 0


if __name__ == "__main__":
    sys.exit(main())
