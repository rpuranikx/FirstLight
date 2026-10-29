# -*- coding: utf-8 -*-
"""
FirstLight - Job Fetcher
Polls Greenhouse, Lever, Ashby, SmartRecruiters, Workable, and Rippling APIs for new
job postings matching your target roles, as defined in config.yml.

Usage:
    python main.py                   # run once, print results
    python main.py --notify          # run once + send Discord alert if new jobs found
    python main.py --watch           # poll continuously (every 5 min)
    python main.py --watch --interval 60  # poll every 60 seconds

GitHub Actions:
    Set DISCORD_WEBHOOK_URL as a repository secret. Optional secrets:
    ANTHROPIC_API_KEY (ranks who to message), HUNTER_API_KEY and/or
    APOLLO_API_KEY (names + emails at each company).
    The workflow commits .seen_jobs.json and .board_cache.json back to main.
"""

import argparse
import html
import io
import json
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

# Force UTF-8 output on Windows
if sys.platform == "win32":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import requests
import yaml

from contacts import who_to_message
from discover import board_alive, discover, load_cache, probe_board, save_cache
from slugs import COMPANY_SLUGS

# ─────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────
CONFIG_PATH           = os.path.join(os.path.dirname(__file__), "config.yml")
SEEN_PATH             = os.path.join(os.path.dirname(__file__), ".seen_jobs.json")
REQUEST_TIMEOUT       = 10   # seconds per HTTP call
SCAN_WORKERS          = 16   # boards fetched in parallel
DESCRIPTION_CHARS     = 6000 # job text kept for the contact picker
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
        json.dump(sorted(seen), f, indent=0)  # sorted so unchanged state makes no diff


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
    "solutions architect":       ["solutions architect", "solution architect"],
    "customer engineer":         ["customer engineer", "customer success engineer"],
    "implementation engineer":   ["implementation engineer", "integration engineer",
                                   "onboarding engineer"],
    "ai engineer":               ["ai engineer", "applied ai", "llm engineer",
                                   "agent engineer", "engineer, ai", "ai solutions"],
    "automation engineer":       ["automation engineer", "test engineer", "sdet",
                                   "test infrastructure", "quality engineer"],
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


DEFAULT_TOO_SENIOR = ["senior", "sr", "staff", "principal", "lead", "head of", "director",
                      "vp", "vice president", "chief", "distinguished", "iii", "iv"]
YEARS_RE = re.compile(
    r"(\d{1,2})\s*\+?\s*(?:-|to|–)?\s*(?:\d{1,2}\s*)?\+?\s*years?(?:\s+of)?(?:\s+[\w/-]+){0,4}?\s+experience",
    re.IGNORECASE,
)


def is_too_senior(title: str, words: list[str]) -> bool:
    """True for titles like "Senior Solutions Engineer" or "Staff FDE"."""
    t = title.lower()
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", t) for w in words)


def years_required(description: str) -> int | None:
    """Smallest "N+ years ... experience" the posting asks for, if any."""
    found = [int(m.group(1)) for m in YEARS_RE.finditer(description or "")]
    found = [n for n in found if 0 < n < 20]
    return min(found) if found else None


def matches_level(job: dict, filters: dict) -> bool:
    if is_too_senior(job["title"], filters.get("exclude_title_words", DEFAULT_TOO_SENIOR)):
        return False
    limit = filters.get("max_years_required")
    need = years_required(job.get("description", ""))
    return not (limit and need and need > limit)


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


NON_US_PLACES = [
    "india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi", "gurgaon", "chennai",
    "uk", "united kingdom", "london", "dublin", "ireland", "emea", "apac", "latam", "europe",
    "germany", "berlin", "munich", "france", "paris", "spain", "madrid", "barcelona", "portugal",
    "lisbon", "italy", "netherlands", "amsterdam", "poland", "warsaw", "sweden", "stockholm",
    "switzerland", "zurich", "israel", "tel aviv", "singapore", "japan", "tokyo", "korea", "seoul",
    "china", "hong kong", "taiwan", "australia", "sydney", "melbourne", "new zealand", "canada",
    "toronto", "vancouver", "montreal", "mexico", "brazil", "sao paulo", "argentina", "colombia",
    "philippines", "vietnam", "uae", "dubai",
]
US_MARKERS = ["united states", "usa", "us", "u.s.", "u.s", "americas", "north america", "us-remote"]


