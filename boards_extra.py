# -*- coding: utf-8 -*-
"""
FirstLight - more job systems
The boards that large engineering firms, utilities and governments use, which
the startup-focused fetchers don't cover. Each fetcher takes (company, slug) and
returns jobs in the same shape as the others. Slug formats are in each docstring.

  oracle      Oracle Recruiting Cloud       "ejov.fa.ca2.oraclecloud.com|CX"
  ukg         UKG / UltiPro                 "recruiting.ultipro.com/GZA1000GZAE/JobBoard/<guid>"
  icims       iCIMS                         "careers-geosyntec"
  phenom      Phenom career sites           "careers.wasteconnections.com/us/en"
  paylocity   Paylocity                     "<company guid>"
  neogov      GovernmentJobs.com (NEOGOV)   "winnetka"
  jobsyn      DirectEmployers (.jobs sites) "aecom.jobs" or "dejobs.org|Veolia North America"
  jibe        Jibe / iCIMS Attract          "careers.trccompanies.com"
  jobs2web    SuccessFactors career sites   full search URL
  taleo       Taleo career sections         "chicago|100"
  usajobs     USAJOBS (needs a free key)    "Environmental Protection Agency"
  page        any page that lists jobs as links (small sites, villages): its URL
"""

import hashlib
import html
import json
import os
import re
from urllib.parse import urljoin, urlparse

import requests

REQUEST_TIMEOUT = 15
# iCIMS answers 405 to the full Chrome UA string ("KHTML, like Gecko"), so keep this short form
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}
MAX_JOBS = 400  # per board, newest first

# Set by the scanner each run: title words worth searching for on big boards
ROLE_TOKENS: list[str] = []


def _strip(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(raw or ""))).strip()


def _job(ats, slug, company, jid, title, url, loc="", remote=False, posted="", desc=""):
    return {"id": f"{ats}:{slug}:{jid}", "company": company, "title": _strip(title), "url": url,
            "location": loc or "", "workplace": "Remote" if remote else "", "is_remote": bool(remote),
            "compensation": None, "posted": posted or "", "description": _strip(desc)[:6000]}


def _get(url, **kw):
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT, headers={**HEADERS, **kw.pop("headers", {})}, **kw)
        return r if r.status_code == 200 else None
    except Exception:
        return None


def _iso(date_text: str) -> str:
    """'Nov 9, 2026' / '11/09/2026' / ISO -> ISO date, or ''."""
    from datetime import datetime
    t = (date_text or "").strip()
    for fmt in ("%b %d, %Y", "%m/%d/%Y", "%Y-%m-%d", "%B %d, %Y"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%dT00:00:00Z")
        except ValueError:
            pass
    return t if re.match(r"\d{4}-\d{2}-\d{2}", t) else ""


# ─────────────────────────────────────────────
# Oracle Recruiting Cloud
# ─────────────────────────────────────────────
def fetch_oracle(company: str, slug: str) -> list[dict]:
    """slug: "<host>|<site number>", e.g. "emcm.fa.us2.oraclecloud.com|WMCareers"."""
    host, site = slug.split("|")
    jobs = []
    for offset in range(0, MAX_JOBS, 100):
        url = (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
               f"?onlyData=true&expand=requisitionList.secondaryLocations&finder=findReqs;"
               f"siteNumber={site},facetsList=NONE,limit=100,offset={offset},sortBy=POSTING_DATES_DESC")
        r = _get(url, headers={"Accept": "application/json"})
        items = (r.json().get("items") or [{}]) if r else [{}]
        reqs = items[0].get("requisitionList") or []
        for j in reqs:
            locs = [j.get("PrimaryLocation", "")] + [s.get("Name", "") for s in j.get("secondaryLocations") or []]
            jobs.append(_job("oracle", host.split(".")[0], company, j.get("Id"), j.get("Title"),
                             f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j.get('Id')}",
                             "; ".join(x for x in locs if x),
                             (j.get("WorkplaceTypeCode") or "") == "ORA_REMOTE",
                             j.get("PostedDate", ""), j.get("ShortDescriptionStr", "")))
        if len(reqs) < 100:
            break
    return jobs


