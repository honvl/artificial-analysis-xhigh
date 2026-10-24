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
    """Drop the effort tag from a name: "GPT-6 Astra (Xhigh)" -> "GPT-6 Astra"."""
    label = parse_label(name)
    if label.effort is None:
        return name
    start = name.rindex("(")
    tokens = [t.strip() for t in name[start + 1 : name.rindex(")")].split(",")]
    rest = [t for t in tokens if t.lower() != label.effort]
    rest = [("fallback" if t.lower() == "default fallback" else t) for t in rest]
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


def leaderboards(
    models: list[dict], variants: list[dict], grades: tuple[str, ...] = GRADES, n: int = TOP_N
) -> dict[str, list[Entry]]:
    """Top ``n`` models by Intelligence Index on each effort level's chart.

    ``variants`` is the site's ``{slug, name, releaseSlug}`` list, used to group the
    variants of one model together.
    """
    release_of = {v["slug"]: v["releaseSlug"] for v in variants}
    families: dict[str, list[Entry]] = {}
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
        families.setdefault(release_of.get(m["slug"]) or m["slug"], []).append(entry)

    boards: dict[str, list[Entry]] = {}
    for grade in grades:
        picked = [e for e in (_pick(c, grade) for c in families.values()) if e]
        picked.sort(key=lambda e: (-e.score, e.name))
        if not picked:
            raise ParseError(f"no models on the {grade} chart")
        boards[grade] = picked[:n]
    return boards


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
    top = max(values)
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


def legend_items(entries: list[Entry], thin_label: str | None = None) -> tuple[list[tuple[float, int, str, str, int]], int]:
    """One swatch per lab in the chart, in rank order, then the cost bar's key, wrapped to the
    chart width. Returns ``([(x, row, fill, name, swatch_width), ...], row_count)``."""
    labs: dict[str, str] = {}
    for e in entries:
        labs.setdefault(e.lab or "Unknown", e.color)
    keys = [(name, color, 10) for name, color in labs.items()]
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
        f"{i}. {e.name}, {e.lab}: {e.score:.1f}"
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
        tip = f"{e.name}, {e.lab}: {e.score:.1f}"
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


# --- the summary page --------------------------------------------------------


def render_summary(
    boards: dict[str, list[Entry]],
    changed_at: str,
    runs: dict[str, float] | None = None,
    axis: tuple[int, int] | None = None,
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
                f'<tr><td class="num">{i}</td><td>{_esc(e.name)}</td><td>{_esc(e.lab)}</td>'
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
<p class="muted">Charts for: {" · ".join(f'<a href="#{g}">{g}</a>' for g in boards)}.
Links at: {views} · <a href="bookmark.html">bookmark</a></p>
{chr(10).join(sections)}
</body>
</html>
"""


def write_charts(
    boards: dict[str, list[Entry]], out: Path, now: datetime | None = None, runs: dict[str, float] | None = None
) -> bool:
    """Write summary.html, charts/<grade>.svg and charts.json under ``out``. False if nothing changed.

    ``runs`` maps a variant slug to the cost in USD of a full index run at API prices; it feeds the
    thin subscription-cost bars.
    """
    runs = runs or {}
    axis = cost_axis(boards, runs)
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
    state = {"grades": data, "cost_axis": list(axis) if axis else None}
    old = read_json(out / "charts.json")
    if old and {k: old.get(k) for k in state} == state and old.get("changed_at"):
        changed_at = old["changed_at"]
    else:
        changed_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    files = {
        "charts.json": json.dumps({"changed_at": changed_at, **state}, indent=2) + "\n",
        "summary.html": render_summary(boards, changed_at, runs, axis),
    }
    for grade, rows in boards.items():
        files[f"charts/{grade}.svg"] = svg_file(grade, rows, runs, axis)
    return any([write_text(out / rel, text) for rel, text in files.items()])
