# Semis Substack Digest

A button-triggered pipeline that scrapes recent posts from a curated list of Substack authors, summarizes each one for a **semiconductor equities investment analyst** (long/short read-throughs, ticker call-outs, change-in-tone vs. the author's prior work), then writes the results into a Notion database.

## How to run

Go to the **Actions** tab → **"Daily Semis Substack Digest"** → **Run workflow**. The form lets you adjust:

| Input | Default | What it does |
|---|---|---|
| `since_days` | `1` | Look back this many days. Bump to `7` after a vacation. |
| `authors_filter` | _(blank)_ | Comma-separated author names from `authors.yml` to run only. Blank = all. |
| `skip_synthesis` | `false` | Skip the Opus cross-article synthesis (faster/cheaper). |
| `dry_run` | `false` | Run everything but skip the Notion write and the `seen.json` update. Useful for testing. |

A run typically takes 2-6 minutes and costs ~$0.20-$0.80 in Anthropic API spend.

## Required GitHub Actions secrets

Set under **Settings → Secrets and variables → Actions → New repository secret**:

| Name | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys |
| `NOTION_TOKEN` | notion.so/profile/integrations → your integration → Internal Integration Secret |
| `NOTION_PARENT_DB_ID` | the 32-char hex from your Notion database's URL |
| `SUBSTACK_COOKIE` | the value of the `substack.sid` cookie from a logged-in Substack browser session (DevTools → Application → Cookies → `https://substack.com`) |

If `SUBSTACK_COOKIE` is missing or expired, the run still works but paywalled posts will only get the public preview, with the summary clearly flagged as truncated.

## Refreshing the Substack cookie

Substack session cookies generally last several weeks. When the canary check fails, refresh:

1. Open https://substack.com in a regular browser tab — make sure you're signed in.
2. DevTools → Application → Cookies → `https://substack.com`
3. Copy the `Value` of the `substack.sid` row.
4. Update the `SUBSTACK_COOKIE` secret in this repo's Actions settings.

## Adding or removing authors

Edit [`authors.yml`](authors.yml). Both forms work:

```yaml
- name: Chipstrat
  url: https://www.chipstrat.com/         # publication URL
  paywalled: true
  tickers_focus: [NVDA, AMD]

- name: photoncap
  url: https://substack.com/@photoncap/posts   # user profile (resolved at runtime)
  paywalled: true
  tickers_focus: []
```

## Seeding "change-in-tone" context

The first run for any author has no prior summaries to compare against. Run the one-shot backfill once after adding new authors:

```bash
# locally, with env vars set
python -m src.backfill --per-author 15
# or specific authors
python -m src.backfill --per-author 15 --authors "Chipstrat,BEP Research"
```

Subsequent daily runs will automatically reference the most recent 5 cached summaries per author.

## What gets written to Notion

Each run creates new pages in your Notion database:

1. **One parent page** titled `YYYY-MM-DD — Semis Substack Digest`, Type=`Daily Synthesis`. Body is the Opus cross-article view.
2. **One child page per article** titled with the article title, Type=`Article Summary`. Body is the per-article note (TL;DR, key facts, long/short reads, change vs. prior work, etc.).

Tickers and tone are auto-extracted and tagged.

## Project layout

```
.github/workflows/daily.yml    button-triggered workflow
authors.yml                    list of Substack sources
src/
  fetch.py                     RSS + archive API + cookie-auth fetch
  extract.py                   HTML → clean text, paywall detection
  summarize.py                 per-article Claude Sonnet call
  synthesize.py                cross-article Claude Opus call
  notion_sink.py               write to Notion database
  state.py                     seen.json + per-author cache
  main.py                      orchestrator
  backfill.py                  one-shot history seeding
prompts/
  per_article.md               instructs Sonnet on output format
  synthesis.md                 instructs Opus on cross-author view
state/
  seen.json                    URLs already summarized (committed)
  cache/<author>/<slug>.json   raw text + past summaries
tests/test_smoke.py            import + helpers tests (no secrets needed)
```

## Local dev

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

export ANTHROPIC_API_KEY=...
export NOTION_TOKEN=...
export NOTION_PARENT_DB_ID=...
export SUBSTACK_COOKIE=...

python -m src.main --dry-run --since-days 3
python -m src.main --authors "Chipstrat" --since-days 7
pytest tests/
```