# ─────────────────────────────────────────────
# UKG / UltiPro
# ─────────────────────────────────────────────
def fetch_ukg(company: str, slug: str) -> list[dict]:
    """slug: "recruiting2.ultipro.com/BRO1018BRACA/JobBoard/<guid>"."""
    base = f"https://{slug.rstrip('/')}"
    jobs = []
    for skip in range(0, MAX_JOBS, 50):
        body = {"opportunitySearch": {"Top": 50, "Skip": skip, "QueryString": "", "Filters": [],
                                      "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate",
                                                   "Ascending": False}]}}
        try:
            r = requests.post(f"{base}/JobBoardView/LoadSearchResults", json=body,
                              headers=HEADERS, timeout=REQUEST_TIMEOUT)
            data = r.json() if r.status_code == 200 else {}
        except Exception:
            data = {}
        opps = data.get("opportunities") or []
        for o in opps:
            locs = []
            for l in o.get("Locations") or []:
                a = l.get("Address") or {}
                locs.append(", ".join(x for x in [a.get("City"), (a.get("State") or {}).get("Code"),
                                                  (a.get("Country") or {}).get("Code")] if x)
                            or l.get("LocalizedDescription") or "")
            jobs.append(_job("ukg", slug.split("/")[1], company, o.get("Id"), o.get("Title"),
                             f"{base}/OpportunityDetail?opportunityId={o.get('Id')}",
                             "; ".join(x for x in locs if x), o.get("IsRemote") or o.get("RemoteType"),
                             o.get("PostedDate", ""), o.get("BriefDescription", "")))
        if len(opps) < 50:
            break
    return jobs


# ─────────────────────────────────────────────
# iCIMS
# ─────────────────────────────────────────────
def fetch_icims(company: str, slug: str) -> list[dict]:
    """slug: the iCIMS subdomain, e.g. "careers-geosyntec"."""
    jobs, seen = [], set()
    for page in range(0, 10):
        r = _get(f"https://{slug}.icims.com/jobs/search?pr={page}&in_iframe=1")
        if not r:
            break
        new = 0
        for card in r.text.split('class="iCIMS_JobCardItem"')[1:]:
            m = re.search(r'href="(https://[^"]+/jobs/(\d+)/[^"?]+/job)[^"]*"[^>]*>(.*?)</a>', card, re.S)
            if not m or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            new += 1
            title = re.sub(r"^(Job )?Title\s+", "", _strip(m.group(3)))
            loc = re.search(r'field-label">(?:Location|City)</span>\s*(?:</dt>\s*<dd[^>]*>)?\s*<span[^>]*>(.*?)</span>', card, re.S)
            remote = re.search(r'Remote</dt>\s*<dd[^>]*>(.*?)</dd>', card, re.S)
            desc = re.search(r'class="col-xs-12 description">(.*?)</div>', card, re.S)
            jobs.append(_job("icims", slug, company, m.group(2), title, m.group(1),
                             _icims_loc(_strip(loc.group(1)) if loc else ""),
                             bool(remote and _strip(remote.group(1)).lower().startswith("yes")),
                             "", desc.group(1) if desc else ""))
        if not new:
            break
    return jobs


def _icims_loc(raw: str) -> str:
    """'US-IL-Chicago' -> 'Chicago, IL, US'."""
    parts = raw.split("-", 2)
    return f"{parts[2]}, {parts[1]}, {parts[0]}" if len(parts) == 3 and len(parts[0]) == 2 else raw


