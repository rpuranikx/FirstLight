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
import html
import io
import json
import os
import re
import sys
import time
from datetime import datetime, timezone

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
# ─────────────────────────────────────────────
# Role matching & Advanced Filters
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


def is_excluded_title(title: str) -> bool:
    """Excludes Director roles and Manager roles (unless Product Manager)."""
    t = title.lower()
    if "director" in t:
        return True
    if "manager" in t or "mgmt" in t or "management" in t:
        if "product manager" not in t:
            return True
    return False


def matches_role(title: str, role_tokens: list[str]) -> bool:
    if is_excluded_title(title):
        return False
    title_lower = title.lower()
    return any(t in title_lower for t in role_tokens)


def is_chicagoland(text: str) -> bool:
    t = text.lower()
    tokens = [
        "chicago", "chicagoland", "illinois", ", il", " il ",
        "naperville", "evanston", "schaumburg", "oak brook", "downers grove"
    ]
    return any(tok in t for tok in tokens)


def matches_location(job: dict) -> bool:
    """
    1) In-Office or Hybrid is cool if location is Chicago/Chicagoland.
    2) If not Chicagoland, must be remote (and US-based / worldwide).
    """
    loc = job.get("location", "")
    workplace = job.get("workplace", "")
    is_remote = job.get("is_remote")

    combined = f"{loc} {workplace}".lower()
    if is_chicagoland(combined):
        return True

    # Exclude non-US international locations unless US is also mentioned
    international_tokens = [
        "india", "uk", "london", "emea", "apac", "germany", "france",
        "singapore", "japan", "australia", "canada", "brazil", "netherlands"
    ]
    if any(token in combined for token in international_tokens):
        if not any(us_tok in combined for us_tok in ["united states", "usa", "us remote", "remote - us"]):
            return False

    # Otherwise, must be remote
    if is_remote is True:
        return True
    return any(term in combined for term in ["remote", "anywhere", "distributed", "virtual"])


def extract_compensation(text: str) -> int | None:
    """Extracts max annualized base salary if explicitly mentioned."""
    if not text:
        return None
    matches = re.finditer(
        r"\$\s*([0-9]{1,3}(?:,[0-9]{3})+|[0-9]{2,3}(?:\.[0-9]+)?\s*k)",
        text,
        re.IGNORECASE,
    )
    nums = []
    for m in matches:
        raw = m.group(1).lower().replace(",", "").replace(" ", "")
        if "k" in raw:
            try:
                nums.append(float(raw.replace("k", "")) * 1000)
            except ValueError:
                pass
        else:
            try:
                nums.append(float(raw))
            except ValueError:
                pass
    if nums:
        return int(max(nums))
    return None


def matches_compensation(job: dict, min_salary: int = 115000) -> bool:
    """If compensation is provided, it should be above $115k."""
    comp = job.get("compensation")
    if comp is not None:
        return comp >= min_salary
    return True


def humanize_time(iso_or_str: str) -> str:
    """Converts ISO or raw timestamp into friendly readable string (e.g. '2 hours ago')."""
    if not iso_or_str:
        return "Recently"
    clean = iso_or_str.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(clean)
    except Exception:
        return iso_or_str[:10]

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    secs = int((now - dt).total_seconds())

    if secs < 60:
        return "Just now"
    if secs < 3600:
        m = secs // 60
        return f"{m}m ago"
    if secs < 86400:
        h = secs // 3600
        return f"{h}h ago"
    d = secs // 86400
    if d == 1:
        return "Yesterday"
    if d < 30:
        return f"{d} days ago"
    return f"{d // 30} months ago"


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
    url  = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    data = _get(url)
    if not data:
        return []
    jobs = []
    for j in data.get("jobs", []):
        content = html.unescape(j.get("content", ""))
        comp = extract_compensation(content)
        loc = j.get("location", {}).get("name", "")
        jobs.append({
            "id":           f"greenhouse:{slug}:{j.get('id')}",
            "company":      company,
            "title":        j.get("title", ""),
            "url":          j.get("absolute_url", ""),
            "location":     loc,
            "workplace":    "",
            "is_remote":    "remote" in loc.lower(),
            "compensation": comp,
            "posted":       j.get("updated_at", ""),
        })
    return jobs


