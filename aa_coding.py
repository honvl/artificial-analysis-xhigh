"""Coding Agent Index comparison for the summary page.

Artificial Analysis measures real-world coding-agent performance separately from the
Intelligence Index this repo charts by default:

https://artificialanalysis.ai/agents/coding-agents

Composite of FrontierCode v1.1, Terminal-Bench 4.0 and SWE-Atlas-QnA-AA. Standard library only.
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from aa_common import ParseError, flight_payload

# Duplicated from aa_charts.clean_color to avoid a circular import (aa_charts takes the
# rendered section string, not this module). Keep the two in sync: plain hex only.
_HEX = re.compile(r"#([0-9a-fA-F]{6}|[0-9a-fA-F]{3})")


def _clean_color(value: object) -> str:
    if isinstance(value, str) and _HEX.fullmatch(value.strip()):
        v = value.strip().lower()
        if len(v) == 4:
            v = "#" + "".join(ch * 2 for ch in v[1:])
        return v
    return "#898781"

CODING_URL = "https://artificialanalysis.ai/agents/coding-agents"


@dataclass(frozen=True)
class CodingAgent:
    label: str  # e.g. "Muse Code - Muse Spark 1.3 (max)"
    agent: str
    model: str
    lab: str
    color: str
    score: float  # 0-100 scale, e.g. 45.7
    cost_usd: float | None  # mean API cost per task, None when the site lists $0.00 / no API
    wall_sec: float | None  # mean agent wall time per task, seconds
    is_highlighted: bool = False
    is_default: bool = False
    is_router: bool = False


def _agent_object(payload: str, label: str) -> dict | None:
    """The full record for ``label``: the dict that has it as its displayLabel."""
    decoder = json.JSONDecoder()
    for m in re.finditer(r'"displayLabel"\s*:\s*"%s"' % re.escape(label), payload):
        for start in range(m.start(), max(0, m.start() - 8000), -1):
            if payload[start] != "{":
                continue
            try:
                obj, _ = decoder.raw_decode(payload, start)
            except json.JSONDecodeError:
                continue
            if isinstance(obj, dict) and obj.get("displayLabel") == label and "indexScore" in obj:
                return obj
    return None


def _num(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        import math as _math

        if _math.isfinite(value) and value > 0:
            return float(value)
    return None


def coding_agents(page: str) -> list[CodingAgent]:
    """Every coding agent the page carries, sorted by Index descending."""
    payload = flight_payload(page)
    labels = list(dict.fromkeys(re.findall(r'"displayLabel"\s*:\s*"([^"]+)"', payload)))
    if not labels:
        raise ParseError("no coding agents found in the coding-agents page data")
    agents: list[CodingAgent] = []
    for label in labels:
        obj = _agent_object(payload, label)
        if obj is None:
            continue
        score = obj.get("indexScore")
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        mean = obj.get("mean") if isinstance(obj.get("mean"), dict) else {}
        display = obj.get("display") if isinstance(obj.get("display"), dict) else {}
        creators = obj.get("modelCreators") if isinstance(obj.get("modelCreators"), list) else []
        lab = ""
        color = "#898781"
        if creators and isinstance(creators[0], dict):
            lab = str(creators[0].get("name") or "")
            color = _clean_color(creators[0].get("color"))
        if not lab:
            creator = obj.get("agentCreator") if isinstance(obj.get("agentCreator"), dict) else {}
            lab = str(creator.get("name") or "")
            color = _clean_color(creator.get("color"))
        agents.append(
            CodingAgent(
                label=label,
                agent=str(display.get("agent") or obj.get("agentName") or ""),
                model=str(display.get("model") or ""),
                lab=lab,
                color=color,
                score=round(float(score) * 100, 1),
                cost_usd=_num(mean.get("costUsd")),
                wall_sec=_num(mean.get("agentWallTimeSec")),
                is_highlighted=bool(obj.get("isHighlighted")),
                is_default=bool(obj.get("isDefault")),
                is_router=bool(obj.get("isRouter")),
            )
        )
    if not agents:
        raise ParseError("coding-agents page has no scored agents")
    agents.sort(key=lambda a: (-a.score, a.label))
    return agents


def select_for_summary(agents: list[CodingAgent], n: int = 10) -> list[CodingAgent]:
    """The comparison table: the highlighted defaults AA shows up top, plus the Muse family so a
    cheap-vs-best read (Spark 1.3 vs Wonder Full / Wonder) is possible even when Spark is not
    highlighted. Sorted by score, de-duplicated, capped at ``n`` plus any Muse extras."""
    highlighted = [a for a in agents if a.is_highlighted]
    base = highlighted or agents[:8]
    # Always include Muse Spark variants when present: they are the cheap Meta fallback and the
    # subject of the Spark-vs-Wonder question, but AA does not highlight them.
    muse_extra = [a for a in agents if "muse spark" in a.label.lower() and a not in base]
    picked = list(dict.fromkeys([*base, *muse_extra]))
    picked.sort(key=lambda a: (-a.score, a.label))
    # Keep the table short but never drop a Muse extra to fit n.
    if len(picked) <= n:
        return picked
    head = [a for a in picked[:n] if a not in muse_extra]
    tail = [a for a in picked if a in muse_extra and a not in head]
    return head + tail


def _fmt_cost(cost: float | None) -> str:
    if cost is None:
        return "–"
    if cost >= 10:
        return f"${cost:,.2f}"
    if cost >= 1:
        return f"${cost:.2f}"
    return f"${cost:.2f}"


def _fmt_wall(wall: float | None) -> str:
    if wall is None:
        return "–"
    minutes = wall / 60
    if minutes >= 60:
        return f"{minutes / 60:.1f}h"
    if minutes >= 10:
        return f"{minutes:.0f}m"
    return f"{minutes:.1f}m"


def render_coding_section(agents: list[CodingAgent], changed_at: str) -> str:
    """An HTML section for the summary page: rank, agent, Index, API cost/task, wall time/task."""
    rows = "".join(
        f'<tr><td class="num">{i}</td><td>{html.escape(a.label)}</td><td>{html.escape(a.lab)}</td>'
        f'<td class="num">{a.score:.1f}</td><td class="num">{_fmt_cost(a.cost_usd)}</td>'
        f'<td class="num">{_fmt_wall(a.wall_sec)}</td></tr>'
        for i, a in enumerate(agents, 1)
    )
    desc = "; ".join(f"{i}. {a.label}: {a.score:.1f}" for i, a in enumerate(agents, 1))
    return (
        '<section id="coding-agents">\n<h2>Coding Agent Index</h2>\n'
        "<p>Artificial Analysis Coding Agent Index v2.0 (higher is better): composite of FrontierCode v1.1, "
        "Terminal-Bench 4.0 and SWE-Atlas-QnA-AA, each benchmark equally weighted. Cost is average pay-per-token "
        "API cost per task; many harnesses are used through subscriptions instead. "
        f"Data last changed {html.escape(changed_at)}. Source: "
        '<a href="https://artificialanalysis.ai/agents/coding-agents">artificialanalysis.ai/agents/coding-agents</a>.</p>\n'
        f'<p class="muted">Highlighted defaults plus Muse Spark variants for the cheap-vs-best read: '
        "Muse Spark 1.3 (max) is the cheap Meta API fallback, Muse Wonder Full / Wonder are the best Meta scores.</p>\n"
        f'<div role="img" aria-label="{html.escape(desc)}"></div>\n'
        "<details open><summary>Table view</summary>\n"
        "<table><thead><tr><th>#</th><th>Agent</th><th>Lab</th><th>Index</th>"
        "<th>Cost / task (API)</th><th>Time / task</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></details>\n</section>"
    )


def coding_state(agents: list[CodingAgent]) -> list[dict]:
    return [
        {
            "label": a.label,
            "agent": a.agent,
            "model": a.model,
            "lab": a.lab,
            "color": a.color,
            "score": a.score,
            "cost_usd": round(a.cost_usd, 2) if a.cost_usd is not None else None,
            "wall_sec": round(a.wall_sec, 1) if a.wall_sec is not None else None,
        }
        for a in agents
    ]