# ─────────────────────────────────────────────
# Phenom
# ─────────────────────────────────────────────
def fetch_phenom(company: str, slug: str) -> list[dict]:
    """slug: the career site root with locale, e.g. "jobs.republicservices.com/us/en"."""
    host, _, path = slug.partition("/")
    lang, country = (path.split("/") + ["en", "us"])[1::-1][:2] if path else ("en", "us")
    jobs, seen = [], set()
    for term in sorted(set(ROLE_TOKENS)) or [""]:
        for offset in range(0, 200, 50):
            body = {"lang": f"{lang}_{country}", "deviceType": "desktop", "country": country,
                    "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "Most recent",
                    "subsearch": "", "from": offset, "jobs": True, "counts": False, "all_fields": [],
                    "size": 50, "clearAll": False, "jdsource": "facets", "isSliderEnable": False,
                    "siteType": "external", "keywords": term, "global": True,
                    "selected_fields": {}, "locationData": {}}
            try:
                r = requests.post(f"https://{host}/widgets", json=body, headers=HEADERS, timeout=REQUEST_TIMEOUT)
                block = (r.json().get("refineSearch") or {}) if r.status_code == 200 else {}
            except Exception:
                block = {}
            posts = (block.get("data") or {}).get("jobs") or []
            for j in posts:
                jid = j.get("jobId") or j.get("reqId")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                loc = j.get("location") or j.get("cityStateCountry", "")
                jobs.append(_job("phenom", host, company, jid, j.get("title"),
                                 f"https://{slug.rstrip('/')}/job/{jid}", loc, "remote" in loc.lower(),
                                 j.get("postedDate", ""), j.get("descriptionTeaser", "")))
            if len(posts) < 50:
                break
    return jobs


# ─────────────────────────────────────────────
# Paylocity
# ─────────────────────────────────────────────
def fetch_paylocity(company: str, slug: str) -> list[dict]:
    """slug: the company guid from recruiting.paylocity.com/recruiting/jobs/All/<guid>."""
    r = _get(f"https://recruiting.paylocity.com/recruiting/jobs/All/{slug}")
    m = re.search(r"window\.pageData\s*=\s*(\{.*?\});\s*\n", r.text, re.S) if r else None
    if not m:
        return []
    jobs = []
    for j in json.loads(m.group(1)).get("Jobs") or []:
        l = j.get("JobLocation") or {}
        loc = ", ".join(x for x in [l.get("City"), l.get("State")] if x) or j.get("LocationName", "")
        jobs.append(_job("paylocity", slug[:8], company, j.get("JobId"), j.get("JobTitle"),
                         f"https://recruiting.paylocity.com/Recruiting/Jobs/Details/{j.get('JobId')}",
                         loc, j.get("IsRemote"), j.get("PublishedDate", "")))
    return jobs


# ─────────────────────────────────────────────
# GovernmentJobs.com (NEOGOV)
# ─────────────────────────────────────────────
def fetch_neogov(company: str, slug: str) -> list[dict]:
    """slug: the agency id in governmentjobs.com/careers/<agency>."""
    jobs, seen = [], set()
    for page in range(1, 6):
        r = _get("https://www.governmentjobs.com/careers/home/index",
                 params={"agency": slug, "sort": "PostingDate", "isDescendingSort": "true", "page": page},
                 headers={"X-Requested-With": "XMLHttpRequest"})
        if not r:
            break
        new = 0
        for row in re.split(r'<li class="list-item"', r.text)[1:]:
            m = re.search(r'href="(/careers/[^/"]+/jobs/(\d+)/[^"]+)"[^>]*>(.*?)</a>', row, re.S)
            if not m or m.group(2) in seen:
                continue
            seen.add(m.group(2))
            new += 1
            meta = [_strip(x) for x in re.findall(r"<li[^>]*>(.*?)</li>", row.split('class="list-meta"', 1)[-1], re.S)]
            text = _strip(row)
            posted = re.search(r"Posted\s+(\d{1,2}/\d{1,2}/\d{4})", text)
            job = _job("neogov", slug, company, m.group(2), m.group(3),
                       f"https://www.governmentjobs.com{m.group(1)}",
                       meta[0] if meta and "$" not in meta[0] else "", False,
                       _iso(posted.group(1)) if posted else "", text)
            sal = re.search(r"\$[\d,.]+\s*-\s*\$([\d,.]+)\s*(Annually|Hourly)", text)
            if sal:
                hi = float(sal.group(1).replace(",", ""))
                job["compensation"] = int(hi * 2080 if sal.group(2) == "Hourly" else hi)
            jobs.append(job)
        if not new:
            break
    return jobs


