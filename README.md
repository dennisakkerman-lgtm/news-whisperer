# News Whisperer · Basic-Fit

Weekly discovery pipeline for the Basic-Fit account (gym/fitness, fr-FR, France).
It collects signals from six layers, scores them deterministically (audience + timeliness + gap + testability, /20),
cross-references Basic-Fit's low AI-visibility prompts from PromptWatch, and publishes:

- a **panel** (read-only for the client, tagging + notes for Seeders), and
- a **weekly HTML digest** e-mailed every Monday.

## Layers and status

| Layer | Source | Needs | Status |
|---|---|---|---|
| 1 Community | Reddit official API | `REDDIT_CLIENT_ID`, `REDDIT_CLIENT_SECRET` | ready, needs keys |
| 1 Community | YouTube Data API v3 | `YOUTUBE_API_KEY` | ready, needs key |
| 1 Community | TikTok + Instagram via Apify | `APIFY_TOKEN` | ready, needs token |
| 2 AI visibility | PromptWatch (project `basic-fit-3`) | seed file `data/promptwatch_prompts.json` | live (153 prompts, 70 below 40.4%) |
| 3 SEO | Ahrefs API v3 | `AHREFS_API_KEY` | ready, needs key (endpoints unverified until first run) |
| 4 Search | Google Trends (pytrends) | – | live |
| 4 Search | Google Search Console | service account | placeholder, pending client access |
| 5 News | Google News RSS + FR sector RSS | – | live |
| 6 Seasonality | windows in config (rentrée, janvier, été) | – | live |

Collectors without credentials are **skipped**, not failed; the report's Sources table shows which ran.

## Config

Everything client-specific lives in `clients/basicfit.yaml` (brand, competitors, personas, include/exclude keywords,
sources, PromptWatch threshold, seasonality, scoring weights, output). A new client is a new YAML file plus
`WHISPERER_CLIENT=<name>`.

Refreshing PromptWatch: re-export the project's prompts (id, prompt, averageVisibilityScore, keywords) into
`data/promptwatch_prompts.json`. Claude can do this with the PromptWatch MCP (`listPrompts`, sorted by
`averageVisibilityScore`).

## Buckets

- **This week**: 15+/20, at most 5, not single-source-with-low-audience.
- **Monitor**: 10-14, or 15+ that needs corroboration.
- **Skip**: everything else, with the reason.

With `ANTHROPIC_API_KEY` set, the top 12 get a working title, brief keyword, 2-3 sentence rationale and a separate
narrative score (0-10) from Claude. The deterministic score is never changed by Claude.

## Run locally

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m whisperer run --no-email      # one pipeline run into data/whisperer.db
TEAM_PASSWORD=x CLIENT_PASSWORD=y .venv/bin/python -m whisperer serve   # panel on :8000
```

## Railway

One repo, three services in one project:

| Service | Start command | Schedule |
|---|---|---|
| `panel` | `python -m whisperer serve` | always on, public domain |
| `weekly-run` | `python -m whisperer run` | cron `0 5 * * 1` (Monday 05:00 UTC = 07:00 Paris summer time) |
| `Postgres` | Railway template | – |

Both app services get `DATABASE_URL=${{Postgres.DATABASE_URL}}` plus the keys from `.env.example`.
The team can also start a run from the panel ("Run now"; no e-mail is sent for those).

## Test mode

`python -m whisperer run --test` (or **Run test** in the panel, or `WHISPERER_TEST_MODE=1`) runs the real sources
and fills every source that has no credentials yet with sample signals marked `[TEST]` (links go to example.com).
Test runs get a banner in the panel and report, are invisible to the client login, send no e-mail unless
`--email` is passed (subject then starts with `[TEST]`), and can be deleted by the team.
