"""Spark helpers shared by the Project Alpha Databricks notebooks (plain module, not a notebook).

    from alpha_spark import active_episode_loader, write_opportunities, latest_decisions
Only pyspark (on the cluster) + pandas + pyyaml are needed - no PDF or AI libraries.
"""
from __future__ import annotations

import json
import os

from pyspark.sql import functions as F
from pyspark.sql.window import Window

EPI_STATUSES = ("CURRENT", "RECERTIFIED", "ON-HOLD")   # same as the booklet pipeline

DECISION_COLUMNS = ["saved_ts", "as_of_date", "branch", "patient_id", "episode_id", "decision",
                    "action_owner", "due_date", "notes_discussion", "reviewed_by"]


def active_episode_loader(spark, ds, as_of, branches=None):
    """Return (load, keys). load(name) reads a dataset through the registry (Unity Catalog table, delta/parquet
    path or csv), keeps only rows of episodes active on as_of (optionally pilot branches) and returns pandas."""
    asof = F.to_date(F.lit(as_of))
    ep = ds.load("episodes").withColumn("episode_status", F.regexp_replace(F.upper(F.trim("episode_status")), "[ _]+", "-"))
    keys = (ep.filter(F.col("episode_status").isin(*EPI_STATUSES))
              .filter((F.col("episode_start") <= asof) & (F.coalesce("episode_end", F.date_add("episode_start", 59)) >= asof)))
    if branches:
        keys = keys.filter(F.col("branch_name").isin(list(branches)))
    keys = keys.select("patient_id", "episode_id").distinct().cache()

    def load(name):
        sdf = ds.load(name)
        if sdf is None:
            return None
        if "episode_id" in sdf.columns:
            sdf = sdf.join(keys, ["patient_id", "episode_id"], "left_semi")
        elif "patient_id" in sdf.columns:
            sdf = sdf.join(keys.select("patient_id").distinct(), ["patient_id"], "left_semi")
        pdf = sdf.toPandas()
        for c in ("patient_id", "episode_id"):
            if c in pdf:
                pdf[c] = pdf[c].astype("string")
        return pdf

    return load, keys


def write_opportunities(spark, opps, as_of, table):
    """Replace this run date's rows in the care-opportunities Delta table."""
    if not opps:
        return 0
    rows = [{**{k: (None if o.get(k) is None else str(o.get(k))) for k in
                ("id", "when", "branch", "patient_id", "episode_id", "type", "discipline", "finding", "action", "owner",
                 "status", "first_seen", "risk_level", "rising")},
             "priority": int(o.get("priority") or 0), "weeks_open": int(o.get("weeks_open") or 1),
             "risk_score": None if o.get("risk_score") is None else float(o["risk_score"]),
             "days_to_end": None if o.get("days_to_end") is None else int(o["days_to_end"]),
             "sources": ", ".join(o.get("sources") or []),
             "last_decision": (o.get("last_decision") or {}).get("decision"),
             "evidence_json": json.dumps(o.get("evidence"), default=str)} for o in opps]
    (spark.createDataFrame(rows).withColumn("as_of_date", F.to_date(F.lit(as_of))).withColumn("published_ts", F.current_timestamp())
          .write.format("delta").mode("overwrite").option("replaceWhere", f"as_of_date = '{as_of}'")
          .option("mergeSchema", "true").partitionBy("as_of_date").saveAsTable(table))
    return len(rows)


def load_decision_files(spark, folder, table):
    """Append every decisions.csv under `folder` (uploaded from the app) to the decisions Delta table.
    Idempotent: rows already in the table (same patient, episode, saved_ts) are skipped."""
    files = [os.path.join(d, f) for d, _, fs in os.walk(folder) for f in fs if f.lower().endswith(".csv")] if os.path.isdir(folder) else []
    if not files:
        return 0, []
    raw = spark.read.option("header", True).option("multiLine", True).option("escape", '"').csv(files)
    df = (raw.select(*[F.col(c).cast("string").alias(c) if c in raw.columns else F.lit(None).cast("string").alias(c)
                       for c in DECISION_COLUMNS], "_metadata")
               .withColumn("source_file", F.col("_metadata.file_path")).drop("_metadata").withColumn("loaded_ts", F.current_timestamp())
               .dropDuplicates(["patient_id", "episode_id", "saved_ts"]))
    if spark.catalog.tableExists(table):
        df = df.join(spark.table(table).select("patient_id", "episode_id", "saved_ts"), ["patient_id", "episode_id", "saved_ts"], "left_anti")
    n = df.count()
    if n:
        df.write.format("delta").mode("append").option("mergeSchema", "true").saveAsTable(table)
    return n, files


def latest_decisions(spark, table):
    """Most recent conference decision per patient-episode, as a list of dicts (for the agent's memory)."""
    if not spark.catalog.tableExists(table):
        return []
    w = Window.partitionBy("patient_id", "episode_id").orderBy(F.col("saved_ts").desc())
    return [r.asDict() for r in spark.table(table).withColumn("_r", F.row_number().over(w)).filter("_r = 1").drop("_r").collect()]
