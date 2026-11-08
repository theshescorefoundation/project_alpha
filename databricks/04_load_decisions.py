# Databricks notebook source
# MAGIC %md
# MAGIC # Project Alpha · load conference decisions
# MAGIC The app saves each conference decision to `webapp/output/<run date>/decisions.csv` on the laptop.
# MAGIC Upload that file to the decisions Volume (see `docs/databricks.md`), then this notebook appends new rows to
# MAGIC **`hsa.cqi_conference_decisions`** (re-running is safe: rows already loaded are skipped).
# MAGIC The weekly jobs run it first, so the agent knows each patient's last decision.
# MAGIC
# MAGIC Columns match the booklet's fillable form fields: `decision` (agree / agree_changes / disagree / escalate),
# MAGIC `action_owner`, `due_date`, `notes_discussion`, `reviewed_by`, plus `saved_ts`, `as_of_date`, `branch`, `patient_id`, `episode_id`.

# COMMAND ----------

import os, sys
ROOT = os.path.abspath("..")
sys.path.insert(0, f"{ROOT}/databricks")
from alpha_spark import load_decision_files

DECISIONS_VOLUME = "/Volumes/hsa/cqi/decisions"            # VERIFY: PHI-approved Volume; one folder per run date
DECISIONS_TABLE = "hsa.cqi_conference_decisions"           # VERIFY schema

n, files = load_decision_files(spark, DECISIONS_VOLUME, DECISIONS_TABLE)
print(f"{len(files)} decision file(s) found, {n} new row(s) -> {DECISIONS_TABLE}")

# COMMAND ----------

if spark.catalog.tableExists(DECISIONS_TABLE):
    display(spark.sql(f"""
        SELECT as_of_date, decision, count(*) AS patients
        FROM (SELECT *, row_number() OVER (PARTITION BY patient_id, episode_id, as_of_date ORDER BY saved_ts DESC) AS r
              FROM {DECISIONS_TABLE}) WHERE r = 1
        GROUP BY as_of_date, decision ORDER BY as_of_date DESC, patients DESC"""))
