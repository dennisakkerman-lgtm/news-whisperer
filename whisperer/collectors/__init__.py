"""Signal collectors. Each module exposes `collect(cfg, since) -> list[Signal]`.

A collector raises `Skipped` when it is disabled or lacks credentials, and any
other exception when the source failed; the pipeline records both per source.
"""
from __future__ import annotations


class Skipped(Exception):
    pass


# name -> module path, in the order they run
REGISTRY = {
    "google_news": "whisperer.collectors.news",
    "rss": "whisperer.collectors.news",
    "reddit": "whisperer.collectors.reddit",
    "youtube": "whisperer.collectors.youtube",
    "tiktok": "whisperer.collectors.apify",
    "instagram": "whisperer.collectors.apify",
    "google_trends": "whisperer.collectors.trends",
    "ahrefs": "whisperer.collectors.ahrefs",
    "gsc": "whisperer.collectors.gsc",
}
