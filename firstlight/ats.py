"""
Job boards: recognise a board from any of its URLs, and read its jobs through the
board's own public API.

A board is {"ats": "greenhouse", "slug": "databricks"}. Workday slugs are
"tenant|wdN|site". Every fetch returns (jobs, status), where status is "ok",
"empty" (board answers, no openings) or an error string, so a broken board is
reported instead of looking like a quiet one.
"""
import re
import time
from datetime import datetime, timezone

import requests

from .text import pay_from_text, plain_text

TIMEOUT = 25
HEADERS = {"User-Agent": "FirstLight/2.0 (job alerts)", "Accept": "application/json",
           "Content-Type": "application/json"}

# Order matters: the first pattern that matches wins.
URL_PATTERNS = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_(?:board|app)(?:/js)?\?(?:[^\"'\s]*&)?for=([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_board\?for=|embed/job_app\?for=)?([A-Za-z0-9_-]+)", re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/([A-Za-z0-9_-]+)", re.I)),
    ("lever",      re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)", re.I)),
    ("ashby",      re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)", re.I)),
    ("ashby",      re.compile(r"api\.ashbyhq\.com/posting-api/job-board/([A-Za-z0-9_.%-]+)", re.I)),
    ("workable",   re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)", re.I)),
    ("workable",   re.compile(r"(?<!apply\.)\b([a-z0-9-]+)\.workable\.com", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)", re.I)),
    ("rippling",   re.compile(r"ats\.rippling\.com/([A-Za-z0-9_-]+)", re.I)),
    ("recruitee",  re.compile(r"\b([a-z0-9-]+)\.recruitee\.com", re.I)),
    ("bamboohr",   re.compile(r"\b([a-z0-9-]+)\.bamboohr\.com", re.I)),
    ("breezy",     re.compile(r"\b([a-z0-9-]+)\.breezy\.hr", re.I)),
    ("gem",        re.compile(r"jobs\.gem\.com/([A-Za-z0-9_-]+)", re.I)),
]
WORKDAY_RE = re.compile(r"([a-z0-9-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([A-Za-z0-9_-]+)")
NOT_SLUGS = {"embed", "api", "v1", "jobs", "job", "careers", "www", "apply", "j", "o"}


def board_from_url(url: str) -> dict | None:
    """The board a job or careers URL belongs to, e.g. jobs.lever.co/acme/123 -> lever:acme."""
    if not url:
        return None
    m = WORKDAY_RE.search(url)
    if m:
        return {"ats": "workday", "slug": f"{m[1]}|{m[2]}|{m[3]}"}
    for ats, rx in URL_PATTERNS:
        m = rx.search(url)
        if m and m[1].lower() not in NOT_SLUGS:
            return {"ats": ats, "slug": m[1]}
    return None


def board_key(board: dict) -> str:
    return f"{board['ats']}:{board['slug']}"


def _get(url: str, **kw):
    for wait in (2, 6, 15, None):  # back off on rate limits instead of reporting a broken board
        r = requests.get(url, headers=HEADERS, timeout=TIMEOUT, **kw)
        if r.status_code != 429 or wait is None:
            break
        time.sleep(wait)
    r.raise_for_status()
    return r.json()


def _iso_ms(ms) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat() if ms else ""


def _job(board, jid, title, url, location, remote, posted, description, pay=None, workplace=""):
    desc = plain_text(description)
    pay = pay or pay_from_text(desc)
    return {
        "id": f"{board['ats']}:{board['slug']}:{jid}",
        "title": (title or "").strip(),
        "url": url,
        "location": location or "",
        "workplace": workplace or "",
        "remote": remote,
        "posted": posted or "",
        "pay_max": pay.get("max"),
        "pay_ote": pay.get("ote", False),
        "description": desc,
    }


def fetch_greenhouse(board):
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{board['slug']}/jobs?content=true")
    out = []
    for j in data.get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        out.append(_job(board, j.get("id"), j.get("title"), j.get("absolute_url"), loc,
                        "remote" in loc.lower(), j.get("first_published") or j.get("updated_at"),
                        j.get("content", "")))
    return out


def fetch_lever(board):
    data = _get(f"https://api.lever.co/v0/postings/{board['slug']}?mode=json")
    out = []
    for j in data if isinstance(data, list) else []:
        loc = (j.get("categories") or {}).get("location", "") or ""
        wp = j.get("workplaceType", "") or ""
        desc = " ".join([j.get("descriptionPlain", "")] + [
            f"{l.get('text', '')}\n{plain_text(l.get('content', ''))}" for l in j.get("lists") or []
        ] + [j.get("additionalPlain", "")])
        rng = j.get("salaryRange") or {}
        pay = ({"max": int(rng["max"]), "ote": False}
               if rng.get("max") and str(rng.get("interval", "")).startswith("per-year")
               and (rng.get("currency") or "USD").upper() == "USD" else None)
        out.append(_job(board, j.get("id"), j.get("text"), j.get("hostedUrl"), loc,
                        wp.lower() == "remote" or "remote" in loc.lower(), _iso_ms(j.get("createdAt")),
                        desc, pay, wp))
    return out


def fetch_ashby(board):
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{board['slug']}?includeCompensation=true")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        places = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
        loc = "; ".join(p for p in places if p)
        country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry") or ""
        if country and country.lower() not in loc.lower():
            loc += f" ({country})"
        wp = j.get("workplaceType") or ""
        comp = j.get("compensation") or {}
        pay = None
        for c in comp.get("summaryComponents") or []:
            if (c.get("compensationType") == "Salary" and c.get("maxValue") and c.get("interval", "1 YEAR") == "1 YEAR"
                    and (c.get("currencyCode") or "USD").upper() == "USD"):
                pay = {"max": int(c["maxValue"]), "ote": False}
        if pay is None:
            pay = pay_from_text(comp.get("compensationTierSummary", "") or j.get("descriptionPlain", ""))
        # Ashby marks hybrid roles isRemote too, so the workplace type decides
        remote = wp == "Remote" or (not wp and j.get("isRemote") is True)
        out.append(_job(board, j.get("id"), j.get("title"), j.get("jobUrl"), loc, remote,
                        j.get("publishedAt"), j.get("descriptionHtml") or j.get("descriptionPlain", ""), pay, wp))
    return out


def fetch_workable(board):
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{board['slug']}?details=true")
    out = []
    for j in data.get("jobs", []):
        loc = ", ".join(x for x in [j.get("city"), j.get("state"), j.get("country")] if x) or j.get("location", "")
        tele = bool(j.get("telecommuting"))
        out.append(_job(board, j.get("shortcode"), j.get("title"),
                        j.get("url") or f"https://apply.workable.com/{board['slug']}/j/{j.get('shortcode')}",
                        loc, tele, j.get("published_on"), j.get("description", ""),
                        workplace="Remote" if tele else ""))
    return out


def fetch_smartrecruiters(board):
    out = []
    for offset in range(0, 2000, 100):
        data = _get(f"https://api.smartrecruiters.com/v1/companies/{board['slug']}/postings?limit=100&offset={offset}")
        for j in data.get("content") or []:
            l = j.get("location") or {}
            loc = l.get("fullLocation") or ", ".join(x for x in [l.get("city"), l.get("region"), (l.get("country") or "").upper()] if x)
            out.append(_job(board, j.get("id"), j.get("name"),
                            f"https://jobs.smartrecruiters.com/{board['slug']}/{j.get('id')}",
                            loc, l.get("remote") is True, j.get("releasedDate"), ""))
        if offset + 100 >= data.get("totalFound", 0):
            break
    return out


def fetch_rippling(board):
    data = _get(f"https://api.rippling.com/platform/api/ats/v2/board/{board['slug']}/jobs?page=0&pageSize=500")
    out = []
    for j in data.get("items", []):
        loc = "; ".join(l.get("name") or l.get("city") or "" for l in j.get("locations") or [])
        out.append(_job(board, j.get("id"), j.get("name"), j.get("url") or f"https://ats.rippling.com/{board['slug']}/jobs/{j.get('id')}",
                        loc, "remote" in loc.lower(), j.get("publishedAt") or j.get("createdOn"), j.get("description", "")))
    return out


def fetch_recruitee(board):
    data = _get(f"https://{board['slug']}.recruitee.com/api/offers/")
    out = []
    for j in data.get("offers", []):
        loc = j.get("location", "") or ""
        out.append(_job(board, j.get("id"), j.get("title"), j.get("careers_url"), loc,
                        bool(j.get("remote")) or "remote" in loc.lower(), j.get("published_at") or j.get("created_at"),
                        (j.get("description") or "") + (j.get("requirements") or "")))
    return out


def fetch_bamboohr(board):
    data = _get(f"https://{board['slug']}.bamboohr.com/careers/list")
    out = []
    for j in data.get("result", []):
        l = j.get("location") or {}
        loc = ", ".join(x for x in [l.get("city"), l.get("state")] if x)
        remote = str(j.get("isRemote")).lower() in ("1", "true") or "remote" in loc.lower()
        out.append(_job(board, j.get("id"), j.get("jobOpeningName"),
                        f"https://{board['slug']}.bamboohr.com/careers/{j.get('id')}", loc, remote, "", ""))
    return out


def fetch_breezy(board):
    data = _get(f"https://{board['slug']}.breezy.hr/json")
    out = []
    for j in data if isinstance(data, list) else []:
        l = j.get("location") or {}
        loc = l.get("name", "") if isinstance(l, dict) else str(l)
        out.append(_job(board, j.get("id"), j.get("name"), j.get("url"), loc,
                        bool(l.get("is_remote")) if isinstance(l, dict) else "remote" in loc.lower(),
                        j.get("published_date"), ""))
    return out


def fetch_gem(board):
    out = []
    for j in _get(f"https://api.gem.com/job_board/v0/{board['slug']}/job_posts/"):
        if j.get("employment_type") not in (None, "", "full_time"):
            continue
        loc = (j.get("location") or {}).get("name", "")
        kind = j.get("location_type") or ""
        out.append(_job(board, j.get("id"), j.get("title"), j.get("absolute_url"), loc, kind == "remote",
                        j.get("first_published_at"), j.get("content_plain") or j.get("content") or "",
                        workplace=kind))
    return out


def fetch_workday(board, search_terms=("",)):
    """Workday has no "all jobs" call, so it is searched by role keywords."""
    tenant, wd, site = board["slug"].split("|")
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}/jobs"
    out, seen = [], set()
    for term in search_terms:
        for offset in range(0, 200, 20):
            r = requests.post(api, json={"limit": 20, "offset": offset, "searchText": term, "appliedFacets": {}},
                              headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            data = r.json()
            posts = data.get("jobPostings") or []
            for p in posts:
                path = p.get("externalPath", "")
                if not path or path in seen:
                    continue
                seen.add(path)
                loc = p.get("locationsText", "")
                out.append(_job(board, path.rsplit("_", 1)[-1], p.get("title"), f"{host}/{site}{path}",
                                loc, "remote" in loc.lower(), "", ""))
            if len(posts) < 20 or offset + 20 >= data.get("total", 0):
                break
    return out


FETCHERS = {
    "greenhouse": fetch_greenhouse, "lever": fetch_lever, "ashby": fetch_ashby,
    "workable": fetch_workable, "smartrecruiters": fetch_smartrecruiters, "rippling": fetch_rippling,
    "recruitee": fetch_recruitee, "bamboohr": fetch_bamboohr, "breezy": fetch_breezy, "workday": fetch_workday,
    "gem": fetch_gem,
}


def fetch(board: dict, search_terms=("",)) -> tuple[list[dict], str]:
    """(jobs, status). Never raises: a failure comes back as the status."""
    fn = FETCHERS.get(board["ats"])
    if not fn:
        return [], f"unsupported board type {board['ats']}"
    try:
        jobs = fn(board, search_terms) if board["ats"] == "workday" else fn(board)
    except requests.HTTPError as e:
        return [], f"HTTP {e.response.status_code}"
    except Exception as e:  # network, bad JSON, changed schema
        return [], f"{type(e).__name__}: {str(e)[:120]}"
    return jobs, "ok" if jobs else "empty"


def board_name(board: dict) -> str | None:
    """The company name a board reports about itself, where the ATS exposes one."""
    try:
        if board["ats"] == "greenhouse":
            return _get(f"https://boards-api.greenhouse.io/v1/boards/{board['slug']}").get("name")
        if board["ats"] == "workable":
            return _get(f"https://apply.workable.com/api/v1/widget/accounts/{board['slug']}").get("name")
    except Exception:
        return None
    return None
