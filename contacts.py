# -*- coding: utf-8 -*-
"""
FirstLight - Who to message
For a new job, picks the 3 best people at the company to reach out to,
ranked, with a one-line reason and an opening angle for each.

  1. Candidates come from Hunter (HUNTER_API_KEY) and/or Apollo
     (APOLLO_API_KEY): names, titles, emails, LinkedIn URLs. Cached per
     company domain so free-tier credits stretch across many alerts.
  2. Claude (ANTHROPIC_API_KEY) reads the job, your profile and the
     candidates, checks public sources with web search, and ranks the top 3.
     It may add someone it finds on the web, but never invents an email.
"""

import json
import os
import re
import time

import requests

REQUEST_TIMEOUT    = 20
HUNTER_SEARCH_URL  = "https://api.hunter.io/v2/domain-search"
HUNTER_FINDER_URL  = "https://api.hunter.io/v2/email-finder"
APOLLO_PEOPLE_URL  = "https://api.apollo.io/api/v1/mixed_people/api_search"
CONTACT_CACHE_DAYS = 30

PICK_SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name":     {"type": "string"},
                    "title":    {"type": "string"},
                    "email":    {"type": ["string", "null"]},
                    "linkedin": {"type": ["string", "null"]},
                    "why":      {"type": "string"},
                    "angle":    {"type": "string"},
                },
                "required": ["name", "title", "email", "linkedin", "why", "angle"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["picks"],
    "additionalProperties": False,
}


# ─────────────────────────────────────────────
# Candidate sources
# ─────────────────────────────────────────────
def hunter_people(domain: str) -> list[dict]:
    key = os.environ.get("HUNTER_API_KEY", "")
    if not key or not domain:
        return []
    try:
        r = requests.get(HUNTER_SEARCH_URL, timeout=REQUEST_TIMEOUT, params={
            "domain": domain, "api_key": key, "type": "personal", "limit": 10,
        })
        r.raise_for_status()
        emails = r.json().get("data", {}).get("emails", [])
    except Exception as e:
        print(f"  [contacts] Hunter failed for {domain}: {e}")
        return []
    return [{
        "name":       f"{e.get('first_name') or ''} {e.get('last_name') or ''}".strip(),
        "title":      e.get("position") or "",
        "email":      e.get("value"),
        "linkedin":   e.get("linkedin"),
        "seniority":  e.get("seniority") or "",
        "department": e.get("department") or "",
        "source":     "Hunter",
    } for e in emails if e.get("first_name")]


def apollo_people(domain: str) -> list[dict]:
    key = os.environ.get("APOLLO_API_KEY", "")
    if not key or not domain:
        return []
    try:
        r = requests.post(APOLLO_PEOPLE_URL, timeout=REQUEST_TIMEOUT,
                          headers={"X-Api-Key": key, "Cache-Control": "no-cache"},
                          json={
                              "q_organization_domains_list": [domain],
                              "person_seniorities": ["founder", "c_suite", "vp", "head", "director", "manager", "senior"],
                              "per_page": 25,
                          })
        r.raise_for_status()
        people = r.json().get("people", [])
    except Exception as e:
        print(f"  [contacts] Apollo failed for {domain}: {e}")
        return []
    out = []
    for p in people:
        last = p.get("last_name") or p.get("last_name_obfuscated") or ""
        name = p.get("name") or f"{p.get('first_name', '')} {last}".strip()
        if not name:
            continue
        out.append({
            "name":       name,
            "title":      p.get("title") or "",
            "email":      p.get("email") if p.get("email_status") == "verified" else None,
            "linkedin":   p.get("linkedin_url"),
            "seniority":  p.get("seniority") or "",
            "department": ", ".join(p.get("departments") or []),
            "source":     "Apollo",
        })
    return out


def hunter_find_email(name: str, domain: str) -> str | None:
    key = os.environ.get("HUNTER_API_KEY", "")
    parts = name.split()
    if not key or not domain or len(parts) < 2:
        return None
    try:
        r = requests.get(HUNTER_FINDER_URL, timeout=REQUEST_TIMEOUT, params={
            "domain": domain, "first_name": parts[0], "last_name": parts[-1], "api_key": key,
        })
        r.raise_for_status()
        return r.json().get("data", {}).get("email")
    except Exception:
        return None


