"""
Local scrape runner - the same code path the Databricks bronze job uses.

Writes one JSONL file per election year plus a combined file. Pages are cached
on disk, so a re-run after an interruption resumes for free.

    python scrape_local.py --years 2024 --out ../data/raw --cache ../.cache
    python scrape_local.py --all --out ../data/raw --cache ../.cache
"""
import argparse, io, json, pathlib, sys, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from myneta import LOK_SABHA_SITES, Fetcher, scrape_year, write_jsonl


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", nargs="*", type=int, default=[])
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out", default="../data/raw")
    ap.add_argument("--cache", default="../.cache")
    ap.add_argument("--delay", type=float, default=1.0)
    ap.add_argument("--limit", type=int, default=None,
                    help="constituencies per year (smoke tests)")
    args = ap.parse_args()

    years = sorted(LOK_SABHA_SITES) if args.all else sorted(args.years)
    if not years:
        ap.error("pass --years 2024 [2019 ...] or --all")

    out_dir = pathlib.Path(args.out).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    fetcher = Fetcher(cache_dir=args.cache, delay=args.delay)

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    combined, summary = [], []
    for year in years:
        log(f"=== {year} :: {LOK_SABHA_SITES[year]}")
        t0 = time.time()
        rows = scrape_year(year, fetcher, limit=args.limit, progress=log)
        path = out_dir / f"candidates_{year}.jsonl"
        write_jsonl(rows, path)
        combined.extend(rows)

        winners = sum(1 for r in rows if r.is_winner)
        with_assets = sum(1 for r in rows if r.assets is not None)
        stat = {
            "year": year,
            "candidates": len(rows),
            "winners": winners,
            "constituencies": len({r.constituency_id for r in rows}),
            "states": len({r.state for r in rows if r.state}),
            "with_assets": with_assets,
            "asset_coverage_pct": round(100 * with_assets / len(rows), 1) if rows else 0,
            "elapsed_sec": round(time.time() - t0, 1),
        }
        summary.append(stat)
        log(f"    -> {json.dumps(stat)}")
        log(f"    -> wrote {path}")

    if len(years) > 1:
        write_jsonl(combined, out_dir / "candidates_all.jsonl")
        log(f"=== combined: {len(combined)} rows -> candidates_all.jsonl")

    (out_dir / "scrape_summary.json").write_text(
        json.dumps(summary, indent=2), encoding="utf-8")
    log("=== SCRAPE COMPLETE")
    for s in summary:
        log(f"    {s['year']}: {s['candidates']:>5} candidates, "
            f"{s['winners']:>3} winners, {s['states']:>2} states, "
            f"assets {s['asset_coverage_pct']}%")


if __name__ == "__main__":
    main()