def _has_word(text: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", text) for w in words)


def is_non_us(text: str) -> bool:
    """True for locations outside the US, e.g. "IN-Bangalore-MSO" or "Remote - Canada"."""
    t = text.lower()
    if re.match(r"^(in|gb|uk|de|fr|ca|ie|sg|au|jp|br|mx|pl|es|nl|il)-", t):
        return True
    return _has_word(t, NON_US_PLACES) and not _has_word(t, US_MARKERS)


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

    # Exclude non-US locations unless the US is also mentioned
    if is_non_us(combined):
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


def parse_time(iso_or_str: str) -> datetime | None:
    if not iso_or_str:
        return None
    try:
        dt = datetime.fromisoformat(iso_or_str.replace("Z", "+00:00"))
    except Exception:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def is_fresh(job: dict, max_age_days: int) -> bool:
    """Skips postings older than max_age_days; unknown dates count as fresh."""
    dt = parse_time(job.get("posted", ""))
    if not dt or not max_age_days:
        return True
    return datetime.now(timezone.utc) - dt <= timedelta(days=max_age_days)


def plain_text(raw: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))
    return re.sub(r"\s+", " ", text).strip()[:DESCRIPTION_CHARS]


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
            # first_published is when it went live; updated_at moves on every edit
            "posted":       j.get("first_published") or j.get("updated_at", ""),
            "description":  plain_text(content),
        })
    return jobs


def lever_comp(j: dict) -> int | None:
    rng = j.get("salaryRange") or {}
    if rng.get("max") and str(rng.get("interval", "")).lower().startswith("per-year"):
        return int(rng["max"])
    return extract_compensation(j.get("descriptionPlain", "") + " " + j.get("additionalPlain", ""))


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
        jobs.append({
            "id":           f"lever:{slug}:{j.get('id')}",
            "company":      company,
            "title":        j.get("text", ""),
            "url":          j.get("hostedUrl", ""),
            "location":     loc_str,
            "workplace":    workplace,
            "is_remote":    workplace.lower() == "remote" or "remote" in loc_str.lower(),
            "compensation": lever_comp(j),
            "posted":       datetime.fromtimestamp(
                                j.get("createdAt", 0) / 1000, tz=timezone.utc
                            ).isoformat() if j.get("createdAt") else "",
            "description":  plain_text(desc),
        })
    return jobs


def ashby_comp(j: dict) -> int | None:
    comp = j.get("compensation") or {}
    for c in comp.get("summaryComponents") or []:
        if c.get("compensationType") == "Salary" and c.get("maxValue") \
                and c.get("interval", "1 YEAR") == "1 YEAR":
            return int(c["maxValue"])
    return extract_compensation(comp.get("compensationTierSummary", "") or j.get("descriptionPlain", ""))


def fetch_ashby(company: str, slug: str) -> list[dict]:
    url  = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    data = _get(url)
    if not data:
        return []
    jobs = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        loc = j.get("location", "")
        workplace = j.get("workplaceType", "") or ""
        jobs.append({
            "id":           f"ashby:{slug}:{j.get('id')}",
            "company":      company,
            "title":        j.get("title", ""),
            "url":          j.get("jobUrl", ""),
            "location":     loc,
            "workplace":    workplace,
            "is_remote":    j.get("isRemote") is True or "remote" in loc.lower(),
            "compensation": ashby_comp(j),
            "posted":       j.get("publishedAt", ""),
            "description":  plain_text(j.get("descriptionPlain", "")),
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
            "description":  plain_text(desc),
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
            "description":  plain_text(desc),
        })
    return jobs