def fetch_lever(company: str, slug: str) -> list[dict]:
    url  = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    data = _get(url)
    if not data or not isinstance(data, list):
        return []
    jobs = []
    for j in data:
        loc_str = j.get("categories", {}).get("location", "")
        workplace = j.get("workplaceType", "")
        desc = j.get("descriptionPlain", "")
        comp = extract_compensation(desc)
        jobs.append({
            "id":           f"lever:{slug}:{j.get('id')}",
            "company":      company,
            "title":        j.get("text", ""),
            "url":          j.get("hostedUrl", ""),
            "location":     loc_str,
            "workplace":    workplace,
            "is_remote":    workplace.lower() == "remote" or "remote" in loc_str.lower(),
            "compensation": comp,
            "posted":       datetime.fromtimestamp(
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
        loc = j.get("location", "")
        workplace = j.get("workplaceType", "") or ""
        desc = j.get("descriptionPlain", "")
        comp = extract_compensation(desc)
        # Check Ashby structured compensation field if available
        if not comp and isinstance(j.get("compensation"), dict):
            comp_val = j["compensation"].get("maxSalary") or j["compensation"].get("minSalary")
            if comp_val:
                comp = int(comp_val)

        jobs.append({
            "id":           f"ashby:{slug}:{j.get('id')}",
            "company":      company,
            "title":        j.get("title", ""),
            "url":          j.get("jobUrl", ""),
            "location":     loc,
            "workplace":    workplace,
            "is_remote":    j.get("isRemote") is True or "remote" in loc.lower(),
            "compensation": comp,
            "posted":       j.get("publishedAt", ""),
        })
    return jobs


def fetch_workable(company: str, slug: str) -> list[dict]:
    url  = f"https://apply.workable.com/api/v1/widget/accounts/{slug}"
    data = _get(url)
    if not data:
        return []
    jobs = []
    for j in data.get("jobs", []):
        loc = j.get("location", "")
        is_tele = j.get("telecommuting", False)
        desc = j.get("description", "")
        comp = extract_compensation(desc)
        jobs.append({
            "id":           f"workable:{slug}:{j.get('shortcode')}",
            "company":      company,
            "title":        j.get("title", ""),
            "url":          f"https://apply.workable.com/{slug}/j/{j.get('shortcode', '')}",
            "location":     loc,
            "workplace":    "Remote" if is_tele else "",
            "is_remote":    is_tele or "remote" in str(loc).lower(),
            "compensation": comp,
            "posted":       j.get("published_on", ""),
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
        loc = ", ".join(loc.get("city", "") for loc in j.get("locations", []))
        desc = j.get("description", "")
        comp = extract_compensation(desc)
        jobs.append({
            "id":           f"rippling:{slug}:{j.get('id')}",
            "company":      company,
            "title":        j.get("name", ""),
            "url":          f"https://ats.rippling.com/{slug}",
            "location":     loc,
            "workplace":    "",
            "is_remote":    "remote" in loc.lower(),
            "compensation": comp,
            "posted":       j.get("publishedAt", ""),
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

        # 1. Matches role & not excluded (no directors, no non-product managers)
        # 2. Chicagoland in-office/hybrid or Remote
        # 3. Compensation >= $115k if provided
        matched = [
            j for j in all_jobs
            if matches_role(j["title"], role_tokens)
            and matches_location(j)
            and matches_compensation(j)
        ]

        for job in matched:
            if job["id"] not in seen:
                new_jobs.append(job)
                seen.add(job["id"])

        status = f"{len(matched)} match(es) / {len(all_jobs)} total"
        _p(C.DIM, f"  {company:<25} [{ats:<11}]  {status}")

    return new_jobs


def print_job(job: dict):
    human_posted = humanize_time(job.get("posted", ""))
    _p(C.BOLD + C.GREEN, f"\n  >>> {job['title']}")
    _p(C.CYAN,           f"      {job['company']}")
    if job.get("location"):
        _p("",            f"      Location: {job['location']}")
    if job.get("compensation"):
        _p(C.YELLOW,      f"      Comp:     ${job['compensation']:,}+")
    _p(C.DIM,             f"      Posted:   {human_posted}")
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
        _p(C.DIM, "  [discord] DISCORD_WEBHOOK_URL not set -- skipping notification")
        return

    for job in new_jobs:
        location     = job.get("location") or "Remote / Not specified"
        human_posted = humanize_time(job.get("posted", ""))
        apply_url    = job["url"]
        ats_name     = job["id"].split(":")[0].capitalize()

        fields = [
            {
                "name":   "🔗 Apply Link",
                "value":  f"[Click to apply]({apply_url})\n```{apply_url}```",
                "inline": False,
            },
            {
                "name":   "📅 Posted",
                "value":  human_posted,
                "inline": True,
            },
            {
                "name":   "🏢 ATS",
                "value":  ats_name,
                "inline": True,
            },
        ]

        if job.get("compensation"):
            fields.append({
                "name":   "💰 Comp",
                "value":  f"${job['compensation']:,}+",
                "inline": True,
            })

        embed = {
            "title":       job["title"],
            "url":         apply_url,
            "color":       DISCORD_COLOR_NEW_JOB,
            "description": f"**{job['company']}**\n📍 {location}",
            "fields":      fields,
            "footer":      {"text": "FirstLight Job Scanner"},
            "timestamp":   datetime.utcnow().isoformat() + "Z",
        }

        # Plain-text content surfaces in push notifications before embed renders
        comp_str = f" (${job['compensation']:,}+)" if job.get("compensation") else ""
        plain = f"**New job ({human_posted}):** {job['title']} @ {job['company']}{comp_str}\n{apply_url}"
        _discord_post({"content": plain, "embeds": [embed]})
        time.sleep(0.5)  # stay under Discord rate-limit

    # Summary embed listing all new jobs
    summary_lines = "\n".join(
        f"- [{j['title']} @ {j['company']}]({j['url']}) ({humanize_time(j.get('posted', ''))})"
        for j in new_jobs
    )
    summary_embed = {
        "title":       f"FirstLight: {len(new_jobs)} new job(s) found",
        "color":       DISCORD_COLOR_SUMMARY,
        "description": summary_lines,
        "footer":      {"text": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
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
