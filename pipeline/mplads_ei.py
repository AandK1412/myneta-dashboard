"""
mplads_ei.py - pull Empowered Indian's MPLADS rendering as an INDEPENDENT CROSS-CHECK.

https://empoweredindian.in/mplads is a civic-tech dashboard built on the same
official MPLADS portal we read directly in mplads.py. Because both derive from
one upstream, the places where they DISAGREE are the interesting part: a delta
means a definitional difference, a snapshot-timing difference, or a bug in one
of the two renderings. We publish those deltas rather than picking a winner.

Their public API (observed from their own dashboard's network calls):
    GET https://api.empoweredindian.in/api/metadata/sync-info
    GET https://api.empoweredindian.in/api/summary/overview
    GET https://api.empoweredindian.in/api/summary/mps?page=1&limit=800
    GET https://api.empoweredindian.in/api/summary/states?limit=50
    GET https://api.empoweredindian.in/api/mplads/terms

Known quirks, verified 2026-08-01 (surfaced in the payload, not hidden):
  * Covers BOTH houses (755 MPs = 543 Lok Sabha + 212 Rajya Sabha). Rajya Sabha
    rows carry constituency "Sitting Rajya Sabha" and cannot join to constituency
    data, so only the Lok Sabha subset is comparable to ours.
  * ~147 records (19%) report completedWorksCount > recommendedWorksCount, which
    yields a NEGATIVE pendingWorks. Their own dataQuality field still reads 98.
  * The `lsTerm` query parameter is accepted but ignored - the MP endpoint serves
    current-term data regardless, even though /mplads/terms advertises the 17th.
"""

from __future__ import annotations

import json
import urllib.request

BASE = "https://api.empoweredindian.in/api"
UA = "myneta-public-dashboard/1.0 (civic data project; contact via repo issues)"


def _get(path: str, timeout: int = 90):
    req = urllib.request.Request(f"{BASE}/{path}", headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def fetch(progress=None) -> dict:
    """Everything we need for reconciliation, in four requests."""
    out = {}
    for key, path in [
        ("sync", "metadata/sync-info"),
        ("overview", "summary/overview"),
        ("terms", "mplads/terms"),
        ("mps", "summary/mps?page=1&limit=800"),
        ("states", "summary/states?limit=50"),
    ]:
        if progress:
            progress(f"  EI: {path}")
        try:
            out[key] = _get(path).get("data")
        except Exception as e:  # a cross-check must never break the main build
            if progress:
                progress(f"  EI: {path} FAILED ({e})")
            out[key] = None
    return out


def audit(mps: list[dict]) -> dict:
    """Internal-consistency audit of their per-MP rows."""
    if not mps:
        return {}
    ls = [m for m in mps if m.get("house") == "Lok Sabha"]
    inconsistent = [m for m in mps
                    if (m.get("completedWorksCount") or 0) > (m.get("recommendedWorksCount") or 0)]
    return {
        "records_total": len(mps),
        "records_lok_sabha": len(ls),
        "records_rajya_sabha": len(mps) - len(ls),
        "records_completed_exceeds_recommended": len(inconsistent),
        "pct_inconsistent": round(100 * len(inconsistent) / len(mps), 1),
        "ls_allocated": round(sum(m.get("allocatedAmount") or 0 for m in ls)),
        "ls_expenditure": round(sum(m.get("totalExpenditure") or 0 for m in ls)),
        "ls_works_completed": sum(m.get("completedWorksCount") or 0 for m in ls),
        "ls_works_recommended": sum(m.get("recommendedWorksCount") or 0 for m in ls),
    }