def fetch_smartrecruiters(company: str, slug: str) -> list[dict]:
    jobs = []
    for offset in range(0, 1000, 100):  # paged, 100 per page
        url  = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset={offset}"
        data = _get(url)
        if not data or not data.get("content"):
            break
        for j in data["content"]:
            l = j.get("location") or {}
            loc = l.get("fullLocation") or ", ".join(
                x for x in [l.get("city"), l.get("region"), l.get("country", "").upper()] if x)
            remote = l.get("remote") is True
            jobs.append({
                "id":           f"smartrecruiters:{slug}:{j.get('id')}",
                "company":      company,
                "title":        j.get("name", ""),
                "url":          f"https://jobs.smartrecruiters.com/{slug}/{j.get('id')}",
                "location":     loc + (" (Remote)" if remote and "remote" not in loc.lower() else ""),
                "workplace":    "Remote" if remote else "",
                "is_remote":    remote,
                "compensation": None,  # only in the per-posting detail call
                "posted":       j.get("releasedDate", ""),
                "description":  "",
            })
        if offset + 100 >= data.get("totalFound", 0):
            break
    return jobs


FETCHERS = {
    "greenhouse": fetch_greenhouse,
    "lever":      fetch_lever,
    "ashby":      fetch_ashby,
    "workable":   fetch_workable,
    "rippling":   fetch_rippling,
    "smartrecruiters": fetch_smartrecruiters,
}


# ─────────────────────────────────────────────
# Targets: hand-picked companies + discovered startups
# ─────────────────────────────────────────────
HEAL_CHECK_DAYS = 7


def manual_targets(cfg: dict, cache: dict) -> list[dict]:
    """
    Companies from config.yml. A board that stops answering (wrong slug, company
    switched ATS) is re-probed once a week and the fix is cached.
    """
    now = time.time()
    targets = []
    for name in get_companies(cfg):
        mapping = COMPANY_SLUGS.get(name)
        if not mapping:
            _p(C.DIM, f"  [skip] {name} — no slug mapping in slugs.py")
            continue
        board = {"ats": mapping["ats"], "slug": mapping["slug"]}
        healed = cache["boards"].get(f"manual:{name}")
        if healed and now - healed.get("checked", 0) < HEAL_CHECK_DAYS * 86400:
            if healed.get("ats"):
                board = {"ats": healed["ats"], "slug": healed["slug"]}
        else:
            if not board_alive(board["ats"], board["slug"]):
                found = probe_board(name, mapping.get("domain", ""), mapping["slug"])
                if found:
                    _p(C.YELLOW, f"  [heal] {name}: {board['ats']}/{board['slug']} is dead, "
                                 f"using {found['ats']}/{found['slug']}")
                    board = found
                else:
                    _p(C.RED, f"  [dead] {name}: no board found at {board['ats']}/{board['slug']}")
            cache["boards"][f"manual:{name}"] = {**board, "checked": now}
        targets.append({
            "name":      name,
            **board,
            "domain":    mapping.get("domain", ""),
            "about":     "",
            "team_size": None,
            "source":    "config.yml",
        })
    return targets


def all_targets(cfg: dict, cache: dict) -> list[dict]:
    targets = manual_targets(cfg, cache)
    disc_cfg = cfg.get("Discovery") or {}
    if disc_cfg.get("enabled", True):
        targets += discover(disc_cfg, cache)
    unique, seen_boards = [], set()
    for t in targets:
        key = f"{t['ats']}:{t['slug']}"
        if t["ats"] in FETCHERS and key not in seen_boards:
            seen_boards.add(key)
            unique.append(t)
    return unique


# ─────────────────────────────────────────────
# Core scan
# ─────────────────────────────────────────────
def _fetch_board(target: dict):
    try:
        return target, FETCHERS[target["ats"]](target["name"], target["slug"]), None
    except Exception as e:
        return target, [], e


