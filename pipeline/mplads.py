"""
mplads.py - fetch MPLADS fund-utilization data from the eSAKSHI portal
(https://mplads.mospi.gov.in), the Ministry of Statistics' public dashboard.

Endpoints are the same unauthenticated REST calls the portal's own dashboard
makes (POST /rest/PreLoginDashboardData/*). Stdlib only, like myneta.py.

SCOPE AND HONESTY NOTES (these drive how the data may be presented):

* eSAKSHI tracks the REVISED fund-flow procedure effective 1 April 2023. It is
  authoritative for the CURRENT (18th) Lok Sabha, but its "17th Lok Sabha" view
  covers only that term's final ~14 months - the national allocated figure it
  reports (~Rs 4,765 Cr) is a third of a real five-year term. We therefore fetch
  the current tenure only and never present eSAKSHI numbers as term history.

* "Allocated limit" is the entitlement accrued TO DATE, including carry-forward
  of unspent balances from the seat's previous MP - not the full-term Rs 25 Cr.

* The MP shown for a seat is the CURRENT MP, which differs from the 2024 general
  election winner wherever a seat changed hands since (bye-elections, deaths,
  resignations). The join layer must surface those, not paper over them.

* Funds are released to district authorities and spent by them; the MP only
  recommends works. Low expenditure is not automatically MP inaction.

Cross-checked against Empowered Indian (see mplads_ei.py). As of 1 Aug 2026 the
Lok Sabha allocated totals agree to the rupee; works counts differ materially by
definition. Both are published side by side in the dashboard.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from dataclasses import dataclass, asdict

BASE = "https://mplads.mospi.gov.in/rest/PreLoginDashboardData"
UA = "myneta-public-dashboard/1.0 (civic data project; contact via repo issues)"

HOUSE_LOK_SABHA = "2"


def _digits(x) -> int | None:
    """'₹14,94,58,905.11' -> 149458905 (rupees, truncated). None if no number."""
    if x is None:
        return None
    s = re.sub(r"[^\d.]", "", str(x))
    if not s or s == ".":
        return None
    try:
        return int(float(s))
    except ValueError:
        return None


class Api:
    """Polite JSON POST client with disk cache (same pattern as myneta.Fetcher)."""

    def __init__(self, cache_dir=None, delay: float = 0.7, max_retries: int = 3,
                 timeout: int = 60):
        self.cache_dir = cache_dir
        self.delay = delay
        self.max_retries = max_retries
        self.timeout = timeout
        self._last = 0.0
        if cache_dir:
            import pathlib
            pathlib.Path(cache_dir).mkdir(parents=True, exist_ok=True)

    def _cache_path(self, key: str):
        import hashlib, pathlib
        h = hashlib.sha1(key.encode()).hexdigest()[:16]
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", key)[:60]
        return pathlib.Path(self.cache_dir) / f"{safe}_{h}.json"

    def post(self, endpoint: str, body: dict):
        key = f"{endpoint}_{json.dumps(body, sort_keys=True)}"
        if self.cache_dir:
            p = self._cache_path(key)
            if p.exists() and p.stat().st_size > 0:
                return json.loads(p.read_text(encoding="utf-8"))

        payload = json.dumps(body).encode("utf-8")
        last_err = None
        for attempt in range(self.max_retries):
            gap = time.time() - self._last
            if gap < self.delay:
                time.sleep(self.delay - gap)
            try:
                req = urllib.request.Request(
                    f"{BASE}/{endpoint}", data=payload, method="POST",
                    headers={"User-Agent": UA,
                             "Content-Type": "application/json; charset=utf-8"})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    # The portal emits the rupee sign in a legacy codepage, so a
                    # strict UTF-8 decode dies on byte 0xA0. Lossy decode is fine:
                    # every amount is re-parsed digits-only downstream.
                    data = json.loads(r.read().decode("utf-8", errors="replace"))
                self._last = time.time()
                if self.cache_dir:
                    self._cache_path(key).write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8")
                return data
            except Exception as e:  # noqa: BLE001 - retry any transport error
                last_err = e
                self._last = time.time()
                time.sleep(2 ** attempt)
        raise RuntimeError(f"{endpoint} failed after {self.max_retries} tries: {last_err}")


@dataclass
class MpFunds:
    tenure: str
    state: str
    state_id: int
    constituency: str
    constituency_id: int
    mp_name: str
    mp_id: int
    allocated: int | None          # entitlement accrued to date, incl. carry-forward
    expenditure: int | None        # spend on completed + ongoing works to date
    works_recommended: int | None
    works_recommended_amt: int | None
    works_sanctioned: int | None
    works_sanctioned_amt: int | None
    works_completed: int | None
    works_completed_amt: int | None
    pct_spent: float | None        # expenditure / allocated

    def to_dict(self):
        return asdict(self)


# Tile captions -> (count?, exact-amount index). The API returns arrays whose
# first element is the exact figure; "Works *" tiles prepend a count.
_TILES = {
    "Allocated Limit for Hon'ble MPs": ("allocated", None),
    "Expenditure on Completed and On-going Works as on Date": ("expenditure", None),
    "Works Recommended": ("works_recommended", "works_recommended_amt"),
    "Works Sanctioned": ("works_sanctioned", "works_sanctioned_amt"),
    "Works Completed": ("works_completed", "works_completed_amt"),
}


def parse_tiles(tiles: dict) -> dict:
    out = {}
    for caption, (a, b) in _TILES.items():
        val = tiles.get(caption) or tiles.get(caption.replace("'", "'"))
        if not isinstance(val, list) or not val:
            continue
        if b is None:
            out[a] = _digits(val[0])
        else:                       # ["count", "exact amount", "pretty"]
            out[a] = _digits(val[0])
            out[b] = _digits(val[1]) if len(val) > 1 else None
    return out


def current_tenure(api: Api) -> tuple[int, str]:
    tiles = api.post("getTilesData", {"uname": f"0,0,0,{HOUSE_LOK_SABHA}"})
    cur = (tiles.get("Current Tenure") or [{}])[0]
    return int(cur.get("ID", 0)), str(cur.get("CAPTION", "")).strip()


def fetch_all(api: Api, progress=None) -> tuple[list[MpFunds], dict]:
    """Every current-tenure Lok Sabha MP's fund position, plus national tiles."""
    tenure_id, tenure_name = current_tenure(api)
    national = parse_tiles(api.post("getTilesData", {"uname": f"0,0,0,{HOUSE_LOK_SABHA}"}))
    national["tenure"] = tenure_name

    states = api.post("getStateData", {"uname": f"0,0,0,{HOUSE_LOK_SABHA}"})
    rows: list[MpFunds] = []
    for si, st in enumerate(states, 1):
        sid, sname = st["STATE_ID"], st["STATE_NAME"].strip()
        consts = api.post("getConstituencyData", {"id": sid}) or []
        for c in consts:
            cid, cname = c["ID"], str(c["CAPTION"]).strip()
            mps = api.post("getMpAndConstCombo",
                           {"const_combo": f"{cid},{HOUSE_LOK_SABHA},{tenure_id}"}) or []
            for mp in mps:
                mp_id, mp_name = mp["ID"], str(mp["CAPTION"]).strip()
                tiles = api.post("getTilesData",
                                 {"uname": f"{sid},{cid},{mp_id},{HOUSE_LOK_SABHA}"})
                t = parse_tiles(tiles)
                alloc, spent = t.get("allocated"), t.get("expenditure")
                rows.append(MpFunds(
                    tenure=tenure_name, state=sname, state_id=sid,
                    constituency=cname, constituency_id=cid,
                    mp_name=mp_name, mp_id=mp_id,
                    allocated=alloc, expenditure=spent,
                    works_recommended=t.get("works_recommended"),
                    works_recommended_amt=t.get("works_recommended_amt"),
                    works_sanctioned=t.get("works_sanctioned"),
                    works_sanctioned_amt=t.get("works_sanctioned_amt"),
                    works_completed=t.get("works_completed"),
                    works_completed_amt=t.get("works_completed_amt"),
                    pct_spent=round(100 * spent / alloc, 1)
                        if alloc and spent is not None and alloc > 0 else None,
                ))
        if progress:
            progress(f"  {sname}: done ({si}/{len(states)} states, {len(rows)} MPs so far)")
    return rows, national


