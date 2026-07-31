# Databricks notebook source
# MAGIC %md
# MAGIC # Gold - presentation marts
# MAGIC
# MAGIC One table per question the dashboard asks. These are deliberately tiny: the whole
# MAGIC point is that the published site ships pre-aggregated JSON of a few hundred KB
# MAGIC rather than making a browser chew through 45,000 candidate rows.
# MAGIC
# MAGIC Medians use `percentile_approx`. Mean assets are meaningless here - a handful of
# MAGIC billionaire candidates drag the average into nonsense, so the dashboard leads
# MAGIC with medians throughout.

# COMMAND ----------

dbutils.widgets.text("catalog", "workspace", "Unity Catalog")
dbutils.widgets.text("schema", "myneta", "Schema")
CATALOG = dbutils.widgets.get("catalog")
SCHEMA = dbutils.widgets.get("schema")
spark.sql(f"USE {CATALOG}.{SCHEMA}")

from pyspark.sql import functions as F, Window

s = spark.table(f"{CATALOG}.{SCHEMA}.silver_candidates")
winners = s.filter(F.col("is_winner"))


def pct(col):
    return F.round(100 * F.avg(F.col(col).cast("int")), 1)


def median(col):
    return F.expr(f"percentile_approx({col}, 0.5)")


def save(df, name, partition=None):
    w = df.write.mode("overwrite").option("overwriteSchema", "true")
    if partition:
        w = w.partitionBy(partition)
    w.saveAsTable(f"{CATALOG}.{SCHEMA}.{name}")
    print(f"  {name:<28} {df.count():>6} rows")


# COMMAND ----------

# MAGIC %md
# MAGIC ## 1. National trend, 2004 - 2024
# MAGIC The headline series: are winners getting richer, and more likely to face charges?

# COMMAND ----------

def national(df, label):
    return (
        df.groupBy("election_year")
        .agg(
            F.count("*").alias("n"),
            pct("has_criminal_case").alias("pct_criminal"),
            F.round(100 * F.avg((F.col("criminal_cases") >= 2).cast("int")), 1).alias("pct_multi_case"),
            pct("is_crorepati").alias("pct_crorepati"),
            median("assets").alias("median_assets"),
            F.round(F.avg("age"), 1).alias("avg_age"),
            F.round(100 * F.avg((F.col("education_rank") >= 6).cast("int")), 1).alias("pct_graduate_plus"),
            F.round(F.avg("criminal_cases"), 2).alias("avg_cases"),
        )
        .withColumn("cohort", F.lit(label))
    )


gold_trends = national(s, "All candidates").unionByName(national(winners, "Winners (MPs)"))
save(gold_trends.orderBy("cohort", "election_year"), "gold_national_trends")
display(gold_trends.orderBy("cohort", "election_year"))

# COMMAND ----------

# MAGIC %md
# MAGIC ## 2. Party performance

# COMMAND ----------

gold_party = (
    s.groupBy("election_year", "party_group")
    .agg(
        F.count("*").alias("candidates"),
        F.sum(F.col("is_winner").cast("int")).alias("seats_won"),
        pct("has_criminal_case").alias("pct_criminal"),
        pct("is_crorepati").alias("pct_crorepati"),
        median("assets").alias("median_assets"),
        F.round(F.avg("age"), 1).alias("avg_age"),
    )
    .withColumn("strike_rate",
                F.round(100 * F.col("seats_won") / F.col("candidates"), 1))
    .filter(F.col("candidates") >= 5)
)
save(gold_party.orderBy("election_year", F.col("seats_won").desc()), "gold_party_summary")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 3. State picture (winners only)

# COMMAND ----------

gold_state = (
    winners.groupBy("election_year", "state")
    .agg(
        F.count("*").alias("seats"),
        pct("has_criminal_case").alias("pct_criminal"),
        pct("is_crorepati").alias("pct_crorepati"),
        median("assets").alias("median_assets"),
        F.round(F.avg("age"), 1).alias("avg_age"),
        F.sum(F.col("criminal_cases")).alias("total_cases"),
    )
)
save(gold_state.orderBy("election_year", F.col("seats").desc()), "gold_state_summary")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 4. Education and age distributions

# COMMAND ----------

save(
    s.groupBy("election_year", "education_group")
     .agg(F.count("*").alias("candidates"),
          F.sum(F.col("is_winner").cast("int")).alias("winners"))
     .orderBy("election_year", "education_group"),
    "gold_education_dist",
)