def scan(targets: list[dict], role_tokens: list[str], seen: set, cache: dict,
         filters: dict) -> list[dict]:
    """
    Returns new matching jobs. The first time a board is scanned, matches older
    than `first_scan_alert_hours` are flagged "backlog" so they go out as one
    compact digest instead of a flood of full alerts. Nothing is dropped silently.
    """
    new_jobs = []
    seeded = set(cache["seeded_boards"])
    min_salary = filters.get("min_salary", 115000)
    max_age = filters.get("max_age_days", 21)
    seed_hours = filters.get("first_scan_alert_hours", 48)

    with ThreadPoolExecutor(SCAN_WORKERS) as pool:
        results = list(pool.map(_fetch_board, targets))

    for target, all_jobs, err in results:
        board = f"{target['ats']}:{target['slug']}"
        if err:
            _p(C.RED, f"  [error] {target['name']} ({board}): {err}")
            continue

        # 1. Matches role & not excluded (no directors, no non-product managers)
        # 2. Chicagoland in-office/hybrid or Remote
        # 3. Compensation >= floor if provided, and posted recently
        # 4. Not senior/staff/lead, and doesn't ask for more years than you have
        matched = [
            j for j in all_jobs
            if matches_role(j["title"], role_tokens)
            and matches_location(j)
            and matches_compensation(j, min_salary)
            and is_fresh(j, max_age)
            and matches_level(j, filters)
        ]

        first_scan = board not in seeded
        for job in matched:
            if job["id"] in seen:
                continue
            seen.add(job["id"])
            job["target"] = target
            job["backlog"] = first_scan and not is_fresh(job, seed_hours / 24)
            new_jobs.append(job)
        if all_jobs:
            seeded.add(board)

        if matched:
            _p(C.DIM, f"  {target['name']:<25} [{target['ats']:<11}]  "
                      f"{len(matched)} match(es) / {len(all_jobs)} total")

    cache["seeded_boards"] = sorted(seeded)
    _p(C.DIM, f"\n  Scanned {len(targets)} boards")
    return new_jobs


def add_contacts(new_jobs: list[dict], cfg: dict, cache: dict):
    """Attaches the top 3 people to message to the first few new jobs."""
    c_cfg = dict(cfg.get("Contacts") or {})
    if not c_cfg.get("enabled", True):
        return
    c_cfg.setdefault("profile", cfg.get("Profile", ""))
    for job in new_jobs[: c_cfg.get("max_jobs_per_run", 8)]:
        try:
            job["contacts"] = who_to_message(job, job["target"], c_cfg, cache)
        except Exception as e:
            _p(C.RED, f"  [contacts] {job['company']}: {e}")


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
    for i, c in enumerate(job.get("contacts") or [], 1):
        _p("",            f"      {i}. {c['name']} ({c['title']}) {c.get('email') or ''}")
        _p(C.DIM,         f"         {c['why']}")


def format_contacts(contacts: list[dict]) -> str:
    """Discord field text for the ranked picks (fits the 1024-char limit)."""
    lines = []
    for i, c in enumerate(contacts, 1):
        head = f"**{i}. {c['name']}**, {c['title']}"
        reach = " · ".join(x for x in [
            f"`{c['email']}`" if c.get("email") else "",
            f"[LinkedIn]({c['linkedin']})" if c.get("linkedin") else "",
        ] if x)
        block = head + (f" · {reach}" if reach else "") + f"\n{c['why']}"
        if c.get("angle"):
            block += f"\n> {c['angle']}"
        lines.append(block[:330])
    return "\n".join(lines)[:1024]


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


def company_line(job: dict) -> str:
    t = job.get("target") or {}
    bits = [t.get("about", ""), f"{t['team_size']} people" if t.get("team_size") else "",
            t.get("source", "") if t.get("source") != "config.yml" else ""]
    bits = [b for b in bits if b]
    return ("\n" + " · ".join(bits)) if bits else ""