def norm_key(state: str, constituency: str) -> str:
    """Join key tolerant of '&' vs 'And', (SC)/(ST) tags, case and spacing."""
    def n(x):
        x = (x or "").upper()
        x = re.sub(r"\(\s*(SC|ST)\s*\)", "", x)
        x = x.replace("&", " AND ")
        return re.sub(r"[^A-Z]", "", x)
    return n(state) + "|" + n(constituency)


# ---------------------------------------------------------------------------
# Fuzzy matching.
#
# The two sources transliterate Indian place and person names differently -
# "Ananthapur" vs "Anantapur", "Purandeshwari" vs "Purandheshwari" - so exact
# keys leave real matches on the floor. Everything below is deliberately
# conservative: a fuzzy match must be both strong AND clearly better than the
# runner-up, and every one is reported so a reader can audit it.
# ---------------------------------------------------------------------------

_HONORIFICS = r"\b(SHRI|SHRIMATI|SMT|SRI|DR|ADV|ADVOCATE|PROF|MR|MRS|MS|KUMARI|SARDAR|COL|CAPT|CAPTAIN|MAJ|GEN|JUSTICE)\b\.?"


def _clean_person(name: str, keep_bracketed: bool) -> str:
    """
    Normalise a person name. Brackets are ambiguous in this data - sometimes a
    nickname to discard ("(Tea Time Uday)"), sometimes part of the actual name
    ("G M Harish (Balayogi)") - so callers try it both ways.
    """
    n = (name or "").upper()
    n = re.sub(r"\bS\s*/\s*O\b.*$", " ", n)        # patronymic tail: "S/O Harikesh"
    n = re.sub(r"\b(ALIAS|URF|@)\b", " ", n)       # "X Alias Y"
    n = re.sub(r"[()]", " ", n) if keep_bracketed else re.sub(r"\([^)]*\)", " ", n)
    n = re.sub(_HONORIFICS, " ", n)
    n = re.sub(r"[^A-Z ]", " ", n)
    # Single letters are initials ("P V Midhun Reddy" -> "MIDHUN REDDY"); one
    # source routinely includes them and the other does not.
    return " ".join(t for t in n.split() if len(t) > 1)


