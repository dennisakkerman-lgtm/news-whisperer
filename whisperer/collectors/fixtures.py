"""Test mode: sample signals for sources that have no credentials yet.

Every sample is marked [TEST] and links to example.com, so it can never be
mistaken for a real signal in the panel or the report.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import List

from whisperer.core import Signal

# (title, text, days ago, engagement, meta)
SAMPLES = {
    "reddit": [
        ("Basic-Fit ou Fitness Park pour débuter la musculation à Lyon ?",
         "Je cherche une salle pas chère, ouverte tard, pour débuter. Avis sur Basic-Fit vs Fitness Park à Lyon ? "
         "Un peu intimidé par les habitués.", 1, 240, {"subreddit": "lyon", "comments": 87, "score": 66}),
        ("Salle de sport 24h/24 à Paris : ça vaut vraiment le coup la nuit ?",
         "Quelqu'un s'entraîne après 23h ? Sécurité, propreté, affluence ?", 2, 150,
         {"subreddit": "paris", "comments": 54, "score": 42}),
        ("Mon programme de reprise après 2 ans sans salle (rentrée)",
         "Programme 3 jours full body pour la reprise en septembre, conseils bienvenus.", 3, 320,
         {"subreddit": "musculation", "comments": 112, "score": 98}),
    ],
    "youtube": [
        ("J'ai testé 5 salles de sport pas chères à Marseille",
         "Comparatif Basic-Fit, Fitness Park, L'Orange Bleue : prix, machines, affluence.", 2, 1850,
         {"channel": "Sample channel", "views": 180000, "comments": 640}),
        ("Salle de sport quand on est une femme débutante : mes conseils",
         "Comment gérer le regard des autres et choisir une salle calme.", 4, 920,
         {"channel": "Sample channel", "views": 85000, "comments": 410}),
    ],
    "tiktok": [
        ("POV : premier jour à la salle de sport #salledesport #gymtok",
         "Le stress du premier jour à la salle, qui s'y reconnaît ? #basicfit #debutant", 1, 3400,
         {"author": "sample_creator", "plays": 320000, "comments": 1200}),
        ("Combien coûte vraiment un abonnement fitness en 2026 ? #fitnessfrance",
         "Comparatif prix abonnement salle de sport : Basic-Fit, Fitness Park, Keep Cool.", 3, 2100,
         {"author": "sample_creator", "plays": 190000, "comments": 800}),
    ],
    "instagram": [
        ("Routine pilates en salle pour débutantes #musculationfemme",
         "Ma routine pilates + cardio en salle de sport, 3x par semaine.", 2, 900,
         {"author": "sample_account", "likes": 8200, "comments": 310}),
    ],
    "ahrefs": [
        ("Fitness Park got a new link: Les 10 meilleures salles de sport à Toulouse en 2026",
         "New backlink to fitnesspark.fr (DR 62). Pitch target for Basic-Fit.", 4, 62,
         {"competitor": "Fitness Park", "dr": 62, "kind": "new_competitor_link"}),
        ("example-magazine.fr links to Fitness Park, Neoness but not Basic-Fit",
         "Link gap: domain cites several competitors but not the brand.", 0, 20,
         {"competitors": ["Fitness Park", "Neoness"], "kind": "link_gap"}),
    ],
}

SAMPLES["promptwatch"] = [
    ("AI visibility dropped on “Quelle salle de sport à Lyon est la moins chère ?” (38% → 21%)",
     "Basic-Fit lost 17 visibility points week over week on this prompt. Quelle salle de sport à Lyon est la moins chère ?",
     0, 170, {"kind": "visibility_drop", "prev": 38, "cur": 21,
               "prompt_id": "dfe761a8-0db5-4aa4-99c2-bb61bead0ffc"}),
    ("AI engines cite example-guide-sport.fr more often for gym questions (4 → 11 citations/week)",
     "Third-party site gaining AI citations: pitch target for Basic-Fit (get mentioned or cited there). salle de sport",
     0, 11, {"kind": "rising_cited_domain", "domain": "example-guide-sport.fr", "competitor": False}),
]

LAYER = {"reddit": 1, "youtube": 1, "tiktok": 1, "instagram": 1, "ahrefs": 3, "promptwatch": 2}


def sample(source: str) -> List[Signal]:
    now = datetime.now(timezone.utc)
    out = []
    for i, (title, text, days, eng, meta) in enumerate(SAMPLES.get(source, [])):
        out.append(Signal(
            source, LAYER[source], f"[TEST] {title}", f"https://example.com/test/{source}/{i + 1}",
            now - timedelta(days=days, hours=3), text, engagement=eng, meta={**meta, "test": True},
        ))
    return out
