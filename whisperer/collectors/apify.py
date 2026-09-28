"""Layer 1 · TikTok + Instagram via Apify actors (needs APIFY_TOKEN).

Actor ids can be swapped with APIFY_TIKTOK_ACTOR / APIFY_INSTAGRAM_ACTOR.
"""
from __future__ import annotations

import os
from typing import List

import requests

from whisperer.collectors import Skipped
from whisperer.core import Signal, parse_dt

RUN = "https://api.apify.com/v2/acts/{actor}/run-sync-get-dataset-items"


def _run(actor: str, token: str, payload: dict) -> list:
    r = requests.post(RUN.format(actor=actor.replace("/", "~")), params={"token": token}, json=payload, timeout=300)
    r.raise_for_status()
    return r.json()


def collect(cfg, since, name) -> List[Signal]:
    sc = cfg["sources"].get(name, {})
    token = os.environ.get("APIFY_TOKEN")
    if not sc.get("enabled"):
        raise Skipped("disabled")
    if not token:
        raise Skipped("APIFY_TOKEN not set")
    n = sc.get("results_per_hashtag", 30)
    out = []

    if name == "tiktok":
        actor = os.environ.get("APIFY_TIKTOK_ACTOR", "clockworks/tiktok-scraper")
        items = _run(actor, token, {"hashtags": sc["hashtags"], "resultsPerPage": n, "shouldDownloadVideos": False,
                                    "shouldDownloadCovers": False})
        for it in items:
            dt = parse_dt(it.get("createTimeISO") or it.get("createTime"))
            if dt and dt < since:
                continue
            text = it.get("text", "")
            out.append(Signal(
                "tiktok", 1, text[:140] or "(TikTok video)", it.get("webVideoUrl", ""), dt, text,
                engagement=int(it.get("playCount", 0)) // 100 + int(it.get("commentCount", 0)),
                meta={"author": (it.get("authorMeta") or {}).get("name"), "plays": it.get("playCount"),
                      "comments": it.get("commentCount")},
            ))
    else:
        actor = os.environ.get("APIFY_INSTAGRAM_ACTOR", "apify/instagram-hashtag-scraper")
        items = _run(actor, token, {"hashtags": sc["hashtags"], "resultsLimit": n})
        for it in items:
            dt = parse_dt(it.get("timestamp"))
            if dt and dt < since:
                continue
            text = it.get("caption", "") or ""
            out.append(Signal(
                "instagram", 1, text[:140] or "(Instagram post)", it.get("url", ""), dt, text,
                engagement=int(it.get("likesCount", 0) or 0) // 10 + int(it.get("commentsCount", 0) or 0),
                meta={"author": it.get("ownerUsername"), "likes": it.get("likesCount"),
                      "comments": it.get("commentsCount")},
            ))
    return out