# ─────────────────────────────────────────────
# DirectEmployers (.jobs sites, dejobs.org)
# ─────────────────────────────────────────────
JOBSYN_API = "https://prod-search-api.jobsyn.org/api/v1/solr/search"
# Set from the profile (Filters.search_location, e.g. "Illinois"): big boards are
# searched there plus once for remote jobs, instead of paging through the world
SEARCH_LOCATION = ""


def fetch_jobsyn(company: str, slug: str) -> list[dict]:
    """
    slug: a company's .jobs site ("aecom.jobs", "burnsmcd.jobs"), or
    "dejobs.org|<exact company name>" for members without their own site.
    """
    origin, _, only = slug.partition("|")
    jobs, seen = [], set()
    queries = []
    for term in sorted(set(ROLE_TOKENS)) or [""]:
        q = f"{term} {only}".strip() if only else term
        queries += [(q, SEARCH_LOCATION), (f"{q} remote", "")] if SEARCH_LOCATION else [(q, "")]
    for q, where in queries:
        for page in range(1, 11):
            params = {"q": q, "page": page, "num_items": 50}
            if where:
                params["location"] = where
            try:
                r = requests.get(JOBSYN_API, params=params, timeout=REQUEST_TIMEOUT,
                                 headers={**HEADERS, "Accept": "application/json", "x-origin": origin})
                data = r.json() if r.status_code == 200 else {}
            except Exception:
                data = {}
            for j in data.get("jobs") or []:
                if only and j.get("company_exact") != only:
                    continue
                jid = j.get("guid") or j.get("id")
                if not jid or jid in seen:
                    continue
                seen.add(jid)
                loc = j.get("location_exact") or ", ".join(x for x in [j.get("city_exact"), j.get("state_short_exact")] if x)
                if j.get("country_exact") and j.get("country_short_exact") != "USA":
                    loc = f"{loc}, {j['country_exact']}"  # e.g. "Virtual, COL" -> "..., Colombia"
                url = j.get("url") or f"https://{origin}/{jid}/job/"
                if url.startswith("/"):
                    url = f"https://{origin}{url}"
                jobs.append(_job("jobsyn", origin.split(".")[0], company, jid,
                                 j.get("title_exact") or j.get("title"), url, loc,
                                 bool(j.get("is_remote")) or "remote" in (loc or "").lower(),
                                 j.get("date_new", ""), j.get("description", "")))
            if not (data.get("pagination") or {}).get("has_more_pages"):
                break
    return jobs


# ─────────────────────────────────────────────
# Jibe (iCIMS Attract) career sites
# ─────────────────────────────────────────────
def fetch_jibe(company: str, slug: str) -> list[dict]:
    """slug: the career site host, e.g. "careers.trccompanies.com"."""
    jobs = []
    for page in range(1, 10):
        r = _get(f"https://{slug}/api/jobs", params={"page": page, "limit": 100, "sortBy": "posted_date",
                                                      "descending": "true"})
        posts = (r.json().get("jobs") or []) if r else []
        for p in posts:
            j = p.get("data") or {}
            loc = j.get("full_location") or ", ".join(x for x in [j.get("city"), j.get("state")] if x)
            jobs.append(_job("jibe", slug.split(".")[1], company, j.get("req_id") or j.get("slug"), j.get("title"),
                             j.get("apply_url") or f"https://{slug}/jobs/{j.get('slug')}",
                             loc, "remote" in (loc + " " + str(j.get("location_type", ""))).lower(),
                             j.get("posted_date", ""), j.get("description", "")))
        if len(posts) < 100:
            break
    return jobs


