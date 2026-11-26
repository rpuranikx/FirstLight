"""
Broad feeds: searches by job, not by company, so they find companies that are on no list.

  - Getro search across each VC portfolio board (also covers portfolio companies that
    host jobs on their own careers site, which the watchlist can't poll)
  - Built In Chicago search
  - HN "Who is hiring?" (monthly thread)
  - Remote boards: Remotive, RemoteOK, We Work Remotely

Each returns jobs in the same shape as ats._job plus "company" and "source". Filtering
happens later, in filters.py, exactly as for watchlist jobs.
"""
import html as htmllib
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import requests

from .sources import GETRO_API, SKIP_STAGES, UA, domain_of, getro_collection
from .text import pay_from_text, plain_text

TIMEOUT = 30
BROWSER = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
DEFAULT_TERMS = ["solutions engineer", "sales engineer", "solutions architect", "solutions consultant",
                 "forward deployed", "presales", "implementation engineer", "deployment engineer",
                 "technical consultant", "applied ai", "ai engineer", "product manager"]


def _job(source, jid, title, url, company, location="", remote=False, posted="", description="",
         pay_max=None, ote=False, workplace="", domain=""):
    desc = plain_text(description)
    if pay_max is None:
        p = pay_from_text(desc)
        pay_max, ote = p["max"], p["ote"]
    return {"id": f"{source}:{jid}", "title": (title or "").strip(), "url": url, "location": location or "",
            "workplace": workplace, "remote": bool(remote), "posted": posted or "", "pay_max": pay_max,
            "pay_ote": ote, "description": desc, "company": (company or "").strip(), "domain": domain,
            "source": source}


def _iso(epoch) -> str:
    try:
        return datetime.fromtimestamp(int(epoch), tz=timezone.utc).isoformat()
    except Exception:
        return ""


# ── Getro: newest first, so a run stops at the first job it has already passed ──
def getro_search(host: str, terms=DEFAULT_TERMS, since: int = 0, max_pages: int = 25) -> list[dict]:
    coll = getro_collection(host)
    if not coll:
        return []
    cid, vc = coll
    out, seen = [], set()
    for term in terms:
        for page in range(max_pages):
            try:
                r = requests.post(GETRO_API.format(cid=cid, kind="jobs"), headers=UA, timeout=TIMEOUT,
                                  json={"hitsPerPage": 20, "page": page, "query": term,
                                        "filters": {"searchable_locations": ["United States"]}})
                r.raise_for_status()
                rows = (r.json().get("results") or {}).get("jobs") or []
            except Exception as e:
                print(f"  [feeds] {host} '{term}' page {page}: {e}")
                break
            fresh = [j for j in rows if int(j.get("created_at") or 0) > since]
            for j in fresh:
                if j["id"] in seen:
                    continue
                seen.add(j["id"])
                org = j.get("organization") or {}
                if org.get("stage") in SKIP_STAGES:  # acquired: Getro files the buyer's jobs under the old name
                    continue
                usd = (j.get("compensation_currency") or "USD") == "USD"
                cents = j.get("compensation_amount_max_cents") if usd else None
                mode = j.get("work_mode") or ""
                out.append(_job(f"getro-{cid}", j["id"], j.get("title"), j.get("url"), org.get("name"),
                                "; ".join(j.get("locations") or []), mode == "remote", _iso(j.get("created_at")),
                                pay_max=int(cents) // 100 if cents else None, workplace=mode,
                                domain=domain_of(org.get("domain") or "")))
                out[-1]["getro_host"] = host
            if len(fresh) < len(rows) or len(rows) < 20:
                break
    print(f"  [feeds] {vc}: {len(out)} jobs")
    return out


# ── Built In Chicago ──
CARD = re.compile(r'id="job-card-(\d+)"(.*?)(?=id="job-card-\d+"|$)', re.S)


def builtin_chicago(terms=DEFAULT_TERMS, pages: int = 3) -> list[dict]:
    out, seen = [], set()
    for term in terms:
        for page in range(1, pages + 1):
            try:
                t = requests.get("https://www.builtinchicago.org/jobs", params={"search": term, "page": page},
                                 headers=BROWSER, timeout=TIMEOUT).text
            except Exception as e:
                print(f"  [feeds] Built In '{term}': {e}")
                break
            cards = CARD.findall(t)
            for jid, card in cards:
                if jid in seen:
                    continue
                seen.add(jid)
                company = re.search(r'data-id="company-title"[^>]*>\s*<span>([^<]+)', card)
                title = re.search(r'data-id="job-card-title"[^>]*>([^<]+)', card)
                href = re.search(r'href="(/job/[^"]+)"', card)
                spans = [htmllib.unescape(s).strip() for s in re.findall(r"<span[^>]*>([^<]{2,80})</span>", card)]
                where = next((s for s in spans if re.search(r"remote|hybrid|in-office", s, re.I)), "")
                loc = next((s for s in spans if re.search(r", [A-Z]{2}\b|USA", s)), "")
                pay = next((s for s in spans if re.search(r"\d+K", s) and "Annually" in s), "")
                nums = [int(n) * 1000 for n in re.findall(r"(\d+)K", pay)]
                if not (title and href):
                    continue
                out.append(_job("builtin", jid, htmllib.unescape(title[1]), "https://www.builtinchicago.org" + href[1],
                                htmllib.unescape(company[1]) if company else "", f"{loc} ({where})" if where else loc,
                                "remote" in where.lower(), pay_max=max(nums) if nums else None,
                                workplace=where.lower()))
            if len(cards) < 20:
                break
    print(f"  [feeds] Built In Chicago: {len(out)} jobs")
    return out


