import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import aa_coding as cod  # noqa: E402
import aa_charts as ch  # noqa: E402
import aa_common as common  # noqa: E402


def agent_obj(label, score, cost=None, wall=None, highlighted=False, default=True, lab="Meta", color="#0089f4"):
    return {
        "displayLabel": label,
        "agentName": label.split(" - ")[0],
        "provider": "meta",
        "display": {"agent": label.split(" - ")[0], "model": label.split(" - ")[1] if " - " in label else label},
        "indexScore": score,
        "mean": {"reward": score, "costUsd": cost, "agentWallTimeSec": wall},
        "agentCreator": {"slug": "meta", "name": lab, "color": color},
        "modelCreators": [{"slug": "meta", "name": lab, "color": color}],
        "isDefault": default,
        "isHighlighted": highlighted,
        "isRouter": False,
    }


def make_coding_page(objs, chunk_size=97):
    # The real page embeds agent records inside flight chunks; the parser only needs
    # displayLabel + indexScore + mean to survive chunk splits.
    flight = "28:" + json.dumps({"agents": objs}) + "\n"
    chunks = [flight[i : i + chunk_size] for i in range(0, len(flight), chunk_size)]
    pushes = "".join(f"<script>self.__next_f.push([1,{json.dumps(c)}])</script>" for c in chunks)
    return "<html>" + pushes + "</html>"


OBJS = [
    agent_obj("Claude Code - Opus 5.5 (max)", 0.617, 11.42, 3661.0, True, True, "Anthropic", "#cc785c"),
    agent_obj("Codex - GPT-6.1 Sol (max)", 0.541, 1.50, 1421.0, True, True, "OpenAI", "#1f1f1f"),
    agent_obj("Antigravity CLI - Gemini 4 Argon (high)", 0.532, 5.86, 2081.0, True, True, "Google", "#34a853"),
    agent_obj("Muse Code - Muse Wonder Full (max)", 0.589, None, 2096.0, True, True, "Meta", "#0089f4"),
    agent_obj("Muse Code - Muse Wonder (max)", 0.520, 8.05, 1472.0, True, True, "Meta", "#0089f4"),
    agent_obj("Grok Build - Grok 4.7 (xhigh)", 0.466, 9.48, 2146.0, True, True, "SpaceXAI", "#736cd3"),
    agent_obj("Muse Code - Muse Spark 1.3 (max)", 0.457, 3.91, 1398.0, False, False, "Meta", "#0089f4"),
    agent_obj("Muse Code - Muse Spark Open (xhigh)", 0.263, 3.54, 1558.0, False, True, "Meta", "#0089f4"),
]


class Parse(unittest.TestCase):
    def test_parses_across_chunks(self):
        agents = cod.coding_agents(make_coding_page(OBJS, chunk_size=11))
        self.assertEqual(len(agents), len(OBJS))
        by = {a.label: a for a in agents}
        self.assertAlmostEqual(by["Muse Code - Muse Spark 1.3 (max)"].score, 45.7)
        self.assertAlmostEqual(by["Muse Code - Muse Spark 1.3 (max)"].cost_usd, 3.91)
        self.assertEqual(by["Muse Code - Muse Wonder Full (max)"].cost_usd, None)
        self.assertEqual(by["Muse Code - Muse Wonder Full (max)"].lab, "Meta")

    def test_sorted_by_score(self):
        agents = cod.coding_agents(make_coding_page(OBJS))
        scores = [a.score for a in agents]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertEqual(agents[0].label, "Claude Code - Opus 5.5 (max)")

    def test_missing_data_is_parse_error(self):
        with self.assertRaises(common.ParseError):
            cod.coding_agents("<html></html>")

    def test_select_keeps_highlighted_plus_spark(self):
        agents = cod.coding_agents(make_coding_page(OBJS))
        picked = cod.select_for_summary(agents)
        labels = [a.label for a in picked]
        # All 6 highlighted defaults are in, plus both Spark variants even though Spark 1.3 is not highlighted.
        self.assertIn("Muse Code - Muse Spark 1.3 (max)", labels)
        self.assertIn("Muse Code - Muse Spark Open (xhigh)", labels)
        self.assertIn("Muse Code - Muse Wonder Full (max)", labels)
        self.assertLessEqual(len(picked), 12)


class Render(unittest.TestCase):
    def test_section_has_table_and_source(self):
        agents = cod.select_for_summary(cod.coding_agents(make_coding_page(OBJS)))
        section = cod.render_coding_section(agents, "2030-01-01 00:00 UTC")
        self.assertIn('id="coding-agents"', section)
        self.assertIn("Muse Code - Muse Spark 1.3 (max)", section)
        self.assertIn("45.7", section)
        self.assertIn("$3.91", section)
        self.assertIn("artificialanalysis.ai/agents/coding-agents", section)
        self.assertIn("2030-01-01 00:00 UTC", section)

    def test_state_round_trips(self):
        agents = cod.select_for_summary(cod.coding_agents(make_coding_page(OBJS)))
        state = cod.coding_state(agents)
        self.assertEqual(state[0]["label"], agents[0].label)
        self.assertEqual(state[0]["score"], agents[0].score)


class Integration(unittest.TestCase):
    def test_write_charts_includes_coding(self):
        import aa_xhigh as aa

        models = [
            {"slug": "opus", "name": "Claude Opus 5.5 (Max)", "intelligenceIndex": 57.6,
             "modelCreatorName": "Anthropic", "modelCreatorColor": "#cc785c"},
            {"slug": "muse", "name": "Muse Wonder (Max)", "intelligenceIndex": 52.4,
             "modelCreatorName": "Meta", "modelCreatorColor": "#0089f4"},
        ]
        variants = [{"slug": m["slug"], "name": m["name"], "releaseSlug": m["slug"]} for m in models]
        boards = ch.leaderboards(models, variants, grades=("max",), n=10)
        coding = cod.select_for_summary(cod.coding_agents(make_coding_page(OBJS)))
        state = cod.coding_state(coding)
        section = cod.render_coding_section(coding, "2030-01-01 00:00 UTC")
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            self.assertTrue(
                ch.write_charts(
                    boards, out, now=datetime(2030, 1, 1, tzinfo=timezone.utc),
                    coding_agents=state, coding_changed_at="2030-01-01 00:00 UTC",
                    coding_section=section,
                )
            )
            page = (out / "summary.html").read_text()
            self.assertIn('id="coding-agents"', page)
            self.assertIn('href="#coding-agents"', page)
            self.assertIn("Muse Code - Muse Spark 1.3 (max)", page)
            data = json.loads((out / "charts.json").read_text())
            self.assertEqual(data["coding_agents"], state)
            self.assertEqual(data["coding_changed_at"], "2030-01-01 00:00 UTC")
            # Idempotent when nothing changes.
            self.assertFalse(
                ch.write_charts(
                    boards, out, now=datetime(2031, 1, 1, tzinfo=timezone.utc),
                    coding_agents=state, coding_changed_at="2030-01-01 00:00 UTC",
                    coding_section=section,
                )
            )


if __name__ == "__main__":
    unittest.main()
