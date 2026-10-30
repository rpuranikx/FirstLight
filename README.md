# FirstLight
Retrieves job postings ASAP from common startup sources that provide API access,
and tells you who to message at the company to get in the door.

## What a run does
1. **Builds the company list.** Your hand-picked companies in `config.yml`, plus
   high-growth startups discovered automatically from the YC directory
   (active, hiring, 15–1000 people, US or remote; ~575 companies today).
   Optional: Apollo company search for non-YC venture-backed startups.
2. **Finds where each company posts jobs.** First it probes likely slugs on Ashby,
   Greenhouse, Lever and SmartRecruiters. If none answer, it opens the company's own
   careers page (`/careers`, `/jobs`, `careers.<domain>`, ...) and uses whatever is
   behind it: an embedded board, a Workday site, or the company's self-hosted job
   pages (read from the schema.org JobPosting data sites publish for Google Jobs).
   Results are cached in `.board_cache.json`. If a hand-picked board stops working,
   it is re-probed and fixed automatically (`[heal]` in the log).
3. **Scans every board in parallel** and keeps jobs matching your roles,
   location (Chicagoland or remote US), pay floor and freshness.
4. **Picks 3 people to message** for each new job, ranked: names, titles and
   emails come from Hunter and/or Apollo; Claude checks public sources with web
   search and ranks them with a one-line reason and an opening angle each.
5. **Sends a Discord alert** per job with the apply link and the 3 picks.

The first time a company is scanned, matches older than 48h (or undated) are sent
as one digest message instead of one alert each, so adding hundreds of companies
doesn't flood Discord. Nothing is dropped silently.

Not covered yet: iCIMS, BambooHR, and custom career pages that only render jobs
with JavaScript and publish no JobPosting data.

## Setup
Repository secrets (Settings → Secrets and variables → Actions):

| Secret | Needed for | Notes |
| --- | --- | --- |
| `DISCORD_WEBHOOK_URL` | alerts | required |
| `ANTHROPIC_API_KEY` | ranking who to message | without it, picks are ranked by title only |
| `HUNTER_API_KEY` | names + emails | free tier: 25 domain searches/month, cached 30 days per company |
| `APOLLO_API_KEY` | names, titles, LinkedIn; company discovery | optional |

Tune everything else in `config.yml`: `Roles`, `Filters`, `Discovery`, `Contacts`, and
`Profile` (what Claude knows about you when choosing who to message).

## Run locally
```
pip install -r requirements.txt
python main.py            # one scan, print results
python main.py --notify   # one scan + Discord
python -m pytest -q       # tests
```

## Notes
- People data (`.contacts_cache.json`) is never committed; CI keeps it in the Actions cache.
- Claude never invents an email: emails only come from Hunter/Apollo.
- No LinkedIn scraping. LinkedIn URLs only appear when Hunter/Apollo/public pages provide them.
