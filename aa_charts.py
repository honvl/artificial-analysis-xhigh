"""Per-effort leaderboards: the top models at each effort level, drawn as SVG column charts.

Scores, lab names and lab colors come from the leaderboard that Artificial Analysis embeds
in its ``/leaderboards/models`` page. Variants of the same model are grouped by the release
they belong to, and each chart shows every model at the best setting it has at or below that
chart's level:

* a model with a variant at the level is shown at that variant;
* otherwise it is shown at its highest setting below the level, because that is the
  best it can do (Gemini 4 Argon only has High, so it appears on the max, xhigh and
  high charts);
* a model with no effort setting at all can be anything up to max, so it appears only
  on the max chart and is marked with a dagger.

Every column wears its lab's own color, the one Artificial Analysis uses for that lab. Beside
each column of a lab we know a subscription multiplier for there is a thin bar: what one full
run of the index costs on that lab's subscription. It is a second measure, so it has its own
labelled axis on the right, and that axis is shared by all four charts so the effort levels
can be compared. The chart is a white card like Artificial Analysis's, with thin columns that
have a 4px rounded cap, the value on the cap, a solid hairline grid, and a legend. Because lab
colors can be close to each other, the lab is also named in each column's tooltip and in the
table view on the summary page.
"""
from __future__ import annotations

import html
import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from aa_common import (
    LEVELS,
    ORDER,
    ParseError,
    display_name,
    flight_payload,
    level_href,
    parse_label,
    read_json,
    write_text,
)
from aa_costs import PLANS

LEADERBOARD_URL = "https://artificialanalysis.ai/leaderboards/models"
GRADES = ("max", "xhigh", "high", "medium")
TOP_N = 10
NEUTRAL = "#898781"  # a lab the site gives no usable color
THIN_COLOR = "#52514e"  # the cost bars: one neutral, since they are a measure, not a lab
THIN_LABEL = "Cost of one full index run on a subscription (right axis)"

# Geometry of one chart, in SVG user units. The plot starts lower when the legend wraps.
W, LEFT, RIGHT = 760, 128, 24
RIGHT_AXIS = 60  # room on the right for the dollar axis when cost bars are drawn
TOP, PLOT_H, LABEL_BAND = 92, 240, 150
LEGEND_ROW = 16
MAX_BAR = 24  # columns are capped at 24px; the rest of the slot is air
THIN_BAR = 8
BAR_GAP = 2  # surface gap between the two touching bars
LABEL_CHARS = 34
UNLABELLED = " †"

# Always light: a white card with a hairline border, like Artificial Analysis.
STYLE = """
.aa-chart{--surface:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:#e1e0d9}
.aa-chart text{font-family:system-ui,-apple-system,"Segoe UI",sans-serif}
.aa-chart .surface{fill:var(--surface);stroke:var(--border)}
.aa-chart .grid{stroke:var(--grid);stroke-width:1}
.aa-chart .axis{stroke:var(--axis);stroke-width:1}
.aa-chart .title{fill:var(--ink);font-size:16px;font-weight:600}
.aa-chart .sub,.aa-chart .tick,.aa-chart .credit,.aa-chart .note{fill:var(--muted);font-size:11px}
.aa-chart .sub{font-size:12px;fill:var(--ink2)}
.aa-chart .lbl{fill:var(--ink2);font-size:11px}
.aa-chart .val{fill:var(--ink);font-size:12px;font-weight:600}
.aa-chart .legend{fill:var(--ink2);font-size:12px}
.aa-chart .tval{fill:var(--ink2);font-size:9px}
""".strip()

_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})")


def clean_color(value: object) -> str:
    """A hex color from the site, or the neutral gray. The value is remote data that ends up in
    an SVG attribute, so anything that is not a plain hex color is dropped."""
    if isinstance(value, str) and _HEX.fullmatch(value.strip()):
        v = value.strip().lower()
        if len(v) == 4:
            v = "#" + "".join(ch * 2 for ch in v[1:])
        return v
    return NEUTRAL


@dataclass(frozen=True)
class Entry:
    slug: str
    name: str  # as the site names it, e.g. "Claude Opus 5.5 (Xhigh, Default Fallback)"
    label: str  # shorter, without the effort tag, for the axis
    lab: str
    color: str  # the lab's color, as a lowercase 6-digit hex
    score: float
    effort: str | None  # the setting this entry is at, None when Artificial Analysis labels none


