"""Deterministic scoring: audience + timeliness + gap + testability (0-5 each, /20)."""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from whisperer.core import ROOT, Signal, contains_any, norm, tokens

FORMATS = {
    "news_peg": "Timely hook: react to news while it is fresh.",
    "explainer": "Answers a question people are actively asking.",
    "playbook": "Step-by-step how-to / programme / checklist.",
    "contrarian": "Challenges a myth or popular take.",
    "test": "Hands-on test, comparison or review people can verify.",
}

URGENCY = {"news_peg": "48h", "contrarian": "1 week", "test": "2 weeks", "explainer": "2 weeks", "playbook": "1 month"}


@dataclass
class Scored:
    signal: Signal
    audience: int = 0
    timeliness: int = 0
    gap: int = 0
    testability: int = 0
    format: str = "explainer"
    personas: List[str] = field(default_factory=list)
    city: Optional[str] = None
    gap_prompt: Optional[Dict[str, Any]] = None
    corroboration: int = 1              # number of distinct sources carrying the same story
    evidence: List[str] = field(default_factory=list)
    bucket: str = "skip"
    skip_reason: str = ""
    # filled by narrative.py (optional)
    narrative_score: Optional[int] = None
    rationale: str = ""
    keyword: str = ""
    title: str = ""

    @property
    def total(self) -> int:
        return self.audience + self.timeliness + self.gap + self.testability


class GapIndex:
    """PromptWatch prompts where the brand has low AI visibility.

    Matching is IDF-weighted over the prompt vocabulary, so generic words that
    appear in most prompts ("salle", "sport") carry almost no weight and a match
    needs at least two distinctive shared terms.
    """

    def __init__(self, cfg, prompts: Optional[List[Dict[str, Any]]] = None):
        pw = cfg["promptwatch"]
        if prompts is None:
            with open(ROOT / pw["seed_file"], encoding="utf-8") as f:
                prompts = json.load(f)["prompts"]
        allp = [p for p in prompts if p.get("visibility") is not None]
        avg = sum(p["visibility"] for p in allp) / max(1, len(allp))
        self.threshold = float(pw.get("low_visibility_threshold") or avg)
        df = Counter(t for p in allp for t in set(tokens(p["prompt"])))
        n = len(allp)
        self.idf = {t: math.log(n / c) for t, c in df.items()}
        self.by_id = {p["id"]: p for p in allp}
        self.prompts = []
        for p in allp:
            vis = p.get("visibility")
            if vis is None or vis >= self.threshold:
                continue
            core = {t for t in tokens(p["prompt"]) if self.idf.get(t, 0) > 0.7}
            if core:
                self.prompts.append((p, core, sum(self.idf[t] for t in core)))

    def match(self, text: str) -> Tuple[int, Optional[Dict[str, Any]]]:
        toks = set(tokens(text))
        best, best_p = 0.0, None
        for p, core, weight in self.prompts:
            shared = toks & core
            if not shared:
                continue
            sim = sum(self.idf[t] for t in shared) / weight
            if len(shared) < 2 and len(core) > 1:
                sim = min(sim, 0.2)  # one shared term is a weak hint, never more than 1-2 points
            sim *= 1 - p["visibility"] / self.threshold * 0.4  # lower visibility = bigger gap
            if sim > best:
                best, best_p = sim, p
        score = 5 if best >= 0.6 else 4 if best >= 0.45 else 3 if best >= 0.33 else 2 if best >= 0.22 else 1 if best >= 0.12 else 0
        return score, best_p if score else None


def relevant(sig: Signal, cfg) -> Tuple[bool, str]:
    t = norm(f"{sig.title} {sig.text}")
    bad = contains_any(t, cfg["keywords"]["exclude"])
    if bad:
        return False, f"excluded keyword: {bad[0]}"
    if sig.source in ("google_trends", "ahrefs", "promptwatch"):
        return True, ""
    brands = cfg["brand"]["aliases"] + [a for c in cfg["competitors"] for a in c["aliases"]]
    if contains_any(t, cfg["keywords"]["include"]) or contains_any(t, brands):
        return True, ""
    return False, "off-topic (no include keyword or brand mention)"


def _format(sig: Signal, t: str) -> str:
    if sig.source == "promptwatch":
        return "explainer" if sig.meta.get("kind") == "visibility_drop" else "test"
    if re.search(r"\b(mythe|idee recue|faux|arretez|stop|en fait|contrairement|surcote|inutile|arnaque)\b", t):
        return "contrarian"
    if re.search(r"\b(test|teste|j'ai essaye|comparatif|vs|versus|avis|classement|meilleure?s?)\b", t):
        return "test"
    if re.search(r"\b(programme|routine|conseils|astuces|etapes|guide|plan|comment (faire|commencer|reprendre))\b", t):
        return "playbook"
    if sig.source in ("google_news", "rss") and sig.age_days <= 3:
        return "news_peg"
    if "?" in sig.title or re.search(r"\b(pourquoi|comment|quel|quelle|est-ce)\b", t):
        return "explainer"
    return "news_peg" if sig.source in ("google_news", "rss") else "explainer"


