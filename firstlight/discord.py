"""Discord webhook posts. Webhook URLs come from environment variables named in the profile."""
import json
import os
import time

import requests

LIMIT = 1900  # Discord caps messages at 2000 characters


def webhook(profile: dict, channel: str) -> str | None:
    env = ((profile.get("discord") or {}).get(channel)) or ""
    return os.environ.get(env) or os.environ.get("DISCORD_WEBHOOK_URL")


def post(url: str | None, content: str, file: tuple[str, str] | None = None):
    """Posts one message (split if long). `file` is (filename, text) sent as an attachment."""
    if not url:
        print("  [discord] no webhook set; would post:\n" + content[:500])
        return
    chunks, cur = [], ""
    for line in content.split("\n"):
        if len(cur) + len(line) + 1 > LIMIT:
            chunks.append(cur)
            cur = ""
        cur += line + "\n"
    chunks.append(cur)
    for i, chunk in enumerate(chunks):
        payload = {"content": chunk, "allowed_mentions": {"parse": []}, "flags": 4}  # 4 = no link previews
        for attempt in range(4):
            if file and i == len(chunks) - 1:
                r = requests.post(url, data={"payload_json": json.dumps(payload)},
                                  files={"files[0]": (file[0], file[1].encode(), "text/markdown")}, timeout=30)
            else:
                r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 429:
                time.sleep(float(r.json().get("retry_after", 2)) + 0.5)
                continue
            r.raise_for_status()
            break
        time.sleep(0.6)


def pay_text(job: dict) -> str:
    if not job.get("pay_max"):
        return "pay not listed"
    return f"up to ${job['pay_max'] // 1000}k" + (" OTE" if job.get("pay_ote") else "")


TIER_LABEL = {"ringer": "🎯 Dead ringer", "stretch": "🚀 Stretch", "stepping": "🪜 Stepping stone", "meh": "Meh"}


def job_line(job: dict) -> str:
    bits = [pay_text(job), job.get("where") == "chicago" and "Chicago" or "Remote"]
    if job.get("years"):
        bits.append(f"asks {job['years']}+ yrs")
    if job.get("note"):
        bits.append(job["note"])
    if job.get("tag"):
        bits.append(f"⚠ {job['tag']}")
    return " · ".join(b for b in bits if b)


def job_alert(job: dict) -> str:
    return (f"{TIER_LABEL.get(job['tier'], job['tier'])}: **{job['title']}** at **{job['company']}**\n"
            f"{job_line(job)}\n{job['url']}")


def summary_line(job: dict) -> str:
    return f"• {job['title']} at {job['company']} ({pay_text(job)}) <{job['url']}>"
