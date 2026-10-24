# artificial-analysis-xhigh

An auto-updating link to the [Artificial Analysis](https://artificialanalysis.ai/models)
Intelligence Index chart with every `max` variant swapped for its `xhigh` variant.

## Why I made this

Artificial Analysis charts Claude, GPT and other models at their `max` effort by default, and
xhigh is cheaper and faster. Artificial Analysis's own per-task measurements show it for every model
in the default chart that has both variants (snapshot from 2026-10-24, max → xhigh):

| Model | Cost per task | Time per task | Output tokens per task | Intelligence Index |
| --- | --- | --- | --- | --- |
| Claude Opus 5.5 | $5.98 → $3.46 (−42%) | −38% | −45% | 57.6 → 56.0 |
| Claude Sonnet 5.5 | $5.46 → $2.01 (−63%) | −55% | −62% | 56.0 → 51.9 |
| Claude Fable 5.1 | $7.63 → $5.98 (−22%) | −18% | −22% | 53.4 → 53.2 |
| Claude Haiku 5.5 | $0.213 → $0.124 (−42%) | −31% | −45% | 43.4 → 41.2 |
| GPT-6 Astra | $3.26 → $2.31 (−29%) | −35% | −38% | 52.7 → 52.4 |
| GPT-6.1 Sol | $0.724 → $0.393 (−46%) | −50% | −54% | 51.8 → 51.0 |
| GPT-6 Luna | $0.068 → $0.042 (−38%) | −38% | −45% | 38.1 → 34.6 |

xhigh uses 22% to 62% fewer output tokens, which is where the savings come from. The trade-off is a
slightly lower Intelligence Index score, 0.2 to 4.1 points lower in this snapshot. Anthropic's docs
describe `max` as ["absolute maximum capability with no constraints on token spending"](https://platform.claude.com/docs/en/build-with-claude/effort#effort-levels).

The site does show xhigh scores, but you have to pick every model by hand in its model selector.
This repo builds the link that does that for me and keeps it current as the site's default models
change.

**Bookmark: https://honvl.github.io/artificial-analysis-xhigh/** redirects to the latest swapped
link. Add `#info` to the end to see which models were swapped instead of redirecting. See
[Add it to your bookmarks](#add-it-to-your-bookmarks) for the easy way to save it.

## How it works

The site picks its default 27 models server-side and accepts an override as
`/models?models=slug1,slug2,...`. Each effort level is its own slug
(`claude-opus-5-5` is Max, `claude-opus-5-5-xhigh` is Xhigh).

`aa_xhigh.py` fetches the page and reads the data the site embeds in it:

1. The default selection (`chartDefaultSelected`), in the site's order.
2. The full variant list, each entry with a `releaseSlug` and an effort label in its name.
3. For every default model labelled Max, it finds the sibling with the same `releaseSlug`, the
   same qualifier (e.g. "Default Fallback") and the label Xhigh, and substitutes it.

Models that are not Max are left alone. A Max model with no xhigh sibling (GLM-5.3, Muse Wonder
and DeepSeek V4.1 Flash 1017 at the time of writing) stays on Max, because it has no xhigh entry
on the site to swap to. Use `--unmatched drop` to remove such models from the chart instead.

The scheduled workflow re-runs this every 3 hours and commits `docs/` only when the link changes.

## Setup

Needs a public repo (Pages on private repos needs a paid plan).

```bash
gh repo create artificial-analysis-xhigh --public --source=. --push
gh api -X POST repos/{owner}/artificial-analysis-xhigh/pages -f 'source[branch]=main' -f 'source[path]=/docs'
gh workflow run update-link
```

Or in the web UI: Settings, Pages, deploy from branch `main`, folder `/docs`.

Then the stable URL is `https://<your-username>.github.io/artificial-analysis-xhigh/`. Raw link as
text: `.../link.txt`. Details as JSON: `.../latest.json`.

## Add it to your bookmarks

The stable URL redirects, so pressing your browser's "bookmark this page" shortcut on it saves the
long chart URL it lands on, which goes stale when the default models change. Instead:

- **Drag (easiest):** open the [bookmark page](https://honvl.github.io/artificial-analysis-xhigh/bookmark.html)
  and drag its link onto your bookmarks bar.
- **Paste the URL:** copy `https://honvl.github.io/artificial-analysis-xhigh/`, then in Chrome press
  Cmd/Ctrl+Shift+O, use the menu at the top right, choose Add new bookmark and paste. In Firefox,
  Cmd/Ctrl+Shift+O, Organize, New Bookmark. Safari has no add-by-URL, so use the drag link.

## Use it locally

No dependencies. Needs Python 3.9+ (tested on 3.12).

```bash
python3 aa_xhigh.py                 # print the link
python3 aa_xhigh.py --explain       # also show what was swapped
python3 aa_xhigh.py --open          # open it in your browser
python3 aa_xhigh.py --unmatched drop
python3 aa_xhigh.py --write docs    # regenerate the Pages output
```

## Tests

```bash
python3 -m unittest discover -s tests -v
AA_LIVE=1 python3 -m unittest discover -s tests -k Live -v   # also hit the real site
```

## When it breaks

The script reads the Next.js data embedded in the page, which is not a public API. If the site
changes its shape the script exits with an error (and checks that every slug it emits exists in
the site's own model list) rather than publishing a bad link. The workflow run then fails, GitHub
emails you, and `docs/` keeps pointing at the last good link.

GitHub disables scheduled workflows after 60 days without repo activity, so the workflow adds an
empty keepalive commit when the last commit is older than 50 days.
