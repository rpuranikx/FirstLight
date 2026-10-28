# -*- coding: utf-8 -*-
"""
FirstLight - Company discovery
Builds the list of companies to scan on top of the hand-picked ones in
config.yml, so the net covers high-growth startups you have never heard of.

Sources:
  - YC directory (free, refreshed daily by github.com/yc-oss/api):
    active, currently hiring companies filtered by team size and region.
  - Apollo organization search (optional, needs APOLLO_API_KEY):
    venture-backed companies filtered by headcount and funding stage.

Each discovered company is matched to a public job board by probing the
Ashby, Greenhouse and Lever APIs with likely slugs. Results are cached in
.board_cache.json so the probing only happens once per company.
"""

import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

CACHE_PATH      = os.path.join(os.path.dirname(__file__), ".board_cache.json")
# People data stays out of git (the repo is public); CI keeps it with actions/cache
CONTACTS_PATH   = os.path.join(os.path.dirname(__file__), ".contacts_cache.json")
REQUEST_TIMEOUT = 10
YC_HIRING_URL   = "https://yc-oss.github.io/api/companies/hiring.json"
APOLLO_ORG_URL  = "https://api.apollo.io/api/v1/mixed_companies/search"
PROBE_ORDER     = ["ashby", "greenhouse", "lever", "smartrecruiters"]
PROBE_WORKERS   = 16
NO_BOARD_RETRY_DAYS = 14


