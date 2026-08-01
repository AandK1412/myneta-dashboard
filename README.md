# Who Represents India

A public dashboard of Lok Sabha candidate affidavits, 2004–2024 — declared assets,
liabilities, pending criminal cases, education and age for **every candidate**, not just
the winners.

Data comes from [MyNeta](https://www.myneta.info/), the affidavit archive run by the
[Association for Democratic Reforms](https://adrindia.org/) and National Election Watch,
who transcribe the sworn affidavits candidates file with the Election Commission of India.

**Pipeline:** Databricks (bronze → silver → gold) → pre-aggregated JSON → GitHub Pages.

---

## Why the dashboard is static

The obvious design — a live Databricks dashboard embedded in a public page — does not
work, for two independent reasons:

| Approach | Why it fails |
|---|---|
| **Databricks Apps** | [Anonymous access is explicitly unsupported.](https://docs.databricks.com/aws/en/dev-tools/databricks-apps/permissions) Every viewer must authenticate as an account user. |
| **AI/BI dashboard embedding** | [Embedding for external users](https://docs.databricks.com/aws/en/ai-bi/admin/embed) requires your app to authenticate with a **service principal** and mint short-lived tokens. That needs a backend to hold the secret. GitHub Pages is static — there is nowhere to put one. |

So the pipeline precomputes every number the page needs and commits it as JSON. This is
not a workaround so much as the better design for public traffic: no warehouse spins up
when a visitor loads the page, the site costs nothing at any scale, and there is no
credential in the browser to leak.

Databricks does the part it is actually good at — ingestion, conforming twenty years of
drifting source data, and scheduled recomputation.

---

## Repository layout

```
pipeline/
  myneta.py          Scraper + parser. Stdlib only, so it runs unchanged
                     locally and on Databricks serverless.
  mplads.py          MPLADS (eSAKSHI) REST client - current-term fund
                     utilization per sitting MP. Stdlib only.
  fetch_mplads.py    MPLADS fetch runner -> data/raw/mplads.jsonl.
  scrape_local.py    Local/CI scrape runner -> JSONL, resumable via disk cache.
  build_static.py    Local mirror of the silver+gold+export notebooks,
                     plus the MPLADS join (docs/data/mplads.json).
  test_parser.py     Parser tests against real cached pages.

databricks/
  01_bronze_ingest.py   Scrape -> bronze Delta (raw, replayable)
  02_silver_clean.py    Conform 2004-2024, derive measures
  03_gold_marts.py      Presentation aggregates
  04_export_static.py   Gold -> JSON -> push to this repo via GitHub API
  databricks.yml        Asset Bundle: the four notebooks as one scheduled job

docs/                GitHub Pages root - the published site
  index.html
  css/style.css
  js/charts.js       Hand-rolled SVG charts, zero dependencies
  js/app.js
  data/              Generated. What the pipeline publishes.

.github/workflows/
  deploy.yml         Validate data, deploy Pages
  refresh-data.yml   Standalone scrape+build in Actions (no-Databricks path)
```

---

## Getting it running

### 1. Local, no Databricks

The fastest way to see it work. The scraper has no dependencies.

```bash
cd pipeline && python scrape_local.py --years 2024 --out ../data/raw --cache ../.cache
```

Then build the payloads and serve:

```bash
cd pipeline && python build_static.py --raw ../data/raw --out ../docs/data
```

```bash
cd docs && python -m http.server 8099
```

Open <http://localhost:8099>. Serve over HTTP rather than opening `index.html`
directly — `fetch()` is blocked on `file://` URLs.

A full five-year scrape is ~2,800 requests at one per second, so budget about an hour.
Pages are cached on disk, so an interrupted run resumes for free.

### 2. Publishing to GitHub Pages

Push the repo, then in **Settings → Pages** either:

- set **Source → GitHub Actions** (uses `deploy.yml`, which validates the data first), or
- set **Source → Deploy from a branch**, branch `main`, folder `/docs` — no CI needed.

### 3. Wiring up Databricks

Works on **Databricks Free Edition** (serverless, Unity Catalog included).

1. Add this repo as a Git folder: **Workspace → Repos → Add Repo**.
2. Create a GitHub fine-grained PAT with **Contents: read & write** on this repo, and
   store it as a secret:

```bash
databricks secrets create-scope myneta && databricks secrets put-secret myneta github_token
```

3. Run the notebooks in order (`01` → `04`), or deploy the whole job:

```bash
cd databricks && databricks bundle deploy -t dev
```

4. Set `github_repo` to `your-username/your-repo` in the job parameters. Leave it blank
   to write JSON to the Unity Catalog Volume without pushing.

The job's schedule ships **paused**. Run it by hand once, confirm the output, then
unpause. Quarterly is plenty — Lok Sabha elections are five years apart.

---

## Data notes

**Everything here is a self-declaration.** Candidates swear these figures in an affidavit;
nobody audits them. The dashboard aggregates the published record and adds no estimates.

- **"Criminal cases" means cases *pending*, not convictions.** A declared case may be a
  serious charge or a protest-related FIR. It is not a finding of guilt.
- **Assets are nominal.** Not inflation-adjusted, so part of the 2004→2024 rise is just
  the rupee.
- **Coverage is uneven.** Some candidates — more of them in 2004 — have blank asset
  fields. These are stored as `null` and *excluded* from medians, never counted as zero.
  `Nil` is different: it is an explicit declaration of zero, and is stored as `0`.
- **Medians, not means.** A few billionaire candidates make the mean meaningless.
- **Telangana** was created in 2014; earlier rows attribute those seats to Andhra Pradesh.
- **General elections only.** Each year's source site also lists bye-elections held years
  later (a 2017 bye-poll appears on the 2014 site). Those rows are excluded from all
  aggregates and flagged `is_bye_election` in the CSV.
- **2004 winner tagging is incomplete at the source** (~29 constituencies carry no winner
  marker), so 2004 reports 514 MPs rather than 543.
- **Repeat-candidate matching is loose** (normalised name + state). Common names collide.

### The published dataset

`docs/data/candidates.csv` is the tidy, flat version of everything — one row per
candidate per election. Reuse it; the point is to save the next person from writing this
scraper again.

### MPLADS (development funds) — extra caveats

The MPLADS tab joins each sitting MP's fund position from MoSPI's
[eSAKSHI portal](https://mplads.mospi.gov.in/digigov/dashboard.html) to the election
data. Known discrepancy sources, all surfaced in the UI:

- **eSAKSHI only tracks the revised procedure from 1 April 2023.** Its "17th Lok Sabha"
  view covers that term's final ~14 months (~₹4,765 Cr allocated vs ~₹13,500 Cr for a
  real five-year term), so this project shows the **current term only** and never
  presents eSAKSHI figures as historical utilisation.
- **"Allocated limit" ≠ full-term entitlement.** It is the entitlement accrued to date
  *including carry-forward* of the seat's previous unspent balances.
- **The sitting MP is not always the 2024 winner** (bye-elections, resignations). The
  join flags such seats (`mp_differs_from_winner`) instead of pretending they match.
- **MPs recommend; district authorities execute.** Low spend ≠ MP inaction, and
  early-term percentages are structurally low.
- **Lok Sabha only** — Rajya Sabha MPLADS entitlements are excluded, so totals will not
  match scheme-wide figures. The portal is live; published numbers are a dated snapshot.
- MPLADS currently ships via the local/Actions path (`fetch_mplads.py` +
  `build_static.py`); the Databricks job does not yet produce `mplads.json`.

#### Independent cross-check

The tab reconciles itself against [Empowered Indian](https://empoweredindian.in/mplads),
a civic-tech dashboard built from the same official portal, and **publishes the deltas**
rather than quietly picking a winner. Measured 1 Aug 2026:

| Metric | This dashboard (eSAKSHI direct) | Empowered Indian |
|---|---|---|
| Allocated to Lok Sabha MPs | ₹8,304.66 Cr | ₹8,304.66 Cr — **exact match** |
| Expenditure | ₹2,569.06 Cr | ₹2,522.11 Cr (~1.8%, snapshot timing) |
| Works recommended | 99,738 | 66,571 — **definitional difference** |

The allocated figures agreeing to the rupee is the strongest available evidence that both
readings of the portal are sound. The works-count gap is a definition difference (the
portal's own tile counts recommendations differently from a per-MP sum), not an error in
either. Separately, ~19.5% of their per-MP records report more completed works than
recommended — producing negative "pending works" — so **treat per-MP works counts from
either source as indicative, not exact**. Their scope is both Houses (755 MPs); only
their Lok Sabha subset is comparable here.

---

## Maintenance

The scraper depends on MyNeta's HTML layout. As of the last verified run, all five
election years share an identical table structure — 8 columns, winner tagged with a green
`Winner` label, state in the page header. If ADR redesigns the site, `test_parser.py` is
where the breakage will show first:

```bash
cd pipeline && python test_parser.py ../.cache
```

The bronze notebook also fails loudly if a year returns fewer than ~500 winners or 2,000
candidates, rather than quietly publishing a truncated dashboard.

> **One duplication to know about.** `pipeline/build_static.py` mirrors the aggregation
> logic in `databricks/02_silver_clean.py` and `03_gold_marts.py`. The JSON schema is the
> contract between them. Change an aggregate in one place and you must change it in the
> other — the state-name conforming rules in particular.

### Scraping etiquette

[`myneta.info/robots.txt`](https://www.myneta.info/robots.txt) disallows only
`?print=true` URLs; the constituency pages this reads are permitted. The scraper
rate-limits to one request per second and the Databricks job parallelises across *years*
rather than constituencies, capping concurrency at five. **Please don't raise this.** ADR
is a nonprofit running this as a public service.

---

## Credit and licence

All underlying data is the work of **ADR / National Election Watch**, compiled from
Election Commission of India affidavits. This project only re-presents it, and is not
affiliated with ADR, MyNeta, or the ECI. If you find it useful, consider
[supporting ADR](https://adrindia.org/donate).

Code is MIT. The data carries whatever terms ADR applies to it.
