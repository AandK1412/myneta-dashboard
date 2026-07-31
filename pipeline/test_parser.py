"""Validate the parser against real cached pages before running a long scrape."""
import sys, pathlib, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from myneta import (parse_rupees, parse_int, clean, parse_constituency_page,
                    discover_constituency_ids)

CACHE = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None
fails = []


def check(label, got, want):
    ok = got == want
    if not ok:
        fails.append(f"{label}: got {got!r} want {want!r}")
    print(f"  [{'ok ' if ok else 'FAIL'}] {label:<44} -> {got!r}")


print("=" * 72)
print("1. money parsing")
print("=" * 72)
check("crore value", parse_rupees("Rs 3,09,16,833 ~ 3 Crore+"), 30916833)
check("lakh value", parse_rupees("Rs 29,01,575 ~ 29 Lacs+"), 2901575)
check("zero", parse_rupees("Rs 0 ~"), 0)
check("blank (2004 gaps)", parse_rupees(""), None)
check("nil = declared zero", parse_rupees("Nil"), 0)
check("dashes = no data", parse_rupees("--"), None)
check("not given = no data", parse_rupees("Not Given"), None)
check("nbsp-separated", parse_rupees("Rs&nbsp;23,54,678 ~ 23&nbsp;Lacs+"), 2354678)
check("big crore", parse_rupees("Rs 56,81,54,912 ~ 56 Crore+"), 568154912)

print()
print("=" * 72)
print("2. misc cell parsing")
print("=" * 72)
check("age", parse_int("31"), 31)
check("age blank", parse_int(""), None)
check("crime count", parse_int("52"), 52)
check("entity strip", clean("Gumma Thanuja Rani &nbsp&nbsp Winner "), "Gumma Thanuja Rani Winner")

if not CACHE or not CACHE.exists():
    print("\n(no cache dir given - skipping page tests)")
    sys.exit(1 if fails else 0)

print()
print("=" * 72)
print("3. real constituency pages")
print("=" * 72)
cases = [
    ("cons1.html", 2024, 1, "ARAKU"),
    ("cons_2014_1.html", 2014, 1, "ADILABAD"),
    ("cons_2004_1.html", 2004, 1, "ANDAMAN"),
]
for fname, year, cid, expect_cons in cases:
    p = CACHE / fname
    if not p.exists():
        print(f"  -- {fname} not cached, skipping")
        continue
    rows = parse_constituency_page(p.read_text(encoding="utf-8", errors="replace"), year, cid)
    winners = [r for r in rows if r.is_winner]
    print(f"\n  {fname}  year={year}")
    print(f"    candidates : {len(rows)}")
    print(f"    winners    : {len(winners)}  <- must be exactly 1")
    if rows:
        r0 = rows[0]
        print(f"    state      : {r0.state!r}")
        print(f"    constituency: {r0.constituency!r}")
        print(f"    with assets: {sum(1 for r in rows if r.assets is not None)}/{len(rows)}")
        print(f"    with age   : {sum(1 for r in rows if r.age is not None)}/{len(rows)}")
        print(f"    cand_ids   : {sum(1 for r in rows if r.candidate_id)}/{len(rows)}")
        w = winners[0] if winners else None
        if w:
            print(f"    WINNER     : {w.name} ({w.party}) assets={w.assets} age={w.age} crime={w.criminal_cases}")
    if len(winners) != 1:
        fails.append(f"{fname}: expected exactly 1 winner, got {len(winners)}")
    if expect_cons not in (rows[0].constituency.upper() if rows else ""):
        fails.append(f"{fname}: constituency {rows[0].constituency!r} missing {expect_cons!r}" if rows else f"{fname}: no rows")
    if not rows:
        fails.append(f"{fname}: parsed zero candidates")

print()
print("=" * 72)
print("4. id discovery")
print("=" * 72)
for fname, year in [("home2024.html", 2024), ("home2014.html", 2014), ("home2004.html", 2004)]:
    p = CACHE / fname
    if p.exists():
        ids = discover_constituency_ids(p.read_text(encoding="utf-8", errors="replace"))
        print(f"  {year}: {len(ids)} constituency ids (min={min(ids)}, max={max(ids)})")
        if len(ids) < 500:
            fails.append(f"{year}: only {len(ids)} ids, expected ~543")

print()
print("=" * 72)
if fails:
    print(f"FAILURES ({len(fails)}):")
    for f in fails:
        print(f"  - {f}")
    sys.exit(1)
print("ALL CHECKS PASSED")
