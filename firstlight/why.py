"""
Miss check. Given a job you found yourself, says why FirstLight didn't alert on it:
company not watched, board unreadable, job filtered (and by which rule), or it did alert.
"""
import json
import os
import re

import requests

from . import ats, enrich, filters
from .text import plain_text


def why(profile: dict, data_dir: str, url: str) -> str:
    wl = json.load(open(os.path.join(data_dir, "watchlist.json")))
    state = json.load(open(os.path.join(data_dir, "state.json"))) if os.path.exists(
        os.path.join(data_dir, "state.json")) else {}
    lines = [f"Miss check: {url}"]
    board = ats.board_from_url(url)
    company = None
    if board:
        key = ats.board_key(board)
        company = next((c for c in wl if c.get("board") and ats.board_key(c["board"]).lower() == key.lower()), None)
        if company:
            lines.append(f"Board {key} belongs to {company['name']} on the watchlist: {company['status']} "
                         f"({company.get('how', '')}).")
        else:
            lines.append(f"Board {key} is NOT on the watchlist. Feeds could still catch it; add the company to the profile.")
        job = enrich._from_board(url)
        if not job:
            return "\n".join(lines + ["The job isn't on that board any more (or the link isn't a job link)."])
    else:
        domain = re.sub(r"^www\.", "", re.sub(r"https?://([^/]+).*", r"\1", url))
        company = next((c for c in wl if c.get("domain") and domain.endswith(c["domain"])), None)
        lines.append(f"Not a job board link. Company on watchlist: "
                     f"{company['name'] + ' (' + company['status'] + ')' if company else 'no'}.")
        page = requests.get(url, headers=enrich.PAGE_UA, timeout=20).text
        title = re.search(r"<title[^>]*>([^<]+)", page)
        job = {"id": url, "title": title[1].split("|")[0].split(" - ")[0].strip() if title else "", "url": url,
               "location": "", "remote": "remote" in page.lower(), "description": plain_text(page)}
    seen = (state.get("seen") or {}).get(job["id"])
    if seen:
        if seen.get("dup"):
            what = f"same job as {seen['dup']} (another location or board), which was sent instead."
        elif seen.get("tier"):
            what = f"sent as {seen['tier']}."
        elif seen.get("drop"):
            what = f"dropped after reading the description: {seen['drop']}."
        else:
            what = "same company and title as a job already sent."
        lines.append(f"FirstLight saw it on {seen['t']}: {what}")
    result = filters.check(job, {"name": company["name"] if company else job.get("company", "")}, profile)
    lines.append(f"Title: {job['title']!r}, location: {job.get('location')!r}")
    lines.append(f"Filters today: {'passes as ' + result['tier'] if result['tier'] else 'drops it: ' + result['drop']}.")
    return "\n".join(lines)