save(
    s.groupBy("election_year", "age_band")
     .agg(F.count("*").alias("candidates"),
          F.sum(F.col("is_winner").cast("int")).alias("winners"))
     .orderBy("election_year", "age_band"),
    "gold_age_dist",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 5. The searchable winners table
# MAGIC Every MP elected 2004-2024, the row-level backbone of the dashboard.

# COMMAND ----------

gold_winners = winners.select(
    "election_year", "state", "constituency", "seat_category", "name", "party",
    "party_group", "criminal_cases", "education", "education_group", "age",
    "assets", "liabilities", "net_worth", "is_crorepati", "candidate_id",
).orderBy("election_year", "state", "constituency")
save(gold_winners, "gold_winners")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 6. Leaderboards
# MAGIC Top 50 per year by wealth and by declared cases - the entries people look for.

# COMMAND ----------

def top_n(df, order_col, label, n=50):
    w = Window.partitionBy("election_year").orderBy(F.col(order_col).desc_nulls_last())
    return (
        df.filter(F.col(order_col).isNotNull())
        .withColumn("rank", F.row_number().over(w))
        .filter(F.col("rank") <= n)
        .withColumn("board", F.lit(label))
        .select("board", "rank", "election_year", "name", "party", "state",
                "constituency", "assets", "liabilities", "criminal_cases",
                "education", "age", "is_winner", "candidate_id")
    )


save(
    top_n(s, "assets", "Richest candidates")
    .unionByName(top_n(s, "criminal_cases", "Most declared cases")),
    "gold_leaderboards",
)

# COMMAND ----------

# MAGIC %md
# MAGIC ## 7. Re-contest tracking
# MAGIC Candidates who appear in more than one election, and how their declared
# MAGIC wealth moved between them. This is the analysis the raw site cannot do.

# COMMAND ----------

# Two guards matter here. Requiring >= 2 DISTINCT election years keeps namesakes
# contesting the same election out - without it, two different people sharing a
# name in one year merge into a single candidate whose wealth appears to explode.
# And growth compares first election to last (via min/max over a struct keyed on
# year), not min to max assets, which would cherry-pick the most dramatic pair.
norm_name = F.upper(F.regexp_replace(F.col("name"), r"[^A-Za-z ]", ""))
appearances = (
    s.withColumn("_key", F.concat_ws("|", norm_name, F.col("state")))
    .filter(F.col("assets").isNotNull())
    .groupBy("_key")
    .agg(
        F.max("name").alias("name"),
        F.max("state").alias("state"),
        F.countDistinct("election_year").alias("elections_contested"),
        F.sum(F.col("is_winner").cast("int")).alias("elections_won"),
        F.min("election_year").alias("first_year"),
        F.max("election_year").alias("last_year"),
        # struct ordering picks the assets belonging to the earliest/latest year
        F.min(F.struct("election_year", "assets")).alias("_first"),
        F.max(F.struct("election_year", "assets")).alias("_last"),
        F.max("criminal_cases").alias("peak_cases"),
    )
    .filter(F.col("elections_contested") >= 2)
    .withColumn("min_assets", F.col("_first.assets"))
    .withColumn("max_assets", F.col("_last.assets"))
    .withColumn(
        "asset_growth_pct",
        F.when(F.col("min_assets") > 0,
               F.round(100.0 * (F.col("max_assets") - F.col("min_assets")) / F.col("min_assets"), 0)),
    )
    .drop("_key", "_first", "_last")
)
save(appearances.orderBy(F.col("asset_growth_pct").desc_nulls_last()), "gold_repeat_candidates")

# COMMAND ----------

# MAGIC %md
# MAGIC ## 8. Per-state slices
# MAGIC The dashboard's State/UT filter re-scopes every panel, so each aggregate above
# MAGIC also needs a by-state version. These feed `states.json`.

# COMMAND ----------

def with_cohort(df, label):
    return df.withColumn("cohort", F.lit(label))


def state_trend(df):
    return (
        df.groupBy("election_year", "state")
        .agg(
            F.count("*").alias("n"),
            pct("has_criminal_case").alias("pct_criminal"),
            F.round(100 * F.avg((F.col("criminal_cases") >= 2).cast("int")), 1).alias("pct_multi_case"),
            pct("is_crorepati").alias("pct_crorepati"),
            median("assets").alias("median_assets"),
            F.round(F.avg("age"), 1).alias("avg_age"),
            F.round(100 * F.avg((F.col("education_rank") >= 6).cast("int")), 1).alias("pct_graduate_plus"),
            F.round(F.avg("criminal_cases"), 2).alias("avg_cases"),
        )
    )


save(
    with_cohort(state_trend(s), "All candidates")
    .unionByName(with_cohort(state_trend(winners), "Winners (MPs)"))
    .orderBy("state", "cohort", "election_year"),
    "gold_state_trends",
)

save(
    s.groupBy("election_year", "state", "party_group")
     .agg(F.count("*").alias("candidates"),
          F.sum(F.col("is_winner").cast("int")).alias("seats_won"),
          pct("has_criminal_case").alias("pct_criminal"),
          median("assets").alias("median_assets"))
     .filter((F.col("candidates") >= 5) | (F.col("seats_won") > 0))
     .orderBy("state", "election_year", F.col("seats_won").desc()),
    "gold_party_state",
)

save(
    s.groupBy("election_year", "state", "education_group")
     .agg(F.count("*").alias("candidates"),
          F.sum(F.col("is_winner").cast("int")).alias("winners"))
     .orderBy("state", "election_year", "education_group"),
    "gold_education_state",
)

save(
    s.groupBy("election_year", "state", "age_band")
     .agg(F.count("*").alias("candidates"),
          F.sum(F.col("is_winner").cast("int")).alias("winners"))
     .orderBy("state", "election_year", "age_band"),
    "gold_age_state",
)

# COMMAND ----------

print("\nGold marts built:")
display(spark.sql(f"SHOW TABLES IN {CATALOG}.{SCHEMA} LIKE 'gold_*'"))
