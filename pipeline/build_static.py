"""
build_static.py - turn scraped JSONL into the dashboard's JSON payloads, locally.

This mirrors the silver + gold + export notebooks in plain Python so the site can be
developed and verified without a Databricks run. **The JSON schema is the contract
between the two paths** - if you change an aggregate here, change it in
databricks/02_silver_clean.py and 03_gold_marts.py too, and vice versa.

    python build_static.py --raw ../data/raw --out ../docs/data
"""
from __future__ import annotations

import argparse, io, json, pathlib, re, sys, statistics
from collections import defaultdict
from datetime import datetime, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

CRORE = 10_000_000

STATE_FIXES = {
    "Andaman And Nicobar Islands": "Andaman & Nicobar Islands",
    "Nct Of Delhi": "Delhi", "Delhi Nct": "Delhi",
    "National Capital Territory Of Delhi": "Delhi",
    "Nct": "Delhi",
    "Dadra & Nagar Haveli": "Dadra & Nagar Haveli and Daman & Diu",
    "Daman & Diu": "Dadra & Nagar Haveli and Daman & Diu",
    "Dadra & Nagar Haveli & Daman & Diu": "Dadra & Nagar Haveli and Daman & Diu",
    "Delhi (Nct)": "Delhi",
    "Orissa": "Odisha", "Pondicherry": "Puducherry",
    "Uttaranchal": "Uttarakhand", "Chattisgarh": "Chhattisgarh",
}

EDUCATION_RANK = {
    "Illiterate": 0, "Literate": 1, "5Th Pass": 2, "8Th Pass": 3, "10Th Pass": 4,
    "12Th Pass": 5, "Graduate": 6, "Diploma": 6, "Graduate Professional": 7,
    "Post Graduate": 8, "Doctorate": 9,
}


def titlecase(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().title()


def median(vals):
    vals = [v for v in vals if v is not None]
    return int(statistics.median(vals)) if vals else None


def pct(vals):
    """Percentage of truthy values, ignoring None."""
    vals = [v for v in vals if v is not None]
    return round(100 * sum(1 for v in vals if v) / len(vals), 1) if vals else None


def avg(vals, nd=1):
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), nd) if vals else None


# --------------------------------------------------------------------------
# silver: conform + enrich
# --------------------------------------------------------------------------