@dataclass(frozen=True)
class Cost:
    api: float  # one full index run at API prices, USD
    sub: float  # the same run paid from the lab's subscription, USD
    plan: str


# --- reading the leaderboard -------------------------------------------------


def leaderboard_models(page: str) -> list[dict]:
    """The models on the leaderboard page: ``{slug, name, intelligenceIndex, modelCreatorName, modelCreatorColor}``."""
    payload = flight_payload(page)
    key = '"initialModels":'
    i = payload.find(key + "[")
    if i == -1:
        raise ParseError('"initialModels" not found in the leaderboard page data')
    models, _ = json.JSONDecoder().raw_decode(payload, i + len(key))
    usable = [m for m in models if isinstance(m, dict) and {"slug", "name", "intelligenceIndex"} <= m.keys()]
    if not usable:
        raise ParseError("leaderboard has no entries with an Intelligence Index")
    return usable


def short_name(name: str) -> str:
    """Drop the effort tag and any fallback note from a name: "GPT-6 Astra (Xhigh)" -> "GPT-6 Astra"."""
    name = display_name(name)
    label = parse_label(name)
    if label.effort is None:
        return name
    start = name.rindex("(")
    tokens = [t.strip() for t in name[start + 1 : name.rindex(")")].split(",")]
    rest = [t for t in tokens if t.lower() != label.effort]
    base = name[:start].rstrip()
    return f"{base} ({', '.join(rest)})" if rest else base


def _pick(candidates: list[Entry], grade: str) -> Entry | None:
    """The entry that represents a model on the ``grade`` chart, if it belongs there at all."""
    rank = ORDER.index(grade)
    labelled = [c for c in candidates if c.effort]
    if labelled:
        eligible = [c for c in labelled if ORDER.index(c.effort) <= rank]
        if not eligible:
            return None  # every setting it has is above this level
        best = max(ORDER.index(c.effort) for c in eligible)
        return max((c for c in eligible if ORDER.index(c.effort) == best), key=lambda c: (c.score, c.name))
    if grade != ORDER[-1]:
        return None  # an unlabelled setting could be anything up to max, so only the max chart can hold it
    reasoning = [c for c in candidates if "non-reasoning" not in c.name.lower()]
    return max(reasoning, key=lambda c: (c.score, c.name)) if reasoning else None


def families(models: list[dict], variants: list[dict]) -> dict[str, list[Entry]]:
    """Every scored variant, grouped by the release (model) it belongs to.

    ``variants`` is the site's ``{slug, name, releaseSlug}`` list; a slug missing from it
    stands alone.
    """
    release_of = {v["slug"]: v["releaseSlug"] for v in variants}
    grouped: dict[str, list[Entry]] = {}
    for m in models:
        score = m["intelligenceIndex"]
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        entry = Entry(
            slug=m["slug"],
            name=m["name"],
            label=short_name(m["name"]),
            lab=m.get("modelCreatorName") or "",
            color=clean_color(m.get("modelCreatorColor")),
            score=round(float(score), 1),
            effort=parse_label(m["name"]).effort,
        )
        grouped.setdefault(release_of.get(m["slug"]) or m["slug"], []).append(entry)
    return grouped


def leaderboards(
    models: list[dict], variants: list[dict], grades: tuple[str, ...] = GRADES, n: int = TOP_N
) -> dict[str, list[Entry]]:
    """Top ``n`` models by Intelligence Index on each effort level's chart."""
    grouped = families(models, variants)
    boards: dict[str, list[Entry]] = {}
    for grade in grades:
        picked = [e for e in (_pick(c, grade) for c in grouped.values()) if e]
        picked.sort(key=lambda e: (-e.score, e.name))
        if not picked:
            raise ParseError(f"no models on the {grade} chart")
        boards[grade] = picked[:n]
    return boards


# --- best model one level down vs the next model -----------------------------

# (level of the lab's best model, level of its next model): the best one always one step lower.
STEPS = (("medium", "high"), ("high", "xhigh"), ("xhigh", "max"))


@dataclass(frozen=True)
class Pair:
    lab: str
    low: str  # the best model's level
    high: str  # the next model's level
    best: Entry
    nxt: Entry


def _at(family: list[Entry], level: str) -> Entry | None:
    hits = [e for e in family if e.effort == level]
    return max(hits, key=lambda e: (e.score, e.name)) if hits else None


