"""Shared types, config loading and text helpers."""
from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

ROOT = Path(__file__).resolve().parent.parent
UA = "Mozilla/5.0 (compatible; SeedersNewsWhisperer/1.0; +https://seeders.com)"


@dataclass
class Signal:
    source: str            # google_news, rss, reddit, youtube, tiktok, instagram, google_trends, ahrefs
    layer: int             # 1..6 as in the setup doc
    title: str
    url: str
    published: Optional[datetime] = None
    text: str = ""
    engagement: int = 0    # upvotes/comments/views, normalised per source
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def age_days(self) -> float:
        if not self.published:
            return 3.0
        return max(0.0, (datetime.now(timezone.utc) - self.published).total_seconds() / 86400)


def load_config(client: Optional[str] = None) -> Dict[str, Any]:
    client = client or os.environ.get("WHISPERER_CLIENT", "basicfit")
    with open(ROOT / "clients" / f"{client}.yaml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    cfg["_client"] = client
    return cfg


def norm(text: str) -> str:
    """Lowercase, strip accents, collapse whitespace."""
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


STOPWORDS = set(norm(
    "le la les un une des de du d l et ou a au aux en dans pour par sur avec sans ce cette ces est sont "
    "qui que quoi quel quelle quels quelles comment pourquoi mon ma mes ton ta tes son sa ses notre votre leur "
    "je tu il elle on nous vous ils elles ne pas plus moins tres tout tous toute toutes y se sa s c qu "
    "the of and to in is for on with at by from it this that be are was"
).split())


def _stem(t: str) -> str:
    """Very light French plural/feminine folding: meilleures -> meilleur, equipements -> equipement."""
    if len(t) > 4 and t[-1] in "sx":
        t = t[:-1]
    if len(t) > 4 and t.endswith("e"):
        t = t[:-1]
    return t


def tokens(text: str) -> List[str]:
    return [_stem(t) for t in re.findall(r"[a-z0-9/]+", norm(text)) if len(t) > 2 and t not in STOPWORDS]


def contains_any(text_norm: str, needles: List[str]) -> List[str]:
    padded = f" {text_norm} "
    return [n for n in needles if norm(n) in padded or (n.startswith(" ") and n in padded)]


def parse_dt(value: Any) -> Optional[datetime]:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None