def name_similarity(a: str, b: str) -> float:
    """
    0-1 similarity tolerant of word order, initials, honorifics, patronymics,
    bracketed aliases and transliteration drift.

    Token containment short-circuits to 1.0: when every meaningful word of the
    shorter name appears in the longer one, it is the same person written at
    different lengths ("Krishan Pal" vs "Krishan Pal Gurjar"). This is safe here
    because the comparison only ever runs between two names for the SAME seat.
    """
    from difflib import SequenceMatcher
    best = 0.0
    for ka in (False, True):
        for kb in (False, True):
            na, nb = _clean_person(a, ka), _clean_person(b, kb)
            if not na or not nb:
                continue
            if na == nb:
                return 1.0

            ta, tb = set(na.split()), set(nb.split())
            small, large = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
            if small and small <= large:
                # Guard against a lone common surname ("SINGH") matching anything.
                if len(small) >= 2 or max(len(t) for t in small) >= 6:
                    return 1.0

            # Space-insensitive: the sources disagree on where words break
            # ("Hema Malini" vs "Hemamalini", "Hasmukh Bhai" vs "Hasmukhbhai").
            ca, cb = na.replace(" ", ""), nb.replace(" ", "")
            if len(ca) >= 8 and len(cb) >= 8 and (ca in cb or cb in ca):
                return 1.0

            sa, sb = " ".join(sorted(ta)), " ".join(sorted(tb))
            best = max(best,
                       SequenceMatcher(None, na, nb).ratio(),
                       SequenceMatcher(None, sa, sb).ratio(),
                       SequenceMatcher(None, ca, cb).ratio())
    return best


SAME_PERSON_THRESHOLD = 0.82


def best_fuzzy_key(target_key: str, candidate_keys, min_ratio: float = 0.88,
                   min_margin: float = 0.04):
    """
    Closest candidate key, or None when the match is weak or ambiguous.

    Only keys sharing the same state prefix are considered, and the winner must
    beat the runner-up by `min_margin` - otherwise two similarly-named seats
    could silently swap.
    """
    from difflib import SequenceMatcher
    state, _, cons = target_key.partition("|")
    scored = []
    for k in candidate_keys:
        ks, _, kc = k.partition("|")
        if ks != state:
            continue
        scored.append((SequenceMatcher(None, cons, kc).ratio(), k))
    if not scored:
        return None
    scored.sort(reverse=True)
    best_ratio, best_key = scored[0]
    if best_ratio < min_ratio:
        return None
    if len(scored) > 1 and best_ratio - scored[1][0] < min_margin:
        return None
    return best_key, round(best_ratio, 3)
