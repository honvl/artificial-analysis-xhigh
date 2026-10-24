"""Per-effort leaderboards: the top models at each effort level, drawn as SVG column charts.

Scores come from the model picker that Artificial Analysis embeds in its
``/models/comparisons`` page (an Intelligence Index per model variant). Variants of the
same model are grouped by the release they belong to, and each chart shows every model
at the best setting it has at or below that chart's level:

* a model with a variant at the level is shown at that variant;
* otherwise it is shown at its highest setting below the level, because that is the
  best it can do (Gemini 4 Argon only has High, so it appears on the max, xhigh and
  high charts);
* a model with no effort setting at all can be anything up to max, so it appears only
  on the max chart and is marked with a dagger.

Chart design follows the dataviz skill's specs: thin columns with a 4px rounded cap and
the value on the cap, solid hairline grid, a legend, a table twin on the summary page,
and the validated categorical slots for the three labs that dominate the rankings (all
other labs share one neutral). The surface is always white, like Artificial Analysis.
"""
from __future__ import annotations

import html
import json
import math
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

PICKER_URL = "https://artificialanalysis.ai/models/comparisons"
GRADES = ("max", "xhigh", "high", "medium")
TOP_N = 10

# Color follows the entity, never its rank: these three labs always get the same slot.
LABS = {"Anthropic": "anthropic", "OpenAI": "openai", "Google": "google"}
LAB_ORDER = ("anthropic", "openai", "google", "other")
LAB_NAMES = {"anthropic": "Anthropic", "openai": "OpenAI", "google": "Google", "other": "Other labs"}

# Geometry of one chart, in SVG user units.
W, LEFT, RIGHT = 760, 128, 24
TOP, PLOT_H, LABEL_BAND = 92, 240, 150
BASE = TOP + PLOT_H
H = BASE + LABEL_BAND
MAX_BAR = 24  # columns are capped at 24px; the rest of the slot is air
LABEL_CHARS = 34
UNLABELLED = " †"

# Always light: a white card with a hairline border, like Artificial Analysis.
STYLE = """
.aa-chart{--surface:#ffffff;--ink:#0b0b0b;--ink2:#52514e;--muted:#898781;--grid:#e1e0d9;--axis:#c3c2b7;--border:#e1e0d9;--anthropic:#eb6834;--openai:#1baf7a;--google:#2a78d6;--other:#898781}
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
.aa-chart .anthropic{fill:var(--anthropic)}
.aa-chart .openai{fill:var(--openai)}
.aa-chart .google{fill:var(--google)}
.aa-chart .other{fill:var(--other)}
""".strip()


@dataclass(frozen=True)
class Entry:
    slug: str
    name: str  # as the site names it, e.g. "Claude Opus 5.5 (Xhigh, Default Fallback)"
    label: str  # shorter, without the effort tag, for the axis
    lab: str
    score: float
    effort: str | None  # the setting this entry is at, None when Artificial Analysis labels none

    @property
    def lab_class(self) -> str:
        return LABS.get(self.lab, "other")


# --- reading the picker ------------------------------------------------------


def picker_models(page: str) -> list[dict]:
    """The model picker embedded in a comparison page: ``{slug, name, intelligenceIndex, creator}``."""
    payload = flight_payload(page)
    key = '"pickerModels":'
    i = payload.find(key + "[")
    if i == -1:
        raise ParseError('"pickerModels" not found in page data')
    models, _ = json.JSONDecoder().raw_decode(payload, i + len(key))
    usable = [m for m in models if isinstance(m, dict) and {"slug", "name", "intelligenceIndex"} <= m.keys()]
    if not usable:
        raise ParseError("model picker has no entries with an Intelligence Index")
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
            lab=(m.get("creator") or {}).get("name", ""),
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


def _scale(top_score: float) -> tuple[int, int]:
    """Tick step and axis maximum, with headroom above the tallest column."""
    step = 10 if top_score <= 40 else 20
    return step, (math.floor(top_score / step) + 1) * step


def _column(x: float, width: float, top: float) -> str:
    """A column path: 4px rounded cap, square at the baseline."""
    r = min(4.0, BASE - top, width / 2)
    return (
        f"M{x:.1f},{BASE} V{top + r:.1f} Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} "
        f"H{x + width - r:.1f} Q{x + width:.1f},{top:.1f} {x + width:.1f},{top + r:.1f} V{BASE} Z"
    )