def candidates_for(domain: str, cache: dict, now: float | None = None) -> list[dict]:
    """Hunter + Apollo people at `domain`, cached for CONTACT_CACHE_DAYS."""
    now = now or time.time()
    hit = cache["contacts"].get(domain)
    if hit and now - hit.get("fetched", 0) < CONTACT_CACHE_DAYS * 86400:
        return hit["people"]
    people = merge_people(hunter_people(domain) + apollo_people(domain))
    if people:  # don't cache misses: keys may be added later, APIs may hiccup
        cache["contacts"][domain] = {"fetched": now, "people": people}
    return people


def merge_people(people: list[dict]) -> list[dict]:
    """Dedupes by name, keeping the first non-empty value of each field."""
    merged: dict[str, dict] = {}
    for p in people:
        key = re.sub(r"[^a-z]", "", p["name"].lower())
        if key not in merged:
            merged[key] = dict(p)
        else:
            for k, v in p.items():
                if v and not merged[key].get(k):
                    merged[key][k] = v
    return list(merged.values())


# ─────────────────────────────────────────────
# Ranking with Claude
# ─────────────────────────────────────────────
SYSTEM_PROMPT = """You help a job seeker get a warm introduction at a startup. \
For one open job, pick the 3 people at the company most worth a short, specific \
message, ranked best first.

Who tends to be best, roughly in order: the likely hiring manager for this role; \
someone currently in this exact role or on this team; a founder or exec at a small \
company (under ~100 people) where founders still hire directly; an engineer or \
leader whose background overlaps the job seeker's (shared employer, school, \
domain, or tech). Avoid recruiters unless nobody better exists, and avoid people \
whose role is unrelated to the job.

Use web search to check candidates and to find people the candidate list misses. \
Plain searches work well, e.g. `"<company>" "solutions engineer"` or \
`site:linkedin.com/in "<company>" "forward deployed"`: the result titles and \
snippets show names and current titles. Company team pages, blog posts, talks and \
GitHub are good for an opening angle. Snippets can be out of date, so prefer people \
who show up in more than one place.

Rules:
- Only give an email if it appears in the candidate list. Never guess or construct one; use null.
- "why" is one sentence on why this person, for this job.
- "angle" is one sentence the job seeker could open with: something concrete you found \
(a post they wrote, a shared background, a product launch) tied to the job seeker's experience.
- If you are unsure someone still works there, say so in "why"."""


def _job_brief(job: dict, company: dict) -> str:
    lines = [
        f"Company: {job['company']} ({company.get('domain') or 'domain unknown'})",
        f"About: {company.get('about') or 'n/a'}  Team size: {company.get('team_size') or 'n/a'}",
        f"Job: {job['title']}  Location: {job.get('location') or 'n/a'}",
        f"Link: {job['url']}",
    ]
    if job.get("description"):
        lines.append("Description (trimmed):\n" + job["description"][:4000])
    return "\n".join(lines)


def _extract_json(text: str) -> dict | None:
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S) or re.search(r"(\{\s*\"picks\".*\})", text, re.S)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return None
    return data if isinstance(data.get("picks"), list) else None


