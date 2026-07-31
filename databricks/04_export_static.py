# Databricks notebook source
# MAGIC %md
# MAGIC # Export - gold marts to static JSON, then to GitHub
# MAGIC
# MAGIC This is the seam between Databricks and the public site.
# MAGIC
# MAGIC A published Databricks dashboard cannot be shown to anonymous visitors: Databricks
# MAGIC Apps explicitly forbid anonymous access, and AI/BI external embedding needs a
# MAGIC service principal minting short-lived tokens from a backend - which GitHub Pages,
# MAGIC being static, cannot host. So instead of serving queries at request time, we
# MAGIC precompute every number the dashboard needs and commit it as JSON.
# MAGIC
# MAGIC The result is faster and free at any traffic level: no warehouse spins up when a
# MAGIC visitor loads the page, and there is no credential anywhere in the browser.
# MAGIC
# MAGIC **Auth.** Set a GitHub fine-grained PAT with `Contents: read & write` on the target
# MAGIC repo, stored as a Databricks secret. Create it once from the CLI:
# MAGIC ```
# MAGIC databricks secrets create-scope myneta
# MAGIC databricks secrets put-secret myneta github_token
# MAGIC ```

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog")
dbutils.widgets.text("schema", "myneta", "Schema")
dbutils.widgets.text("volume", "exports", "UC Volume for JSON output")
dbutils.widgets.text("github_repo", "", "GitHub repo (owner/name), blank = skip push")
dbutils.widgets.text("github_branch", "main", "Branch")
dbutils.widgets.text("data_path", "docs/data", "Path within repo")
dbutils.widgets.text("secret_scope", "myneta", "Databricks secret scope")
dbutils.widgets.text("secret_key", "github_token", "Secret key holding the PAT")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
VOLUME = dbutils.widgets.get("volume")
REPO = dbutils.widgets.get("github_repo").strip()
BRANCH = dbutils.widgets.get("github_branch").strip() or "main"
DATA_PATH = dbutils.widgets.get("data_path").strip().strip("/")
SCOPE = dbutils.widgets.get("secret_scope")
KEY = dbutils.widgets.get("secret_key")

spark.sql(f"USE {CATALOG}.{SCHEMA}")
spark.sql(f"CREATE VOLUME IF NOT EXISTS {CATALOG}.{SCHEMA}.{VOLUME}")
VOL_DIR = f"/Volumes/{CATALOG}/{SCHEMA}/{VOLUME}"
print("export dir:", VOL_DIR)

# COMMAND ----------

import json, math, os
from datetime import datetime, timezone
from pyspark.sql import functions as F


def rows(table, order=None):
    """Collect a gold table as JSON-safe dicts. These are all small by design."""
    df = spark.table(f"{CATALOG}.{SCHEMA}.{table}")
    if order:
        df = df.orderBy(*order)
    out = []
    for r in df.collect():
        d = r.asDict()
        for k, v in list(d.items()):
            if isinstance(v, float):
                if math.isnan(v) or math.isinf(v):
                    d[k] = None
                else:  # keep payload small - no 15-digit float noise
                    d[k] = round(v, 2)
            elif hasattr(v, "isoformat"):
                d[k] = v.isoformat()
        out.append(d)
    return out


# COMMAND ----------

# MAGIC %md
# MAGIC ## Assemble the payloads

# COMMAND ----------

silver = spark.table(f"{CATALOG}.{SCHEMA}.silver_candidates")
generated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

years = [r[0] for r in silver.select("election_year").distinct()
         .orderBy("election_year").collect()]

meta = {
    "generated_at": generated,
    "source": "myneta.info - Association for Democratic Reforms (ADR) / National Election Watch",
    "source_url": "https://www.myneta.info/",
    "underlying_source": "Candidate affidavits filed with the Election Commission of India",
    "election_years": years,
    "total_candidates": silver.count(),
    "total_winners": silver.filter(F.col("is_winner")).count(),
    "pipeline": "Databricks (bronze -> silver -> gold) -> static JSON -> GitHub Pages",
    "licence_note": (
        "Data is republished from ADR/NEW, who compile it from Election Commission "
        "affidavits. Figures are candidate self-declarations, not audited assessments."
    ),
}