def backlog_lines(jobs: list[dict], limit: int = 3800) -> str:
    """One line per job, newest first, trimmed to fit a Discord embed."""
    jobs = sorted(jobs, key=lambda j: parse_time(j.get("posted", "")) or datetime.min.replace(tzinfo=timezone.utc),
                  reverse=True)
    lines, size = [], 0
    for j in jobs:
        line = (f"**{j['company']}**: [{j['title']}]({j['url']})"
                f" · {j.get('location') or 'Remote'} · {humanize_time(j.get('posted', ''))}")
        if size + len(line) + 1 > limit:
            lines.append(f"...and {len(jobs) - len(lines)} more (see the Actions log)")
            break
        lines.append(line)
        size += len(line) + 1
    return "\n".join(lines)


def notify_discord_backlog(jobs: list[dict]) -> None:
    """Older matches found on boards scanned for the first time, as one digest."""
    if not DISCORD_WEBHOOK_URL:
        return
    embed = {
        "title": f"📋 {len(jobs)} open role(s) already posted when FirstLight started watching",
        "description": backlog_lines(jobs),
        "color": 0x95A5A6,
    }
    try:
        requests.post(DISCORD_WEBHOOK_URL, json={"embeds": [embed]}, timeout=REQUEST_TIMEOUT).raise_for_status()
        time.sleep(2.1)
    except Exception as e:
        _p(C.RED, f"  [discord] backlog digest failed: {e}")


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

        if job.get("contacts"):
            fields.append({
                "name":   "👋 Who to message",
                "value":  format_contacts(job["contacts"]),
                "inline": False,
            })

        embed = {
            "title":       job["title"],
            "url":         apply_url,
            "color":       DISCORD_COLOR_NEW_JOB,
            "description": f"**{job['company']}**\n📍 {location}" + company_line(job),
            "fields":      fields,
            "footer":      {"text": "FirstLight Job Scanner"},
            "timestamp":   datetime.now(timezone.utc).isoformat(),
        }

        # Plain-text content surfaces in push notifications before embed renders
        comp_str = f" (${job['compensation']:,}+)" if job.get("compensation") else ""
        plain = f"**New job ({human_posted}):** {job['title']} @ {job['company']}{comp_str}\n{apply_url}"
        _discord_post({"content": plain, "embeds": [embed]})
        time.sleep(2.1)  # stay under Discord rate-limit (30 msgs/min per webhook)

    # Summary embed listing all new jobs
    summary_lines = "\n".join(
        f"- [{j['title']} @ {j['company']}]({j['url']}) ({humanize_time(j.get('posted', ''))})"
        for j in new_jobs
    )
    summary_embed = {
        "title":       f"FirstLight: {len(new_jobs)} new job(s) found",
        "color":       DISCORD_COLOR_SUMMARY,
        "description": summary_lines[:4000],
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
    cache       = load_cache()

    _p(C.BOLD, f"\n{'='*60}")
    _p(C.BOLD, f"  FirstLight  |  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    _p(C.BOLD, f"{'='*60}")
    _p(C.DIM,  f"  Roles:     {', '.join(roles)}")

    targets = all_targets(cfg, cache)
    _p(C.DIM,  f"  Companies: {len(targets)} boards\n")

    found = scan(targets, role_tokens, seen, cache, cfg.get("Filters") or {})
    new_jobs = [j for j in found if not j["backlog"]]
    backlog  = [j for j in found if j["backlog"]]

    if backlog:
        _p(C.YELLOW, f"\n  {len(backlog)} older match(es) on newly watched boards (sent as a digest)")
        for job in backlog:
            _p(C.DIM, f"    {job['company']}: {job['title']} ({job.get('location', '')})")
        if notify:
            notify_discord_backlog(backlog)

    if new_jobs:
        _p(C.BOLD + C.GREEN, f"\n  *** {len(new_jobs)} new job(s) found! ***\n")
        add_contacts(new_jobs, cfg, cache)
        for job in new_jobs:
            print_job(job)
        if notify:
            notify_discord_jobs(new_jobs)
    else:
        _p(C.DIM, "\n  [ok] No new matching jobs since last scan.")

    save_cache(cache)
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