def rank_contacts(job: dict, company: dict, people: list[dict], cfg: dict) -> list[dict]:
    """Returns up to 3 picks: {name, title, email, linkedin, why, angle}."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return fallback_picks(people)

    import anthropic  # imported here so scanning works without the package

    client = anthropic.Anthropic()
    model = cfg.get("model", "claude-sonnet-5-5")
    candidates = json.dumps(
        [{k: p.get(k) for k in ("name", "title", "email", "seniority", "department")} for p in people],
        indent=0,
    ) if people else "[] (no candidates found; use web search)"
    user = (
        f"<job_seeker>\n{cfg.get('profile', '').strip()}\n</job_seeker>\n\n"
        f"<job>\n{_job_brief(job, company)}\n</job>\n\n"
        f"<candidates>\n{candidates}\n</candidates>\n\n"
        "Research, then end your reply with the top 3 as JSON in a ```json block: "
        '{"picks": [{"name", "title", "email", "linkedin", "why", "angle"}]}'
    )
    tools = [{"type": "web_search_20260209", "name": "web_search",
              "max_uses": cfg.get("max_searches", 4)}] if cfg.get("web_search", True) else []

    messages = [{"role": "user", "content": user}]
    try:
        for _ in range(3):  # resume server-side tool loops that pause
            resp = client.beta.messages.create(
                model=model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=messages,
                tools=tools,
                thinking={"type": "adaptive"},
                output_config={"effort": cfg.get("effort", "low")},
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
            if resp.stop_reason != "pause_turn":
                break
            messages = [messages[0], {"role": "assistant", "content": resp.content}]
    except anthropic.APIError as e:
        print(f"  [contacts] Claude call failed: {e}")
        return fallback_picks(people)

    if resp.stop_reason == "refusal":
        return fallback_picks(people)
    text = "".join(b.text for b in resp.content if b.type == "text")
    data = _extract_json(text) or _structure(client, model, text)
    if not data:
        return fallback_picks(people)
    return clean_picks(data["picks"], people)


def _structure(client, model: str, text: str) -> dict | None:
    """Turns free-text research into the picks JSON when the model didn't emit it."""
    if not text.strip():
        return None
    try:
        resp = client.messages.create(
            model=model,
            max_tokens=4000,
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": PICK_SCHEMA}},
            messages=[{"role": "user", "content": "Extract the ranked top 3 contacts from this research:\n\n" + text}],
        )
        return json.loads(next(b.text for b in resp.content if b.type == "text"))
    except Exception:
        return None


def clean_picks(picks: list[dict], people: list[dict]) -> list[dict]:
    """Keeps 3 picks and drops any email that didn't come from Hunter/Apollo."""
    known = {(p.get("email") or "").lower() for p in people if p.get("email")}
    out = []
    for p in picks[:3]:
        email = (p.get("email") or "").lower()
        out.append({
            "name":     p.get("name", "").strip(),
            "title":    p.get("title", "").strip(),
            "email":    p.get("email") if email in known else None,
            "linkedin": p.get("linkedin"),
            "why":      p.get("why", "").strip(),
            "angle":    p.get("angle", "").strip(),
        })
    return [p for p in out if p["name"]]


SENIORITY_ORDER = ["founder", "owner", "c_suite", "executive", "vp", "head", "director", "manager", "senior"]


def fallback_picks(people: list[dict]) -> list[dict]:
    """Without Claude: prefer technical leaders, then founders, then anyone senior."""
    def score(p):
        t = f"{p.get('title', '')} {p.get('department', '')}".lower()
        s = 0
        if any(k in t for k in ("solutions", "forward deployed", "field", "customer engineer", "sales engineer")):
            s -= 30
        if any(k in t for k in ("engineering", "cto", "technical")):
            s -= 20
        if any(k in t for k in ("founder", "ceo")):
            s -= 15
        if "recruit" in t or "talent" in t:
            s += 10
        sen = (p.get("seniority") or "").lower()
        s += SENIORITY_ORDER.index(sen) if sen in SENIORITY_ORDER else len(SENIORITY_ORDER)
        return s
    ranked = sorted(people, key=score)[:3]
    return [{"name": p["name"], "title": p.get("title", ""), "email": p.get("email"),
             "linkedin": p.get("linkedin"), "why": "Ranked by title (no Claude key set).", "angle": ""}
            for p in ranked]


# ─────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────
def who_to_message(job: dict, company: dict, cfg: dict, cache: dict) -> list[dict]:
    domain = company.get("domain", "")
    people = candidates_for(domain, cache) if domain else []
    picks = rank_contacts(job, company, people, cfg)
    if cfg.get("hunter_email_finder", False):
        for p in picks:
            if not p["email"]:
                p["email"] = hunter_find_email(p["name"], domain)
    return picks
