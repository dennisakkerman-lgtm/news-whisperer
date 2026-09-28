"""Layer 1 · YouTube Data API v3 (needs YOUTUBE_API_KEY)."""
from __future__ import annotations

import os
from typing import Dict, List

import requests

from whisperer.collectors import Skipped
from whisperer.core import Signal, parse_dt

API = "https://www.googleapis.com/youtube/v3"


def _get(path, key, **params):
    r = requests.get(f"{API}/{path}", params={"key": key, **params}, timeout=20)
    r.raise_for_status()
    return r.json()


def collect(cfg, since, name="youtube") -> List[Signal]:
    sc = cfg["sources"].get("youtube", {})
    key = os.environ.get("YOUTUBE_API_KEY")
    if not sc.get("enabled"):
        raise Skipped("disabled")
    if not key:
        raise Skipped("YOUTUBE_API_KEY not set")

    after = since.strftime("%Y-%m-%dT%H:%M:%SZ")
    hits: Dict[str, dict] = {}
    for q in sc.get("queries", []):
        data = _get("search", key, part="snippet", q=q, type="video", regionCode="FR", relevanceLanguage="fr",
                    publishedAfter=after, order="viewCount", maxResults=15)
        for it in data.get("items", []):
            hits[it["id"]["videoId"]] = {"snippet": it["snippet"], "query": q}

    for handle in sc.get("channel_handles", []):
        ch = _get("channels", key, part="contentDetails", forHandle=handle).get("items", [])
        if not ch:
            continue
        uploads = ch[0]["contentDetails"]["relatedPlaylists"]["uploads"]
        for it in _get("playlistItems", key, part="snippet", playlistId=uploads, maxResults=10).get("items", []):
            vid = it["snippet"]["resourceId"]["videoId"]
            hits.setdefault(vid, {"snippet": it["snippet"], "channel": handle})

    out = []
    ids = list(hits)
    for i in range(0, len(ids), 50):
        stats = _get("videos", key, part="statistics,snippet", id=",".join(ids[i:i + 50]))
        for v in stats.get("items", []):
            sn, st = v["snippet"], v.get("statistics", {})
            dt = parse_dt(sn.get("publishedAt"))
            if dt and dt < since:
                continue
            views = int(st.get("viewCount", 0))
            out.append(Signal(
                "youtube", 1, sn.get("title", ""), f"https://www.youtube.com/watch?v={v['id']}", dt,
                (sn.get("description") or "")[:1500],
                engagement=views // 100 + int(st.get("commentCount", 0)),
                meta={"channel": sn.get("channelTitle"), "views": views, "comments": st.get("commentCount"),
                      **{k: hits[v["id"]].get(k) for k in ("query", "channel") if hits[v["id"]].get(k)}},
            ))
    return out
