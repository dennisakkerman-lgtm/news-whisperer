"""Weekly run: collect -> filter -> score -> corroborate -> bucket -> enrich -> store -> report -> email."""
from __future__ import annotations

import importlib
import logging
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from whisperer import db, narrative, report
from whisperer.collectors import REGISTRY, Skipped
from whisperer.core import Signal, load_config
from whisperer.scoring import URGENCY, GapIndex, bucketize, corroborate, relevant, score

log = logging.getLogger(__name__)


def collect_all(cfg, since) -> (List[Signal], Dict[str, dict]):
    signals, stats = [], {}
    for name, mod_path in REGISTRY.items():
        t0 = time.time()
        try:
            mod = importlib.import_module(mod_path)
            got = mod.collect(cfg, since, name)
            signals.extend(got)
            stats[name] = {"status": "ok", "count": len(got)}
        except Skipped as exc:
            stats[name] = {"status": "skipped", "count": 0, "detail": str(exc)}
        except Exception as exc:
            log.exception("collector %s failed", name)
            stats[name] = {"status": "error", "count": 0, "detail": f"{type(exc).__name__}: {exc}"[:300]}
        stats[name]["seconds"] = round(time.time() - t0, 1)
        log.info("%-14s %s", name, stats[name])
    return signals, stats


def run(client: Optional[str] = None, send_email: bool = True) -> int:
    cfg = load_config(client)
    db.init_db()
    lookback = cfg["output"]["lookback_days"]
    since = datetime.now(timezone.utc) - timedelta(days=lookback)

    with db.Session() as s:
        r = db.Run(client=cfg["_client"], config_version=cfg["config_version"], lookback_days=lookback)
        s.add(r)
        s.commit()
        run_id = r.id

    signals, stats = collect_all(cfg, since)

    # dedupe by URL
    seen, unique = set(), []
    for sig in signals:
        if sig.url and sig.url not in seen:
            seen.add(sig.url)
            unique.append(sig)

    gaps = GapIndex(cfg)
    scored, filtered_out = [], Counter()
    for sig in unique:
        ok, why = relevant(sig, cfg)
        if ok:
            scored.append(score(sig, cfg, gaps))
        else:
            filtered_out[why.split(":")[0]] += 1

    ranked = bucketize(corroborate(scored), cfg)
    top = [x for x in ranked if x.bucket in ("this_week", "monitor")][: cfg["output"].get("narrative_top_n", 12)]
    narrative.enrich(top, cfg)

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
            mailer.send_report(cfg, r)
    log.info("run %s done: %s", run_id, stats["_totals"])
    return run_id
