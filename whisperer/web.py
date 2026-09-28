"""Panel: read-only for the client, tagging + notes for the Seeders team.

Auth is HTTP Basic with two passwords:
  TEAM_PASSWORD   -> Seeders team (can tag rows, add notes, trigger a run)
  CLIENT_PASSWORD -> client (read-only, no internal notes)
The username is ignored.
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from typing import Optional

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from jinja2 import Environment, FileSystemLoader, select_autoescape
from sqlalchemy import select

from whisperer import db
from whisperer.core import ROOT, load_config
from whisperer.scoring import FORMATS

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
app = FastAPI(title="News Whisperer", docs_url=None, redoc_url=None)
security = HTTPBasic()
env = Environment(loader=FileSystemLoader(ROOT / "whisperer" / "templates"), autoescape=select_autoescape())
env.filters["host"] = lambda u: (u or "").split("/")[2].replace("www.", "") if "://" in (u or "") else u
STATUSES = ["new", "in_progress", "actioned", "skipped"]
_running = threading.Lock()


db.init_db()


def role(creds: HTTPBasicCredentials = Depends(security)) -> str:
    pw = creds.password.encode()
    team, client = os.environ.get("TEAM_PASSWORD", ""), os.environ.get("CLIENT_PASSWORD", "")
    if team and secrets.compare_digest(pw, team.encode()):
        return "team"
    if client and secrets.compare_digest(pw, client.encode()):
        return "client"
    raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Basic"})


def team_only(r: str = Depends(role)) -> str:
    if r != "team":
        raise HTTPException(403, "Team only")
    return r


def _is_test(run) -> bool:
    return (run.stats or {}).get("_mode") == "test"


@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
def index(r: str = Depends(role)):
    with db.Session() as s:
        runs = s.scalars(select(db.Run).where(db.Run.status == "done").order_by(db.Run.id.desc())).all()
        run = next((x for x in runs if r == "team" or not _is_test(x)), None)
    if not run:
        return _page("empty.html", r, runs=[])
    return RedirectResponse(f"/runs/{run.id}", 302)


@app.get("/runs/{run_id}", response_class=HTMLResponse)
def panel(run_id: int, request: Request, r: str = Depends(role), bucket: Optional[str] = None,
          fmt: Optional[str] = None, status: Optional[str] = None, city: Optional[str] = None):
    with db.Session() as s:
        run = s.get(db.Run, run_id)
        if not run or (r != "team" and _is_test(run)):
            raise HTTPException(404)
        q = select(db.Opportunity).where(db.Opportunity.run_id == run_id)
        q = q.where(db.Opportunity.bucket == bucket) if bucket else q.where(db.Opportunity.bucket != "skip")
        if fmt:
            q = q.where(db.Opportunity.format == fmt)
        if status:
            q = q.where(db.Opportunity.status == status)
        if city:
            q = q.where(db.Opportunity.city == city)
        opps = s.scalars(q.order_by(db.Opportunity.bucket.desc(), db.Opportunity.total.desc())).all()
        runs = [x for x in s.scalars(select(db.Run).order_by(db.Run.id.desc()).limit(30)).all()
                if r == "team" or not _is_test(x)][:20]
        cities = sorted({c for c in s.scalars(select(db.Opportunity.city).where(
            db.Opportunity.run_id == run_id, db.Opportunity.city.is_not(None))).all()})
    return _page("panel.html", r, run=run, is_test=_is_test(run), runs=runs, opps=opps, formats=FORMATS, statuses=STATUSES, cities=cities,
                 f={"bucket": bucket or "", "fmt": fmt or "", "status": status or "", "city": city or ""},
                 running=_running.locked())


@app.get("/runs/{run_id}/report", response_class=HTMLResponse)
def report_html(run_id: int, r: str = Depends(role)):
    with db.Session() as s:
        run = s.get(db.Run, run_id)
        if not run or (r != "team" and _is_test(run)):
            raise HTTPException(404)
        return HTMLResponse(run.report_html or "<p>Report not ready.</p>")


@app.post("/opps/{opp_id}")
def update_opp(opp_id: int, status: str = Form(...), notes: str = Form(""), r: str = Depends(team_only)):
    if status not in STATUSES:
        raise HTTPException(400, "bad status")
    with db.Session() as s:
        o = s.get(db.Opportunity, opp_id)
        if not o:
            raise HTTPException(404)
        o.status, o.notes = status, notes[:5000]
        s.commit()
    return JSONResponse({"ok": True})


@app.post("/run")
def trigger_run(test: str = Form(""), r: str = Depends(team_only)):
    if _running.locked():
        return RedirectResponse("/", 303)

    def _go():
        with _running:
            from whisperer.pipeline import run
            run(send_email=False, test=bool(test))

    threading.Thread(target=_go, daemon=True).start()
    return RedirectResponse("/", 303)


@app.post("/runs/{run_id}/delete")
def delete_run(run_id: int, r: str = Depends(team_only)):
    with db.Session() as s:
        run = s.get(db.Run, run_id)
        if not run:
            raise HTTPException(404)
        if (run.stats or {}).get("_mode") != "test":
            raise HTTPException(400, "Only test runs can be deleted")
        s.delete(run)
        s.commit()
    return RedirectResponse("/", 303)


def _page(tpl, r, **ctx):
    cfg = load_config()
    return HTMLResponse(env.get_template(tpl).render(role=r, cfg=cfg, **ctx))
