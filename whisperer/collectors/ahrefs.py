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


SPAM_TLDS = (".store", ".shop", ".xyz", ".click", ".site", ".online", ".top", ".ru", ".cn", ".info", ".biz", ".icu")


def _is_fr(host: str) -> bool:
    return host.endswith(".fr") or ".fr/" in host or host.startswith("fr.")


def _refdomains(key, target, min_dr=0) -> Set[str]:
    data = _get("refdomains", key, target=target, mode="domain", limit=1000, select="domain,domain_rating",
                order_by="domain_rating:desc")
    return {d["domain"] for d in data.get("refdomains", [])
            if (d.get("domain_rating") or 0) >= min_dr and not d["domain"].endswith(SPAM_TLDS)}


def collect(cfg, since, name="ahrefs") -> List[Signal]:
    sc = cfg["sources"].get("ahrefs", {})
    key = os.environ.get("AHREFS_API_KEY")
    if not sc.get("enabled"):
        raise Skipped("disabled")
    if not key:
        raise Skipped("AHREFS_API_KEY not set")

    kws = sc.get("backlink_keywords", [])
    topic = sc.get("topic_keywords", [])
    excl = [str(x) for x in sc.get("exclude_domains", [])]
    min_dr = sc.get("min_dr", 0)
    brand_domain = cfg["brand"]["domain"]
    out, seen_domains = [], set()

    # New backlinks to competitors in the lookback window, filtered by topic keywords
    for comp in cfg["competitors"]:
        data = _get("all-backlinks", key, target=comp["domain"], mode="domain", limit=200, history="since:" +
                    since.strftime("%Y-%m-%d"), select="url_from,title,first_seen,domain_rating_source",
                    order_by="domain_rating_source:desc")
        for b in data.get("backlinks", []):
            title = b.get("title") or b.get("url_from", "")
            url = b.get("url_from", "")
            host = url.split("/")[2] if "://" in url else url
            hay = norm(title + " " + url.replace("-", " "))
            for a in comp["aliases"]:  # "Fitness Park" must not satisfy the topic check on its own
                hay = hay.replace(norm(a), " ")
            if not (_is_fr(host) or "/fr" in url):
                continue
            if any(x in host for x in excl) or host in seen_domains:
                continue
            if (b.get("domain_rating_source") or 0) < min_dr:
                continue
            if (kws and not contains_any(hay, kws)) or (topic and not contains_any(hay, topic)):
                continue
            seen_domains.add(host)
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
    gap_dr = sc.get("link_gap_min_dr", 30)
    for comp in cfg["competitors"][:4]:
        for d in {x for x in _refdomains(key, comp["domain"], gap_dr) if _is_fr(x)} - brand_refs:
            counts.setdefault(d, []).append(comp["name"])
    for d, comps in sorted(counts.items(), key=lambda kv: -len(kv[1]))[:25]:
        if len(comps) < 2:
            break
        if any(x in d for x in excl):
            continue
        out.append(Signal(
            "ahrefs", 3, f"{d} links to {', '.join(comps)} but not {cfg['brand']['name']}", f"https://{d}", None,
            "Link gap: domain cites several competitors but not the brand.", engagement=10 * len(comps),
            meta={"competitors": comps, "kind": "link_gap"},
        ))
    return out
