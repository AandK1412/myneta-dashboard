"""
myneta.py - fetch and parse candidate data from myneta.info (ADR / National Election Watch).

Stdlib only, on purpose: this module runs unchanged on a laptop and on Databricks
serverless compute, where installing third-party wheels is friction we don't need.

The unit of extraction is a constituency page:
    index.php?action=show_candidates&constituency_id=N

which yields every candidate who contested that seat, with age, party, education,
declared criminal cases, assets and liabilities - plus an explicit "Winner" marker.
That is strictly more than the winners-only summary page exposes.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict, field

# --------------------------------------------------------------------------
# Election sites. Each Lok Sabha year is a separate PHP site with its own
# constituency_id space, so ids are only meaningful within a year.
# --------------------------------------------------------------------------

LOK_SABHA_SITES = {
    2004: "https://www.myneta.info/LokSabha2004/",
    2009: "https://www.myneta.info/ls2009/",
    2014: "https://www.myneta.info/ls2014/",
    2019: "https://www.myneta.info/LokSabha2019/",
    2024: "https://www.myneta.info/LokSabha2024/",
}

USER_AGENT = (
    "myneta-public-dashboard/1.0 (+https://github.com/) "
    "civic data project; contact via repo issues"
)

# myneta.info/robots.txt disallows only ?print=true / ?printer=true URLs.
# Everything below stays well clear of those and rate-limits itself.
DEFAULT_DELAY_SEC = 1.0


# --------------------------------------------------------------------------
# HTML helpers
# --------------------------------------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_ENTITIES = {
    "&nbsp;": " ", "&nbsp": " ", "&amp;": "&", "&quot;": '"',
    "&#39;": "'", "&apos;": "'", "&lt;": "<", "&gt;": ">", "&nabla;": "",
}


def clean(fragment: str) -> str:
    """Strip tags/entities from an HTML fragment and collapse whitespace."""
    if fragment is None:
        return ""
    text = _TAG_RE.sub(" ", fragment)
    for ent, rep in _ENTITIES.items():
        text = text.replace(ent, rep)
    text = re.sub(r"&#\d+;", " ", text)
    return _WS_RE.sub(" ", text).strip()


def parse_rupees(cell: str) -> int | None:
    """
    'Rs 3,09,16,833 ~ 3 Crore+'  -> 30916833
    'Rs 0 ~'                     -> 0
    'Nil'                        -> 0     (an explicit declaration of zero)
    '' / '--' / 'Not Given'      -> None  (no data - ADR published no figure)

    That distinction matters downstream: averaging assets must skip the Nones
    rather than treat them as zero, or every seat with an unanalysed candidate
    drags its mean down.

    Note the Indian digit grouping (crore/lakh); stripping commas handles it.
    """
    text = clean(cell)
    if not text:
        return None
    if re.fullmatch(r"nil", text, re.I):
        return 0
    if re.fullmatch(r"(none|n/?a|-+|not given)", text, re.I):
        return None
    m = re.search(r"Rs\.?\s*([\d,]+)", text, re.I) or re.search(r"([\d,]{2,})", text)
    if not m:
        return None
    digits = m.group(1).replace(",", "")
    return int(digits) if digits.isdigit() else None


def parse_int(cell: str) -> int | None:
    text = clean(cell)
    m = re.search(r"\d+", text)
    return int(m.group(0)) if m else None


def normalise_education(cell: str) -> str:
    text = clean(cell)
    if not text or re.fullmatch(r"(-+|n/?a)", text, re.I):
        return "Not Given"
    return text


# --------------------------------------------------------------------------
# Record
# --------------------------------------------------------------------------

@dataclass
class Candidate:
    election_year: int
    state: str
    constituency: str
    constituency_id: int
    candidate_id: int | None
    name: str
    party: str
    criminal_cases: int | None
    education: str
    age: int | None
    assets: int | None
    liabilities: int | None
    is_winner: bool
    # True when this row comes from a bye-election page. Each year's site also
    # lists bye-polls held long after the general election (a 2017 contest on
    # the 2014 site), so these must be separable or they pollute the trends.
    is_bye_election: bool = False
    source_url: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------

# 'List of Candidates in ARAKU (ST) : ANDHRA PRADESH Lok Sabha 2024'
_HEADER_RE = re.compile(
    r"List of Candidates\s+in\s+(?P<cons>.+?)\s*:\s*(?P<state>.+?)\s+Lok\s*Sabha\s*(?P<year>\d{4})",
    re.I,
)
# Older sites phrase it as 'List of Candidates - ARAKU (ST):ANDHRA PRADESH ( ... )'
_HEADER_RE_ALT = re.compile(
    r"List of Candidates\s*[-in]*\s*(?P<cons>[^:<]+?)\s*:\s*(?P<state>[^(<]+)",
    re.I,
)

_ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL_RE = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_TABLE_RE = re.compile(r"<table.*?</table>", re.S | re.I)
_CAND_ID_RE = re.compile(r"candidate_id=(\d+)")
_WINNER_RE = re.compile(r"winner", re.I)

# Column order on the constituency page:
# SNo | Candidate | Party | Criminal Cases | Education | Age | Total Assets | Liabilities
COL_NAME, COL_PARTY, COL_CRIME, COL_EDU, COL_AGE, COL_ASSETS, COL_LIAB = 1, 2, 3, 4, 5, 6, 7
EXPECTED_COLS = 8


def parse_constituency_page(html: str, year: int, constituency_id: int,
                            source_url: str = "") -> list[Candidate]:
    """Extract every candidate row from one constituency page."""
    header = _HEADER_RE.search(html) or _HEADER_RE_ALT.search(html)
    if header:
        constituency = clean(header.group("cons"))
        state = clean(header.group("state"))
        state = re.sub(r"\s*Lok\s*Sabha.*$", "", state, flags=re.I).strip()
    else:
        constituency, state = "", ""

    # Bye-election pages phrase the header as
    #   'CONSTITUENCY : BYE ELECTION ON 04-02-2017 : STATE'
    # so the captured "state" starts with the bye label and the real state sits
    # after the final colon. Extract it and flag the page.
    is_bye = False
    if re.match(r"BYE[\s-]*ELECTION", state, re.I):
        is_bye = True
        parts = [p.strip() for p in state.split(":") if p.strip()]
        state = parts[-1] if parts else ""
        if re.match(r"BYE[\s-]*ELECTION", state, re.I):
            state = ""  # header had no state segment at all

    # The candidate table is the one with the most 8-column rows.
    best: list[str] = []
    for table in _TABLE_RE.findall(html):
        rows = [r for r in _ROW_RE.findall(table)
                if len(_CELL_RE.findall(r)) == EXPECTED_COLS]
        if len(rows) > len(best):
            best = rows

    out: list[Candidate] = []
    for row in best:
        cells = _CELL_RE.findall(row)
        name = clean(cells[COL_NAME])
        # The winner marker lives in the name cell as a green "Winner" label.
        name = re.sub(r"\s*Winner\s*$", "", name, flags=re.I).strip()
        if not name:
            continue
        # Skip header-ish or aggregate rows that slipped through.
        if name.lower() in {"candidate", "sno", "total"}:
            continue

        cid_m = _CAND_ID_RE.search(cells[COL_NAME])
        out.append(Candidate(
            election_year=year,
            state=state,
            constituency=constituency,
            constituency_id=constituency_id,
            candidate_id=int(cid_m.group(1)) if cid_m else None,
            name=name,
            party=clean(cells[COL_PARTY]) or "Unknown",
            criminal_cases=parse_int(cells[COL_CRIME]),
            education=normalise_education(cells[COL_EDU]),
            age=parse_int(cells[COL_AGE]),
            assets=parse_rupees(cells[COL_ASSETS]),
            liabilities=parse_rupees(cells[COL_LIAB]),
            is_winner=bool(_WINNER_RE.search(cells[COL_NAME])),
            is_bye_election=is_bye,
            source_url=source_url,
        ))
    return out


def discover_constituency_ids(home_html: str) -> list[int]:
    """Every constituency_id linked from a year's homepage."""
    return sorted({int(x) for x in re.findall(r"constituency_id=(\d+)", home_html)})


