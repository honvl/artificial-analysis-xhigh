"""Shared pieces: fetching a page, decoding its Next.js flight data, reading effort labels."""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path

SOURCE = "https://artificialanalysis.ai/models"
USER_AGENT = "Mozilla/5.0 (compatible; artificial-analysis-xhigh/1.0; +https://github.com)"

# Effort levels, lowest to highest.
ORDER = ("minimal", "low", "medium", "high", "xhigh", "max")
EFFORTS = frozenset(ORDER)

# The links we publish. The first is served at the site root, the rest in subfolders.
LEVELS = ("xhigh", "high", "medium")


class ParseError(RuntimeError):
    """A page no longer looks the way this code expects."""


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


def display_name(name: str) -> str:
    """The site's name without its fallback note, for showing to people:
    "Claude Opus 5.5 (Max, Default Fallback)" -> "Claude Opus 5.5 (Max)"."""
    m = re.search(r"\(([^()]*)\)\s*$", name)
    if not m:
        return name
    tokens = [t.strip() for t in m.group(1).split(",") if not t.strip().lower().endswith("fallback")]
    base = name[: m.start()].rstrip()
    return f"{base} ({', '.join(tokens)})" if tokens else base


def level_href(level: str, prefix: str = "", suffix: str = "") -> str:
    """Relative link to a level's page: the first level lives at the root, the rest in folders."""
    sub = "" if level == LEVELS[0] else f"{level}/"
    return ((prefix + sub) or "./") + suffix


def read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def write_text(path: Path, text: str) -> bool:
    """Write only if the content differs. True if the file changed."""
    if path.exists() and path.read_text() == text:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return True
