"""Layer 3 · Ahrefs API v3 (needs AHREFS_API_KEY).

- new backlinks to competitors from pages about gyms (listicles/reviews)
- referring domains that link to competitors but not to the brand (content gap)
"""
from __future__ import annotations

import os
from typing import List, Set

import requests

from whisperer.collectors import Skipped
from whisperer.core import Signal, contains_any, norm, parse_dt

API = "https://api.ahrefs.com/v3/site-explorer"


def _get(path, key, **params):
    r = requests.get(f"{API}/{path}", headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
                     params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def _refdomains(key, target) -> Set[str]:
    data = _get("refdomains", key, target=target, mode="domain", limit=1000, select="domain",
                order_by="domain_rating:desc")
    return {d["domain"] for d in data.get("refdomains", [])}


def collect(cfg, since, name="ahrefs") -> List[Signal]:
    sc = cfg["sources"].get("ahrefs", {})
    key = os.environ.get("AHREFS_API_KEY")
    if not sc.get("enabled"):
        raise Skipped("disabled")
    if not key:
        raise Skipped("AHREFS_API_KEY not set")

    kws = sc.get("backlink_keywords", [])
    brand_domain = cfg["brand"]["domain"]
    out = []

    # New backlinks to competitors in the lookback window, filtered by topic keywords
    for comp in cfg["competitors"]:
        data = _get("all-backlinks", key, target=comp["domain"], mode="domain", limit=200, history="since:" +
                    since.strftime("%Y-%m-%d"), select="url_from,title,first_seen,domain_rating_source",
                    order_by="domain_rating_source:desc")
        for b in data.get("backlinks", []):
            title = b.get("title") or b.get("url_from", "")
            if kws and not contains_any(norm(title + " " + b.get("url_from", "")), kws):
                continue
            dt = parse_dt(b.get("first_seen"))
            out.append(Signal(
                "ahrefs", 3, f"{comp['name']} got a new link: {title}", b.get("url_from", ""), dt,
                f"New backlink to {comp['domain']} (DR {b.get('domain_rating_source')}). Pitch target for {cfg['brand']['name']}.",
                engagement=int(b.get("domain_rating_source") or 0),
                meta={"competitor": comp["name"], "dr": b.get("domain_rating_source"), "kind": "new_competitor_link"},
            ))

    # Link gap: domains citing 2+ competitors but not the brand
    brand_refs = _refdomains(key, brand_domain)
    counts = {}
    for comp in cfg["competitors"][:4]:
        for d in _refdomains(key, comp["domain"]) - brand_refs:
            counts.setdefault(d, []).append(comp["name"])
    for d, comps in sorted(counts.items(), key=lambda kv: -len(kv[1]))[:25]:
        if len(comps) < 2:
            break
        out.append(Signal(
            "ahrefs", 3, f"{d} links to {', '.join(comps)} but not {cfg['brand']['name']}", f"https://{d}", None,
            "Link gap: domain cites several competitors but not the brand.", engagement=10 * len(comps),
            meta={"competitors": comps, "kind": "link_gap"},
        ))
    return out