# --------------------------------------------------------------------------
# Fetching
# --------------------------------------------------------------------------

class Fetcher:
    """Polite HTTP with retries and an optional on-disk cache."""

    def __init__(self, cache_dir=None, delay: float = DEFAULT_DELAY_SEC,
                 max_retries: int = 3, timeout: int = 60):
        self.cache_dir = cache_dir
        self.delay = delay
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_request = 0.0
        if cache_dir:
            import pathlib
            pathlib.Path(cache_dir).mkdir(parents=True, exist_ok=True)

    def _cache_path(self, key: str):
        import pathlib
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", key)[:120]
        return pathlib.Path(self.cache_dir) / f"{safe}.html"

    def get(self, url: str, cache_key: str | None = None) -> str:
        if self.cache_dir and cache_key:
            p = self._cache_path(cache_key)
            if p.exists() and p.stat().st_size > 0:
                return p.read_text(encoding="utf-8", errors="replace")

        last_err = None
        for attempt in range(self.max_retries):
            # Rate limit against the *previous* request, not wall-clock start.
            gap = time.time() - self._last_request
            if gap < self.delay:
                time.sleep(self.delay - gap)
            try:
                req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    html = r.read().decode("utf-8", errors="replace")
                self._last_request = time.time()
                if self.cache_dir and cache_key:
                    self._cache_path(cache_key).write_text(html, encoding="utf-8")
                return html
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
                last_err = e
                self._last_request = time.time()
                time.sleep(2 ** attempt)  # back off: 1s, 2s, 4s
        raise RuntimeError(f"failed after {self.max_retries} attempts: {url} ({last_err})")


def scrape_year(year: int, fetcher: Fetcher, limit: int | None = None,
                progress=None) -> list[Candidate]:
    """Scrape every constituency for one Lok Sabha year."""
    base = LOK_SABHA_SITES[year]
    home = fetcher.get(base, cache_key=f"home_{year}")
    ids = discover_constituency_ids(home)
    if limit:
        ids = ids[:limit]

    rows: list[Candidate] = []
    for i, cid in enumerate(ids, 1):
        url = f"{base}index.php?action=show_candidates&constituency_id={cid}"
        try:
            html = fetcher.get(url, cache_key=f"c_{year}_{cid}")
            rows.extend(parse_constituency_page(html, year, cid, url))
        except Exception as e:  # one bad seat shouldn't kill a 543-seat run
            if progress:
                progress(f"  !! {year} cid={cid} failed: {e}")
        if progress and (i % 25 == 0 or i == len(ids)):
            progress(f"  {year}: {i}/{len(ids)} constituencies, {len(rows)} candidates")
    return rows


def write_jsonl(rows: list[Candidate], path) -> None:
    import pathlib
    p = pathlib.Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