def render_svg(grade: str, entries: list[Entry]) -> str:
    n = len(entries)
    step, ymax = _scale(max(e.score for e in entries))
    slot = min((W - LEFT - RIGHT) / n, 90)
    bar_w = min(float(MAX_BAR), slot * 0.5)
    title = f"Top models at {grade} effort"
    subtitle = "Artificial Analysis Intelligence Index, higher is better"
    desc = "; ".join(
        f"{i}. {e.name}, {e.lab}: {e.score:.1f}" + ("" if e.effort else " (effort not labelled)")
        for i, e in enumerate(entries, 1)
    )

    out = [
        f'<svg xmlns="http://www.w3.org/2000/svg" class="aa-chart" viewBox="0 0 {W} {H}" width="{W}" height="{H}"'
        f' role="img" aria-labelledby="t-{grade} d-{grade}">',
        f'<title id="t-{grade}">{_esc(title)}</title>',
        f'<desc id="d-{grade}">{_esc(desc)}</desc>',
        f"<style>{STYLE}</style>",
        f'<rect class="surface" x="0.5" y="0.5" width="{W - 1}" height="{H - 1}" rx="12"/>',
        f'<text class="title" x="24" y="34">{_esc(title)}</text>',
        f'<text class="credit" x="{W - 24}" y="34" text-anchor="end">Data: artificialanalysis.ai</text>',
        f'<text class="sub" x="24" y="54">{_esc(subtitle)}</text>',
    ]

    x = 24.0
    for key in LAB_ORDER:
        if not any(e.lab_class == key for e in entries):
            continue
        out.append(f'<rect class="{key}" x="{x:.1f}" y="64" width="10" height="10" rx="2"/>')
        out.append(f'<text class="legend" x="{x + 16:.1f}" y="73">{_esc(LAB_NAMES[key])}</text>')
        x += 16 + len(LAB_NAMES[key]) * 6.6 + 20

    for t in range(0, ymax + 1, step):
        y = BASE - t / ymax * PLOT_H
        out.append(f'<line class="{"axis" if t == 0 else "grid"}" x1="{LEFT}" x2="{W - RIGHT}" y1="{y:.1f}" y2="{y:.1f}"/>')
        out.append(f'<text class="tick" x="{LEFT - 8}" y="{y + 4:.1f}" text-anchor="end">{t}</text>')

    for i, e in enumerate(entries):
        slot_x = LEFT + i * slot
        cx = slot_x + slot / 2
        top = BASE - e.score / ymax * PLOT_H
        out.append(
            f"<g><title>{_esc(f'{e.name}, {e.lab}: {e.score:.1f}')}</title>"
            f'<rect x="{slot_x:.1f}" y="{TOP}" width="{slot:.1f}" height="{PLOT_H}" fill="transparent"/>'
            f'<path class="{e.lab_class}" d="{_column(cx - bar_w / 2, bar_w, top)}"/>'
            f'<text class="val" x="{cx:.1f}" y="{top - 6:.1f}" text-anchor="middle">{e.score:.1f}</text>'
            f'<text class="lbl" transform="translate({cx:.1f} {BASE + 12}) rotate(-45)" text-anchor="end">'
            f"{_esc(axis_label(e, grade))}</text></g>"
        )

    note = footnote(grade, entries)
    if note:
        out.append(f'<text class="note" x="24" y="{H - 14}">{_esc(note)}</text>')
    out.append("</svg>")
    return "\n".join(out)


def svg_file(grade: str, entries: list[Entry]) -> str:
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + render_svg(grade, entries) + "\n"


# --- the summary page --------------------------------------------------------


def render_summary(boards: dict[str, list[Entry]], changed_at: str) -> str:
    views = " · ".join(f'<a href="{level_href(lv, "", "#info")}">{lv}</a>' for lv in LEVELS)
    sections = []
    for grade, rows in boards.items():
        trs = "".join(
            f'<tr><td class="num">{i}</td><td>{_esc(e.name)}</td><td>{_esc(e.lab)}</td>'
            f'<td>{_esc(e.effort or "not labelled")}</td><td class="num">{e.score:.1f}</td></tr>'
            for i, e in enumerate(rows, 1)
        )
        sections.append(
            f'<section id="{grade}">\n<h2>{grade} effort</h2>\n{render_svg(grade, rows)}\n'
            f"<details><summary>Table view</summary>\n"
            f"<table><thead><tr><th>#</th><th>Model</th><th>Lab</th><th>Setting</th><th>Index</th></tr></thead>"
            f"<tbody>{trs}</tbody></table></details>\n</section>"
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
appears only on the max chart, marked with a dagger.
Data last changed {_esc(changed_at)}.</p>
<p class="muted">Charts for: {" · ".join(f'<a href="#{g}">{g}</a>' for g in boards)}.
Links at: {views} · <a href="bookmark.html">bookmark</a></p>
{chr(10).join(sections)}
</body>
</html>
"""


def write_charts(boards: dict[str, list[Entry]], out: Path, now: datetime | None = None) -> bool:
    """Write summary.html, charts/<grade>.svg and charts.json under ``out``. False if nothing changed."""
    data = {
        g: [{"name": e.name, "lab": e.lab, "effort": e.effort, "score": e.score} for e in rows]
        for g, rows in boards.items()
    }
    old = read_json(out / "charts.json")
    if old and old.get("grades") == data and old.get("changed_at"):
        changed_at = old["changed_at"]
    else:
        changed_at = (now or datetime.now(timezone.utc)).strftime("%Y-%m-%d %H:%M UTC")
    files = {
        "charts.json": json.dumps({"changed_at": changed_at, "grades": data}, indent=2) + "\n",
        "summary.html": render_summary(boards, changed_at),
    }
    for grade, rows in boards.items():
        files[f"charts/{grade}.svg"] = svg_file(grade, rows)
    return any([write_text(out / rel, text) for rel, text in files.items()])
