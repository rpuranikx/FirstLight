# -*- coding: utf-8 -*-
"""
FirstLight - Job Fetcher
Polls Greenhouse, Lever, Ashby, Workable, and Rippling APIs for new
job postings matching your target roles, as defined in config.yml.

Usage:
    python main.py                   # run once, print results
    python main.py --notify          # run once + send Discord alert if new jobs found
    python main.py --watch           # poll continuously (every 5 min)
    python main.py --watch --interval 60  # poll every 60 seconds

GitHub Actions:
    Set DISCORD_WEBHOOK_URL as a repository secret.
    The workflow commits .seen_jobs.json back to main after each run.
"""

import argparse
import io
import json
import os
import sys
import time
from datetime import datetime

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
import yaml

from slugs import COMPANY_SLUGS

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
CONFIG_PATH           = os.path.join(os.path.dirname(__file__), "config.yml")
SEEN_PATH             = os.path.join(os.path.dirname(__file__), ".seen_jobs.json")
REQUEST_TIMEOUT       = 10   # seconds per HTTP call
DISCORD_WEBHOOK_URL   = os.environ.get("DISCORD_WEBHOOK_URL", "")
DISCORD_COLOR_NEW_JOB = 0x57F287  # green
DISCORD_COLOR_SUMMARY = 0x5865F2  # blurple

# ─────────────────────────────────────────────
# ANSI colours (works in Windows Terminal / VS Code)
# ─────────────────────────────────────────────
class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    GREEN  = "\033[92m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    RED    = "\033[91m"
    DIM    = "\033[2m"

def _p(color, *args):
    print(color + " ".join(str(a) for a in args) + C.RESET)


# ─────────────────────────────────────────────
# Seen-jobs persistence (deduplication)
# ─────────────────────────────────────────────
def load_seen() -> set:
    if os.path.exists(SEEN_PATH):
        with open(SEEN_PATH, "r") as f:
            return set(json.load(f))
    return set()


def save_seen(seen: set):
    with open(SEEN_PATH, "w") as f:
        json.dump(list(seen), f)


# ─────────────────────────────────────────────
# Config loader
# ─────────────────────────────────────────────
def load_config() -> dict:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def get_roles(cfg: dict) -> list[str]:
    return [r.lower() for r in cfg.get("Roles", [])]


def get_companies(cfg: dict) -> list[str]:
    raw = cfg.get("Companies", [])
    # Strip inline YAML comments (values after '#')
    cleaned = []
    for item in raw:
        if isinstance(item, str):
            cleaned.append(item.split("#")[0].strip())
    return cleaned


# ─────────────────────────────────────────────
# Role matching
# ─────────────────────────────────────────────
ROLE_ALIASES = {
    "solutions engineer":        ["solutions engineer", "solutions eng"],
    "sales engineer":            ["sales engineer", "sales eng"],
    "pre-sales engineer":        ["pre-sales", "presales"],
    "consultant":                ["consultant"],
    "implementation consultant": ["implementation consultant", "implementation eng"],
    "technical consultant":      ["technical consultant", "tech consultant"],
    "forward deployed engineer": ["forward deployed", "fde", "field engineer",
                                   "deployment engineer"],
}

def _role_tokens(roles: list[str]) -> list[str]:
    tokens = []
    for r in roles:
        for alias in ROLE_ALIASES.get(r, [r]):
            tokens.append(alias.lower())
    return tokens


def matches_role(title: str, role_tokens: list[str]) -> bool:
    title_lower = title.lower()
    return any(t in title_lower for t in role_tokens)


# ─────────────────────────────────────────────
# ATS Fetchers
# ─────────────────────────────────────────────
def _get(url: str, **kwargs) -> dict | list | None:
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, **kwargs)
        if r.status_code == 200:
            return r.json()
    except Exception:
        pass
    return None


def fetch_greenhouse(company: str, slug: str) -> list[dict]:
    url  = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    data = _get(url)
    if not data:
        return []
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "id":      f"greenhouse:{slug}:{j.get('id')}",
            "company": company,
            "title":   j.get("title", ""),
            "url":     j.get("absolute_url", ""),
            "location": j.get("location", {}).get("name", ""),
            "posted":  j.get("updated_at", ""),
        })
    return jobs


