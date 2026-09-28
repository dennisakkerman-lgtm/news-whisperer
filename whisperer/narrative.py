"""Optional Claude pass: narrative score, rationale and brief keyword for the top opportunities.

Runs only when ANTHROPIC_API_KEY is set. The deterministic score is never changed;
Claude adds a separate narrative score (0-10) and the wording for the report.
"""
from __future__ import annotations

import json
import logging
import os
from typing import List

from whisperer.core import locale
from whisperer.scoring import FORMATS, Scored

log = logging.getLogger(__name__)

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "idx": {"type": "integer"},
                    "title": {"type": "string"},
                    "narrative_score": {"type": "integer"},
                    "keyword": {"type": "string"},
                    "rationale": {"type": "string"},
                },
                "required": ["idx", "title", "narrative_score", "keyword", "rationale"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["items"],
    "additionalProperties": False,
}

SYSTEM = """You are a content strategist at Seeders, a digital PR and SEO agency, working on the {brand} account ({sector}, {market}).
Each week you receive scored signals from social media, news, search trends and AI-visibility data for this market.
For every signal write:
- title: a working title in {content_lang} for a piece of content {brand} could publish or pitch.
- narrative_score: 0-10, how strong the story is for media and communities in this market (newsworthiness, emotion, novelty). Be strict; 8+ is rare.
- keyword: the {content_lang} search keyword a brief should target.
- rationale: 2-3 sentences in {lang} (the reviewing team reads {lang}) on why this matters now for {brand}, referencing the evidence and, when given, the PromptWatch prompt where {brand} has low AI visibility.
Stay factual: only use what is in the signal. Do not invent statistics."""


def enrich(items: List[Scored], cfg) -> None:
    if not os.environ.get("ANTHROPIC_API_KEY") or not items:
        for it in items:
            it.keyword = it.keyword or (it.gap_prompt or {}).get("prompt", "") or it.signal.meta.get("query", "")
            it.rationale = it.rationale or _fallback_rationale(it, cfg)
        return

    import anthropic

    client = anthropic.Anthropic()
    loc = locale(cfg)
    lang = "English" if cfg["output"].get("report_language", "en") == "en" else loc["content_language"]
    payload = []
    for i, it in enumerate(items):
        payload.append({
            "idx": i, "source": it.signal.source, "title": it.signal.title, "text": it.signal.text[:600],
            "url": it.signal.url, "format": f"{it.format}: {FORMATS[it.format]}", "personas": it.personas,
            "city": it.city, "scores": {"audience": it.audience, "timeliness": it.timeliness, "gap": it.gap,
                                        "testability": it.testability},
            "promptwatch_gap": it.gap_prompt and {"prompt": it.gap_prompt["prompt"],
                                                  "visibility": it.gap_prompt["visibility"]},
            "engagement": {k: it.signal.meta.get(k) for k in ("score", "comments", "views", "plays", "growth")
                           if it.signal.meta.get(k) is not None},
        })
    try:
        resp = client.beta.messages.create(
            model=os.environ.get("CLAUDE_MODEL", "claude-opus-5"),
            max_tokens=16000,
            system=SYSTEM.format(brand=cfg["brand"]["name"], lang=lang, sector=loc["sector"], market=loc["market"],
                                 content_lang=loc["content_language"]) + cfg.get("narrative_guidance", ""),
            messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            extra_body={"fallbacks": "default"},
        )
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"refusal: {resp.stop_details}")
        text = next(b.text for b in resp.content if b.type == "text")
        for row in json.loads(text)["items"]:
            if 0 <= row["idx"] < len(items):
                it = items[row["idx"]]
                it.title = row["title"] or it.title
                it.narrative_score = max(0, min(10, int(row["narrative_score"])))
                it.keyword = row["keyword"]
                it.rationale = row["rationale"]
    except Exception:
        log.exception("Claude enrichment failed; falling back to deterministic rationale")
    for it in items:
        it.rationale = it.rationale or _fallback_rationale(it, cfg)


def _fallback_rationale(it: Scored, cfg) -> str:
    parts = [f"{it.signal.source.replace('_', ' ').title()} signal, {it.format.replace('_', ' ')} format ({FORMATS[it.format].lower()})"]
    if it.personas:
        parts.append(f"matches {', '.join(it.personas)}")
    if it.gap_prompt:
        parts.append(f"covers “{it.gap_prompt['prompt']}” where {cfg['brand']['name']} has "
                     f"{it.gap_prompt['visibility']:.0f}% AI visibility")
    if it.corroboration > 1:
        parts.append(f"seen in {it.corroboration} sources")
    return "; ".join(parts) + "."
