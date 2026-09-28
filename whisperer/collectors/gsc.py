"""Layer 4 · Google Search Console. Placeholder until the client grants access.

When access lands: add a service account JSON as GSC_SERVICE_ACCOUNT_JSON, give it
read access on the property, and implement the searchanalytics.query call here
(compare the last 7 days with the 7 before, flag queries whose impressions grew
more than `rising_threshold_pct`).
"""
from __future__ import annotations

from whisperer.collectors import Skipped


def collect(cfg, since, name="gsc"):
    raise Skipped("pending client access" if not cfg["sources"].get("gsc", {}).get("enabled") else "not implemented yet")
