"""Layer 2 · PromptWatch REST API v2 (https://server.promptwatch.com/api/v2).

Three sources, in order of preference:
1. REST API with PROMPTWATCH_API_KEY (Settings > API Keys, project-level key).
2. data/promptwatch_snapshot.json, refreshed weekly by a Claude task through the PromptWatch
   MCP (scripts/pw_snapshot.py). Carries prompts + visibility, previous visibility and the
   offsite-mention opportunities (cited pages naming competitors but not the brand), which
   only the MCP exposes.
3. data/promptwatch_prompts.json, the one-off seed export.

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

from whisperer.core import ROOT, Signal, locale

log = logging.getLogger(__name__)
API = "https://server.promptwatch.com/api/v2"


def _read_json(path) -> Optional[Dict[str, Any]]:
    try:
        with open(ROOT / path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


class PromptWatch:
    def __init__(self, cfg):
        self.cfg = cfg
        self.pw = cfg.get("promptwatch") or {}
        self.key = os.environ.get("PROMPTWATCH_API_KEY") if self.pw.get("project_id") else None
        self.s = requests.Session()
        if self.key:
            self.s.headers.update({"X-API-Key": self.key, "X-Project-Id": self.pw["project_id"],
                                   "Accept": "application/json"})

    def _snapshot(self) -> Optional[Dict[str, Any]]:
        return _read_json(self.pw.get("snapshot_file", "data/promptwatch_snapshot.json"))

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
                log.exception("PromptWatch /prompts failed; falling back to snapshot/seed")
        snap = self._snapshot()
        if snap and snap.get("prompts"):
            return snap["prompts"], f"MCP snapshot {snap.get('exported', '')[:10]}"
        seed = _read_json(self.pw.get("seed_file"))
        return (seed or {}).get("prompts", []), ("seed" if seed else "none")

    # --- signals + summary -------------------------------------------------
    def collect(self, since: datetime, low_threshold: float) -> (List[Signal], Dict[str, Any]):
        summary: Dict[str, Any] = {"origin": "seed"}
        if not self.key:
            snap = self._snapshot()
            if not snap:
                summary["detail"] = "No PROMPTWATCH_API_KEY and no MCP snapshot: gap score uses the seed export"
                return [], summary
            return self._from_snapshot(snap, since)
        summary["origin"] = "api"
        signals: List[Signal] = []
        today = date.today()
        # compare complete ISO weeks only: the running week has too few responses on a Monday
        last_sunday = today - timedelta(days=today.isoweekday())
        end = last_sunday.isoformat()
        start = (last_sunday - timedelta(days=20)).isoformat()
        min_resp = self.pw.get("drop_min_responses", 20)
        now = datetime.now(timezone.utc)
        base = "https://app.promptwatch.com"

        # 1) week-over-week visibility per prompt
        try:
            ts = self._get("/prompt-visibility-time-series", startDate=start, endDate=end, range="week", limit=500)
            by_prompt: Dict[str, List[dict]] = {}
            for row in ts.get("timeSeries", []):
                by_prompt.setdefault(row["prompt"]["id"], []).append(row)
            drops = []
            for rows in by_prompt.values():
                rows.sort(key=lambda r: r["date"])
                if len(rows) < 2:
                    continue
                if min(rows[-2]["totalResponses"], rows[-1]["totalResponses"]) < min_resp:
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
            dom = self._get("/citations/domains-over-time", startDate=(last_sunday - timedelta(days=34)).isoformat(),
                            endDate=end, granularity="weekly", range="WEEKLY", domainLimit=30,
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
                     f"(get mentioned or cited there). {locale(self.cfg)['topic_term']}"),
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
            ms = self._get("/responses/mentions-time-series", startDate=start, endDate=end, range="week")
            ms = sorted(ms, key=lambda r: r["date"])
            if ms:
                summary["mentions"] = {"brand": ms[-1].get("brandMentions"), "competitors": ms[-1].get("competitorMentions"),
                                       "brand_prev": ms[-2].get("brandMentions") if len(ms) > 1 else None}
        except Exception as exc:
            log.exception("PromptWatch mentions-time-series failed")
            summary.setdefault("errors", []).append(f"mentions: {exc}")

        # offsite-mention opportunities only exist in the MCP: add them from the weekly snapshot
        snap = self._snapshot()
        if snap:
            off, off_sum = self._from_snapshot(snap, since, drops=False)
            signals.extend(off)
            summary.update({k: v for k, v in off_sum.items() if k.startswith("offsite")})
            summary["snapshot"] = snap.get("exported", "")[:10]
        return signals, summary

    def _from_snapshot(self, snap: Dict[str, Any], since: datetime, drops: bool = True) -> (List[Signal], Dict[str, Any]):
        brand = self.cfg["brand"]["name"]
        exported = snap.get("exported", "")[:10]
        summary: Dict[str, Any] = {"origin": f"MCP snapshot {exported}"}
        signals: List[Signal] = []
        now = datetime.now(timezone.utc)

        drops = [] if not drops else [(p["prev_visibility"] - p["visibility"], p) for p in snap.get("prompts", [])
                 if p.get("prev_visibility") is not None and p.get("visibility") is not None
                 and p["prev_visibility"] - p["visibility"] >= self.pw.get("drop_alert_points", 10)]
        for delta, p in sorted(drops, key=lambda x: -x[0])[:8]:
            signals.append(Signal(
                "promptwatch", 2,
                f"AI visibility dropped on “{p['prompt']}” ({p['prev_visibility']:.0f}% → {p['visibility']:.0f}%)",
                f"https://app.promptwatch.com/prompts/{p['id']}", now,
                f"{brand} lost {delta:.0f} visibility points since the previous snapshot. {p['prompt']}",
                engagement=int(delta) * 10,
                meta={"kind": "visibility_drop", "prompt_id": p["id"], "prev": p["prev_visibility"], "cur": p["visibility"]},
            ))
        if drops:
            summary["visibility_drops"] = len(drops)

        offsite = snap.get("offsite", [])
        fresh = [o for o in offsite if (o.get("first_seen") or "") >= since.isoformat()[:10]]
        picks = fresh[:10] if fresh else offsite[:5]  # nothing new: surface the biggest standing gaps
        for o in picks:
            comps = ", ".join(o.get("competitors") or []) or "competitors"
            kind = (o.get("content_type") or "page").lower().replace("_", " ")
            signals.append(Signal(
                "promptwatch", 2, f"AI-cited {kind} names {comps} but not {brand}: {o['title']}", o["url"],
                datetime.fromisoformat(o["first_seen"].replace("Z", "+00:00")) if o.get("first_seen") else now,
                f"Page on {o.get('domain')} is cited {o.get('occurrences')} times in AI answers and mentions {comps}, "
                f"not {brand}. Outreach target: get {brand} added. {locale(self.cfg)['topic_term']} {o['title']}",
                engagement=int(o.get("occurrences") or 0),
                meta={"kind": "offsite_gap", "domain": o.get("domain"), "competitors": o.get("competitors"),
                      "occurrences": o.get("occurrences"), "new": o in fresh},
            ))
        summary.update({"offsite_total": len(offsite), "offsite_new": len(fresh)})
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
