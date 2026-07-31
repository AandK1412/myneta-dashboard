# Databricks notebook source
# MAGIC %md
# MAGIC # Bronze - ingest candidate data from myneta.info
# MAGIC
# MAGIC Scrapes every Lok Sabha constituency page for the requested election years and
# MAGIC lands the parsed rows, as-is, in a Delta table. No cleaning happens here: bronze
# MAGIC is the replayable record of what the site said on a given day.
# MAGIC
# MAGIC **Politeness.** Work is parallelised across *years*, not across constituencies, and
# MAGIC each task rate-limits itself to one request per second. That caps us at roughly
# MAGIC five concurrent requests against a nonprofit's web server - deliberately modest.
# MAGIC Do not raise this without a good reason.
# MAGIC
# MAGIC Runs on Databricks Free Edition (serverless). The scraper is stdlib-only, so there
# MAGIC is nothing to `pip install`.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog")
dbutils.widgets.text("schema", "myneta", "Schema")
dbutils.widgets.text("years", "2004,2009,2014,2019,2024", "Election years")
dbutils.widgets.text("limit_per_year", "0", "Constituencies per year (0 = all)")

CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
YEARS = [int(y) for y in dbutils.widgets.get("years").split(",") if y.strip()]
LIMIT = int(dbutils.widgets.get("limit_per_year")) or None

print(f"target : {CATALOG}.{SCHEMA}")
print(f"years  : {YEARS}")
print(f"limit  : {LIMIT or 'all constituencies'}")

# COMMAND ----------

# Make the shared scraper importable. In a Databricks Git folder the repo root is
# the notebook's grandparent directory.
import sys, os

repo_root = os.path.abspath(os.path.join(os.getcwd(), ".."))
if repo_root not in sys.path:
    sys.path.insert(0, repo_root)
    sys.path.insert(0, os.path.join(repo_root, "pipeline"))

from pipeline.myneta import LOK_SABHA_SITES, Fetcher, scrape_year  # noqa: E402

print("scraper loaded; known years:", sorted(LOK_SABHA_SITES))

# COMMAND ----------

# On Free Edition the `workspace` catalog already exists and creating catalogs is
# usually not permitted - so a failure here is expected and harmless. The schema
# is the part that must succeed.
try:
    spark.sql(f"CREATE CATALOG IF NOT EXISTS {CATALOG}")
except Exception as e:
    print(f"(using existing catalog {CATALOG}: {type(e).__name__})")

spark.sql(f"CREATE SCHEMA IF NOT EXISTS {CATALOG}.{SCHEMA}")
spark.sql(f"USE {CATALOG}.{SCHEMA}")
print(f"writing to {CATALOG}.{SCHEMA}")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Scrape, one thread per election year
# MAGIC
# MAGIC Threads on the driver rather than `sc.parallelize`: **SparkContext is not available
# MAGIC on serverless compute** (or on Unity Catalog shared clusters), which is exactly what
# MAGIC Free Edition provides, so an RDD-based fan-out would fail outright. Threads also
# MAGIC dodge the question of whether executors can import the repo.
# MAGIC
# MAGIC The work is I/O-bound anyway, so the GIL costs nothing here, and one thread per
# MAGIC year keeps concurrency pinned at five regardless of cluster size.

# COMMAND ----------

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

RUN_TS = datetime.now(timezone.utc).replace(tzinfo=None)


def scrape_one_year(year: int):
    # Each thread gets its own Fetcher so the 1 req/sec limit is per-year,
    # holding total concurrency at len(YEARS).
    fetcher = Fetcher(cache_dir=None, delay=1.0, max_retries=3)
    rows = scrape_year(year, fetcher, limit=LIMIT,
                       progress=lambda m: print(f"[{year}] {m}", flush=True))
    return [r.to_dict() for r in rows]


records = []
with ThreadPoolExecutor(max_workers=len(YEARS)) as pool:
    for year, rows in zip(YEARS, pool.map(scrape_one_year, YEARS)):
        print(f"  {year}: {len(rows)} rows")
        records.extend(rows)

print(f"scraped {len(records)} candidate rows across {len(YEARS)} years")

# COMMAND ----------

from pyspark.sql import functions as F
from pyspark.sql.types import (StructType, StructField, StringType,
                               IntegerType, LongType, BooleanType)

schema = StructType([
    StructField("election_year", IntegerType()),
    StructField("state", StringType()),
    StructField("constituency", StringType()),
    StructField("constituency_id", IntegerType()),
    StructField("candidate_id", IntegerType()),
    StructField("name", StringType()),
    StructField("party", StringType()),
    StructField("criminal_cases", IntegerType()),
    StructField("education", StringType()),
    StructField("age", IntegerType()),
    StructField("assets", LongType()),
    StructField("liabilities", LongType()),
    StructField("is_winner", BooleanType()),
    StructField("is_bye_election", BooleanType()),
    StructField("source_url", StringType()),
])

bronze = (
    spark.createDataFrame(records, schema=schema)
    .withColumn("_ingested_at", F.lit(RUN_TS).cast("timestamp"))
    .withColumn("_source", F.lit("myneta.info"))
)

# Full refresh per run: the upstream site is itself a corrected, restated dataset,
# so the newest complete scrape is the truth. History stays available through
# Delta time travel (DESCRIBE HISTORY bronze_candidates).
(bronze.write
    .mode("overwrite")
    .option("overwriteSchema", "true")
    .saveAsTable(f"{CATALOG}.{SCHEMA}.bronze_candidates"))

print(f"wrote {bronze.count()} rows to {CATALOG}.{SCHEMA}.bronze_candidates")

# COMMAND ----------

# MAGIC %md
# MAGIC ## Ingest sanity checks
# MAGIC
# MAGIC Every Lok Sabha has 543 seats. If a year reports far fewer winners than seats,
# MAGIC the scrape was truncated or the site layout moved - fail loudly rather than
# MAGIC quietly publishing a partial dashboard.

# COMMAND ----------

checks = (
    bronze.groupBy("election_year")
    .agg(
        F.count("*").alias("candidates"),
        F.sum(F.col("is_winner").cast("int")).alias("winners"),
        F.countDistinct("constituency_id").alias("constituencies"),
        F.countDistinct("state").alias("states"),
        F.round(100 * F.avg(F.col("assets").isNotNull().cast("int")), 1).alias("asset_cov_pct"),
    )
    .orderBy("election_year")
)
display(checks)

if LIMIT is None:
    bad = [r for r in checks.collect() if r["winners"] < 500 or r["candidates"] < 2000]
    if bad:
        raise ValueError(
            "Ingest looks truncated - expected ~543 winners and >2000 candidates per year:\n"
            + "\n".join(str(r.asDict()) for r in bad)
        )
    print("sanity checks passed")
else:
    print("limit set - skipping completeness checks")