# ─────────────────────────────────────────────
# Cache
# ─────────────────────────────────────────────
def load_cache() -> dict:
    if os.path.exists(CACHE_PATH):
        with open(CACHE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault("boards", {})          # company key -> {ats, slug, checked}
    data.setdefault("seeded_boards", [])   # "ats:slug" boards already scanned once
    data.setdefault("discovered", {})      # {"fetched": ts, "companies": [...]}
    data["contacts"] = {}                  # domain -> {"fetched": ts, "people": [...]}
    if os.path.exists(CONTACTS_PATH):
        with open(CONTACTS_PATH, "r", encoding="utf-8") as f:
            data["contacts"] = json.load(f)
    return data


def save_cache(cache: dict):
    cache = dict(cache)
    with open(CONTACTS_PATH, "w", encoding="utf-8") as f:
        json.dump(cache.pop("contacts", {}), f, indent=1, sort_keys=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=1, sort_keys=True)


# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────
def domain_of(website: str) -> str:
    if not website:
        return ""
    if "//" not in website:
        website = "https://" + website
    host = urlparse(website).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def candidate_slugs(name: str, domain: str = "", hint: str = "") -> list[str]:
    """Likely job-board slugs for a company, best guesses first."""
    out = []
    stem = domain.split(".")[0] if domain else ""
    base = re.sub(r"\s*\(.*?\)", "", name).lower()
    base = re.sub(r"\b(inc|labs|ai|hq|technologies|technology)\b\.?", "", base).strip()
    for s in [
        hint,
        stem,
        re.sub(r"[^a-z0-9]", "", name.lower()),
        re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-"),
        re.sub(r"[^a-z0-9]", "", base),
        re.sub(r"[^A-Za-z0-9]", "", name),  # SmartRecruiters IDs are often CamelCase
    ]:
        if s and s not in out:
            out.append(s)
    return out


def _board_jobs(ats: str, slug: str):
    """Returns the board's job list if the board exists, else None."""
    urls = {
        "ashby":      f"https://api.ashbyhq.com/posting-api/job-board/{slug}",
        "greenhouse": f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
        "lever":      f"https://api.lever.co/v0/postings/{slug}?mode=json&limit=1",
        "smartrecruiters": f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=1",
    }
    try:
        r = requests.get(urls[ats], timeout=REQUEST_TIMEOUT)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except ValueError:
        return None
    if ats == "lever":
        return data if isinstance(data, list) else None
    if ats == "smartrecruiters":
        # Unknown company IDs also answer 200, just with nothing in them
        return data["content"] if isinstance(data, dict) and data.get("totalFound") else None
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        return data["jobs"]
    return None


def probe_board(name: str, domain: str = "", hint: str = "") -> dict | None:
    """Finds the company's public job board. Returns {"ats", "slug"} or None."""
    for slug in candidate_slugs(name, domain, hint):
        for ats in PROBE_ORDER:
            if _board_jobs(ats, slug) is not None:
                return {"ats": ats, "slug": slug}
    return None


def board_alive(ats: str, slug: str) -> bool:
    if ats not in PROBE_ORDER:
        return True  # workable/rippling: no cheap check, trust the mapping
    return _board_jobs(ats, slug) is not None


# ─────────────────────────────────────────────
# Sources
# ─────────────────────────────────────────────
def from_yc(cfg: dict) -> list[dict]:
    try:
        r = requests.get(YC_HIRING_URL, timeout=30)
        r.raise_for_status()
        rows = r.json()
    except Exception as e:
        print(f"  [discover] YC directory unavailable: {e}")
        return []
    return filter_yc(rows, cfg)


def filter_yc(rows: list[dict], cfg: dict) -> list[dict]:
    lo = cfg.get("min_team_size", 15)
    hi = cfg.get("max_team_size", 1000)
    regions = [r.lower() for r in cfg.get("regions", [])]
    skip_ind = [i.lower() for i in cfg.get("exclude_industries", [])]
    out = []
    for c in rows:
        size = c.get("team_size") or 0
        if c.get("status") != "Active" or not (lo <= size <= hi):
            continue
        if regions and not any(r.lower() in regions for r in c.get("regions", [])):
            continue
        if any(i.lower() in skip_ind for i in c.get("industries", [])):
            continue
        out.append({
            "name":      c["name"],
            "domain":    domain_of(c.get("website", "")),
            "slug_hint": c.get("slug", ""),
            "team_size": size,
            "about":     c.get("one_liner", ""),
            "source":    f"YC {c.get('batch', '')}".strip(),
        })
    return out


def from_apollo(cfg: dict) -> list[dict]:
    key = os.environ.get("APOLLO_API_KEY", "")
    if not key:
        print("  [discover] APOLLO_API_KEY not set -- skipping Apollo")
        return []
    out = []
    for page in range(1, cfg.get("max_pages", 5) + 1):
        body = {
            "organization_num_employees_ranges": cfg.get("employee_ranges", ["11,50", "51,200", "201,500", "501,1000"]),
            "organization_latest_funding_stage_cd": cfg.get("funding_stages", []),
            "q_organization_keyword_tags": cfg.get("keywords", []),
            "organization_locations": cfg.get("locations", ["United States"]),
            "page": page,
            "per_page": 100,
        }
        body = {k: v for k, v in body.items() if v not in ([], None, "")}
        try:
            r = requests.post(APOLLO_ORG_URL, json=body, timeout=30,
                              headers={"X-Api-Key": key, "Cache-Control": "no-cache"})
            r.raise_for_status()
            orgs = r.json().get("organizations") or r.json().get("accounts") or []
        except Exception as e:
            print(f"  [discover] Apollo search failed on page {page}: {e}")
            break
        for o in orgs:
            if not o.get("name"):
                continue
            out.append({
                "name":      o["name"],
                "domain":    o.get("primary_domain") or domain_of(o.get("website_url", "")),
                "slug_hint": "",
                "team_size": o.get("estimated_num_employees"),
                "about":     o.get("short_description", "") or "",
                "source":    "Apollo",
            })
        if len(orgs) < 100:
            break
    return out


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def discover(cfg: dict, cache: dict, now: float | None = None) -> list[dict]:
    """
    Returns discovered companies that have a job board, as
    {"name", "ats", "slug", "domain", "team_size", "about", "source"}.
    Refreshes sources every `refresh_days` and probes boards for new names.
    """
    now = now or time.time()
    disc = cache["discovered"]
    refresh = cfg.get("refresh_days", 1) * 86400

    if not disc.get("companies") or now - disc.get("fetched", 0) > refresh:
        companies = []
        if cfg.get("yc", {}).get("enabled", True):
            companies += from_yc(cfg.get("yc", {}))
        if cfg.get("apollo", {}).get("enabled", False):
            companies += from_apollo(cfg.get("apollo", {}))
        if companies:
            disc["companies"] = companies
            disc["fetched"] = now
    companies = disc.get("companies", [])

    # Probe boards for companies we haven't resolved (or that had none a while ago)
    def needs_probe(c):
        b = cache["boards"].get(c["name"])
        if not b:
            return True
        return b.get("ats") is None and now - b.get("checked", 0) > NO_BOARD_RETRY_DAYS * 86400

    todo = [c for c in companies if needs_probe(c)][: cfg.get("max_probes_per_run", 400)]
    if todo:
        print(f"  [discover] probing job boards for {len(todo)} companies...")
        with ThreadPoolExecutor(PROBE_WORKERS) as pool:
            results = pool.map(lambda c: probe_board(c["name"], c["domain"], c["slug_hint"]), todo)
            for c, found in zip(todo, results):
                cache["boards"][c["name"]] = {**(found or {"ats": None, "slug": None}), "checked": now}

    out = []
    for c in companies:
        b = cache["boards"].get(c["name"], {})
        if b.get("ats"):
            out.append({**c, "ats": b["ats"], "slug": b["slug"]})
    return out
