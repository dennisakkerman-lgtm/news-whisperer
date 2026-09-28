"""Layers 4 + 6 · Google Trends FR rising queries (pytrends, unofficial)."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import List
from urllib.parse import quote_plus

from whisperer.collectors import Skipped
from whisperer.core import Signal, locale


def collect(cfg, since, name="google_trends") -> List[Signal]:
    sc = cfg["sources"].get("google_trends", {})
    if not sc.get("enabled"):
        raise Skipped("disabled")
    from pytrends.request import TrendReq  # heavy import, only when enabled

    loc = locale(cfg)
    geos = loc["trends_geo"] if isinstance(loc["trends_geo"], list) else [loc["trends_geo"] or ""]  # "" = worldwide
    py = TrendReq(hl=loc["trends_hl"], tz=loc["trends_tz"], timeout=(10, 30), retries=0)
    now = datetime.now(timezone.utc)
    out, errors, seen = [], [], set()
    for geo in geos:
        for seed in sc["seeds"]:
            try:
                py.build_payload([seed], timeframe="now 7-d", geo=geo)
                rising = (py.related_queries().get(seed) or {}).get("rising")
            except Exception as exc:  # Google answers 429 often; keep the other seeds
                errors.append(f"{geo or 'world'}/{seed}: {exc}")
                time.sleep(5)
                continue
            if rising is None:
                continue
            for _, row in rising.head(10).iterrows():
                q, growth = str(row["query"]), row["value"]
                if q.lower() in seen:
                    continue
                seen.add(q.lower())
                label = "Breakout" if growth >= 5000 else f"+{int(growth)}%"
                out.append(Signal(
                    "google_trends", 4, f"Rising search: “{q}” ({label}, 7 days, {geo or 'worldwide'})",
                    f"https://trends.google.com/trends/explore?geo={geo}&date=now%207-d&q={quote_plus(q)}", now,
                    f"Rising related query for seed '{seed}'.",
                    engagement=min(int(growth), 5000) // 50, meta={"seed": seed, "growth": int(growth), "query": q, "geo": geo},
                ))
            time.sleep(2)  # Google rate-limits pytrends aggressively
    if errors and not out:
        raise RuntimeError("; ".join(errors[:2]))
    return out