def _in_season(cfg, today: date, t: str) -> bool:
    md = today.strftime("%m-%d")
    for s in cfg.get("seasonality", []):
        if s["start"] <= md <= s["end"] and contains_any(t, s["themes"]):
            return True
    return False


def score(sig: Signal, cfg, gaps: GapIndex) -> Scored:
    t = norm(f"{sig.title.replace('[TEST] ', '')} {sig.text}")
    sc = Scored(sig, evidence=[sig.url], title=sig.title)

    # Audience: persona fit + city + engagement
    sc.personas = [p for p, kws in cfg["personas"].items() if contains_any(t, kws)]
    cities = [c for c in cfg["brand"]["cities"] if norm(c) in t or norm(c) == norm(sig.meta.get("subreddit", ""))]
    sc.city = cities[0] if cities else None
    eng = 0 if sig.engagement <= 0 else min(2, int(math.log10(sig.engagement + 1)))
    brand = 2 if contains_any(t, cfg["brand"]["aliases"]) else 0
    rival = 1 if contains_any(t, [a for c in cfg["competitors"] for a in c["aliases"]]) else 0
    sc.audience = min(5, min(2, len(sc.personas)) + (1 if sc.city else 0) + eng + max(brand, rival))

    # Timeliness: freshness + seasonality window
    a = sig.age_days
    sc.timeliness = 5 if a <= 2 else 4 if a <= 4 else 3 if a <= 7 else 2 if a <= 14 else 1 if a <= 30 else 0
    if _in_season(cfg, date.today(), t):
        sc.timeliness = min(5, sc.timeliness + 1)

    # Gap: overlap with low-visibility PromptWatch prompts
    own = gaps.by_id.get(sig.meta.get("prompt_id", ""))
    if own:  # a PromptWatch drop is about exactly this prompt
        sc.gap, sc.gap_prompt = (5 if own["visibility"] < gaps.threshold else 3), own
    else:
        sc.gap, sc.gap_prompt = gaps.match(f"{sig.title} {sig.text[:500]}")

    # Testability: can we turn this into something concrete and measurable?
    sc.format = _format(sig, t)
    base = {"test": 4, "playbook": 4, "contrarian": 3, "explainer": 3, "news_peg": 2}[sc.format]
    if re.search(r"\d", sig.title):
        base += 1
    if sig.source in ("reddit", "youtube", "tiktok", "instagram") and sig.meta.get("comments"):
        base += 1  # live conversation to validate against
    if brand or rival:
        base += 1  # concrete brand/competitor angle: easy to react to or pitch against
    sc.testability = min(5, base)

    w = cfg["scoring"]["weights"]
    if any(v != 1.0 for v in w.values()):
        for k in ("audience", "timeliness", "gap", "testability"):
            setattr(sc, k, min(5, round(getattr(sc, k) * w.get(k, 1.0))))
    return sc


def _story_key(title: str) -> frozenset:
    return frozenset(tokens(title)[:8])


def corroborate(items: List[Scored]) -> List[Scored]:
    """Merge near-duplicate stories; count distinct sources as corroboration."""
    merged: List[Scored] = []
    for it in sorted(items, key=lambda s: -s.total):
        if it.signal.source in ("promptwatch", "google_trends", "ahrefs"):  # structured: one row per item
            merged.append(it)
            continue
        k = _story_key(it.signal.title)
        home = None
        for m in merged:
            mk = _story_key(m.signal.title)
            if k and mk and len(k & mk) / max(1, min(len(k), len(mk))) >= 0.6:
                home = m
                break
        if home is None:
            merged.append(it)
            continue
        if it.signal.url not in home.evidence:
            home.evidence.append(it.signal.url)
        srcs = {home.signal.source, it.signal.source, *home.signal.meta.get("_sources", [])}
        home.signal.meta["_sources"] = sorted(srcs)
        home.corroboration = len(srcs) if len(srcs) > 1 else home.corroboration + 1
    return merged


def bucketize(items: List[Scored], cfg) -> List[Scored]:
    s = cfg["scoring"]
    for it in items:  # a story several outlets/sources carry reaches more people
        it.audience = min(5, it.audience + min(2, it.corroboration - 1))
    ranked = sorted(items, key=lambda x: (-x.total, -x.signal.engagement))
    this_week, per_source = 0, Counter()
    cap = s.get("this_week_per_source", 2)
    for it in ranked:
        single_low = it.corroboration <= 1 and it.audience <= 1
        if (it.total >= s["this_week_min"] and not single_low and this_week < s["this_week_max"]
                and per_source[it.signal.source] < cap):
            it.bucket = "this_week"
            this_week += 1
            per_source[it.signal.source] += 1
        elif it.total >= s["monitor_min"]:
            it.bucket = "monitor"
            if single_low:
                it.skip_reason = "single source, low audience match: needs corroboration"
            elif it.total >= s["this_week_min"]:
                it.skip_reason = "scored 15+ but this week's top slots (or this source's share) are full"
            else:
                it.skip_reason = "promising, below the 15/20 bar"
        else:
            it.bucket = "skip"
            it.skip_reason = it.skip_reason or f"score {it.total}/20 below {s['monitor_min']}"
    return ranked
