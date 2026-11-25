"""
The watchlist: every company we poll directly, with the board we read and how we know
it is theirs. Nothing is guessed:

  1. A board comes from a link the company published: a job URL on its VC portfolio
     board, or a job board link/embed on its own website's careers page.
  2. Where the board reports a company name, it must match.
  3. The board must answer. "No openings right now" is fine; an error is not.

Every company ends up with a status, and the coverage report lists all of them, so a
company we can't read is visible instead of silently skipped.
"""
import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

import requests

from . import ats, sources

PAGE_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
CAREER_WORDS = re.compile(r"career|jobs|join|hiring|work-with-us|open-roles|openings|positions", re.I)
COMMON_PATHS = ["/careers", "/jobs", "/company/careers", "/about/careers", "/careers/jobs", "/join-us"]
LINK_RE = re.compile(r"""(?:href|src|data-src|data-url)\s*=\s*["']([^"']+)["']""", re.I)
GH_JID = re.compile(r"[?&]gh_jid=\d+")

VERIFIED = "verified"            # board found from the company's own link and it answers
NO_OPENINGS = "verified, no openings"
MISMATCH = "check: board name differs"
BROKEN = "board error"
MANUAL = "needs a careers link"  # no board link found anywhere
OWN_SITE = "own careers site"    # jobs live on a custom site; covered by the VC feed if it has one


def _page(url: str) -> tuple[str, str]:
    try:
        r = requests.get(url, headers=PAGE_UA, timeout=20, allow_redirects=True)
        return (r.text if r.ok else ""), r.url
    except Exception:
        return "", url


def scan_careers(domain: str) -> list[str]:
    """Job-board links on the company's own site: home page, its careers links, then common paths."""
    if not domain:
        return []
    found, visited = [], set()

    def look(url, follow):
        if url in visited or len(visited) > 8:
            return
        visited.add(url)
        html, final = _page(url)
        links = [urljoin(final, l) for l in LINK_RE.findall(html)]
        found.extend(l for l in links if ats.board_from_url(l))
        found.extend(re.findall(r"https?://[a-z0-9.-]+(?:greenhouse\.io|lever\.co|ashbyhq\.com|workable\.com|"
                                r"myworkdayjobs\.com|smartrecruiters\.com|rippling\.com|recruitee\.com|"
                                r"bamboohr\.com|breezy\.hr|gem\.com)[^\s\"'<>\\]*", html))
        if follow:
            host = urlparse(final).netloc
            for l in links:
                if CAREER_WORDS.search(urlparse(l).path + urlparse(l).netloc) and (
                        urlparse(l).netloc == host or urlparse(l).netloc.endswith(domain)):
                    look(l, follow=False)

    look(f"https://{domain}", follow=True)
    if not found:
        for path in COMMON_PATHS:
            look(f"https://{domain}{path}", follow=False)
            if found:
                break
    if not found:
        look(f"https://careers.{domain}", follow=False)
    return found


def pick_board(urls: list[str]) -> dict | None:
    """The board most of a company's links point to."""
    boards = Counter(ats.board_key(b) for b in (ats.board_from_url(u) for u in urls) if b)
    if not boards:
        return None
    key = boards.most_common(1)[0][0]
    a, slug = key.split(":", 1)
    return {"ats": a, "slug": slug}


def _norm(name: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (name or "").lower())
            if w not in {"inc", "the", "co", "corp", "llc", "ltd", "ai", "hq", "technologies", "labs", "careers"}}


def names_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return True
    return bool(na & nb) or "".join(sorted(na)) in "".join(sorted(nb)) or "".join(sorted(nb)) in "".join(sorted(na))


def _slugs(name: str, domain: str) -> list[str]:
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    base = (domain or "").split(".")[0]
    out = ["".join(words), "-".join(words), base, words[0] if words else ""]
    return [s for i, s in enumerate(out) if s and s not in out[:i]]


