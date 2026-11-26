# FirstLight

Job alerts that don't miss jobs. Every 15 minutes it reads about 1,800 company job boards
directly, plus job feeds (VC portfolio job boards, Built In Chicago, HN Who's Hiring, remote
boards), filters with plain rules, and pings Discord with anything new that fits. No LLM calls.

## How it works

1. **Watchlist** (`python -m firstlight watchlist`, weekly): companies from YC, a16z, VC portfolio
   boards and the profile's own lists. Each company's job board comes from a link the company
   published, or a board named after it that confirms the company name. Nothing is guessed, and
   `coverage.md` lists every company with its status, including the ones we can't read.
2. **Run** (`python -m firstlight run`, every 15 min): reads every verified board and every feed,
   runs the filters, fills in descriptions for new jobs, merges duplicates, and alerts.
   The first run sends one digest of everything open, never a silent skip.
3. **Miss check** (`python -m firstlight why <job url>`): for a job you found yourself, says
   whether the company is watched, whether FirstLight saw it, and which rule dropped it.

## Profiles

Everything specific to one search lives in `profiles/<name>.yml`: sources, named companies,
blocked companies, filter overrides and which Discord secrets to use. Copy one to start a new
search. State (seen jobs, the watchlist) lives on the `firstlight-state` branch.

## Setup

Repo secrets: `DISCORD_JOBS_WEBHOOK` (one ping per job) and `DISCORD_SUMMARY_WEBHOOK`
(every-10 summaries, daily meh digest and board health). `DISCORD_WEBHOOK_URL` works as a
fallback for both.
