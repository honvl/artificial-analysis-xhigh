# artificial-analysis-xhigh

[Artificial Analysis](https://artificialanalysis.ai/models) charts Claude, GPT and others at their
`max` effort by default. This repo keeps a link to the same default chart with every `max`
variant swapped for its `xhigh` variant, and keeps that link current.

Bookmark the GitHub Pages URL (see Setup) and it always redirects to the latest swapped link.
Add `#info` to the end to see which models were swapped instead of redirecting.

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

Then bookmark `https://<your-username>.github.io/artificial-analysis-xhigh/`. Raw link as text:
`.../link.txt`. Details as JSON: `.../latest.json`.

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