def fetch_lever(company: str, slug: str) -> list[dict]:
    url  = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get(url)
    if not data or not isinstance(data, list):
        return []
    jobs = []
    for j in data:
        loc_list = j.get("categories", {}).get("location", "")
        jobs.append({
            "id":      f"lever:{slug}:{j.get('id')}",
            "company": company,
            "title":   j.get("text", ""),
            "url":     j.get("hostedUrl", ""),
            "location": loc_list,
            "posted":  datetime.fromtimestamp(
                           j.get("createdAt", 0) / 1000
                       ).isoformat() if j.get("createdAt") else "",
        })
    return jobs


def fetch_ashby(company: str, slug: str) -> list[dict]:
    url  = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    data = _get(url)
    if not data:
        return []
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "id":      f"ashby:{slug}:{j.get('id')}",
            "company": company,
            "title":   j.get("title", ""),
            "url":     j.get("jobUrl", ""),
            "location": j.get("location", ""),
            "posted":  j.get("publishedAt", ""),
        })
    return jobs


def fetch_workable(company: str, slug: str) -> list[dict]:
    url  = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    data = _get(url)
    if not data:
        return []
    jobs = []
    for j in data.get("jobs", []):
        jobs.append({
            "id":      f"workable:{slug}:{j.get('shortcode')}",
            "company": company,
            "title":   j.get("title", ""),
            "url":     f"https://apply.workable.com/{slug}/j/{j.get('shortcode', '')}",
            "location": j.get("location", ""),
            "posted":  j.get("published_on", ""),
        })
    return jobs


def fetch_rippling(company: str, slug: str) -> list[dict]:
    url  = (f"https://api.rippling.com/platform/api/ats/v2/board/{slug}/jobs"
            f"?groupJobsByLocation=true&page=0&pageSize=500")
    data = _get(url)
    if not data:
        return []
    jobs = []
    for j in data.get("items", []):
        jobs.append({
            "id":      f"rippling:{slug}:{j.get('id')}",
            "company": company,
            "title":   j.get("name", ""),
            "url":     f"https://ats.rippling.com/{slug}",
            "location": ", ".join(
                loc.get("city", "") for loc in j.get("locations", [])
            ),
            "posted":  j.get("publishedAt", ""),
        })
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever":      fetch_lever,
    "ashby":      fetch_ashby,
    "workable":   fetch_workable,
    "rippling":   fetch_rippling,
}


# ─────────────────────────────────────────────
# Core scan
# ─────────────────────────────────────────────
def scan(companies: list[str], role_tokens: list[str], seen: set) -> list[dict]:
    new_jobs = []

    for company in companies:
        mapping = COMPANY_SLUGS.get(company)
        if not mapping:
            _p(C.DIM, f"  [skip] {company} — no slug mapping in slugs.py")
            continue

        ats    = mapping["ats"]
        slug   = mapping["slug"]
        fetch  = FETCHERS.get(ats)

        if not fetch:
            _p(C.DIM, f"  [skip] {company} — unknown ATS '{ats}'")
            continue

        try:
            all_jobs = fetch(company, slug)
        except Exception as e:
            _p(C.RED, f"  [error] {company} ({ats}/{slug}): {e}")
            continue

        matched = [j for j in all_jobs if matches_role(j["title"], role_tokens)]

        for job in matched:
            if job["id"] not in seen:
                new_jobs.append(job)
                seen.add(job["id"])

        status = f"{len(matched)} match(es) / {len(all_jobs)} total"
        _p(C.DIM, f"  {company:<25} [{ats:<11}]  {status}")

    return new_jobs


def print_job(job: dict):
    _p(C.BOLD + C.GREEN, f"\n  >>> {job['title']}")
    _p(C.CYAN,           f"      {job['company']}")
    if job.get("location"):
        _p("",            f"      Location: {job['location']}")
    if job.get("posted"):
        _p(C.DIM,         f"      Posted:   {job['posted'][:19]}")
    _p(C.YELLOW,          f"      Apply:    {job['url']}")