# ─────────────────────────────────────────────
# SuccessFactors (jobs2web) career sites
# ─────────────────────────────────────────────
def fetch_jobs2web(company: str, slug: str) -> list[dict]:
    """slug: a search results URL, e.g. https://illinois.jobs2web.com/search/?q=&optionsFacetsDD_customfield3=..."""
    jobs, seen = [], set()
    host = urlparse(slug).netloc
    for start in range(0, 250, 25):
        r = _get(slug + ("&" if "?" in slug else "?") + f"startrow={start}")
        if not r:
            break
        tiles = re.findall(r'class="jobTitle-link[^"]*"[^>]*href="(/job/[^"]+/(\d+)/)"[^>]*>(.*?)</a>', r.text, re.S)
        new = 0
        for path, jid, title in tiles:
            if jid in seen:
                continue
            seen.add(jid)
            new += 1
            loc = re.search(rf'id="job-{jid}-desktop-section-location-value"[^>]*>\s*([^<]+)', r.text)
            date = re.search(rf'id="job-{jid}-desktop-section-date-value"[^>]*>\s*([^<]+)', r.text)
            jobs.append(_job("jobs2web", host.split(".")[0], company, jid, title, f"https://{host}{path}",
                             _strip(loc.group(1)) if loc else "", False,
                             _iso(_strip(date.group(1))) if date else ""))
        if new < 25:
            break
    return jobs


# ─────────────────────────────────────────────
# Taleo career sections
# ─────────────────────────────────────────────
def fetch_taleo(company: str, slug: str) -> list[dict]:
    """slug: "<tenant>|<career section>", e.g. "chicago|100"."""
    tenant, section = slug.split("|")
    base = f"https://{tenant}.taleo.net/careersection/{section}"
    r = _get(f"{base}/joblist.ftl?lang=en")
    m = re.search(r"api\.fillList\('requisitionListInterface',\s*'listRequisition',\s*\[(.*?)\]\);", r.text, re.S) if r else None
    if not m:
        return []
    cells = [html.unescape(c).replace("%24", "$").replace("%2C", ",")
             for c in re.findall(r"'((?:[^'\\]|\\.)*)'", m.group(1))]
    jobs, seen = [], set()
    for i in range(len(cells) - 2):
        jid, title = cells[i], cells[i + 1]
        if re.fullmatch(r"\d{4,}", jid) and cells[i + 2] == jid and title and not title.isdigit() and jid not in seen:
            seen.add(jid)
            tail = cells[i + 2:i + 14]
            posted = next((_iso(c) for c in tail if re.fullmatch(r"[A-Z][a-z]{2} \d{1,2}, \d{4}", c)), "")
            pay = next((c for c in tail if c.startswith("$")), "")
            jobs.append(_job("taleo", tenant, company, jid, title,
                             f"{base}/jobdetail.ftl?job={jid}&lang=en", "", False, posted, pay))
    return jobs


