"""
python -m firstlight watchlist [--profile profiles/rishi.yml]   rebuild + verify the watchlist
python -m firstlight run [--dry]                                  one alert run
python -m firstlight why <job url or words from the title>        miss check: why didn't this alert?
"""
import argparse
import json
import os

import yaml

from . import watchlist


def main():
    ap = argparse.ArgumentParser(prog="firstlight")
    ap.add_argument("command", choices=["watchlist", "run", "why"])
    ap.add_argument("query", nargs="?")
    ap.add_argument("--profile", default="profiles/rishi.yml")
    ap.add_argument("--dry", action="store_true", help="don't post or save state")
    args = ap.parse_args()
    profile = yaml.safe_load(open(args.profile))
    out = os.path.join(os.environ.get("FIRSTLIGHT_DATA", "data"), profile["name"])
    os.makedirs(out, exist_ok=True)
    if args.command == "watchlist":
        wl = watchlist.build(profile)
        watchlist.save(wl, os.path.join(out, "watchlist.json"))
        with open(os.path.join(out, "coverage.md"), "w") as f:
            f.write(watchlist.coverage_report(wl))
        print(f"{len(wl)} companies -> {out}/watchlist.json, coverage.md")
    elif args.command == "run":
        from .run import run
        run(profile, out, dry=args.dry)
    elif args.command == "why":
        from .why import why
        print(why(profile, out, args.query or ""))


if __name__ == "__main__":
    main()