def confirm_by_name(name: str, domain: str) -> tuple[dict | None, list[dict]]:
    """
    For careers pages that render jobs themselves (Stripe, Databricks, Hex). Tries boards
    named after the company and keeps one only when the board itself proves the match:
    Greenhouse reports its company name; Ashby/Lever jobs must link back to the company's
    domain or name it in most descriptions. A hit is labelled so the report shows it.
    """
    for slug in _slugs(name, domain):
        for a in ("greenhouse", "ashby", "lever", "gem"):
            board = {"ats": a, "slug": slug}
            jobs, status = ats.fetch(board)
            if status != "ok" or not jobs:
                continue
            if a == "greenhouse":
                if names_match(ats.board_name(board) or "", name) and ats.board_name(board):
                    return board, jobs
                continue
            key = (domain or "").split(".")[0].lower()
            named = sum(1 for j in jobs if key and key in (j.get("description") or "").lower()
                        or name.lower() in (j.get("description") or "").lower())
            if named >= max(1, len(jobs) // 2):
                return board, jobs
    return None, []


def resolve(company: dict, workday_terms=("",)) -> dict:
    """Finds, checks and probes one company's board. Returns the company with board/status/evidence."""
    c = dict(company)
    if c.get("board"):  # spelled out in the profile: trust the person, still probe it
        c["how"] = "set in profile"
    else:
        board = pick_board(c.get("evidence") or [])
        c["how"] = "VC board job links" if board else ""
        if not board:
            links = scan_careers(c.get("domain", ""))
            board = pick_board(links)
            c["how"] = "company careers page" if board else ""
        if not board:
            board, _ = confirm_by_name(c["name"], c.get("domain", ""))
            c["how"] = "board named after them, confirmed by the board" if board else ""
        if not board:
            own = [u for u in c.get("evidence") or [] if not ats.board_from_url(u)]
            c["status"] = OWN_SITE if own else MANUAL
            if own:
                c["note"] = urlparse(own[0]).netloc
            return c
        c["board"] = board
    jobs, status = ats.fetch(c["board"], workday_terms)
    c["open_jobs"] = len(jobs)
    if status not in ("ok", "empty"):
        c["status"], c["note"] = BROKEN, status
        return c
    reported = ats.board_name(c["board"])
    if reported and not names_match(reported, c["name"]):
        c["status"], c["note"] = MISMATCH, f"board says {reported!r}"
        return c
    c["status"] = VERIFIED if jobs else NO_OPENINGS
    return c


def merge(companies: list[dict]) -> list[dict]:
    """One row per company domain; keeps every source and all evidence."""
    by_key: dict[str, dict] = {}
    for c in companies:
        key = c.get("domain") or c["name"].lower()
        if key not in by_key:
            by_key[key] = {**c, "sources": [c["source"]]}
            continue
        row = by_key[key]
        row["sources"].append(c["source"])
        row["evidence"] = (row.get("evidence") or []) + (c.get("evidence") or [])
        row["size"] = row.get("size") or c.get("size")
        row["board"] = row.get("board") or c.get("board")
    return list(by_key.values())


def build(profile: dict, workers: int = 24) -> list[dict]:
    w = profile.get("watchlist") or {}
    min_size = w.get("min_size", 15)
    found = []
    for host in w.get("getro_boards") or []:
        found += sources.from_getro(host, min_size)
    if w.get("a16z", True):
        found += sources.from_a16z(min_size)
    if w.get("yc", True):
        found += sources.from_yc(min_size)
    for c in w.get("companies") or []:  # named in the profile, any size
        c = {"name": c} if isinstance(c, str) else dict(c)
        found.append({"name": c["name"], "domain": sources.domain_of(c.get("domain", "")),
                      "board": ats.board_from_url(c["careers"]) if c.get("careers") else None,
                      "size": None, "source": "named in profile", "evidence": [c["careers"]] if c.get("careers") else []})
    blocked = {b.lower() for b in w.get("block") or []}
    companies = [c for c in merge(found) if c["name"].lower() not in blocked]
    terms = tuple(profile.get("workday_search") or ("",))
    with ThreadPoolExecutor(workers) as pool:
        resolved = list(pool.map(lambda c: resolve(c, terms), companies))
    for c in resolved:
        c.pop("getro", None)
        c["evidence"] = (c.get("evidence") or [])[:3]
        c["checked"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return sorted(resolved, key=lambda c: c["name"].lower())


STATUS_HELP = {
    VERIFIED: "We read their job board directly and it has open jobs.",
    NO_OPENINGS: "Board confirmed, nothing open right now. Checked every run.",
    OWN_SITE: "Jobs live on a site we can't poll (Gem, Comeet, Dover, LinkedIn...). Their VC board's feed still catches new posts.",
    MISMATCH: "A board answered but its company name differs. Not polled until someone confirms it.",
    BROKEN: "A board was found but errored. Retried next run; shows in the daily health check.",
    MANUAL: "No job board link anywhere: not on their VC board, site or a board named after them. Mostly small companies not hiring right now. Add `careers:` in the profile for any you care about.",
}


def coverage_report(watchlist: list[dict]) -> str:
    counts = Counter(c["status"] for c in watchlist)
    polled = counts[VERIFIED] + counts[NO_OPENINGS]
    named = [c for c in watchlist if "named in profile" in c.get("sources", [])]
    lines = ["# Watchlist coverage", "",
             f"{len(watchlist)} companies, {polled} polled directly ({polled * 100 // max(1, len(watchlist))}%).", ""]
    lines += [f"- **{s}** ({counts[s]}): {STATUS_HELP[s]}" for s in [VERIFIED, NO_OPENINGS, OWN_SITE, MISMATCH, BROKEN, MANUAL]
              if counts[s]]
    if named:
        lines += ["", "## Your named companies", "", "| Company | Status | Board | Open jobs |", "|---|---|---|---|"]
        lines += [f"| {c['name']} | {c['status']} | {ats.board_key(c['board']) if c.get('board') else ''} | "
                  f"{c.get('open_jobs', '')} |" for c in named]
    lines.append("")
    order = [MANUAL, BROKEN, MISMATCH, OWN_SITE, NO_OPENINGS, VERIFIED]
    for status in order:
        rows = [c for c in watchlist if c["status"] == status]
        if not rows:
            continue
        lines += [f"## {status} ({len(rows)})", "", "| Company | Domain | Board | How found | Open jobs | Note | Sources |",
                  "|---|---|---|---|---|---|---|"]
        for c in rows:
            board = ats.board_key(c["board"]) if c.get("board") else ""
            lines.append(f"| {c['name']} | {c.get('domain', '')} | {board} | {c.get('how', '')} | "
                         f"{c.get('open_jobs', '')} | {c.get('note', '')} | {', '.join(c.get('sources', []))} |")
        lines.append("")
    return "\n".join(lines)


def save(watchlist: list[dict], path: str):
    with open(path, "w") as f:
        json.dump(watchlist, f, indent=1)
