"""python -m firstlight watchlist --profile profiles/rishi.yml"""
import argparse
import os

import yaml

from . import watchlist


def main():
    ap = argparse.ArgumentParser(prog="firstlight")
    ap.add_argument("command", choices=["watchlist"])
    ap.add_argument("--profile", default="profiles/rishi.yml")
    args = ap.parse_args()
    profile = yaml.safe_load(open(args.profile))
    out = os.path.join("data", profile["name"])
    os.makedirs(out, exist_ok=True)
    if args.command == "watchlist":
        wl = watchlist.build(profile)
        watchlist.save(wl, os.path.join(out, "watchlist.json"))
        with open(os.path.join(out, "coverage.md"), "w") as f:
            f.write(watchlist.coverage_report(wl))
        print(f"{len(wl)} companies -> {out}/watchlist.json, coverage.md")


if __name__ == "__main__":
    main()
