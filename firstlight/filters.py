"""
Filtering and taste tiers. Plain rules, no model calls. Every dropped job keeps its reason
so the miss check can say exactly why a job never reached Discord.

The rules a profile can change live under `filters:` and `tiers:` in its YAML; the
defaults below are Rishi's (agreed 2026-11-24).
"""
import re

from .text import pay_from_text, plain_text, years_required

DEFAULTS = {
    "pay_floor": 115_000,        # base; unlisted pay is kept
    "ote_floor": 140_000,        # when the only number is on-target earnings
    "max_years": 5,
    "home_metro": ["chicago", "evanston", "oak brook", "naperville", "schaumburg", "illinois", ", il"],
    "drop_title": r"\b(director|vp|vice president|head of|chief|manager|principal|staff|lead|intern|internship|"
                  r"new grad|apprentice|architect,? enterprise)\b",
    "keep_title_even_if": r"^(?!.*\b(director|vp|head of|staff|principal|lead)\b).*(\bsolutions? architect\b|\bproduct manager\b|\bprogram manager\b|\bproject manager\b|\baccount manager\b)",
    "drop_type": r"\b(contract(or)?|temporary|temp|part[- ]time|freelance|seasonal|1099)\b",
    "clearance": r"\b(active|current)\s+(ts|top secret|secret|ts/sci|security)\s*(/sci)?\s*clearance\b|"
                 r"\bclearance\s+(is\s+)?required\b",
    "surveillance": ["clearview", "soundthinking", "shotspotter", "fog data", "babel street", "anomaly six"],
}

# Title families and the taste tier each lands in (job-taste-profile, 2026-11-23)
FAMILIES = [
    ("ringer", r"\b(solutions?|sales|pre-?sales|customer|value)\s+(engineer|architect|consultant)\b|"
               r"\bsolutions? consultant\b|\bfield (solutions|sales) engineer\b"),
    ("stretch", r"\bforward[- ]deployed\b|\bapplied (ai|ml)\b|\bai engineer\b|\b(ml|ai|llm) (solutions|engineer)\b|"
                r"\bassociate product manager\b|\bapm\b|\bdeployment strategist\b|\bagent engineer\b"),
    ("stepping", r"\bimplementation\b|\bdeployment engineer\b|\bintegrations?\s+engineer\b|\bonboarding engineer\b|"
                 r"\btechnical (program|project|product) manager\b|\bproduct manager\b|\bfield (application|applications)? ?engineer\b|"
                 r"\btechnical consultant\b|\bsolutions? (delivery|specialist)\b|\bpartner engineer\b"),
    ("meh", r"\btechnical account manager\b|\bsupport engineer\b|\bcustomer success engineer\b|\bsolutions analyst\b|"
            r"\btechnical success\b"),
]
FAMILY_RE = [(tier, re.compile(p, re.I)) for tier, p in FAMILIES]
SENIOR = re.compile(r"\bsenior\b|\bsr\.?\b", re.I)
FOREIGN = re.compile(r"\b(uk|united kingdom|london|dublin|ireland|germany|berlin|munich|paris|france|amsterdam|"
                     r"netherlands|spain|madrid|barcelona|lisbon|portugal|poland|warsaw|india|bangalore|bengaluru|"
                     r"hyderabad|singapore|sydney|melbourne|australia|tokyo|japan|toronto|vancouver|canada|"
                     r"mexico|brazil|são paulo|sao paulo|tel aviv|israel|emea|apac|latam|europe|stockholm|zurich|"
                     r"switzerland|seoul|korea|dubai|uae|philippines|manila)\b", re.I)
# A "remote" job whose title names another US city is usually based there (RemoteOK tags
# everything remote; "Senior Sales Engineer - Atlanta" isn't)
US_CITY_TITLE = re.compile(r"[-–(,/|]\s*(atlanta|austin|boston|dallas|denver|houston|los angeles|la|miami|new york|nyc|"
                           r"philadelphia|phoenix|portland|raleigh|salt lake|san diego|san francisco|sf|bay area|"
                           r"seattle|washington,? dc|dc|nashville|charlotte|minneapolis|detroit|pittsburgh|"
                           r"san jose|palo alto|mountain view|menlo park|irvine|tampa|orlando|columbus|kansas city|"
                           r"st\.? louis|baltimore|new jersey|nj|cambridge|brooklyn|carolinas|texas|california|florida)\b",
                           re.I)
