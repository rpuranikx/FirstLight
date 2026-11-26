"""
Fill in what broad feeds leave out (mostly the description, which the years check needs)
and collapse duplicates. Runs only on jobs that already passed the title and location
checks, so it costs a handful of requests per run, not thousands.
"""
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from . import ats
from .text import pay_from_text, plain_text

PAGE_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                         "(KHTML, like Gecko) Version/17.0 Safari/605.1.15"}
_board_cache: dict[str, list[dict]] = {}


def _from_board(url: str) -> dict | None:
    board = ats.board_from_url(url or "")
    if not board:
        return None
    key = ats.board_key(board)
    if key not in _board_cache:
        _board_cache[key] = ats.fetch(board)[0]
    tail = url.rstrip("/").split("/")[-1].split("?")[0]
    return next((j for j in _board_cache[key] if tail and (tail in (j.get("url") or "") or j["id"].endswith(":" + tail))), None)


def _from_page(url: str) -> str:
    try:
        t = requests.get(url, headers=PAGE_UA, timeout=20).text
    except Exception:
        return ""
    return plain_text(re.sub(r"<(script|style|nav|footer)[^>]*>.*?</\1>", "", t, flags=re.S | re.I))


def describe(job: dict) -> dict:
    if len(job.get("description") or "") > 400:
        return job
    j = dict(job)
    hit = _from_board(j.get("url"))
    if hit:
        j["description"] = hit["description"]
        j["ats_id"] = hit["id"]
        j["pay_max"] = j.get("pay_max") or hit.get("pay_max")
        j["pay_ote"] = j.get("pay_ote") or hit.get("pay_ote")
    else:
        j["description"] = _from_page(j.get("url")) or j.get("description", "")
        if not j.get("pay_max"):
            p = pay_from_text(j["description"])
            j["pay_max"], j["pay_ote"] = p["max"], p["ote"]
    return j


def describe_all(jobs: list[dict], workers: int = 12) -> list[dict]:
    with ThreadPoolExecutor(workers) as pool:
        return list(pool.map(describe, jobs))


def _key(j: dict) -> str:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()
    company = re.sub(r"\b(inc|llc|ltd|corp|co|the)\b", "", norm(j.get("company")))
    title = re.sub(r"\b(sr|senior)\b", "senior", norm(j.get("title")))
    return f"{company.strip()}|{title}"


def dedupe(jobs: list[dict]) -> list[dict]:
    """One job per company + title. The same posting shows up on several VC boards, on Built In
    and on the company's own board; keep the copy with the most detail and remember the others."""
    best: dict[str, dict] = {}
    by_url: dict[str, str] = {}
    for j in jobs:
        url = (j.get("url") or "").split("#")[0].split("?")[0].rstrip("/").lower()
        k = by_url.get(url) or _key(j)  # same link under two company names is one job
        if url:
            by_url.setdefault(url, k)
        if k not in best:
            best[k] = {**j, "also_seen": []}
            continue
        cur = best[k]
        richer = (bool(j.get("ats_id") or ats.board_from_url(j.get("url") or "")), len(j.get("description") or ""))
        mine = (bool(cur.get("ats_id") or ats.board_from_url(cur.get("url") or "")), len(cur.get("description") or ""))
        if richer > mine:
            best[k] = {**j, "also_seen": cur["also_seen"] + [cur["id"]]}
        else:
            cur["also_seen"].append(j["id"])
    return list(best.values())
