"""Layer 2 · PromptWatch REST API v2 (https://server.promptwatch.com/api/v2).

Needs PROMPTWATCH_API_KEY (Settings > API Keys; a project-level key for the Basic-Fit
project is enough). Without a key, or when the API fails, the pipeline falls back to the
seed export in data/promptwatch_prompts.json.

Per run it pulls:
- all active prompts with their average AI visibility (feeds the gap score)
- week-over-week visibility per prompt (drops become signals)
- cited domains over the last weeks (rising third-party domains become pitch targets)
- brand vs competitor mentions this week vs last week (report header)
- content-gap recommendations for the lowest-visibility prompts, when PromptWatch has them
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import requests

from whisperer.core import ROOT, Signal

log = logging.getLogger(__name__)
API = "https://server.promptwatch.com/api/v2"


class PromptWatch:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pw = cfg["promptwatch"]
        self.key = os.environ.get("PROMPTWATCH_API_KEY")
        self.s = requests.Session()
        if self.key:
            self.s.headers.update({"X-API-Key": self.key, "X-Project-Id": self.pw["project_id"],
                                   "Accept": "application/json"})

    def _get(self, path: str, **params) -> Any:
        r = self.s.get(f"{API}{path}", params={k: v for k, v in params.items() if v is not None}, timeout=60)
        r.raise_for_status()
        return r.json()

    # --- prompts -----------------------------------------------------------
    def prompts(self) -> (List[Dict[str, Any]], str):
        """Active prompts with visibility. Returns (prompts, origin) where origin is 'api' or 'seed'."""
        if self.key:
            try:
                out, page = [], 1
                while True:
                    data = self._get("/prompts", page=page, size=100, isActive="true")
                    for p in data["prompts"]:
                        out.append({"id": p["id"], "prompt": p["prompt"], "type": p["type"], "intent": p.get("intent"),
                                    "visibility": p.get("averageVisibility"), "volume": p.get("volume"),
                                    "keywords": []})
                    if page >= data.get("totalPages", 1):
                        break
                    page += 1
                if out:
                    return out, "api"
            except Exception:
                log.exception("PromptWatch /prompts failed; using seed file")
        with open(ROOT / self.pw["seed_file"], encoding="utf-8") as f:
            return json.load(f)["prompts"], "seed"

    # --- signals + summary -------------------------------------------------
    def collect(self, since: datetime, low_threshold: float) -> (List[Signal], Dict[str, Any]):
        summary: Dict[str, Any] = {"origin": "seed"}
        if not self.key:
            summary["detail"] = "PROMPTWATCH_API_KEY not set: gap score uses the seed export"
            return [], summary
        summary["origin"] = "api"
        signals: List[Signal] = []
        today = date.today()
        start = (today - timedelta(days=14)).isoformat()
        now = datetime.now(timezone.utc)
        base = "https://app.promptwatch.com"

        # 1) week-over-week visibility per prompt
        try:
            ts = self._get("/prompt-visibility-time-series", startDate=start, endDate=today.isoformat(),
                           range="week", limit=500)
            by_prompt: Dict[str, List[dict]] = {}
            for row in ts.get("timeSeries", []):
                by_prompt.setdefault(row["prompt"]["id"], []).append(row)
            drops = []
            for rows in by_prompt.values():
                rows.sort(key=lambda r: r["date"])
                if len(rows) < 2:
                    continue
                prev, cur = rows[-2]["averageVisibility"], rows[-1]["averageVisibility"]
                if prev - cur >= self.pw.get("drop_alert_points", 10):
                    drops.append((prev - cur, prev, cur, rows[-1]["prompt"]))
            for delta, prev, cur, p in sorted(drops, key=lambda x: -x[0])[:8]:
                signals.append(Signal(
                    "promptwatch", 2, f"AI visibility dropped on “{p['prompt']}” ({prev:.0f}% → {cur:.0f}%)",
                    f"{base}/prompts/{p['id']}", now,
                    f"{self.cfg['brand']['name']} lost {delta:.0f} visibility points week over week on this prompt. "
                    f"{p['prompt']}", engagement=int(delta) * 10,
                    meta={"kind": "visibility_drop", "prompt_id": p["id"], "prev": prev, "cur": cur},
                ))
            summary["visibility_drops"] = len(drops)
        except Exception as exc:
            log.exception("PromptWatch prompt-visibility-time-series failed")
            summary.setdefault("errors", []).append(f"visibility: {exc}")

        # 2) cited domains: brand share + rising third-party domains
        try:
            dom = self._get("/citations/domains-over-time", startDate=(today - timedelta(days=35)).isoformat(),
                            endDate=today.isoformat(), granularity="weekly", range="WEEKLY", domainLimit=30,
                            includeSelf="true")
            brand_dom = self.cfg["brand"]["domain"].replace("www.", "")
            own = [x for x in dom.get("series", []) if brand_dom in x["domain"]]
            summary["citation_share_pct"] = round(own[0]["percentage"], 1) if own else 0.0
            summary["total_citations"] = dom.get("totalCitations")
            comp_domains = {c["domain"] for c in self.cfg["competitors"]}
            rising = []
            for x in dom.get("series", []):
                data = [v or 0 for v in x.get("data", [])]
                if len(data) < 2 or brand_dom in x["domain"]:
                    continue
                prev, cur = data[-2], data[-1]
                if cur >= 3 and cur > prev * 1.3:
                    rising.append((cur - prev, prev, cur, x))
            for growth, prev, cur, x in sorted(rising, key=lambda r: -r[0])[:6]:
                is_comp = any(c in x["domain"] for c in comp_domains)
                signals.append(Signal(
                    "promptwatch", 2,
                    f"AI engines cite {x['domain']} more often for gym questions ({int(prev)} → {int(cur)} citations/week)",
                    f"https://{x['domain']}", now,
                    ("Competitor domain gaining AI citations." if is_comp else
                     f"Third-party site gaining AI citations: pitch target for {self.cfg['brand']['name']} "
                     "(get mentioned or cited there). salle de sport"),
                    engagement=int(cur),
                    meta={"kind": "rising_cited_domain", "domain": x["domain"], "competitor": is_comp,
                          "share_pct": x.get("percentage")},
                ))
            summary["top_cited_domains"] = [
                {"domain": x["domain"], "pct": round(x["percentage"], 1)} for x in dom.get("series", [])[:8]]
        except Exception as exc:
            log.exception("PromptWatch citations/domains-over-time failed")
            summary.setdefault("errors", []).append(f"citations: {exc}")

        # 3) brand vs competitor mentions, this week vs last week
        try:
            ms = self._get("/responses/mentions-time-series", startDate=start, endDate=today.isoformat(), range="week")
            ms = sorted(ms, key=lambda r: r["date"])
            if ms:
                summary["mentions"] = {"brand": ms[-1].get("brandMentions"), "competitors": ms[-1].get("competitorMentions"),
                                       "brand_prev": ms[-2].get("brandMentions") if len(ms) > 1 else None}
        except Exception as exc:
            log.exception("PromptWatch mentions-time-series failed")
            summary.setdefault("errors", []).append(f"mentions: {exc}")
        return signals, summary

    def recommendations(self, prompt_id: str) -> Optional[Dict[str, Any]]:
        """Content-gap recommendations for one prompt, or None when PromptWatch has no analysis yet."""
        if not self.key:
            return None
        try:
            data = self._get(f"/content-gap/prompts/{prompt_id}/latest/recommendations")
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            log.warning("PromptWatch recommendations %s failed: %s", prompt_id, exc)
            return None
        recs = sorted(data.get("recommendations") or [], key=lambda r: r.get("priority", 99))
        return recs[0] if recs else None