# -------------------------------------------------
# Discord notifications
# -------------------------------------------------
def _discord_post(payload: dict) -> bool:
    """POST a payload to the Discord webhook. Returns True on success."""
    if not DISCORD_WEBHOOK_URL:
        return False
    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )
        return r.status_code in (200, 204)
    except Exception as e:
        _p(C.RED, f"  [discord] failed to send: {e}")
        return False


def notify_discord_jobs(new_jobs: list[dict]) -> None:
    """Send one Discord embed per new job, then a summary embed."""
    if not DISCORD_WEBHOOK_URL:
        _p(C.DIM, "  [discord] DISCORD_WEBHOOK_URL not set — skipping notification")
        return

    for job in new_jobs:
        location = job.get("location") or "Remote / Not specified"
        posted   = (job.get("posted") or "")[:19]

        embed = {
            "title":       job["title"],
            "url":         job["url"],
            "color":       DISCORD_COLOR_NEW_JOB,
            "description": f"**{job['company']}**  |  {location}",
            "fields": [
                {"name": "Apply",  "value": job["url"], "inline": False},
            ],
            "footer": {"text": f"Posted: {posted}  |  FirstLight"},
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }
        _discord_post({"embeds": [embed]})
        time.sleep(0.5)  # avoid Discord rate-limit (50 req/s)

    # Summary embed
    summary_embed = {
        "title":       f"{len(new_jobs)} new job(s) found by FirstLight",
        "color":       DISCORD_COLOR_SUMMARY,
        "description": "\n".join(
            f"- [{j['title']} @ {j['company']}]({j['url']})" for j in new_jobs
        ),
        "footer": {"text": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
    }
    _discord_post({"embeds": [summary_embed]})
    _p(C.GREEN, f"  [discord] Notified for {len(new_jobs)} job(s)")


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def run_once(seen: set, notify: bool = False) -> list[dict]:
    cfg         = load_config()
    roles       = get_roles(cfg)
    role_tokens = _role_tokens(roles)
    companies   = get_companies(cfg)

    _p(C.BOLD, f"\n{'='*60}")
    _p(C.BOLD, f"  FirstLight  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _p(C.BOLD, f"{'='*60}")
    _p(C.DIM,  f"  Roles:     {', '.join(roles)}")
    _p(C.DIM,  f"  Companies: {len(companies)} targets\n")

    new_jobs = scan(companies, role_tokens, seen)

    if new_jobs:
        _p(C.BOLD + C.GREEN, f"\n  *** {len(new_jobs)} new job(s) found! ***\n")
        for job in new_jobs:
            print_job(job)
        if notify:
            notify_discord_jobs(new_jobs)
    else:
        _p(C.DIM, "\n  [ok] No new matching jobs since last scan.")

    return new_jobs


def main():
    parser = argparse.ArgumentParser(description="FirstLight job watcher")
    parser.add_argument("--watch",    action="store_true",
                        help="Continuously poll (don't exit after one scan)")
    parser.add_argument("--interval", type=int, default=300,
                        help="Seconds between polls in --watch mode (default: 300)")
    parser.add_argument("--notify",   action="store_true",
                        help="Send Discord notification when new jobs are found")
    args = parser.parse_args()

    # Enable ANSI escape codes on Windows
    if sys.platform == "win32":
        os.system("")

    seen = load_seen()

    if args.watch:
        _p(C.BOLD, f"  Watching every {args.interval}s -- press Ctrl+C to stop")
        try:
            while True:
                run_once(seen, notify=args.notify)
                save_seen(seen)
                _p(C.DIM, f"\n  Next scan in {args.interval}s...")
                time.sleep(args.interval)
        except KeyboardInterrupt:
            _p(C.YELLOW, "\n  Stopped.")
    else:
        run_once(seen, notify=args.notify)
        save_seen(seen)


if __name__ == "__main__":
    main()
