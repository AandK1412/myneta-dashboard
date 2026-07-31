# Databricks notebook source
# MAGIC %md
# MAGIC # Silver - conform and enrich
# MAGIC
# MAGIC Bronze holds whatever the site said. Silver makes twenty years of it comparable:
# MAGIC casing and punctuation differ between the 2004 site and the 2024 one, party
# MAGIC labels drift, and education is a free-text field that needs ordering before it
# MAGIC can be charted.
# MAGIC
# MAGIC Derived columns added here: `net_worth`, `is_crorepati`, `has_criminal_case`,
# MAGIC `education_rank`, `party_group`, `age_band`.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog")
dbutils.widgets.text("schema", "myneta", "Schema")
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
spark.sql(f"USE {CATALOG}.{SCHEMA}")

from pyspark.sql import functions as F, Window

CRORE = 10_000_000  # 1 crore rupees - the threshold ADR uses for "crorepati"

# COMMAND ----------

bronze = spark.table(f"{CATALOG}.{SCHEMA}.bronze_candidates")

# General elections only. Each year's source site also lists bye-elections held
# years later (a 2017 contest on the 2014 site); keeping them would count those
# rows inside the wrong year's trends. Bronze retains them for the record.
bronze = bronze.filter(~F.coalesce(F.col("is_bye_election"), F.lit(False)))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Text conforming
# MAGIC
# MAGIC `&` vs `and`, trailing reservation tags like `(SC)` / `(ST)`, and inconsistent
# MAGIC casing all have to be reconciled before a constituency can be tracked across
# MAGIC elections.

# COMMAND ----------

def tidy(col):
    """Trim, collapse whitespace, standardise ampersands and dot-spacing."""
    c = F.regexp_replace(col, r"\s+", " ")
    c = F.regexp_replace(c, r"\s*&\s*", " & ")
    c = F.regexp_replace(c, r"\s*\.\s*", ". ")
    return F.trim(c)


silver = (
    bronze
    .withColumn("state", F.initcap(tidy(F.col("state"))))
    .withColumn("constituency_raw", tidy(F.col("constituency")))
    .withColumn("constituency", F.initcap(F.col("constituency_raw")))
    # Reservation category is real information - pull it out rather than discard it.
    .withColumn(
        "seat_category",
        F.when(F.col("constituency_raw").rlike(r"\(\s*SC\s*\)"), "SC")
         .when(F.col("constituency_raw").rlike(r"\(\s*ST\s*\)"), "ST")
         .otherwise("GEN"),
    )
    .withColumn("constituency",
                F.trim(F.regexp_replace(F.col("constituency"), r"\s*\((sc|st)\)\s*$", "")))
    .withColumn("name", tidy(F.col("name")))
    .withColumn("party", F.upper(tidy(F.col("party"))))
)

# A handful of state spellings genuinely differ between sites.
#
# Note the deliberate absence of a generic "And" -> "&" rewrite here. Doing that
# without a word boundary turns "Andhra Pradesh" into "&hra Pradesh" and
# "Andaman" into "&aman" - map the specific names instead.
STATE_FIXES = {
    "Andaman & Nicobar Islands": "Andaman & Nicobar Islands",
    "Andaman And Nicobar Islands": "Andaman & Nicobar Islands",
    "Nct Of Delhi": "Delhi",
    "Delhi Nct": "Delhi",
    "National Capital Territory Of Delhi": "Delhi",
    "Dadra & Nagar Haveli": "Dadra & Nagar Haveli and Daman & Diu",
    "Daman & Diu": "Dadra & Nagar Haveli and Daman & Diu",
    "Dadra & Nagar Haveli & Daman & Diu": "Dadra & Nagar Haveli and Daman & Diu",
    "Delhi (Nct)": "Delhi",
    "Orissa": "Odisha",
    "Pondicherry": "Puducherry",
    "Uttaranchal": "Uttarakhand",
    "Chattisgarh": "Chhattisgarh",
    "Jammu & Kashmir": "Jammu & Kashmir",
}
state_map = F.create_map([F.lit(x) for kv in STATE_FIXES.items() for x in kv])
silver = silver.withColumn(
    "state", F.coalesce(state_map[F.col("state")], F.col("state")))

# COMMAND ----------

# MAGIC %md
# MAGIC ## Education, ordered
# MAGIC
# MAGIC ADR's labels are ordinal but stored as text. Ranking them lets the dashboard
# MAGIC draw a meaningful distribution instead of an alphabetical one.

# COMMAND ----------