# ── HN "Ask HN: Who is hiring?" ──
def hn_whos_hiring() -> list[dict]:
    try:
        hits = requests.get("https://hn.algolia.com/api/v1/search_by_date",
                            params={"tags": "story,author_whoishiring", "hitsPerPage": 5}, timeout=TIMEOUT).json()["hits"]
        story = next(h for h in hits if "who is hiring" in h["title"].lower())
        thread = requests.get(f"https://hn.algolia.com/api/v1/items/{story['objectID']}", timeout=60).json()
    except Exception as e:
        print(f"  [feeds] HN unavailable: {e}")
        return []
    out = []
    for c in thread.get("children") or []:
        text = plain_text(c.get("text") or "")
        if not text:
            continue
        head = text.split("\n", 1)[0]
        parts = [p.strip() for p in head.split("|")]
        if len(parts) < 2:
            continue
        # One post often lists several roles; the title we keep is the header line, and
        # filters run on the whole post, so "Solutions Engineer" anywhere in it counts.
        role = next((p for p in parts[1:] if re.search(r"engineer|architect|consultant|manager|deploy", p, re.I)), parts[1])
        remote = bool(re.search(r"\bremote\b", head, re.I))
        out.append(_job("hn", c["id"], role, f"https://news.ycombinator.com/item?id={c['id']}", parts[0],
                        " | ".join(parts[2:4]), remote, c.get("created_at"), text))
    print(f"  [feeds] HN Who's Hiring: {len(out)} posts")
    return out


# ── Remote boards ──
def remotive(terms=DEFAULT_TERMS) -> list[dict]:
    out, seen = [], set()
    for term in terms:
        try:
            jobs = requests.get("https://remotive.com/api/remote-jobs", params={"search": term},
                                timeout=TIMEOUT).json().get("jobs") or []
        except Exception as e:
            print(f"  [feeds] Remotive: {e}")
            break
        for j in jobs:
            if j["id"] in seen or j.get("job_type") not in ("full_time", "", None):
                continue
            seen.add(j["id"])
            out.append(_job("remotive", j["id"], j.get("title"), j.get("url"), j.get("company_name"),
                            j.get("candidate_required_location", ""), True, j.get("publication_date"),
                            f"{j.get('salary', '')}\n{j.get('description', '')}", workplace="remote"))
    print(f"  [feeds] Remotive: {len(out)} jobs")
    return out


def remoteok() -> list[dict]:
    try:
        rows = requests.get("https://remoteok.com/api", headers=BROWSER, timeout=TIMEOUT).json()[1:]
    except Exception as e:
        print(f"  [feeds] RemoteOK: {e}")
        return []
    out = [_job("remoteok", j["id"], j.get("position"), j.get("url"), j.get("company"), j.get("location", ""),
                True, j.get("date"), j.get("description", ""),
                pay_max=int(j["salary_max"]) if str(j.get("salary_max") or "0") != "0" else None, workplace="remote")
           for j in rows if j.get("id")]
    print(f"  [feeds] RemoteOK: {len(out)} jobs")
    return out


WWR = ["remote-sales-and-marketing-jobs", "remote-customer-support-jobs", "remote-devops-sysadmin-jobs",
       "remote-full-stack-programming-jobs", "remote-product-jobs", "all-other-remote-jobs"]


def weworkremotely() -> list[dict]:
    out = []
    for cat in WWR:
        try:
            root = ET.fromstring(requests.get(f"https://weworkremotely.com/categories/{cat}.rss",
                                              headers=BROWSER, timeout=TIMEOUT).content)
        except Exception as e:
            print(f"  [feeds] WWR {cat}: {e}")
            continue
        for it in root.iter("item"):
            title = it.findtext("title") or ""
            company, _, role = title.partition(":")
            out.append(_job("wwr", it.findtext("guid") or it.findtext("link"), role or title, it.findtext("link"),
                            company, it.findtext("region") or "", True, it.findtext("pubDate"),
                            it.findtext("description") or "", workplace="remote"))
    print(f"  [feeds] We Work Remotely: {len(out)} jobs")
    return out


def collect(profile: dict, since: dict | None = None) -> list[dict]:
    """All broad-feed jobs for a profile. `since` maps a Getro host to the newest created_at seen."""
    f = profile.get("feeds") or {}
    terms = f.get("terms") or DEFAULT_TERMS
    since = since or {}
    jobs = []
    for host in (profile.get("watchlist") or {}).get("getro_boards") or []:
        jobs += getro_search(host, terms, since.get(host, 0))
    if f.get("builtin_chicago", True):
        jobs += builtin_chicago(terms)
    if f.get("hn", True):
        jobs += hn_whos_hiring()
    if f.get("remote_boards", True):
        jobs += remotive(terms) + remoteok() + weworkremotely()
    return jobs
