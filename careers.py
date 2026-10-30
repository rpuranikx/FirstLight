# -*- coding: utf-8 -*-
"""
FirstLight - Company career sites
Covers companies whose jobs aren't on a board we can guess from their name:

  1. Finds the careers page (/careers, /jobs, careers.<domain>, ...) and looks
     for the job board behind it: an embedded Greenhouse/Lever/Ashby/Workable/
     SmartRecruiters/Rippling board, or a Workday site.
  2. If the company hosts jobs itself, reads them straight off the site using
     the schema.org JobPosting data that career sites publish for Google Jobs.
"""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import requests

REQUEST_TIMEOUT = 10
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; FirstLight job alerts)"}
CAREER_PATHS = ["/careers", "/jobs", "/careers/", "/company/careers", "/about/careers",
                "/join-us", "/work-with-us", "/open-positions"]
MAX_DETAIL_PAGES = 40

# Set by the scanner each run so self-hosted sites only fetch job pages worth reading
ROLE_TOKENS: list[str] = []
SEEN: set = set()

ATS_PATTERNS = [
    ("greenhouse", r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board(?:/js)?\?for=)?([A-Za-z0-9_-]+)"),
    ("greenhouse", r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)"),
    ("lever",      r"jobs\.lever\.co/([A-Za-z0-9_.-]+)"),
    ("ashby",      r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)"),
    ("ashby",      r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)"),
    ("workable",   r"apply\.workable\.com/([A-Za-z0-9_-]+)"),
    ("smartrecruiters", r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ("rippling",   r"ats\.rippling\.com/([A-Za-z0-9_-]+)"),
]
WORKDAY_RE = re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")
IGNORED_SLUGS = {"embed", "js", "v1", "api", "jobs", "job_board", "en-us", "careers"}


def _html(url: str) -> tuple[str, str]:
    """Returns (final url, html), or ("", "") if the page isn't there."""
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS, allow_redirects=True)
    except Exception:
        return "", ""
    if r.status_code != 200 or "html" not in r.headers.get("content-type", "html"):
        return "", ""
    return r.url, r.text


def board_in_html(text: str) -> dict | None:
    """The job board a page links to or embeds, if any."""
    m = WORKDAY_RE.search(text)
    if m:
        return {"ats": "workday", "slug": "|".join(m.groups())}
    for ats, pattern in ATS_PATTERNS:
        for m in re.finditer(pattern, text):
            slug = m.group(1).strip(".")
            if slug.lower() not in IGNORED_SLUGS:
                return {"ats": ats, "slug": slug}
    return None


def career_urls(domain: str) -> list[str]:
    root = f"https://{domain}"
    return ([root + p for p in CAREER_PATHS]
            + [f"https://careers.{domain}", f"https://jobs.{domain}", root])


def find_careers_board(domain: str) -> dict | None:
    """
    Finds where a company lists its jobs. Returns {"ats", "slug"}; for jobs
    hosted on the company's own site that is {"ats": "site", "slug": <careers url>}.
    """
    if not domain:
        return None
    for url in career_urls(domain):
        final, text = _html(url)
        if not text:
            continue
        found = board_in_html(final + " " + text)
        if found:
            return found
        if url.endswith(domain):
            continue  # the home page only counts if it links to a board
        if job_postings_in(text) or len(job_links(final, text)) >= 2:
            return {"ats": "site", "slug": final}
    return None


# ─────────────────────────────────────────────
# Self-hosted career pages
# ─────────────────────────────────────────────
def job_postings_in(text: str) -> list[dict]:
    """schema.org JobPosting objects embedded as JSON-LD."""
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', text, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                kind = node.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    out.append(node)
                stack.extend(v for k, v in node.items() if k in ("@graph", "itemListElement", "item"))
    return out


JOB_PATH_RE = re.compile(r"/(jobs?|careers?|positions?|openings?|roles?|opportunities)/[^/?#]+", re.I)


def job_links(base: str, text: str) -> list[tuple[str, str]]:
    """(url, link text) for links on the page that look like individual job pages."""
    host = urlparse(base).netloc
    out, seen = [], set()
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', text, re.S | re.I):
        url = urljoin(base, href)
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", label)).strip()
        if urlparse(url).netloc != host or not JOB_PATH_RE.search(urlparse(url).path):
            continue
        if url.rstrip("/") == base.rstrip("/") or url in seen or not label:
            continue
        seen.add(url)
        out.append((url, label))
    return out


def _location(p: dict) -> str:
    parts = []
    locs = p.get("jobLocation") or []
    for loc in locs if isinstance(locs, list) else [locs]:
        addr = (loc or {}).get("address") or {}
        if isinstance(addr, str):
            parts.append(addr)
            continue
        country = addr.get("addressCountry")
        if isinstance(country, dict):
            country = country.get("name")
        parts.append(", ".join(x for x in [addr.get("addressLocality"), addr.get("addressRegion"), country] if x))
    if p.get("jobLocationType") == "TELECOMMUTE":
        req = p.get("applicantLocationRequirements") or {}
        req = req[0] if isinstance(req, list) and req else req
        where = req.get("name", "") if isinstance(req, dict) else ""
        parts.append(f"Remote {where}".strip())
    return "; ".join(x for x in parts if x)


