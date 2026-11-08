# Databricks notebook source
# MAGIC %md
# MAGIC # Project Alpha · resolve the CQI run date
# MAGIC First task of the weekly job. Uses the `as_of_date` job parameter if given, otherwise the latest
# MAGIC `as_of_date` in `risk_scores` (read through the same dataset registry as the booklet pipeline).
# MAGIC Publishes it as the task value `as_of_date` for the next tasks.

# COMMAND ----------

dbutils.widgets.text("as_of_date", "")
dbutils.widgets.dropdown("source", "auto", ["auto", "csv", "table", "path"])

import os, sys
ROOT = os.path.abspath("..")                       # this folder sits in project_alpha/databricks
sys.path.insert(0, f"{ROOT}/cqi_booklet/src")
from pyspark.sql import functions as F
from cqi_datasets import Datasets

as_of = dbutils.widgets.get("as_of_date").strip()
if not as_of:
    ds = Datasets(f"{ROOT}/cqi_booklet/config/datasets.yaml", engine="spark", spark=spark, source=dbutils.widgets.get("source"))
    as_of = ds.load("risk_scores").agg(F.max("as_of_date")).first()[0].strftime("%Y-%m-%d")
dbutils.jobs.taskValues.set(key="as_of_date", value=as_of)
print("CQI run date:", as_of)
