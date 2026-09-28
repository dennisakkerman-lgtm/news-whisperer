"""Build data/promptwatch_snapshot.json from raw PromptWatch MCP results.

Used by the weekly Claude task that has the PromptWatch MCP connected (no API key needed):

    python scripts/pw_snapshot.py --prompts p1.json p2.json ... --offsite o1.json o2.json ...

Each input file holds one MCP tool result (listPrompts or listOffsiteMentionOpportunities);
text around the JSON object is ignored. Visibility from the previous snapshot is kept as
`prev_visibility`, so the pipeline can flag week-over-week drops.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "promptwatch_snapshot.json"


def _load(path: str) -> dict:
    text = Path(path).read_text(encoding="utf-8")
    obj, _ = json.JSONDecoder().raw_decode(text[text.index("{"):])
    return obj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompts", nargs="*", default=[])
    ap.add_argument("--offsite", nargs="*", default=[])
    a = ap.parse_args()

    old = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() else {}
    old_vis = {p["id"]: p.get("visibility") for p in old.get("prompts", [])}

    prompts = {}
    for f in a.prompts:
        for p in _load(f).get("prompts", []):
            vis = p.get("averageVisibilityScore", p.get("averageVisibility"))
            prompts[p["id"]] = {
                "id": p["id"], "prompt": p["prompt"], "type": p.get("type"), "intent": p.get("intent"),
                "visibility": vis, "prev_visibility": old_vis.get(p["id"]), "volume": p.get("volume"),
                "keywords": sorted({k["text"] for k in p.get("keywords", [])})[:40],
            }

    offsite, seen = [], set()
    for f in a.offsite:
        data = _load(f)
        names = {b["id"]: b["name"] for b in data.get("brands", [])}
        self_ids = {b["id"] for b in data.get("brands", []) if b.get("relation") == "SELF"}
        for u in data.get("urls", []):
            if u["url"] in seen:
                continue
            seen.add(u["url"])
            mentioned = [names.get(bid, bid) for bid, on in (u.get("brandMentions") or {}).items()
                         if on and bid not in self_ids]
            offsite.append({"url": u["url"], "title": u.get("title", ""), "domain": u.get("domain"),
                            "content_type": u.get("contentType"), "source_type": u.get("sourceType"),
                            "first_seen": u.get("firstSeenAt"), "occurrences": u.get("occurrenceCount", 0),
                            "competitors": mentioned})

    snap = {
        "exported": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "project_id": old.get("project_id", "5a7e8d16-e318-4f7c-bab1-182ed3a9adb3"),
        "prompts": list(prompts.values()) or old.get("prompts", []),
        "offsite": sorted(offsite, key=lambda x: -x["occurrences"]) or old.get("offsite", []),
    }
    OUT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"snapshot: {len(snap['prompts'])} prompts, {len(snap['offsite'])} offsite opportunities -> {OUT}")


if __name__ == "__main__":
    main()
