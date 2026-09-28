"""Layer 1 · Reddit via the official API (app-only OAuth).

Needs REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET (a "script" app at reddit.com/prefs/apps).
Without them it falls back to the public JSON listing, which Reddit often blocks
from cloud IPs, so set the credentials on Railway.
"""
from __future__ import annotations

import os
from typing import List

import requests

from whisperer.collectors import Skipped
from whisperer.core import UA, Signal, parse_dt


def _session():
    s = requests.Session()
    s.headers["User-Agent"] = UA
    cid, secret = os.environ.get("REDDIT_CLIENT_ID"), os.environ.get("REDDIT_CLIENT_SECRET")
    if cid and secret:
        r = s.post("https://www.reddit.com/api/v1/access_token", auth=(cid, secret),
                   data={"grant_type": "client_credentials"}, timeout=20)
        r.raise_for_status()
        s.headers["Authorization"] = f"bearer {r.json()['access_token']}"
        return s, "https://oauth.reddit.com"
    return s, "https://www.reddit.com"


def collect(cfg, since, name="reddit") -> List[Signal]:
    sc = cfg["sources"].get("reddit", {})
    if not sc.get("enabled"):
        raise Skipped("disabled")
    s, base = _session()
    out, errors = [], []
    for sub in sc["subreddits"]:
        try:
            r = s.get(f"{base}/r/{sub}/new.json", params={"limit": sc.get("limit_per_sub", 50)}, timeout=20)
            r.raise_for_status()
            children = r.json()["data"]["children"]
        except Exception as exc:
            errors.append(f"r/{sub}: {exc}")
            continue
        for c in children:
            d = c["data"]
            dt = parse_dt(d.get("created_utc"))
            if dt and dt < since:
                continue
            out.append(Signal(
                "reddit", 1, d.get("title", ""), "https://www.reddit.com" + d.get("permalink", ""), dt,
                (d.get("selftext") or "")[:2000],
                engagement=int(d.get("score", 0)) + 2 * int(d.get("num_comments", 0)),
                meta={"subreddit": sub, "comments": d.get("num_comments", 0), "score": d.get("score", 0)},
            ))
    if errors and not out:
        if base == "https://www.reddit.com" and all("403" in e for e in errors):
            raise Skipped("Reddit blocks unauthenticated requests: set REDDIT_CLIENT_ID + REDDIT_CLIENT_SECRET")
        raise RuntimeError("; ".join(errors[:3]))
    return out