US_REMOTE = re.compile(r"\b(us|u\.s\.|usa|united states|north america|americas|anywhere)\b", re.I)


def _cfg(profile):
    c = dict(DEFAULTS)
    c.update((profile or {}).get("filters") or {})
    return c


def family(title: str) -> str | None:
    for tier, rx in FAMILY_RE:
        if rx.search(title or ""):
            return tier
    return None


def location_ok(job: dict, cfg) -> tuple[bool, str]:
    """Chicago area (any workplace) or remote in the US. A remote job that names a foreign
    place in its title or location is out (v1 leak: 'FDE - London (Remote)')."""
    title, loc = job.get("title") or "", (job.get("location") or "")
    where = f"{title} | {loc}".lower()
    if any(m in where for m in cfg["home_metro"]):
        return True, "chicago"
    remote = job.get("remote") or job.get("workplace") == "remote" or "remote" in where
    if not remote:
        return False, f"location: {loc or 'not given'}"
    if FOREIGN.search(title):  # the title wins: "Sales Engineer - Germany" is in Germany, whatever the tags say
        return False, f"remote outside the US: {FOREIGN.search(title).group(0)} in the title"
    if FOREIGN.search(where) and not US_REMOTE.search(where):
        return False, f"remote outside the US: {loc or title}"
    city = US_CITY_TITLE.search(title)
    if city and "remote" not in title.lower():
        return False, f"based in {city.group(1).title()} (named in the title)"
    return True, "remote"


def check(job: dict, company: dict, profile: dict | None = None) -> dict:
    """Returns the job with tier (ringer/stretch/stepping/meh) or drop reason, plus parsed facts."""
    cfg = _cfg(profile)
    j = dict(job)
    title = j.get("title") or ""
    desc = plain_text(j.get("description") or "")
    j["company"] = company.get("name")

    def drop(reason):
        j["tier"], j["drop"] = None, reason
        return j

    j["family"] = family(title)
    if not j["family"]:
        return drop("title not in a target family")
    if re.search(cfg["drop_title"], title, re.I) and not re.search(cfg["keep_title_even_if"], title, re.I):
        return drop("title level")
    if re.search(cfg["drop_type"], title, re.I) or re.search(r"\b(contract|temporary|part[- ]time) (role|position)\b", desc, re.I):
        return drop("contract/temp/part-time")
    ok, where = location_ok(j, cfg)
    if not ok:
        return drop(where)
    j["where"] = where
    if re.search(cfg["clearance"], desc, re.I):
        return drop("needs active clearance")

    years = years_required(desc)
    j["years"] = years
    if years and years > cfg["max_years"]:
        return drop(f"asks {years}+ years")
    if SENIOR.search(title) and years is None:
        j["note"] = "Senior title, years not stated"

    pay = {"max": j.get("pay_max"), "ote": j.get("pay_ote")}
    if not pay["max"]:
        pay = pay_from_text(desc)
    j["pay_max"], j["pay_ote"] = pay["max"], bool(pay["ote"])
    if pay["max"]:
        floor = cfg["ote_floor"] if pay["ote"] else cfg["pay_floor"]
        if pay["max"] < floor:
            return drop(f"pay tops out at ${pay['max']:,}")

    j["tier"] = j["family"]
    # Travel-heavy roles drop a tier: "Chicago is meaningless if travelling"
    travel = re.search(r"(\d{2})\s*%\s*(?:of the time\s*)?travel|travel\s*(?:up to\s*)?(\d{2})\s*%", desc, re.I)
    if travel and int(travel.group(1) or travel.group(2)) >= 50:
        j["tier"] = {"ringer": "stepping", "stretch": "stepping", "stepping": "meh"}.get(j["tier"], "meh")
        j["note"] = (j.get("note", "") + f" {travel.group(0)}").strip()
    if any(s in (company.get("name") or "").lower() for s in cfg["surveillance"]):
        j["tag"] = "surveillance"
    return j