def _salary(p: dict) -> int | None:
    base = p.get("baseSalary") or {}
    value = base.get("value") if isinstance(base, dict) else None
    if isinstance(value, dict):
        n = value.get("maxValue") or value.get("value") or value.get("minValue")
        unit = (value.get("unitText") or "YEAR").upper()
        try:
            n = float(n)
        except (TypeError, ValueError):
            return None
        return int(n * 2080) if unit == "HOUR" else int(n)
    return None


def _strip(raw: str) -> str:
    import html as _h
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", _h.unescape(raw or ""))).strip()


def posting_to_job(p: dict, company: str, site: str, url: str) -> dict:
    loc = _location(p)
    remote = p.get("jobLocationType") == "TELECOMMUTE" or "remote" in loc.lower()
    url = p.get("url") or url
    return {
        "id":           f"site:{urlparse(site).netloc}:{hashlib.sha1(url.encode()).hexdigest()[:12]}",
        "company":      company,
        "title":        _strip(p.get("title", "")),
        "url":          url,
        "location":     loc,
        "workplace":    "Remote" if remote else "",
        "is_remote":    remote,
        "compensation": _salary(p),
        "posted":       p.get("datePosted", ""),
        "description":  _strip(p.get("description", ""))[:6000],
    }


def _wanted(label: str) -> bool:
    t = label.lower()
    return not ROLE_TOKENS or any(tok in t for tok in ROLE_TOKENS)


def fetch_site(company: str, careers_url: str) -> list[dict]:
    """Jobs listed on a company's own careers page."""
    final, text = _html(careers_url)
    if not text:
        return []
    found = [posting_to_job(p, company, careers_url, final) for p in job_postings_in(text)]
    if found:
        return found

    links = [(u, l) for u, l in job_links(final, text) if _wanted(l)]
    todo = [(u, l) for u, l in links
            if f"site:{urlparse(careers_url).netloc}:{hashlib.sha1(u.encode()).hexdigest()[:12]}" not in SEEN]
    jobs = []

    def detail(item):
        u, label = item
        _, page = _html(u)
        posts = job_postings_in(page) if page else []
        if posts:
            return posting_to_job(posts[0], company, careers_url, u)
        # No structured data: keep the listing so a human can judge it
        body = _strip(page)[:6000] if page else ""
        return {
            "id": f"site:{urlparse(careers_url).netloc}:{hashlib.sha1(u.encode()).hexdigest()[:12]}",
            "company": company, "title": label, "url": u,
            "location": guess_location(body), "workplace": "", "is_remote": "remote" in body.lower()[:1500],
            "compensation": None, "posted": "", "description": body,
        }

    with ThreadPoolExecutor(8) as pool:
        jobs = list(pool.map(detail, todo[:MAX_DETAIL_PAGES]))
    return jobs


def guess_location(body: str) -> str:
    head = body[:1500]
    for pattern in [r"Location[:\s]+([A-Z][^.|•\n]{2,60})", r"((?:Chicago|New York|San Francisco|Remote)[^.|•\n]{0,40})"]:
        m = re.search(pattern, head)
        if m:
            return m.group(1).strip()
    return ""


# ─────────────────────────────────────────────
# Workday
# ─────────────────────────────────────────────
def _workday_posted(text: str) -> str:
    now = datetime.now(timezone.utc)
    t = (text or "").lower()
    if "today" in t:
        return now.isoformat()
    if "yesterday" in t:
        return (now - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?", t)
    return (now - timedelta(days=int(m.group(1)))).isoformat() if m else ""


def fetch_workday(company: str, slug: str) -> list[dict]:
    """slug is "tenant|wdN|site", e.g. "acme|wd5|External"."""
    tenant, wd, site = slug.split("|")
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}/jobs"
    searches = sorted(set(ROLE_TOKENS)) or [""]
    jobs, ids = [], set()
    for term in searches:
        for offset in range(0, 60, 20):
            try:
                r = requests.post(api, json={"limit": 20, "offset": offset, "searchText": term,
                                             "appliedFacets": {}},
                                  headers={**HEADERS, "Accept": "application/json"}, timeout=REQUEST_TIMEOUT)
                data = r.json() if r.status_code == 200 else {}
            except Exception:
                data = {}
            posts = data.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath", "")
                if not path or path in ids:
                    continue
                ids.add(path)
                loc = p.get("locationsText", "")
                jobs.append({
                    "id":           f"workday:{tenant}:{path.rsplit('_', 1)[-1]}",
                    "company":      company,
                    "title":        p.get("title", ""),
                    "url":          f"{host}/{site}{path}",
                    "location":     loc,
                    "workplace":    "",
                    "is_remote":    "remote" in loc.lower(),
                    "compensation": None,
                    "posted":       _workday_posted(p.get("postedOn", "")),
                    "description":  "",
                })
            if len(posts) < 20 or offset + 20 >= data.get("total", 0):
                break
    return jobs
