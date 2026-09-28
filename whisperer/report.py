"""Weekly HTML digest (e-mail + panel + print-to-PDF)."""
from __future__ import annotations

from collections import Counter

from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from whisperer import db
from whisperer.core import ROOT
from whisperer.scoring import FORMATS

env = Environment(loader=FileSystemLoader(ROOT / "whisperer" / "templates"), autoescape=select_autoescape())
env.filters["host"] = lambda u: (u or "").split("/")[2].replace("www.", "") if "://" in (u or "") else u


def render_run(session, run: db.Run, cfg) -> str:
    opps = session.scalars(select(db.Opportunity).where(db.Opportunity.run_id == run.id)
                           .order_by(db.Opportunity.total.desc())).all()
    by = {b: [o for o in opps if o.bucket == b] for b in ("this_week", "monitor", "skip")}
    stats = dict(run.stats or {})
    totals = stats.pop("_totals", {})
    stats.pop("_mode", None)
    skip_reasons = Counter(o.skip_reason.split(":")[0] for o in by["skip"])
    for why, n in (totals.get("filtered_out") or {}).items():
        skip_reasons[why] += n
    return env.get_template("report.html").render(
        run=run, cfg=cfg, this_week=by["this_week"], monitor=by["monitor"][:15], skip=by["skip"][:10],
        skip_reasons=skip_reasons.most_common(), sources=stats, totals=totals, formats=FORMATS,
    )
