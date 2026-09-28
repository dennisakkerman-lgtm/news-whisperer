"""Layer 5 · Google News per-query RSS + FR sector RSS feeds."""
from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from typing import List
from urllib.parse import quote_plus

import feedparser
import requests

from whisperer.collectors import Skipped
from whisperer.core import UA, Signal, locale

GNEWS = "https://news.google.com/rss/search?q={q}+when:{days}d&hl={hl}&gl={gl}&ceid={ceid}"


def _entry_dt(e):
    st = e.get("published_parsed") or e.get("updated_parsed")
    return datetime(*st[:6], tzinfo=timezone.utc) if st else None


def _clean(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s or "")).strip()


def _fetch(url: str):
    r = requests.get(url, headers={"User-Agent": UA}, timeout=20)
    r.raise_for_status()
    return feedparser.parse(r.content)


def collect_google_news(cfg, since) -> List[Signal]:
    sc = cfg["sources"].get("google_news", {})
    if not sc.get("enabled"):
        raise Skipped("disabled")
    days = cfg["output"]["lookback_days"]
    loc = locale(cfg)
    out = []
    for q in sc["queries"]:
        feed = _fetch(GNEWS.format(q=quote_plus(q), days=days, hl=loc["gnews_hl"], gl=loc["gnews_gl"],
                                   ceid=loc["gnews_ceid"]))
        for e in feed.entries:
            dt = _entry_dt(e)
            if dt and dt < since:
                continue
            src = (e.get("source") or {}).get("title", "")
            title = _clean(e.title)
            if src and title.endswith(" - " + src):
                title = title[: -len(src) - 3]
            out.append(Signal("google_news", 5, title, e.link, dt, _clean(e.get("summary", "")),
                              meta={"query": q, "publisher": src}))
    return out


def collect_rss(cfg, since) -> List[Signal]:
    sc = cfg["sources"].get("rss", {})
    if not sc.get("enabled"):
        raise Skipped("disabled")
    out, errors = [], []
    for url in sc["feeds"]:
        try:
            feed = _fetch(url)
        except Exception as exc:  # one dead feed should not kill the layer
            errors.append(f"{url}: {exc}")
            continue
        publisher = feed.feed.get("title", url)
        for e in feed.entries:
            dt = _entry_dt(e)
            if dt and dt < since:
                continue
            out.append(Signal("rss", 5, _clean(e.get("title", "")), e.get("link", ""), dt,
                              _clean(e.get("summary", ""))[:1500], meta={"publisher": publisher}))
    if errors and not out:
        raise RuntimeError("; ".join(errors))
    return out


def collect(cfg, since, name):
    return collect_google_news(cfg, since) if name == "google_news" else collect_rss(cfg, since)