def step_down_pairs(models: list[dict], variants: list[dict], steps: tuple[tuple[str, str], ...] = STEPS) -> list[Pair]:
    """For each lab, its best model one effort level below its next model, at every step where
    both settings exist. "Best" and "next" rank the lab's models by their highest score at any
    labelled level; models with no labelled level cannot be paired."""
    by_lab: dict[str, list[list[Entry]]] = {}
    for family in families(models, variants).values():
        labelled = [e for e in family if e.effort]
        if labelled:
            by_lab.setdefault(labelled[0].lab, []).append(labelled)
    ranked = []
    for lab, fams in by_lab.items():
        fams.sort(key=lambda f: (-max(e.score for e in f), min(e.name for e in f)))
        if len(fams) >= 2:
            ranked.append((max(e.score for e in fams[0]), lab, fams[0], fams[1]))
    pairs = []
    for _, lab, best, nxt in sorted(ranked, key=lambda r: (-r[0], r[1])):
        for low, high in steps:
            a, b = _at(best, low), _at(nxt, high)
            if a and b:
                pairs.append(Pair(lab, low, high, a, b))
    return pairs


# --- subscription cost -------------------------------------------------------


def usd(value: float) -> str:
    if value >= 100:
        return f"${value:,.0f}"
    if value >= 10:
        return f"${value:.0f}"
    return f"${value:.2f}"


def subscription_cost(e: Entry, runs: dict[str, float]) -> Cost | None:
    """What a full index run costs for ``e`` on its lab's plan, or None when we have no measured
    multiplier for the lab or no cost for this variant. ``runs`` maps a slug to the API cost."""
    plan = PLANS.get(e.lab)
    api = runs.get(e.slug)
    if plan is None or api is None:
        return None
    return Cost(api, api / plan[1], plan[0])


def wanted_slugs(boards: dict[str, list[Entry]]) -> list[str]:
    """The variants whose full-run cost the cost bars need: every column of a lab with a plan."""
    return list(dict.fromkeys(e.slug for rows in boards.values() for e in rows if e.lab in PLANS))


def cost_axis(boards: dict[str, list[Entry]], runs: dict[str, float]) -> tuple[int, int] | None:
    """Tick step and maximum of the dollar axis, shared by every chart so effort levels compare."""
    values = [c.sub for rows in boards.values() for e in rows if (c := subscription_cost(e, runs))]
    if not values:
        return None
    return _nice(max(values))


def _nice(top: float) -> tuple[int, int]:
    """A 1/2/5 step giving at most four steps up to ``top``, and the rounded-up maximum."""
    step = next(m * 10**p for p in range(10) for m in (1, 2, 5) if math.ceil(top / (m * 10**p)) <= 4)
    return step, math.ceil(top / step) * step


# --- drawing -----------------------------------------------------------------


def _esc(text: str) -> str:
    return html.escape(text, quote=True)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def axis_label(e: Entry, grade: str) -> str:
    """The label under a column. A model not at the chart's level says what it is at."""
    if e.effort is None:
        return _clip(e.label, LABEL_CHARS - len(UNLABELLED)) + UNLABELLED
    if e.effort == grade:
        return _clip(e.label, LABEL_CHARS)
    if e.label.endswith(")"):
        body, tag = e.label[:-1], f", {e.effort})"
    else:
        body, tag = e.label, f" ({e.effort})"
    return _clip(body, LABEL_CHARS - len(tag)) + tag


def footnote(grade: str, entries: list[Entry]) -> str:
    parts = []
    if any(e.effort and e.effort != grade for e in entries):
        parts.append(f"Level in brackets: the model's highest setting below {grade}")
    if any(e.effort is None for e in entries):
        parts.append("† effort not labelled by Artificial Analysis")
    return " · ".join(parts)


