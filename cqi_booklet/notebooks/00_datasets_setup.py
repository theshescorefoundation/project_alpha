# Databricks notebook source
# MAGIC %md
# MAGIC # CQI datasets on Databricks
# MAGIC The same `datasets.yaml` serves **CSV** (today), **tables** (`hsa.cqi_*`) and **lake paths** (`/tempdata/lvl3/*`).
# MAGIC 1. Read CSVs with Spark (same code path as production).
# MAGIC 2. Switch one or all datasets to tables / paths.
# MAGIC 3. Snapshot real tables into CSVs for a small cohort (dev data).
# MAGIC 4. Then run `01_build_booklets`.

# COMMAND ----------

import sys
ROOT = "/Workspace/Shared/cqi/cqi_booklet"          # VERIFY: where this folder lives
sys.path.insert(0, f"{ROOT}/src")
from cqi_datasets import Datasets

# COMMAND ----------

# MAGIC %md ## 1. Read the CSVs with Spark

# COMMAND ----------

ds = Datasets(f"{ROOT}/config/datasets.yaml", engine="spark", spark=spark)        # every dataset: source from yaml (csv)
display(spark.createDataFrame(ds.describe()))
display(ds.load("visits"))
print([ds.check(n) for n in ds.names()])

# COMMAND ----------

# MAGIC %md ## 2. Switch sources
# MAGIC - Production: `Datasets(..., source="auto")` -> each dataset reads its `table` if configured, else its `path`.
# MAGIC - One dataset only: change its `source:` in `datasets.yaml`, or `ds.load("risk_scores", source="table")`.
# MAGIC - Mixed is normal while you build: CQI outputs from `table`, Lighthouse from `path`, not-yet-built ones from `csv`.

# COMMAND ----------

mixed = Datasets(f"{ROOT}/config/datasets.yaml", engine="spark", spark=spark)
risk = mixed.load("risk_scores", source="table")      # hsa.cqi_risk_scores, renamed to canonical columns
visits = mixed.load("visits", source="path")          # /tempdata/lvl3/client_episode_visits_all
align = mixed.load("alignment", source="csv")         # not in production yet -> keep CSV
print("\n".join(mixed.log))

# COMMAND ----------

# MAGIC %md ## 3. Build dev CSVs from real tables for a small cohort
# MAGIC Writes canonical-column CSVs. **Contains PHI** - write only to an approved Volume, never to a Repo.

# COMMAND ----------

from pyspark.sql import functions as F
SNAP_DIR = "/Volumes/hsa/cqi/dev_csv"                  # VERIFY: PHI-approved location
AS_OF = "2026-08-15"
src = Datasets(f"{ROOT}/config/datasets.yaml", engine="spark", spark=spark, source="auto")
cohort = (src.load("risk_scores").filter(F.col("as_of_date") == AS_OF)
          .filter(F.col("outcome_risk_level").isin("high", "very_high")).limit(25).select("patient_id", "episode_id"))
for name in src.names():
    try:
        path = src.snapshot_to_csv(name, out_dir=SNAP_DIR, keys_df=cohort)
        print("wrote", path)
    except Exception as e:
        print("skip", name, e.__class__.__name__, str(e)[:120])
# To develop on these files: set defaults.csv_dir in a copy of datasets.yaml to SNAP_DIR.

# COMMAND ----------

# MAGIC %md ## 4. Flatten OASIS JSON -> `hsa.cqi_oasis_items`
# MAGIC The dashboard OASIS source holds one JSON array per assessment in `oasis_event_desc`.
# MAGIC The booklet needs one row per item: patient, episode, assessment, date, timepoint, item, section, description, answer.

# COMMAND ----------

from pyspark.sql import functions as F, types as T
OASIS_SRC = "hsa.cqi_oasis_events"            # VERIFY: table behind the Power BI "Patient Profile: OASIS" page
item_t = T.ArrayType(T.MapType(T.StringType(), T.StringType()))
raw = spark.table(OASIS_SRC)
oasis_items = (raw
    .withColumn("it", F.explode(F.from_json("oasis_event_desc", item_t)))
    .select(F.col("patient_id").cast("string"), F.col("episode_id").cast("string"),
            F.col("oasis_assessment_id").cast("string").alias("assessment_id"),
            F.to_date(F.coalesce(F.col("it")["event_date"], F.col("it")["last_update_oasis"])).alias("event_date"),
            F.regexp_extract("oasis_event_type_long", r"type=(.*)$", 1).alias("oasis_event_type_long"),
            F.col("it")["om_monumber_c"].alias("om_monumber_c"),
            F.col("it")["parent_section_title"].alias("parent_section_title"),
            F.coalesce(F.col("it")["OM_DESCRIPTION"], F.col("it")["question_text"]).alias("OM_DESCRIPTION"),
            F.coalesce(F.col("it")["CEOA_ANSWER"], F.col("it")["CEOA_OASISANSWER"]).alias("CEOA_ANSWER"))
    .filter(F.col("om_monumber_c").isNotNull() & (F.col("om_monumber_c") != "NotFound")))
oasis_items.write.format("delta").mode("overwrite").saveAsTable("hsa.cqi_oasis_items")
display(oasis_items.limit(20))

# COMMAND ----------

# MAGIC %md ## 5. Next
# MAGIC Run `01_build_booklets` - it uses this same registry (`source` widget: auto | csv | table | path).