payloads = {
    "meta.json": meta,
    "summary.json": {
        "meta": meta,
        "national_trends": rows("gold_national_trends", ["cohort", "election_year"]),
        "party_summary": rows("gold_party_summary", ["election_year", F.col("seats_won").desc()]),
        "state_summary": rows("gold_state_summary", ["election_year", F.col("seats").desc()]),
        "education_dist": rows("gold_education_dist", ["election_year", "education_group"]),
        "age_dist": rows("gold_age_dist", ["election_year", "age_band"]),
    },
    "winners.json": {"meta": {"generated_at": generated},
                     "winners": rows("gold_winners", ["election_year", "state", "constituency"])},
    "leaderboards.json": {"meta": {"generated_at": generated},
                          "entries": rows("gold_leaderboards", ["board", "election_year", "rank"])},
    "repeat_candidates.json": {
        "meta": {"generated_at": generated},
        "candidates": rows("gold_repeat_candidates",
                           [F.col("asset_growth_pct").desc_nulls_last()])[:500],
    },
    "states.json": {
        "meta": {"generated_at": generated},
        "state_trends": rows("gold_state_trends", ["state", "cohort", "election_year"]),
        "party_by_state": rows("gold_party_state",
                               ["state", "election_year", F.col("seats_won").desc()]),
        "education_by_state": rows("gold_education_state",
                                   ["state", "election_year", "education_group"]),
        "age_by_state": rows("gold_age_state", ["state", "election_year", "age_band"]),
    },
}

# COMMAND ----------

written = {}
for fname, payload in payloads.items():
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    path = os.path.join(VOL_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(body)
    written[fname] = body
    print(f"  {fname:<26} {len(body)/1024:>8.1f} KB")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Also emit a flat CSV
# MAGIC The dashboard does not need it, but publishing the tidy dataset is most of the
# MAGIC civic value - it saves the next person from writing this scraper again.

# COMMAND ----------

csv_pdf = (
    spark.table(f"{CATALOG}.{SCHEMA}.silver_candidates")
    .select("election_year", "state", "constituency", "seat_category", "name",
            "party", "party_group", "criminal_cases", "education", "age",
            "assets", "liabilities", "net_worth", "is_winner")
    .orderBy("election_year", "state", "constituency", F.col("is_winner").desc())
    .toPandas()
)
csv_body = csv_pdf.to_csv(index=False)
with open(os.path.join(VOL_DIR, "candidates.csv"), "w", encoding="utf-8") as f:
    f.write(csv_body)
print(f"  candidates.csv             {len(csv_body)/1024:>8.1f} KB  ({len(csv_pdf)} rows)")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Push to GitHub
# MAGIC
# MAGIC Uses the Contents API so no git binary or checkout is needed. Each file is
# MAGIC compared against the existing blob SHA and skipped when unchanged, which keeps
# MAGIC the commit history meaningful rather than one empty commit per scheduled run.

# COMMAND ----------

import base64, urllib.request, urllib.error


def gh(method, url, token, payload=None):
    req = urllib.request.Request(
        url, method=method,
        data=json.dumps(payload).encode() if payload else None,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "Content-Type": "application/json",
            "User-Agent": "myneta-dashboard-pipeline",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def put_file(repo, branch, path, content_str, token, message):
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    status, existing = gh("GET", f"{url}?ref={branch}", token)
    sha = existing.get("sha") if status == 200 else None

    if sha:
        current = base64.b64decode(existing.get("content", "")).decode("utf-8", "replace")
        if current == content_str:
            return "unchanged"

    body = {
        "message": message,
        "content": base64.b64encode(content_str.encode("utf-8")).decode(),
        "branch": branch,
    }
    if sha:
        body["sha"] = sha
    status, resp = gh("PUT", url, token, body)
    if status not in (200, 201):
        raise RuntimeError(f"GitHub push failed for {path}: {status} {resp.get('message')}")
    return "updated"


if not REPO:
    print("github_repo is blank - JSON written to the Volume only, nothing pushed.")
    print(f"Download from {VOL_DIR} and commit manually, or set the widget and re-run.")
else:
    token = dbutils.secrets.get(scope=SCOPE, key=KEY)
    msg = f"data: refresh MyNeta dashboard ({generated})"
    all_files = dict(written)
    all_files["candidates.csv"] = csv_body

    results = {}
    for fname, body in all_files.items():
        results[fname] = put_file(REPO, BRANCH, f"{DATA_PATH}/{fname}", body, token, msg)
        print(f"  {fname:<26} {results[fname]}")

    changed = sum(1 for v in results.values() if v == "updated")
    print(f"\n{changed} file(s) updated on {REPO}@{BRANCH}")
    if changed:
        print(f"GitHub Pages will rebuild automatically. Data path: {DATA_PATH}/")

# COMMAND ----------

dbutils.notebook.exit(json.dumps({
    "generated_at": generated,
    "years": years,
    "candidates": meta["total_candidates"],
    "winners": meta["total_winners"],
    "pushed_to": REPO or None,
}))