def legend_items(
    entries: list[Entry], thin_label: str | None = None, extra: tuple[tuple[str, str, int], ...] = ()
) -> tuple[list[tuple[float, int, str, str, int]], int]:
    """One swatch per lab in the chart, in rank order, then any ``extra`` keys and the cost bar's
    key, wrapped to the chart width. Returns ``([(x, row, fill, name, swatch_width), ...], row_count)``."""
    labs: dict[str, str] = {}
    for e in entries:
        labs.setdefault(e.lab or "Unknown", e.color)
    keys = [(name, color, 10) for name, color in labs.items()] + list(extra)
    if thin_label:
        keys.append((thin_label, THIN_COLOR, THIN_BAR // 2))
    items, x, row = [], 24.0, 0
    for name, color, swatch in keys:
        width = 16 + len(name) * 6.6
        if x > 24 and x + width > W - 24:
            x, row = 24.0, row + 1
        items.append((x, row, color, name, swatch))
        x += width + 20
    return items, row + 1


def _scale(top_score: float) -> tuple[int, int]:
    """Tick step and axis maximum, with headroom above the tallest column."""
    step = 10 if top_score <= 40 else 20
    return step, (math.floor(top_score / step) + 1) * step


def _column(x: float, width: float, top: float, base: float) -> str:
    """A column path: 4px rounded cap, square at the baseline."""
    r = min(4.0, base - top, width / 2)
    return (
        f"M{x:.1f},{base:.1f} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
        f"H{x + width - r:.1f} Q{x + width:.1f},{top:.1f} {x + width:.1f},{top + r:.1f} V{base:.1f} Z"
    )


def render_svg(
    grade: str,
    entries: list[Entry],
    runs: dict[str, float] | None = None,
    axis: tuple[int, int] | None = None,
) -> str:
    """One chart. With ``runs`` (slug -> full-run API cost) and ``axis`` (the shared dollar axis),
    each column of a lab with a subscription plan gets a thin cost bar beside it."""
    n = len(entries)
    costs = [subscription_cost(e, runs or {}) for e in entries]
    thin = axis is not None and any(costs)
    right = RIGHT_AXIS if thin else RIGHT
    legend, rows = legend_items(entries, THIN_LABEL if thin else None)
    plot_top = TOP + LEGEND_ROW * (rows - 1)
    base = plot_top + PLOT_H
    height = base + LABEL_BAND
    step, ymax = _scale(max(e.score for e in entries))
    slot = min((W - LEFT - right) / n, 90)
    bar_w = min(float(MAX_BAR), slot * 0.45)
    group_w = bar_w + (BAR_GAP + THIN_BAR if thin else 0)
    title = f"Top models at {grade} effort"
    subtitle = "Artificial Analysis Intelligence Index, higher is better"
    desc = "; ".join(
        f"{i}. {display_name(e.name)}, {e.lab}: {e.score:.1f}"
        + ("" if e.effort else " (effort not labelled)")
        + (f", full index run on {c.plan} {usd(c.sub)}" if c and thin else "")
        for i, (e, c) in enumerate(zip(entries, costs), 1)
    )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="aa-chart" viewBox="0 0 {W} {height}" width="{W}" height="{height}"'
        f' role="img" aria-labelledby="t-{grade} d-{grade}">',
        f'<title id="t-{grade}">{_esc(title)}</title>',
        f'<desc id="d-{grade}">{_esc(desc)}</desc>',
        f"<style>{STYLE}</style>",
        f'<rect class="surface" x="0.5" y="0.5" width="{W - 1}" height="{height - 1}" rx="12"/>',
        f'<text class="title" x="24" y="34">{_esc(title)}</text>',
        f'<text class="credit" x="{W - 24}" y="34" text-anchor="end">Data: artificialanalysis.ai</text>',
        f'<text class="sub" x="24" y="54">{_esc(subtitle)}</text>',
    ]

    for x, row, fill, name, swatch in legend:
        y = LEGEND_ROW * row
        out.append(f'<rect x="{x:.1f}" y="{64 + y}" width="{swatch}" height="10" rx="2" fill="{fill}"/>')
        out.append(f'<text class="legend" x="{x + 16:.1f}" y="{73 + y}">{_esc(name)}</text>')

    for t in range(0, ymax + 1, step):
        y = base - t / ymax * PLOT_H
        out.append(f'<line class="{"axis" if t == 0 else "grid"}" x1="{LEFT}" x2="{W - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{t}</text>')

    if thin:
        cstep, cmax = axis
        out.append(f'<line class="axis" x1="{W - right}" x2="{W - right}" y1="{plot_top}" y2="{base}"/>')
        for t in range(0, cmax + 1, cstep):
            y = base - t / cmax * PLOT_H
            out.append(f'<line class="axis" x1="{W - right}" x2="{W - right + 4}" y1="{y:.1f}" y2="{y:.1f}"/>')
            out.append(f'<text class="tick" x="{W - right + 8}" y="{y + 4:.1f}">${t:,}</text>')

    for i, (e, c) in enumerate(zip(entries, costs)):
        slot_x = LEFT + i * slot
        left_edge = slot_x + slot / 2 - group_w / 2
        cx = left_edge + bar_w / 2
        top = base - e.score / ymax * PLOT_H
        tip = f"{display_name(e.name)}, {e.lab}: {e.score:.1f}"
        shapes = f'<path fill="{e.color}" d="{_column(left_edge, bar_w, top, base)}"/>'
        if thin and c:
            tip += f" · one full index run costs {usd(c.sub)} on {c.plan} ({usd(c.api)} at API prices)"
            cost_top = base - c.sub / axis[1] * PLOT_H
            shapes += f'<path fill="{THIN_COLOR}" d="{_column(left_edge + bar_w + BAR_GAP, THIN_BAR, cost_top, base)}"/>'
        out.append(
            f"<g><title>{_esc(tip)}</title>"
            f'<rect x="{slot_x:.1f}" y="{plot_top}" width="{slot:.1f}" height="{PLOT_H}" fill="transparent"/>'
            f"{shapes}"
            f'<text class="val" x="{cx:.1f}" y="{top - 6:.1f}" text-anchor="middle">{e.score:.1f}</text>'
            f'<text class="lbl" transform="translate({cx:.1f} {base + 12:.1f}) rotate(-45)" text-anchor="end">'
            f"{_esc(axis_label(e, grade))}</text></g>"
        )

    note = footnote(grade, entries)
    if note:
        out.append(f'<text class="note" x="24" y="{height - 14}">{_esc(note)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_file(
    grade: str, entries: list[Entry], runs: dict[str, float] | None = None, axis: tuple[int, int] | None = None
) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + render_svg(grade, entries, runs, axis) + "\n"


def tint(color: str, amount: float = 0.5) -> str:
    """``color`` mixed with white: the lighter shade marks a lab's next model."""
    rgb = [int(color[i : i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(c + (255 - c) * amount):02x}" for c in rgb)


def pair_label(e: Entry) -> str:
    """'Claude Opus 5.5 · high': the model without its qualifier, then the level."""
    return _clip(re.sub(r"\s*\([^()]*\)\s*$", "", e.label), LABEL_CHARS - len(f" · {e.effort}")) + f" · {e.effort}"


PAIRS_TITLE = "Best model one level down vs the next model"
NEXT_KEY = "Lighter shade: the lab's next model"
TIME_BAR = 6
TIME_COLOR = "#6f7f96"  # a cool slate, apart from the warm grays of the cost bar and the next-model key
TIME_LABEL = "End-to-end response time in seconds, lower is better (first answer token + 500 tokens)"


def speed_text(s: dict[str, float | None]) -> str:
    parts = []
    if s.get("ttfat_s") is not None:
        parts.append(f"first answer token {s['ttfat_s']:.1f} s")
    if s.get("tps") is not None:
        parts.append(f"{s['tps']:.0f} tokens/s")
    parts.append(f"end-to-end {s['e2e_s']:.1f} s")
    return ", ".join(parts)


def render_pairs_svg(
    pairs: list[Pair],
    runs: dict[str, float] | None = None,
    axis: tuple[int, int] | None = None,
    speeds: dict[str, dict[str, float | None]] | None = None,
) -> str:
    """Each lab's best model one effort level below its next model, as pairs of columns.

    The best model wears the lab's color, the next model a lighter shade of it, and pairs are
    separated by a gap. Thin cost bars and the dollar axis work as on the level charts. With
    ``speeds`` (slug -> ttfat_s, tps, e2e_s) a third, slate bar shows the end-to-end response
    time, labelled in seconds on its own scale, since staying in the flow depends on it.
    """
    if not pairs:
        raise ValueError("no pairs to draw")
    entries = [e for p in pairs for e in (p.best, p.nxt)]
    costs = [subscription_cost(e, runs or {}) for e in entries]
    thin = axis is not None and any(costs)
    times = [(speeds or {}).get(e.slug) for e in entries]
    timed = any(times)
    tmax = _nice(max(t["e2e_s"] for t in times if t))[1] if timed else 0
    right = RIGHT_AXIS if thin else RIGHT
    keys = [(NEXT_KEY, tint(THIN_COLOR), 10)] + ([(TIME_LABEL, TIME_COLOR, TIME_BAR)] if timed else [])
    legend, rows = legend_items(entries, THIN_LABEL if thin else None, tuple(keys))
    plot_top = TOP + LEGEND_ROW * (rows - 1)
    base = plot_top + PLOT_H
    height = base + LABEL_BAND
    step, ymax = _scale(max(e.score for e in entries))
    group_gap = 0.5  # in slots
    slot = (W - LEFT - right) / (len(entries) + group_gap * (len(pairs) - 1))
    bar_w = min(float(MAX_BAR), slot * 0.4)
    group_w = bar_w + (BAR_GAP + THIN_BAR if thin else 0) + (BAR_GAP + TIME_BAR if timed else 0)
    subtitle = "Intelligence Index. Each pair: a lab's best model one effort level below its next model"
    desc = "; ".join(
        f"{p.lab}: {display_name(p.best.name)} {p.best.score:.1f} vs {display_name(p.nxt.name)} {p.nxt.score:.1f}" for p in pairs
    )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="aa-chart" viewBox="0 0 {W} {height}" width="{W}" height="{height}"'
        ' role="img" aria-labelledby="t-pairs d-pairs">',
        f'<title id="t-pairs">{_esc(PAIRS_TITLE)}</title>',
        f'<desc id="d-pairs">{_esc(desc)}</desc>',
        f"<style>{STYLE}</style>",
        f'<rect class="surface" x="0.5" y="0.5" width="{W - 1}" height="{height - 1}" rx="12"/>',
        f'<text class="title" x="24" y="34">{_esc(PAIRS_TITLE)}</text>',
        f'<text class="credit" x="{W - 24}" y="34" text-anchor="end">Data: artificialanalysis.ai</text>',
        f'<text class="sub" x="24" y="54">{_esc(subtitle)}</text>',
    ]
    for x, row, fill, name, swatch in legend:
        y = LEGEND_ROW * row
        out.append(f'<rect x="{x:.1f}" y="{64 + y}" width="{swatch}" height="10" rx="2" fill="{fill}"/>')
        out.append(f'<text class="legend" x="{x + 16:.1f}" y="{73 + y}">{_esc(name)}</text>')
    for t in range(0, ymax + 1, step):
        y = base - t / ymax * PLOT_H
        out.append(f'<line class="{"axis" if t == 0 else "grid"}" x1="{LEFT}" x2="{W - right}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{t}</text>')
    if thin:
        cstep, cmax = axis
        out.append(f'<line class="axis" x1="{W - right}" x2="{W - right}" y1="{plot_top}" y2="{base}"/>')
        for t in range(0, cmax + 1, cstep):
            y = base - t / cmax * PLOT_H
            out.append(f'<line class="axis" x1="{W - right}" x2="{W - right + 4}" y1="{y:.1f}" y2="{y:.1f}"/>')
            out.append(f'<text class="tick" x="{W - right + 8}" y="{y + 4:.1f}">${t:,}</text>')

    for i, (e, c, t) in enumerate(zip(entries, costs, times)):
        slot_x = LEFT + (i + group_gap * (i // 2)) * slot
        left_edge = slot_x + slot / 2 - group_w / 2
        cx = left_edge + bar_w / 2
        top = base - e.score / ymax * PLOT_H
        fill = e.color if i % 2 == 0 else tint(e.color)
        tip = f"{display_name(e.name)}, {e.lab}: {e.score:.1f}"
        shapes = f'<path fill="{fill}" d="{_column(left_edge, bar_w, top, base)}"/>'
        x = left_edge + bar_w + BAR_GAP
        if thin:
            if c:
                tip += f" · one full index run costs {usd(c.sub)} on {c.plan} ({usd(c.api)} at API prices)"
                cost_top = base - c.sub / axis[1] * PLOT_H
                shapes += f'<path fill="{THIN_COLOR}" d="{_column(x, THIN_BAR, cost_top, base)}"/>'
            x += THIN_BAR + BAR_GAP
        if timed and t:
            tip += f" · {speed_text(t)}"
            t_top = base - t["e2e_s"] / tmax * PLOT_H
            shapes += f'<path fill="{TIME_COLOR}" d="{_column(x, TIME_BAR, t_top, base)}"/>'
            shapes += f'<text class="tval" x="{x + TIME_BAR / 2:.1f}" y="{t_top - 4:.1f}" text-anchor="middle">{t["e2e_s"]:.0f}s</text>'
        out.append(
            f"<g><title>{_esc(tip)}</title>"
            f'<rect x="{slot_x:.1f}" y="{plot_top}" width="{slot:.1f}" height="{PLOT_H}" fill="transparent"/>'
            f"{shapes}"
            f'<text class="val" x="{cx:.1f}" y="{top - 6:.1f}" text-anchor="middle">{e.score:.1f}</text>'
            f'<text class="lbl" transform="translate({cx:.1f} {base + 12:.1f}) rotate(-45)" text-anchor="end">'
            f"{_esc(pair_label(e))}</text></g>"
        )
    out.append("</svg>")
    return "\n".join(out)


# --- the summary page --------------------------------------------------------


def _pairs_section(
    pairs: list[Pair], runs: dict[str, float], axis: tuple[int, int] | None, speeds: dict[str, dict]
) -> str:
    def cell(e: Entry) -> str:
        c = subscription_cost(e, runs)
        run = f" · {usd(c.api)} → {usd(c.sub)} on {_esc(c.plan)}" if c else ""
        speed = f"<br><span class=\"muted\">{_esc(speed_text(speeds[e.slug]))}</span>" if e.slug in speeds else ""
        return f"{_esc(pair_label(e))}: {e.score:.1f}{run}{speed}"

    rows = "".join(
        f"<tr><td>{_esc(p.lab)}</td><td>{p.low} vs {p.high}</td><td>{cell(p.best)}</td><td>{cell(p.nxt)}</td>"
        f'<td class="num">{p.best.score - p.nxt.score:+.1f}</td></tr>'
        for p in pairs
    )
    return (
        '<section id="step-down">\n<h2>Best model one level down vs the next model</h2>\n'
        "<p>For each lab, its best model one effort level below its next model, for example Opus 5.5 at high "
        "against Sonnet 5.5 at xhigh. A lab appears when it has two models with matching effort levels. "
        "The slate bar is Artificial Analysis's end-to-end response time, labelled in seconds: the time to the "
        "first answer token (thinking included) plus a 500-token answer at the model's speed, so lower keeps you "
        "in the flow. The table view also gives the first-token time and speed separately.</p>\n"
        f"{render_pairs_svg(pairs, runs, axis, speeds)}\n"
        "<details><summary>Table view</summary>\n"
        "<table><thead><tr><th>Lab</th><th>Levels</th><th>Best model</th><th>Next model</th><th>Index difference</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>\n</section>"
    )


def render_summary(
    boards: dict[str, list[Entry]],
    changed_at: str,
    runs: dict[str, float] | None = None,
    axis: tuple[int, int] | None = None,
    pairs: list[Pair] | None = None,
    speeds: dict[str, dict] | None = None,
) -> str:
    runs = runs or {}
    views = " · ".join(f'<a href="{level_href(lv, "", "#info")}">{lv}</a>' for lv in LEVELS)
    plans = ", ".join(f"{lab}: {plan} {mult:g}x" for lab, (plan, mult) in PLANS.items())
    sections = []
    for grade, rows in boards.items():
        cells = []
        for i, e in enumerate(rows, 1):
            c = subscription_cost(e, runs)
            full = f"{usd(c.api)} → {usd(c.sub)} on {_esc(c.plan)}" if c else "–"
            cells.append(
                f'<tr><td class="num">{i}</td><td>{_esc(display_name(e.name))}</td><td>{_esc(e.lab)}</td>'
                f'<td>{_esc(e.effort or "not labelled")}</td><td class="num">{e.score:.1f}</td><td>{full}</td></tr>'
            )
        sections.append(
            f'<section id="{grade}">\n<h2>{grade} effort</h2>\n{render_svg(grade, rows, runs, axis)}\n'
            f"<details><summary>Table view</summary>\n"
            f"<table><thead><tr><th>#</th><th>Model</th><th>Lab</th><th>Setting</th><th>Index</th>"
            f"<th>Full index run: API → subscription</th></tr></thead>"
            f"<tbody>{''.join(cells)}</tbody></table></details>\n</section>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Top models by effort level</title>
<style>
  :root {{ color-scheme: light; }}
  body {{ background: #fff; color: #0b0b0b; font: 16px/1.5 system-ui, sans-serif; max-width: 48rem; margin: 3rem auto; padding: 0 1rem; }}
  a {{ color: #2a78d6; }}
  svg {{ max-width: 100%; height: auto; }}
  section {{ margin: 2.5rem 0; }}
  summary {{ cursor: pointer; margin-top: .5rem; }}
  table {{ border-collapse: collapse; margin-top: .5rem; }}
  th, td {{ text-align: left; padding: .2rem .9rem .2rem 0; }}
  .num {{ text-align: right; font-variant-numeric: tabular-nums; }}
  .muted {{ color: #52514e; }}
</style>
</head>
<body>
<h1>Top models by effort level</h1>
<p>The highest-scoring models on the Artificial Analysis Intelligence Index at each effort level.
Each chart shows a model at the best setting it has at or below that level: a model with no variant
at the level appears at its highest setting below it, so a model that only has High is on the max,
xhigh and high charts. A model with no labelled effort setting could be anything up to max, so it
appears only on the max chart, marked with a dagger. Columns use the color Artificial Analysis
gives each lab. Data last changed {_esc(changed_at)}.</p>
<p class="muted">The thin bar beside a column is what one full run of the index costs on that lab's
subscription, read off the dollar axis on the right. That axis is shared by all four charts, so the
bars show how cost falls as effort drops, and it is a different measure from the score, so it has
its own scale. It is Artificial Analysis's cost for the run divided by how much more usage the plan
gives than API list prices ({_esc(plans)}), as measured by
<a href="https://x.com/PawelHuryn/status/2107780775558979757">Paweł Huryn</a>. Only labs with a
measured multiplier get a bar. These are one person's estimates on one model per vendor, so treat
the bars as rough. The table views give the exact figures.</p>
<p class="muted">Charts for: {" · ".join(f'<a href="#{g}">{g}</a>' for g in boards)}{' · <a href="#step-down">best model one level down</a>' if pairs else ""}.
Links at: {views} · <a href="bookmark.html">bookmark</a></p>
{chr(10).join(sections)}
{_pairs_section(pairs, runs, axis, speeds or {}) if pairs else ""}
</body>
</html>
"""


def write_charts(
    boards: dict[str, list[Entry]],
    out: Path,
    now: datetime | None = None,
    runs: dict[str, float] | None = None,
    pairs: list[Pair] | None = None,
    speeds: dict[str, dict] | None = None,
) -> bool:
    """Write summary.html, charts/<grade>.svg, charts/step-down.svg and charts.json under ``out``.
    False if nothing changed.

    ``runs`` maps a variant slug to the cost in USD of a full index run at API prices; it feeds the
    thin subscription-cost bars. ``pairs`` feeds the best-model-one-level-down chart.
    """
    runs = runs or {}
    pairs = pairs or []
    speeds = speeds or {}
    axis = cost_axis({**boards, "pairs": [e for p in pairs for e in (p.best, p.nxt)]}, runs)
    data = {}
    for g, rows in boards.items():
        data[g] = []
        for e in rows:
            c = subscription_cost(e, runs)
            data[g].append(
                {
                    "name": e.name,
                    "lab": e.lab,
                    "color": e.color,
                    "effort": e.effort,
                    "score": e.score,
                    "full_run_api": c and round(c.api, 2),
                    "full_run_subscription": c and round(c.sub, 2),
                }
            )
    pair_data = [
        {"lab": p.lab, "levels": [p.low, p.high], "best": p.best.name, "best_score": p.best.score,
         "next": p.nxt.name, "next_score": p.nxt.score,
         "best_speed": speeds.get(p.best.slug), "next_speed": speeds.get(p.nxt.slug)}
        for p in pairs
    ]
    state = {"grades": data, "cost_axis": list(axis) if axis else None, "step_down": pair_data}
    old = read_json(out / "charts.json")
    if old and {k: old.get(k) for k in state} == state and old.get("changed_at"):
        changed_at = old["changed_at"]
    else:
        changed_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    files = {
        "charts.json": json.dumps({"changed_at": changed_at, **state}, indent=2) + "\n",
        "summary.html": render_summary(boards, changed_at, runs, axis, pairs, speeds),
    }
    for grade, rows in boards.items():
        files[f"charts/{grade}.svg"] = svg_file(grade, rows, runs, axis)
    if pairs:
        files["charts/step-down.svg"] = '<?xml version="1.0" encoding="UTF-8"?>\n' + render_pairs_svg(pairs, runs, axis, speeds) + "\n"
    return any([write_text(out / rel, text) for rel, text in files.items()])
