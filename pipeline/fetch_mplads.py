"""Fetch MPLADS current-tenure data -> data/raw/mplads.jsonl (+ national tiles).

    python fetch_mplads.py --out ../data/raw --cache ../.cache/mplads
"""
import argparse, io, json, pathlib, sys, time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.path.insert(0, str(pathlib.Path(__file__).parent))

from mplads import Api, fetch_all
import mplads_ei


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="../data/raw")
    ap.add_argument("--cache", default="../.cache/mplads")
    ap.add_argument("--delay", type=float, default=0.7)
    ap.add_argument("--skip-crosscheck", action="store_true",
                    help="skip the Empowered Indian reconciliation fetch")
    args = ap.parse_args()

    out = pathlib.Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)

    def log(msg):
        print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)

    api = Api(cache_dir=args.cache, delay=args.delay)
    log("fetching MPLADS (eSAKSHI) - current Lok Sabha tenure")
    rows, national = fetch_all(api, progress=log)

    with (out / "mplads.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r.to_dict(), ensure_ascii=False) + "\n")
    (out / "mplads_national.json").write_text(
        json.dumps(national, ensure_ascii=False, indent=2), encoding="utf-8")

    with_data = sum(1 for r in rows if r.allocated)
    log(f"eSAKSHI: {len(rows)} MPs ({with_data} with allocation data) "
        f"-> mplads.jsonl / mplads_national.json  [{national.get('tenure')}]")

    if not args.skip_crosscheck:
        log("fetching Empowered Indian cross-check")
        ei = mplads_ei.fetch(progress=log)
        ei["audit"] = mplads_ei.audit(ei.get("mps") or [])
        # Their per-MP rows are only needed for the audit and state comparison;
        # drop the bulky list so the raw artefact stays small.
        ei_states = ei.pop("states", None)
        ei.pop("mps", None)
        ei["states"] = ei_states
        (out / "mplads_crosscheck.json").write_text(
            json.dumps(ei, ensure_ascii=False, indent=2), encoding="utf-8")
        a = ei.get("audit", {})
        log(f"cross-check: {a.get('records_total')} records "
            f"({a.get('records_lok_sabha')} LS), "
            f"{a.get('pct_inconsistent')}% internally inconsistent")

    log("DONE")


if __name__ == "__main__":
    main()