def to_silver(raw: list[dict]) -> list[dict]:
    out = []
    for r in raw:
        cons_raw = re.sub(r"\s+", " ", (r.get("constituency") or "")).strip()
        seat_cat = ("SC" if re.search(r"\(\s*SC\s*\)", cons_raw, re.I)
                    else "ST" if re.search(r"\(\s*ST\s*\)", cons_raw, re.I) else "GEN")
        cons = titlecase(re.sub(r"\s*\((sc|st)\)\s*$", "", cons_raw, flags=re.I))

        state = titlecase(r.get("state") or "")
        # \b matters: without it this eats the middle of "Andhra" and "Andaman".
        state = re.sub(r"\bAnd\b", "&", state)
        state = re.sub(r"\s+", " ", state).strip()
        state = STATE_FIXES.get(state, state)

        edu = titlecase(r.get("education") or "Not Given")
        rank = EDUCATION_RANK.get(edu)
        edu_group = ("Graduate or above" if rank is not None and rank >= 6
                     else "Class 5-12" if rank is not None and 2 <= rank <= 5
                     else "Illiterate / Literate" if rank is not None and rank <= 1
                     else "Not disclosed")

        assets = r.get("assets")
        liab = r.get("liabilities")
        age = r.get("age")
        age = age if isinstance(age, int) and 21 <= age <= 100 else None
        cases = r.get("criminal_cases") or 0

        out.append({
            "election_year": r["election_year"],
            "is_bye_election": bool(r.get("is_bye_election")),
            "state": state,
            "constituency": cons,
            "seat_category": seat_cat,
            "name": re.sub(r"\s+", " ", (r.get("name") or "")).strip(),
            "party": (r.get("party") or "UNKNOWN").upper().strip(),
            "criminal_cases": cases,
            "has_criminal_case": cases > 0,
            "education": edu,
            "education_rank": rank,
            "education_group": edu_group,
            "age": age,
            "age_band": ("Under 40" if age and age < 40 else "40-54" if age and age < 55
                         else "55-69" if age and age < 70 else "70+" if age else "Unknown"),
            "assets": assets,
            "liabilities": liab,
            "net_worth": (assets - (liab or 0)) if assets is not None else None,
            "is_crorepati": (assets >= CRORE) if assets is not None else None,
            "is_winner": bool(r.get("is_winner")),
            "candidate_id": r.get("candidate_id"),
        })

    # party_group: anything that has ever won 5+ seats in one election keeps its name
    wins = defaultdict(int)
    for r in out:
        if r["is_winner"]:
            wins[(r["election_year"], r["party"])] += 1
    major = {p for (_, p), n in wins.items() if n >= 5}
    for r in out:
        r["party_group"] = ("Independent" if r["party"] in ("IND", "INDEPENDENT")
                            else r["party"] if r["party"] in major else "Other parties")

    # dedupe (bye-elections can repeat a seat within a year)
    seen, deduped = set(), []
    for r in out:
        key = (r["election_year"], r["candidate_id"], r["name"], r["constituency"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


# --------------------------------------------------------------------------
# gold: aggregates
# --------------------------------------------------------------------------

def group(rows, *keys):
    g = defaultdict(list)
    for r in rows:
        g[tuple(r[k] for k in keys)].append(r)
    return g


def national(rows, label):
    out = []
    for (year,), rs in sorted(group(rows, "election_year").items()):
        out.append({
            "election_year": year, "cohort": label, "n": len(rs),
            "pct_criminal": pct([r["has_criminal_case"] for r in rs]),
            "pct_multi_case": pct([r["criminal_cases"] >= 2 for r in rs]),
            "pct_crorepati": pct([r["is_crorepati"] for r in rs]),
            "median_assets": median([r["assets"] for r in rs]),
            "avg_age": avg([r["age"] for r in rs]),
            "pct_graduate_plus": pct([(r["education_rank"] or -1) >= 6 for r in rs]),
            "avg_cases": avg([r["criminal_cases"] for r in rs], 2),
        })
    return out


def build_gold(silver):
    winners = [r for r in silver if r["is_winner"]]

    trends = national(silver, "All candidates") + national(winners, "Winners (MPs)")

    party = []
    for (year, pg), rs in group(silver, "election_year", "party_group").items():
        if len(rs) < 5:
            continue
        seats = sum(1 for r in rs if r["is_winner"])
        party.append({
            "election_year": year, "party_group": pg, "candidates": len(rs),
            "seats_won": seats,
            "strike_rate": round(100 * seats / len(rs), 1),
            "pct_criminal": pct([r["has_criminal_case"] for r in rs]),
            "pct_crorepati": pct([r["is_crorepati"] for r in rs]),
            "median_assets": median([r["assets"] for r in rs]),
            "avg_age": avg([r["age"] for r in rs]),
        })
    party.sort(key=lambda x: (x["election_year"], -x["seats_won"]))

    state = []
    for (year, st), rs in group(winners, "election_year", "state").items():
        if not st:
            continue
        state.append({
            "election_year": year, "state": st, "seats": len(rs),
            "pct_criminal": pct([r["has_criminal_case"] for r in rs]),
            "pct_crorepati": pct([r["is_crorepati"] for r in rs]),
            "median_assets": median([r["assets"] for r in rs]),
            "avg_age": avg([r["age"] for r in rs]),
            "total_cases": sum(r["criminal_cases"] for r in rs),
        })
    state.sort(key=lambda x: (x["election_year"], -x["seats"]))

    edu = [{"election_year": y, "education_group": g, "candidates": len(rs),
            "winners": sum(1 for r in rs if r["is_winner"])}
           for (y, g), rs in sorted(group(silver, "election_year", "education_group").items())]

    ages = [{"election_year": y, "age_band": b, "candidates": len(rs),
             "winners": sum(1 for r in rs if r["is_winner"])}
            for (y, b), rs in sorted(group(silver, "election_year", "age_band").items())]

    win_cols = ("election_year", "state", "constituency", "seat_category", "name",
                "party", "party_group", "criminal_cases", "education",
                "education_group", "age", "assets", "liabilities", "net_worth",
                "is_crorepati", "candidate_id")
    win_rows = sorted(({k: r[k] for k in win_cols} for r in winners),
                      key=lambda r: (r["election_year"], r["state"], r["constituency"]))

    boards = []
    lb_cols = ("election_year", "name", "party", "state", "constituency", "assets",
               "liabilities", "criminal_cases", "education", "age", "is_winner",
               "candidate_id")
    for label, key in [("Richest candidates", "assets"), ("Most declared cases", "criminal_cases")]:
        for (year,), rs in group(silver, "election_year").items():
            ranked = sorted((r for r in rs if r[key] is not None),
                            key=lambda r: -r[key])[:50]
            for i, r in enumerate(ranked, 1):
                boards.append({"board": label, "rank": i, **{k: r[k] for k in lb_cols}})
    boards.sort(key=lambda x: (x["board"], x["election_year"], x["rank"]))

    # Repeat candidates: same person across DIFFERENT elections.
    #
    # Two guards matter here. Requiring >= 2 *distinct* election years keeps
    # namesakes contesting the same election out - without it, two different
    # people sharing a name in one year become a single candidate whose wealth
    # appears to explode. And growth compares first election to last, not
    # min to max, which would cherry-pick the most dramatic pair.
    repeats = []
    for _, rs in group([r for r in silver if r["assets"] is not None], "state").items():
        by_name = defaultdict(list)
        for r in rs:
            by_name[re.sub(r"[^A-Z ]", "", r["name"].upper())].append(r)

        for _, apps in by_name.items():
            years = {r["election_year"] for r in apps}
            if len(years) < 2:
                continue
            # one record per election year (a candidate can appear twice in a
            # year via a bye-election); keep the higher declaration
            per_year = {}
            for r in apps:
                y = r["election_year"]
                if y not in per_year or (r["assets"] or 0) > (per_year[y]["assets"] or 0):
                    per_year[y] = r
            ordered = [per_year[y] for y in sorted(per_year)]
            first, last = ordered[0], ordered[-1]
            fa, la = first["assets"], last["assets"]

            repeats.append({
                "name": last["name"], "state": last["state"],
                "elections_contested": len(ordered),
                "elections_won": sum(1 for r in ordered if r["is_winner"]),
                "first_year": first["election_year"],
                "last_year": last["election_year"],
                "min_assets": fa, "max_assets": la,
                "peak_cases": max(r["criminal_cases"] for r in ordered),
                "asset_growth_pct": round(100 * (la - fa) / fa) if fa and fa > 0 else None,
            })
    repeats.sort(key=lambda x: (x["asset_growth_pct"] is None, -(x["asset_growth_pct"] or 0)))

    return {"national_trends": trends, "party_summary": party, "state_summary": state,
            "education_dist": edu, "age_dist": ages, "winners": win_rows,
            "leaderboards": boards, "repeat_candidates": repeats[:500]}


def build_mplads(raw_dir: "pathlib.Path", silver, generated: str):
    """
    Join eSAKSHI MPLADS rows (current Lok Sabha term) to the most recent
    election's winners by normalised state+constituency.

    The join is deliberately loud about its failure modes: seats where the
    sitting MP's name no longer matches the general-election winner (bye-
    elections, resignations) are listed in `mp_changed`, and constituencies
    that failed to join at all are listed in `unmatched`. Those lists are
    shipped in the payload - the dashboard shows them rather than hiding them.
    """
    import pathlib
    src = pathlib.Path(raw_dir) / "mplads.jsonl"
    nat_src = pathlib.Path(raw_dir) / "mplads_national.json"
    if not src.exists():
        return None

    from mplads import (norm_key, name_similarity, best_fuzzy_key,
                        SAME_PERSON_THRESHOLD)

    # eSAKSHI still uses some pre-rename state spellings; alias them onto the
    # conformed names used everywhere else BEFORE keying, or whole states
    # silently fail to join.
    ESAKSHI_STATE_ALIASES = {
        "ORISSA": "Odisha", "PONDICHERRY": "Puducherry",
        "UTTARANCHAL": "Uttarakhand", "NCT OF DELHI": "Delhi",
        "DADRA AND NAGAR HAVELI": "Dadra & Nagar Haveli and Daman & Diu",
        "DAMAN AND DIU": "Dadra & Nagar Haveli and Daman & Diu",
        "DADRA AND NAGAR HAVELI AND DAMAN AND DIU":
            "Dadra & Nagar Haveli and Daman & Diu",
    }

    mrows = [json.loads(l) for l in src.read_text(encoding="utf-8").splitlines() if l.strip()]
    for m in mrows:
        m["state"] = ESAKSHI_STATE_ALIASES.get(m["state"].strip().upper(), m["state"])
    national = json.loads(nat_src.read_text(encoding="utf-8")) if nat_src.exists() else {}

    latest = max(r["election_year"] for r in silver)
    winners = {norm_key(r["state"], r["constituency"]): r
               for r in silver if r["is_winner"] and r["election_year"] == latest}

    # eSAKSHI lists some seats twice - a stale row carrying no allocation
    # alongside the live one. Keep the row that actually has money against it.
    by_seat = defaultdict(list)
    for m in mrows:
        by_seat[(m["state"], m["constituency"])].append(m)
    deduped, dropped_dupes = [], 0
    for _, rs in by_seat.items():
        if len(rs) > 1:
            rs = sorted(rs, key=lambda r: (r.get("allocated") or 0,
                                           r.get("expenditure") or 0), reverse=True)
            dropped_dupes += len(rs) - 1
        deduped.append(rs[0])
    mrows = deduped

    joined, unmatched, mp_changed, fuzzy_joins = [], [], [], []
    for m in mrows:
        key = norm_key(m["state"], m["constituency"])
        w = winners.get(key)
        if not w:
            # Fall back to a conservative fuzzy match within the same state,
            # and record it so the reader can audit every approximate join.
            hit = best_fuzzy_key(key, winners.keys())
            if hit:
                fk, ratio = hit
                w = winners[fk]
                fuzzy_joins.append({
                    "state": m["state"], "mplads_name": m["constituency"],
                    "matched_to": w["constituency"], "similarity": ratio,
                })
        row = {
            "state": m["state"].title(), "constituency": m["constituency"].title(),
            "mp_name": m["mp_name"], "allocated": m["allocated"],
            "expenditure": m["expenditure"], "pct_spent": m["pct_spent"],
            "works_recommended": m["works_recommended"],
            "works_sanctioned": m["works_sanctioned"],
            "works_completed": m["works_completed"],
        }
        if w:
            row.update({
                "state": w["state"], "constituency": w["constituency"],
                "party": w["party"], "party_group": w["party_group"],
                "criminal_cases": w["criminal_cases"], "assets": w["assets"],
                "candidate_id": w["candidate_id"], "winner_2024": w["name"],
            })
            # Only flag a genuine change of MP. The two sources spell the same
            # person differently often enough ("P V Midhun Reddy" vs "Midhun
            # Reddy", "Purandeshwari" vs "Purandheshwari") that a strict compare
            # would claim ~120 seats changed hands when almost none did.
            sim = name_similarity(w["name"], m["mp_name"])
            if sim < SAME_PERSON_THRESHOLD:
                row["mp_differs_from_winner"] = True
                mp_changed.append({"constituency": w["constituency"], "state": w["state"],
                                   "winner_2024": w["name"], "current_mp": m["mp_name"],
                                   "name_similarity": round(sim, 2)})
        else:
            unmatched.append({"state": m["state"], "constituency": m["constituency"],
                              "mp_name": m["mp_name"]})
        joined.append(row)

    with_alloc = [r for r in joined if r.get("allocated")]
    by_state = defaultdict(list)
    for r in with_alloc:
        by_state[r["state"]].append(r)
    state_util = sorted(
        ({"state": st,
          "seats": len(rs),
          "allocated": sum(r["allocated"] or 0 for r in rs),
          "expenditure": sum(r["expenditure"] or 0 for r in rs),
          "pct_spent": round(100 * sum(r["expenditure"] or 0 for r in rs)
                             / sum(r["allocated"] or 0 for r in rs), 1),
          "works_completed": sum(r["works_completed"] or 0 for r in rs)}
         for st, rs in by_state.items() if sum(r["allocated"] or 0 for r in rs) > 0),
        key=lambda x: -x["pct_spent"])

    match_rate = round(100 * (len(joined) - len(unmatched)) / len(joined), 1) if joined else 0

    # ---- reconciliation against an independent rendering of the same scheme ----
    reconciliation = None
    cc_path = pathlib.Path(raw_dir) / "mplads_crosscheck.json"
    if cc_path.exists():
        cc = json.loads(cc_path.read_text(encoding="utf-8"))

        def unwrap(v):
            """Tolerate both the raw {success, data} envelope and the unwrapped form."""
            if isinstance(v, dict) and "data" in v and set(v) <= {"success", "data",
                                                                 "cached", "cache_timestamp"}:
                return v["data"] or {}
            return v or {}

        a = unwrap(cc.get("audit"))
        sync = unwrap(cc.get("sync"))
        ours_alloc = sum(r["allocated"] or 0 for r in joined)
        ours_spend = sum(r["expenditure"] or 0 for r in joined)
        ours_done = sum(r["works_completed"] or 0 for r in joined)
        ours_reco = sum(r["works_recommended"] or 0 for r in joined)

        def cmp(label, ours, theirs, note=""):
            if not ours or not theirs:
                return None
            delta = ours - theirs
            return {"metric": label, "ours": ours, "theirs": theirs, "delta": delta,
                    "pct_delta": round(100 * delta / theirs, 2) if theirs else None,
                    "note": note}

        reconciliation = {
            "source_name": "Empowered Indian",
            "source_url": "https://empoweredindian.in/mplads",
            "their_last_updated": sync.get("lastUpdated"),
            "their_update_frequency": sync.get("updateFrequency"),
            "their_quality_claim": sync.get("dataQuality"),
            "scope_note": (
                f"They publish both Houses ({a.get('records_total')} MPs = "
                f"{a.get('records_lok_sabha')} Lok Sabha + {a.get('records_rajya_sabha')} "
                f"Rajya Sabha). Only their Lok Sabha subset is comparable to this page."
            ),
            "their_internal_inconsistency": {
                "records": a.get("records_completed_exceeds_recommended"),
                "pct": a.get("pct_inconsistent"),
                "description": ("rows where completed works exceed recommended works, "
                                "producing a negative 'pending works' count"),
            },
            "comparisons": [c for c in [
                cmp("Allocated to Lok Sabha MPs", ours_alloc, a.get("ls_allocated"),
                    "Agreement here is the strongest signal that both readings of the "
                    "official portal are sound."),
                cmp("Expenditure", ours_spend, a.get("ls_expenditure"),
                    "Small gaps are expected: the portal is live and the two snapshots "
                    "were taken at different moments."),
                cmp("Works completed", ours_done, a.get("ls_works_completed"),
                    "Counts may be scoped differently by term."),
                cmp("Works recommended", ours_reco, a.get("ls_works_recommended"),
                    "Large gaps here indicate a definitional difference, not an error - "
                    "the portal's own tile counts recommendations differently from a "
                    "per-MP sum. Do not read this as one source being wrong."),
            ] if c],
        }

    return {
        "meta": {
            "generated_at": generated,
            "tenure": national.get("tenure") or (mrows[0]["tenure"] if mrows else ""),
            "source": "MPLADS eSAKSHI portal (mplads.mospi.gov.in), Ministry of Statistics and Programme Implementation",
            "joined_to": f"Lok Sabha {latest} winners by state + constituency",
            "match_rate_pct": match_rate,
            "duplicate_rows_dropped": dropped_dupes,
            "fuzzy_join_count": len(fuzzy_joins),
            "caveats": [
                "eSAKSHI tracks the revised MPLADS procedure from 1 April 2023 onward; figures cover the current term only and its 'allocated limit' includes balances carried forward from a seat's previous MP - it is not the full-term entitlement.",
                "The portal is live; these numbers are a dated snapshot and will differ from the portal on any later day.",
                "MPs only RECOMMEND works. Funds go to district authorities, who sanction and execute - low expenditure is not by itself MP inaction, and early-term percentages are structurally low.",
                "Lok Sabha MPs only; Rajya Sabha MPLADS entitlements are excluded, so totals here will not match scheme-wide figures.",
                f"Rows are joined to election data by constituency name; {match_rate}% matched. "
                f"The two sources transliterate names differently, so {len(fuzzy_joins)} seats "
                f"were matched approximately and {dropped_dupes} duplicate rows were dropped. "
                f"Every approximate match, every unmatched seat, and every seat whose sitting "
                f"MP differs from the 2024 winner is listed below rather than hidden.",
            ],
        },
        "national": national,
        "mps": sorted(joined, key=lambda r: (-(r.get("pct_spent") or 0),
                                             r["state"], r["constituency"])),
        "state_utilization": state_util,
        "mp_changed": mp_changed,
        "unmatched": unmatched,
        "fuzzy_joins": fuzzy_joins,
        "reconciliation": reconciliation,
    }


def build_state_slices(silver):
    """
    Per-state versions of every aggregate the page shows, so the State/UT filter
    can re-scope the whole dashboard - KPIs, trends, education, age, parties -
    not just the winners table. Kept in a separate states.json so the national
    summary stays small for first paint.
    """
    named = [r for r in silver if r["state"]]

    trends = []
    for (st,), rs in group(named, "state").items():
        for label, subset in (("All candidates", rs),
                              ("Winners (MPs)", [r for r in rs if r["is_winner"]])):
            for row in national(subset, label):
                row["state"] = st
                trends.append(row)
    trends.sort(key=lambda r: (r["state"], r["cohort"], r["election_year"]))

    party = []
    for (year, st, pg), rs in group(named, "election_year", "state", "party_group").items():
        seats = sum(1 for r in rs if r["is_winner"])
        if len(rs) < 5 and seats == 0:
            continue  # keep the payload small: skip micro-parties that won nothing
        party.append({
            "election_year": year, "state": st, "party_group": pg,
            "candidates": len(rs), "seats_won": seats,
            "pct_criminal": pct([r["has_criminal_case"] for r in rs]),
            "median_assets": median([r["assets"] for r in rs]),
        })
    party.sort(key=lambda x: (x["state"], x["election_year"], -x["seats_won"]))

    edu = [{"election_year": y, "state": st, "education_group": g,
            "candidates": len(rs), "winners": sum(1 for r in rs if r["is_winner"])}
           for (y, st, g), rs in sorted(
               group(named, "election_year", "state", "education_group").items())]

    ages = [{"election_year": y, "state": st, "age_band": b,
             "candidates": len(rs), "winners": sum(1 for r in rs if r["is_winner"])}
            for (y, st, b), rs in sorted(
                group(named, "election_year", "state", "age_band").items())]

    return {"state_trends": trends, "party_by_state": party,
            "education_by_state": edu, "age_by_state": ages}


# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default="../data/raw")
    ap.add_argument("--out", default="../docs/data")
    args = ap.parse_args()

    raw_dir = pathlib.Path(args.raw).resolve()
    out_dir = pathlib.Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    files = sorted(raw_dir.glob("candidates_[0-9]*.jsonl"))
    if not files:
        sys.exit(f"no candidates_YYYY.jsonl found in {raw_dir} - run scrape_local.py first")

    raw = []
    for f in files:
        n = 0
        with f.open(encoding="utf-8") as fh:
            for line in fh:
                if line.strip():
                    raw.append(json.loads(line))
                    n += 1
        print(f"  read {f.name:<28} {n:>6} rows")

    silver_all = to_silver(raw)

    # Aggregates cover GENERAL elections only. Bye-elections held years later are
    # listed on the same source sites; leaving them in would count a 2017 contest
    # inside the "2014" trend lines. They stay in the CSV, flagged.
    silver = [r for r in silver_all if not r["is_bye_election"]]
    n_bye = len(silver_all) - len(silver)

    gold = build_gold(silver)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    years = sorted({r["election_year"] for r in silver})

    meta = {
        "generated_at": generated,
        "source": "myneta.info - Association for Democratic Reforms (ADR) / National Election Watch",
        "source_url": "https://www.myneta.info/",
        "underlying_source": "Candidate affidavits filed with the Election Commission of India",
        "election_years": years,
        "total_candidates": len(silver),
        "total_winners": sum(1 for r in silver if r["is_winner"]),
        "bye_election_rows_excluded": n_bye,
        "pipeline": "local build (mirrors the Databricks medallion job)",
        "licence_note": ("Data is republished from ADR/NEW, who compile it from Election "
                         "Commission affidavits. Figures are candidate self-declarations, "
                         "not audited assessments."),
    }

    payloads = {
        "meta.json": meta,
        "summary.json": {"meta": meta, **{k: gold[k] for k in
                         ("national_trends", "party_summary", "state_summary",
                          "education_dist", "age_dist")}},
        "winners.json": {"meta": {"generated_at": generated}, "winners": gold["winners"]},
        "leaderboards.json": {"meta": {"generated_at": generated}, "entries": gold["leaderboards"]},
        "repeat_candidates.json": {"meta": {"generated_at": generated},
                                   "candidates": gold["repeat_candidates"]},
        "states.json": {"meta": {"generated_at": generated},
                        **build_state_slices(silver)},
    }

    mplads = build_mplads(raw_dir, silver, generated)
    if mplads:
        payloads["mplads.json"] = mplads
    else:
        print("  (no mplads.jsonl in raw dir - skipping MPLADS payload)")

    print()
    for fname, payload in payloads.items():
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        (out_dir / fname).write_text(body, encoding="utf-8")
        print(f"  wrote {fname:<26} {len(body)/1024:>8.1f} KB")

    # flat CSV of the tidy dataset - the reusable artefact. Bye-election rows are
    # included here (flagged) even though the dashboard aggregates exclude them.
    import csv
    cols = ["election_year", "state", "constituency", "seat_category", "name", "party",
            "party_group", "criminal_cases", "education", "age", "assets",
            "liabilities", "net_worth", "is_winner", "is_bye_election"]
    with (out_dir / "candidates.csv").open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(silver_all, key=lambda r: (r["election_year"], r["state"],
                                                      r["constituency"], not r["is_winner"])))
    print(f"  wrote {'candidates.csv':<26} "
          f"{(out_dir / 'candidates.csv').stat().st_size/1024:>8.1f} KB")

    print(f"\n{len(silver)} general-election candidates | {meta['total_winners']} winners "
          f"| {n_bye} bye-election rows kept in CSV only | years {years}")


if __name__ == "__main__":
    main()
