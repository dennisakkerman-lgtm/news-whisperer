"""Weekly run: collect -> filter -> score -> corroborate -> bucket -> enrich -> store -> report -> email."""
from __future__ import annotations

import importlib
import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from whisperer import db, narrative, report
from whisperer.promptwatch import PromptWatch
from whisperer.collectors import REGISTRY, Skipped, fixtures
from whisperer.core import Signal, load_config
from whisperer.scoring import URGENCY, GapIndex, bucketize, corroborate, relevant, score

log = logging.getLogger(__name__)


def collect_all(cfg, since, test: bool = False) -> (List[Signal], Dict[str, dict]):
    signals, stats = [], {}
    for name, mod_path in REGISTRY.items():
        t0 = time.time()
        try:
            mod = importlib.import_module(mod_path)
            got = mod.collect(cfg, since, name)
            signals.extend(got)
            stats[name] = {"status": "ok", "count": len(got)}
        except Skipped as exc:
            got = fixtures.sample(name) if test and "disabled" not in str(exc) else []
            if got:
                signals.extend(got)
                stats[name] = {"status": "test-data", "count": len(got), "detail": f"sample data ({exc})"}
            else:
                stats[name] = {"status": "skipped", "count": 0, "detail": str(exc)}
        except Exception as exc:
            log.exception("collector %s failed", name)
            stats[name] = {"status": "error", "count": 0, "detail": f"{type(exc).__name__}: {exc}"[:300]}
        stats[name]["seconds"] = round(time.time() - t0, 1)
        log.info("%-14s %s", name, stats[name])
    return signals, stats


def run(client: Optional[str] = None, send_email: bool = True, test: bool = False) -> int:
    """One pipeline run. test=True fills sources without credentials with [TEST] sample data
    and labels the run as a test everywhere (panel banner, report header, e-mail subject)."""
    cfg = load_config(client)
    db.init_db()
    lookback = cfg["output"]["lookback_days"]
    since = datetime.now(timezone.utc) - timedelta(days=lookback)

    with db.Session() as s:
        r = db.Run(client=cfg["_client"], config_version=cfg["config_version"], lookback_days=lookback,
                   stats={"_mode": "test"} if test else {})
        s.add(r)
        s.commit()
        run_id = r.id

    try:
        return _run(cfg, run_id, since, send_email, test)
    except Exception:
        with db.Session() as s:
            r = s.get(db.Run, run_id)
            r.status, r.finished_at = "failed", db.now()
            s.commit()
        raise


def _run(cfg, run_id, since, send_email, test) -> int:
    signals, stats = collect_all(cfg, since, test)
    if test:
        stats["_mode"] = "test"

    # Layer 2 · PromptWatch: live prompts for the gap score + its own signals
    pw = PromptWatch(cfg)
    prompts, origin = pw.prompts()
    gaps = GapIndex(cfg, prompts)
    t0 = time.time()
    pw_signals, pw_summary = pw.collect(since, gaps.threshold)
    pw_summary.update({"prompts": len(prompts), "prompts_origin": origin, "low_visibility": len(gaps.prompts),
                       "threshold": round(gaps.threshold, 1),
                       "avg_visibility": round(sum(p["visibility"] for p in prompts if p.get("visibility") is not None)
                                               / max(1, sum(1 for p in prompts if p.get("visibility") is not None)), 1)})
    if not prompts:
        pw_summary["detail"] = "No PromptWatch prompts for this client yet: gap score is 0 until the project has data."
    if not pw.key and test:
        pw_signals = fixtures.sample("promptwatch")
        stats["promptwatch"] = {"status": "test-data", "count": len(pw_signals),
                                "detail": f"sample data (PROMPTWATCH_API_KEY not set; gap score uses {origin} prompts)"}
    elif not pw.key:
        stats["promptwatch"] = {"status": "ok" if pw_signals or pw_summary.get("origin", "").startswith("MCP") else "skipped",
                                "count": len(pw_signals),
                                "detail": f"no API key; using {pw_summary.get('origin')} ({origin} prompts)"}
    else:
        stats["promptwatch"] = {"status": "error" if pw_summary.get("errors") and not pw_signals else "ok",
                                "count": len(pw_signals), "detail": "; ".join(pw_summary.get("errors", []))[:300]}
    stats["promptwatch"]["seconds"] = round(time.time() - t0, 1)
    stats["_promptwatch"] = pw_summary
    signals.extend(pw_signals)

    # dedupe by URL
    seen, unique = set(), []
    for sig in signals:
        if sig.url and sig.url not in seen:
            seen.add(sig.url)
            unique.append(sig)

    scored, filtered_out = [], Counter()
    for sig in unique:
        ok, why = relevant(sig, cfg)
        if ok:
            scored.append(score(sig, cfg, gaps))
        else:
            filtered_out[why.split(":")[0]] += 1

    ranked = bucketize(corroborate(scored), cfg)
    top = [x for x in ranked if x.bucket in ("this_week", "monitor")][: cfg["output"].get("narrative_top_n", 12)]
    for it in top[:8]:  # attach PromptWatch's own content brief when it has one for the matched prompt
        rec = it.gap_prompt and pw.recommendations(it.gap_prompt["id"])
        if rec:
            it.signal.meta["pw_rec"] = f"{rec.get('title')} ({(rec.get('contentType') or '').lower()}, impact {rec.get('impact')})"
    narrative.enrich(top, cfg)
    for it in top:
        if it.signal.meta.get("pw_rec"):
            it.rationale = f"{it.rationale} PromptWatch brief: {it.signal.meta['pw_rec']}."

    stats["_totals"] = {
        "signals": len(signals), "unique": len(unique), "relevant": len(scored), "stories": len(ranked),
        "filtered_out": dict(filtered_out), "gap_prompts": len(gaps.prompts), "gap_threshold": gaps.threshold,
        "by_source": dict(Counter(s.source for s in unique)),
    }

    with db.Session() as s:
        r = s.get(db.Run, run_id)
        keep = [x for x in ranked if x.bucket != "skip"] + [x for x in ranked if x.bucket == "skip"][:40]
        for it in keep:
            s.add(db.Opportunity(
                run_id=run_id, bucket=it.bucket, title=it.title or it.signal.title, signal_title=it.signal.title,
                url=it.signal.url, source=it.signal.source, layer=it.signal.layer, format=it.format,
                urgency=URGENCY[it.format], audience=it.audience, timeliness=it.timeliness, gap=it.gap,
                testability=it.testability, total=it.total, narrative_score=it.narrative_score, keyword=it.keyword,
                rationale=it.rationale, skip_reason=it.skip_reason, evidence=it.evidence[:6], city=it.city,
                personas=it.personas, gap_prompt=(it.gap_prompt or {}).get("prompt"),
                gap_visibility=(it.gap_prompt or {}).get("visibility"), published=it.signal.published,
            ))
        r.stats = stats
        r.finished_at = db.now()
        r.status = "done"
        s.commit()
        s.refresh(r)
        r.report_html = report.render_run(s, r, cfg)
        s.commit()

    if send_email and cfg["output"].get("weekly_report"):
        from whisperer import mailer
        with db.Session() as s:
            r = s.get(db.Run, run_id)
            mailer.send_report(cfg, r, test=test)
    log.info("run %s done: %s", run_id, stats["_totals"])
    return run_id
