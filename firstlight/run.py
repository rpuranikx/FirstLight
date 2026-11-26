"""
One FirstLight run: read every polled board and every feed, filter, alert on what's new.

State (data/<profile>/state.json) remembers which passing jobs were already sent, so each
job pings once. The very first run sends one digest of everything open instead of
pinging each job, and never marks anything seen without sending it.
"""
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from . import ats, discord, enrich, feeds, filters

CHICAGO = ZoneInfo("America/Chicago")
PING = ("ringer", "stretch", "stepping")
KEEP_DAYS = 60


def _load(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=0, sort_keys=True)
    os.replace(tmp, path)


def read_watchlist(watchlist: list[dict], terms, workers=24) -> tuple[list[dict], list[tuple[dict, str]]]:
    polled = [c for c in watchlist if c["status"] in ("verified", "verified, no openings") and c.get("board")]

    def one(c):
        jobs, status = ats.fetch(c["board"], terms)
        for j in jobs:
            j["company"], j["domain"], j["source"] = c["name"], c.get("domain", ""), "watchlist"
        return jobs, status

    jobs, problems = [], []
    with ThreadPoolExecutor(workers) as pool:
        for c, (js, status) in zip(polled, pool.map(one, polled)):
            jobs += js
            if status not in ("ok", "empty"):
                problems.append((c, status))
    print(f"  [run] watchlist: {len(polled)} boards, {len(jobs)} jobs, {len(problems)} errors")
    return jobs, problems


def run(profile: dict, data_dir: str, dry: bool = False) -> dict:
    state_path = os.path.join(data_dir, "state.json")
    state = _load(state_path, {})
    first_run = not state.get("seen")
    seen, keys = state.setdefault("seen", {}), state.setdefault("keys", {})
    watchlist = _load(os.path.join(data_dir, "watchlist.json"), [])
    now = datetime.now(timezone.utc)
    today = now.astimezone(CHICAGO).date().isoformat()

    # 1. Collect
    terms = tuple(profile.get("workday_search") or ("",))
    w_jobs, problems = read_watchlist(watchlist, terms)
    f_jobs = feeds.collect(profile, state.get("getro_since"))
    blocked = {b.lower() for b in (profile.get("watchlist") or {}).get("block") or []}
    everything = [j for j in w_jobs + f_jobs if (j.get("company") or "").lower() not in blocked]

    # 2. Cheap checks on everything, full checks (with descriptions) only on what's new
    passed = [r for r in (filters.check(j, {"name": j.get("company")}, profile) for j in everything) if r["tier"]]
    new = [j for j in passed if j["id"] not in seen and enrich._key(j) not in keys]
    new = enrich.dedupe(new)
    new = enrich.describe_all(new)
    final = [filters.check(j, {"name": j.get("company")}, profile) for j in new]
    hits = [j for j in final if j["tier"]]
    order = {t: i for i, t in enumerate(["ringer", "stretch", "stepping", "meh"])}
    hits.sort(key=lambda j: (order[j["tier"]], j["company"].lower()))
    counts = Counter(j["tier"] for j in hits)
    print(f"  [run] {len(everything)} jobs, {len(passed)} past the title/location check, "
          f"{len(new)} new, {len(hits)} fit: {dict(counts)}")

    # 3. Alerts
    jobs_hook, summary_hook = discord.webhook(profile, "jobs"), discord.webhook(profile, "summaries")
    if not jobs_hook and not dry:
        # Never mark jobs seen without sending them (v1 lost the Databricks posting that way)
        print("  [run] No Discord webhook secret set, so nothing is sent and nothing is marked seen. "
              "Add DISCORD_JOBS_WEBHOOK (or DISCORD_WEBHOOK_URL) in the repo's Actions secrets.")
        dry = True
    if not dry:
        if first_run:
            digest = ["# FirstLight day-one digest", f"{len(hits)} open jobs that fit, best first.", ""]
            for tier in ("ringer", "stretch", "stepping", "meh"):
                rows = [j for j in hits if j["tier"] == tier]
                if rows:
                    digest += [f"## {discord.TIER_LABEL[tier]} ({len(rows)})", ""]
                    digest += [f"- **{j['title']}** at {j['company']}: {discord.job_line(j)}  \n  {j['url']}" for j in rows]
                    digest.append("")
            discord.post(jobs_hook, f"FirstLight is live. Here's everything open right now that fits: "
                                    f"{counts['ringer']} dead ringers, {counts['stretch']} stretches, "
                                    f"{counts['stepping']} stepping stones, {counts['meh']} meh. "
                                    f"From here on, each new one pings as it shows up.",
                         file=(f"firstlight-digest-{today}.md", "\n".join(digest)))
        else:
            for j in hits:
                if j["tier"] in PING:
                    discord.post(jobs_hook, discord.job_alert(j))
                    state.setdefault("summary_buffer", []).append(discord.summary_line(j))
                else:
                    state.setdefault("meh_buffer", []).append(discord.summary_line(j))
            if len(state.get("summary_buffer", [])) >= 10:
                buf = state["summary_buffer"]
                discord.post(summary_hook, f"**Last {len(buf)} jobs**\n" + "\n".join(buf))
                state["summary_buffer"] = []

        # Daily: meh digest + board health, once a day after 8am Chicago time
        if state.get("last_daily") != today and now.astimezone(CHICAGO).hour >= 8:
            meh = state.get("meh_buffer", [])
            msg = [f"**Daily check, {today}**"]
            msg.append(f"Meh jobs since yesterday ({len(meh)}):" if meh else "No meh jobs since yesterday.")
            msg += meh
            msg.append(f"\nBoards: {len(watchlist)} companies, "
                       f"{sum(1 for c in watchlist if c['status'] in ('verified', 'verified, no openings'))} read directly, "
                       f"{len(problems)} errored this run.")
            msg += [f"• {c['name']}: {s}" for c, s in problems[:15]]
            discord.post(summary_hook, "\n".join(msg))
            state["meh_buffer"], state["last_daily"] = [], today

    # 4. Remember. Everything that passed the cheap check is marked, with why it was dropped if it was.
    stamp = now.date().isoformat()
    for j in passed:
        seen.setdefault(j["id"], {"t": stamp})
    for j in final:
        seen[j["id"]] = {"t": stamp, "tier": j["tier"], **({"drop": j["drop"]} if j.get("drop") else {})}
        for other in j.get("also_seen", []):
            seen[other] = {"t": stamp, "dup": j["id"]}
        if j["tier"]:
            keys[enrich._key(j)] = stamp
    cutoff = (now - timedelta(days=KEEP_DAYS)).date().isoformat()
    state["seen"] = {k: v for k, v in seen.items() if v["t"] >= cutoff}
    state["keys"] = {k: v for k, v in keys.items() if v >= cutoff}
    since = state.setdefault("getro_since", {})
    for j in f_jobs:
        if j["source"].startswith("getro-") and j.get("posted"):
            host = j.get("getro_host")
            if host:
                ts = int(datetime.fromisoformat(j["posted"]).timestamp())
                since[host] = max(since.get(host, 0), ts)
    state["last_run"] = now.isoformat(timespec="seconds")
    if not dry:
        _save(state_path, state)
    return {"hits": hits, "counts": counts, "problems": problems, "first_run": first_run}
