# Databricks notebook source
# MAGIC %md
# MAGIC # CQI Patient Conference Booklets - weekly build
# MAGIC **Clinical Quality Intelligence (CQI) - CenterWell Home Health**
# MAGIC
# MAGIC | Step | What happens |
# MAGIC |---|---|
# MAGIC | 1 | Load the dataset registry (`config/datasets.yaml`): CSV, tables or lake paths per dataset |
# MAGIC | 2 | Pick the cohort in Spark: high / very_high, or score up >= 0.05 vs. prior run; active episodes; optional branches |
# MAGIC | 3 | Percentile over **all** scored episodes on the run date |
# MAGIC | 4 | Pull only cohort rows into pandas and run the same `build_payloads.run()` used locally |
# MAGIC | 5 | Save payload JSON + QC to Delta; PDFs and per-branch booklets to the Volume |
# MAGIC
# MAGIC Cluster libraries: `reportlab matplotlib jsonschema pypdf pyyaml`. Init script: `fonts-liberation` (falls back to DejaVu).

# COMMAND ----------

dbutils.widgets.text("as_of_date", "2026-08-15")
dbutils.widgets.dropdown("source", "auto", ["auto", "csv", "table", "path"])
dbutils.widgets.text("branches", "")                       # comma list for the pilot branches
dbutils.widgets.dropdown("render_pdfs", "yes", ["yes", "no"])
dbutils.widgets.dropdown("format", "both", ["both", "full", "brief"])  # brief = 3-page customer summary

AS_OF = dbutils.widgets.get("as_of_date")
SOURCE = dbutils.widgets.get("source")
BRANCHES = [b.strip() for b in dbutils.widgets.get("branches").split(",") if b.strip()]
RENDER = dbutils.widgets.get("render_pdfs") == "yes"
FORMATS = ("full", "brief") if dbutils.widgets.get("format") == "both" else (dbutils.widgets.get("format"),)

ROOT = "/Workspace/Shared/cqi/cqi_booklet"                  # VERIFY: where this folder lives (Repo or Workspace files)
OUT_DIR = f"/Volumes/hsa/cqi/booklets/{AS_OF}"              # VERIFY: PHI-approved Volume
PAYLOAD_TABLE = "hsa.cqi_booklet_payload"
QC_TABLE = "hsa.cqi_booklet_qc"

# COMMAND ----------

import sys, os, glob, json
sys.path.insert(0, f"{ROOT}/src")
from pyspark.sql import functions as F, Window as Wn
from cqi_datasets import Datasets, CohortPandasView
import build_payloads as bp

ds = Datasets(f"{ROOT}/config/datasets.yaml", engine="spark", spark=spark, source=SOURCE)
display(spark.createDataFrame([{k: str(v) for k, v in r.items()} for r in ds.describe()]))

# COMMAND ----------

# MAGIC %md ## Cohort (same rules as `build_payloads.py`)

# COMMAND ----------

KEY = ["patient_id", "episode_id"]
rs = ds.load("risk_scores").filter(F.col("as_of_date") <= F.to_date(F.lit(AS_OF)))
rs = rs.withColumn("score_prior", F.lag("score").over(Wn.partitionBy(*KEY).orderBy("as_of_date")))
run_rows = (rs.filter(F.col("as_of_date") == F.to_date(F.lit(AS_OF)))
              .withColumn("percentile", F.round(F.percent_rank().over(Wn.orderBy("score")) * 100, 1)))

ep = (ds.load("episodes")
      .withColumn("episode_status", F.regexp_replace(F.upper(F.trim("episode_status")), "[ _]+", "-")))
level = F.regexp_replace(F.lower(F.trim("outcome_risk_level")), "[ -]+", "_")
cohort = (run_rows.join(ep.select(*KEY, "episode_status", "branch_name"), KEY)
          .filter(F.col("episode_status").isin(*bp.EPI_STATUSES))
          .filter(level.isin(*bp.RISK_LEVELS) | ((F.col("score") - F.col("score_prior")) >= bp.RISING_DELTA)))
if BRANCHES:
    cohort = cohort.filter(F.col("branch_name").isin(BRANCHES))
cohort = cohort.cache()
print("cohort episodes:", cohort.count())
display(cohort.groupBy("branch_name").count().orderBy(F.desc("count")))

percentiles = {(r.patient_id, r.episode_id): float(r.percentile) for r in cohort.select(*KEY, "percentile").collect()}

# COMMAND ----------

# MAGIC %md ## Build, validate, render

# COMMAND ----------

view = CohortPandasView(ds, cohort)
qc = bp.run(view, AS_OF, OUT_DIR, render=RENDER, branches=BRANCHES or None, tmp="/tmp", formats=FORMATS, percentiles=percentiles)
print("\n".join(ds.log))

qc_df = spark.createDataFrame(qc, "as_of_date string, patient_id string, episode_id string, branch string, risk string, "
                                  "score double, valid boolean, errors string, length_warnings string, pdf string, pdf_brief string")
display(qc_df.groupBy("valid").count())
display(qc_df.filter("NOT valid"))

# COMMAND ----------

# MAGIC %md ## Persist payloads + QC (Delta, replace this run date)

# COMMAND ----------

rows = []
for f in glob.glob(f"{OUT_DIR}/json/*.json"):
    p = json.load(open(f))
    rows.append((AS_OF, p["meta"]["run_id"], p["patient"]["patient_id"], p["patient"]["episode_id"],
                 p["patient"].get("branch_name"), json.dumps(p)))
if rows:
    pay = (spark.createDataFrame(rows, "as_of_date string, run_id string, patient_id string, episode_id string, branch_name string, payload_json string")
           .withColumn("as_of_date", F.to_date("as_of_date")).withColumn("generated_ts", F.current_timestamp()))
    (pay.write.format("delta").mode("overwrite").option("replaceWhere", f"as_of_date = '{AS_OF}'")
        .partitionBy("as_of_date").saveAsTable(PAYLOAD_TABLE))
    (qc_df.withColumn("as_of_date", F.to_date("as_of_date")).withColumn("logged_ts", F.current_timestamp())
          .write.format("delta").mode("append").saveAsTable(QC_TABLE))
print(f"{len(rows)} payloads -> {PAYLOAD_TABLE};  PDFs -> {OUT_DIR}/pdf")