# ─────────────────────────────────────────────
# USAJOBS (federal)
# ─────────────────────────────────────────────
def fetch_usajobs(company: str, slug: str) -> list[dict]:
    """
    slug: the hiring organization, e.g. "Environmental Protection Agency".
    Needs a free key from developer.usajobs.gov in USAJOBS_API_KEY and the
    email it was issued to in USAJOBS_EMAIL; without them this returns nothing.
    """
    key, email = os.environ.get("USAJOBS_API_KEY"), os.environ.get("USAJOBS_EMAIL")
    if not key or not email:
        return []
    jobs = []
    for page in range(1, 6):
        try:
            r = requests.get("https://data.usajobs.gov/api/search",
                             params={"Organization": "", "Keyword": slug, "ResultsPerPage": 250, "Page": page},
                             headers={"Host": "data.usajobs.gov", "User-Agent": email, "Authorization-Key": key},
                             timeout=REQUEST_TIMEOUT)
            items = r.json()["SearchResult"]["SearchResultItems"] if r.status_code == 200 else []
        except Exception:
            items = []
        for it in items:
            d = it.get("MatchedObjectDescriptor") or {}
            if slug.lower() not in (d.get("OrganizationName", "") + " " + d.get("DepartmentName", "")).lower():
                continue
            pay = (d.get("PositionRemuneration") or [{}])[0]
            remote = ((d.get("UserArea") or {}).get("Details") or {}).get("RemoteIndicator")
            j = _job("usajobs", "fed", company, d.get("PositionID") or it.get("MatchedObjectId"),
                     d.get("PositionTitle"), d.get("PositionURI"), d.get("PositionLocationDisplay", ""),
                     remote, d.get("PublicationStartDate", ""),
                     ((d.get("UserArea") or {}).get("Details") or {}).get("JobSummary", ""))
            try:
                hi = float(pay.get("MaximumRange") or 0)
                j["compensation"] = int(hi * 2080) if pay.get("RateIntervalCode") == "PH" else int(hi) or None
            except ValueError:
                pass
            jobs.append(j)
        if len(items) < 250:
            break
    return jobs


# ─────────────────────────────────────────────
# Any page that lists jobs as links
# ─────────────────────────────────────────────
JOBISH_HREF = re.compile(r"job|career|employ|position|opening|posting|bulpreview|requisition|vacanc|\.pdf", re.I)
SKIP_LABELS = re.compile(r"^(apply|apply now|view|more|details|read more|learn more|click here|here|jobs?|careers?|"
                         r"employment( opportunities)?|search|benefits|faqs?|login|sign in|subscribe|home|back)$", re.I)


def page_links(base: str, text: str, href_re: re.Pattern = JOBISH_HREF) -> list[tuple[str, str]]:
    """(url, label) for links on a page that could be single job postings."""
    out, seen = [], set()
    for href, label in re.findall(r'<a[^>]+href="([^"#]+)"[^>]*>(.*?)</a>', text, re.S | re.I):
        url = urljoin(base, html.unescape(href))
        heading = re.search(r"<h[1-4][^>]*>(.*?)</h[1-4]>", label, re.S)
        label = re.sub(r"^(Job )?Title\s+", "", _strip(heading.group(1) if heading else label))
        if (not label or len(label) < 4 or len(label) > 140 or SKIP_LABELS.match(label)
                or url.rstrip("/") == base.rstrip("/") or url in seen or not url.startswith("http")):
            continue
        if not href_re.search(url):
            continue
        seen.add(url)
        out.append((url, label))
    return out


def fetch_page(company: str, slug: str) -> list[dict]:
    """
    slug: a page URL, optionally "<url>|<regex>" to only count links whose URL
    matches the regex (e.g. "|/job/" to skip category links). Every link left is
    a candidate job; the role filter then keeps the ones whose text names a role
    you want. Used for small employers (villages, districts, small firms)
    without a job board API.
    """
    url, _, pattern = slug.partition("|")
    r = _get(url)
    if not r:
        return []
    host = urlparse(url).netloc
    href_re = re.compile(pattern, re.I) if pattern else JOBISH_HREF
    return [_job("page", host, company, hashlib.sha1(u.encode()).hexdigest()[:12], label, u)
            for u, label in page_links(r.url, r.text, href_re)]


FETCHERS = {
    "oracle":    fetch_oracle,
    "ukg":       fetch_ukg,
    "icims":     fetch_icims,
    "phenom":    fetch_phenom,
    "paylocity": fetch_paylocity,
    "neogov":    fetch_neogov,
    "jobsyn":    fetch_jobsyn,
    "jibe":      fetch_jibe,
    "jobs2web":  fetch_jobs2web,
    "taleo":     fetch_taleo,
    "usajobs":   fetch_usajobs,
    "page":      fetch_page,
}
