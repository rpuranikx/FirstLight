"""
Where the watchlist's companies come from, with the evidence of where each one posts jobs.

Each source returns companies as
  {"name", "domain", "size", "source", "evidence": [job or careers URLs the company itself published]}
so the watchlist can take a board from a link instead of guessing a slug.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

import requests

UA = {"User-Agent": "Mozilla/5.0 (FirstLight job alerts)", "Accept": "application/json"}
TIMEOUT = 30
GETRO_API = "https://api.getro.com/api/v2/collections/{cid}/search/{kind}"
GETRO_HEADCOUNT = {1: 5, 2: 30, 3: 125, 4: 600, 5: 3000, 6: 10000}  # size bucket -> rough people
SKIP_STAGES = {"acquisition", "ipo", "private_equity", "closed"}  # acquired companies post under the buyer
US_HINTS = ("united states", "usa", "remote", ", us")
YC_HIRING = "https://yc-oss.github.io/api/companies/hiring.json"


def domain_of(website: str) -> str:
    if not website:
        return ""
    if "//" not in website:
        website = "https://" + website
    host = urlparse(website).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def _us(locations) -> bool:
    locs = [str(l).lower() for l in locations or []]
    return not locs or any(h in l for l in locs for h in US_HINTS)


# ── Getro-powered VC portfolio boards (Accel, General Catalyst, Greylock, ...) ──
def getro_collection(host: str) -> tuple[int, str] | None:
    try:
        page = requests.get(f"https://{host}/jobs", headers={"User-Agent": UA["User-Agent"]}, timeout=TIMEOUT).text
        m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
        net = json.loads(m.group(1))["props"]["pageProps"]["network"]
        return int(net["id"]), (net.get("legal") or {}).get("name") or net.get("label") or host
    except Exception:
        return None


def _getro(cid: int, kind: str, body: dict) -> dict:
    r = requests.post(GETRO_API.format(cid=cid, kind=kind), json=body, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json().get("results") or {}


def getro_job_urls(cid: int, org_id: int, limit: int = 20) -> list[str]:
    try:
        res = _getro(cid, "jobs", {"hitsPerPage": limit, "page": 0, "query": "", "filters": {"organization.id": [org_id]}})
    except Exception:
        return []
    return [j.get("url") for j in res.get("jobs") or [] if j.get("url")]


def from_getro(host: str, min_size: int) -> list[dict]:
    coll = getro_collection(host)
    if not coll:
        print(f"  [sources] {host}: not a Getro board or unreachable")
        return []
    cid, vc = coll
    companies = []
    seen = 0
    for page in range(0, 2000):  # Getro returns a fixed 12 per page whatever hitsPerPage says
        try:
            res = _getro(cid, "companies", {"hitsPerPage": 100, "page": page, "query": "", "filters": ""})
            rows = res.get("companies") or []
        except Exception as e:
            print(f"  [sources] {host} page {page}: {e}")
            break
        for c in rows:
            size = GETRO_HEADCOUNT.get(c.get("head_count"))
            if not c.get("domain") or c.get("stage") in SKIP_STAGES or (size and size < min_size):
                continue
            if not _us(c.get("locations")):
                continue
            companies.append({"name": c["name"], "domain": domain_of(c["domain"]), "size": size,
                              "source": f"{vc} portfolio", "getro": (cid, c["id"]), "evidence": []})
        seen += len(rows)
        if not rows or seen >= (res.get("count") or 0):
            break
    # One cheap call per company: the URLs of its current jobs say which board it uses
    with ThreadPoolExecutor(16) as pool:
        for c, urls in zip(companies, pool.map(lambda c: getro_job_urls(*c["getro"]), companies)):
            c["evidence"] = urls
    print(f"  [sources] {vc}: {len(companies)} companies")
    return companies


# ── a16z (its own board, not Getro): the companies page embeds the portfolio ──
def from_a16z(min_size: int) -> list[dict]:
    try:
        page = requests.get("https://jobs.a16z.com/companies", headers={"User-Agent": UA["User-Agent"]},
                            timeout=TIMEOUT).text.replace('\\"', '"')
    except Exception as e:
        print(f"  [sources] a16z unavailable: {e}")
        return []
    out = []
    for chunk in page.split('{"id":"')[1:]:
        name = re.search(r'"name":"([^"]+)"', chunk)
        domain = re.search(r'"domain":"([^"]+)"', chunk)
        size = re.search(r'"headcount":(\d+)', chunk)
        loc = re.search(r'"location":"([^"]*)"', chunk)
        if not (name and domain and '"slug":' in chunk[:120]):
            continue
        size = int(size[1]) if size else None
        if (size and size < min_size) or not _us([loc[1]] if loc else []):
            continue
        out.append({"name": name[1], "domain": domain_of(domain[1]), "size": size,
                    "source": "a16z portfolio", "evidence": []})
    print(f"  [sources] a16z: {len(out)} companies")
    return out


# ── YC companies that are hiring (github.com/yc-oss/api, refreshed daily) ──
def from_yc(min_size: int) -> list[dict]:
    try:
        rows = requests.get(YC_HIRING, timeout=TIMEOUT).json()
    except Exception as e:
        print(f"  [sources] YC unavailable: {e}")
        return []
    out = []
    for c in rows:
        size = c.get("team_size") or 0
        if size < min_size or c.get("status") not in ("Active", "Public"):
            continue
        regions = c.get("regions") or []
        if regions and not any(r in ("United States of America", "America / Canada", "Remote", "North America")
                               for r in regions):
            continue
        out.append({"name": c["name"], "domain": domain_of(c.get("website", "")), "size": size,
                    "source": f"YC {c.get('batch', '')}".strip(), "evidence": []})
    print(f"  [sources] YC: {len(out)} companies")
    return out