EDUCATION_ORDER = [
    ("Illiterate", 0), ("Literate", 1), ("5Th Pass", 2), ("8Th Pass", 3),
    ("10Th Pass", 4), ("12Th Pass", 5), ("Graduate", 6), ("Diploma", 6),
    ("Graduate Professional", 7), ("Post Graduate", 8), ("Doctorate", 9),
]

edu_norm = F.initcap(F.trim(F.col("education")))
rank_expr = F.lit(None).cast("int")
for label, rank in EDUCATION_ORDER:
    rank_expr = F.when(edu_norm == F.lit(label), F.lit(rank)).otherwise(rank_expr)

silver = (
    silver
    .withColumn("education", edu_norm)
    .withColumn("education_rank", rank_expr)
    .withColumn(
        "education_group",
        F.when(F.col("education_rank") >= 6, "Graduate or above")
         .when(F.col("education_rank").between(2, 5), "Class 5-12")
         .when(F.col("education_rank") <= 1, "Illiterate / Literate")
         .otherwise("Not disclosed"),
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Derived measures

# COMMAND ----------

silver = (
    silver
    .withColumn("assets", F.when(F.col("assets") >= 0, F.col("assets")))
    .withColumn("liabilities", F.when(F.col("liabilities") >= 0, F.col("liabilities")))
    .withColumn("net_worth", F.col("assets") - F.coalesce(F.col("liabilities"), F.lit(0)))
    .withColumn("is_crorepati",
                F.when(F.col("assets").isNotNull(), F.col("assets") >= CRORE))
    .withColumn("criminal_cases", F.coalesce(F.col("criminal_cases"), F.lit(0)))
    .withColumn("has_criminal_case", F.col("criminal_cases") > 0)
    .withColumn("age", F.when(F.col("age").between(21, 100), F.col("age")))
    .withColumn(
        "age_band",
        F.when(F.col("age") < 40, "Under 40")
         .when(F.col("age") < 55, "40-54")
         .when(F.col("age") < 70, "55-69")
         .when(F.col("age") >= 70, "70+")
         .otherwise("Unknown"),
    )
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Party grouping
# MAGIC
# MAGIC Hundreds of parties contest each election and most field a single candidate.
# MAGIC Keep the raw label, but add a grouped one so charts stay legible: any party
# MAGIC that has ever won 5+ seats in a single election keeps its name; the rest
# MAGIC collapse into Independent / Other.

# COMMAND ----------

seat_wins = (
    silver.filter(F.col("is_winner"))
    .groupBy("election_year", "party").agg(F.count("*").alias("seats"))
)
major = (
    seat_wins.filter(F.col("seats") >= 5)
    .select("party").distinct()
    .withColumn("_is_major", F.lit(True))
)

silver = (
    silver.join(F.broadcast(major), on="party", how="left")
    .withColumn(
        "party_group",
        F.when(F.col("party").isin("IND", "INDEPENDENT"), "Independent")
         .when(F.col("_is_major") == True, F.col("party"))  # noqa: E712
         .otherwise("Other parties"),
    )
    .drop("_is_major")
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## Deduplicate and persist
# MAGIC
# MAGIC A candidate can legitimately appear twice in a year (bye-elections re-poll a
# MAGIC seat). Key on the site's own candidate_id where present.

# COMMAND ----------

dedupe_key = ["election_year", "constituency_id", "candidate_id", "name"]
w = Window.partitionBy(*dedupe_key).orderBy(F.col("assets").desc_nulls_last())

silver_final = (
    silver
    .withColumn("_rn", F.row_number().over(w))
    .filter(F.col("_rn") == 1).drop("_rn", "constituency_raw")
    .withColumn("_processed_at", F.current_timestamp())
)

(silver_final.write
    .mode("overwrite").option("overwriteSchema", "true")
    .partitionBy("election_year")
    .saveAsTable(f"{CATALOG}.{SCHEMA}.silver_candidates"))

print(f"silver_candidates: {silver_final.count()} rows "
      f"({bronze.count() - silver_final.count()} duplicates removed)")

# COMMAND ----------

display(
    spark.table(f"{CATALOG}.{SCHEMA}.silver_candidates")
    .groupBy("election_year")
    .agg(
        F.count("*").alias("candidates"),
        F.sum(F.col("is_winner").cast("int")).alias("winners"),
        F.countDistinct("state").alias("states"),
        F.countDistinct("party").alias("parties"),
        F.round(F.avg("age"), 1).alias("avg_age"),
        F.round(100 * F.avg(F.col("has_criminal_case").cast("int")), 1).alias("pct_criminal"),
        F.round(100 * F.avg(F.col("is_crorepati").cast("int")), 1).alias("pct_crorepati"),
    )
    .orderBy("election_year")
)
