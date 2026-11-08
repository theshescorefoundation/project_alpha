# Databricks notebook source
# MAGIC %md
# MAGIC # Project Alpha · weekly CQI agent + branch reports
# MAGIC Runs after `cqi_booklet/notebooks/01_build_booklets` (patient payloads + booklets on the Volume).
# MAGIC
# MAGIC | Step | What happens |
# MAGIC |---|---|
# MAGIC | 1 | Dataset registry (`cqi_booklet/config/datasets.yaml`): each dataset from a Unity Catalog `table`, a lake `path` (delta / parquet) or csv |
# MAGIC | 2 | Active episodes for the run date (optionally pilot branches); only their rows go to pandas |
# MAGIC | 3 | **CQI agent**: sense → detect care gaps → prioritise → explain → publish, with memory of last week |
# MAGIC | 4 | Branch summaries + branded branch PDFs (same code as the laptop) |
# MAGIC | 5 | Delta tables for Power BI / the app: `cqi_care_opportunities`, `cqi_branch_summary` |
# MAGIC
# MAGIC Cluster libraries: `reportlab matplotlib pyyaml` (+ `anthropic` or `google-genai` only if `agent.explain_with_llm: true`).

# COMMAND ----------

dbutils.widgets.text("as_of_date", "")
dbutils.widgets.dropdown("source", "auto", ["auto", "csv", "table", "path"])
dbutils.widgets.text("branches", "")              # optional pilot list, e.g. "BRANCH 0412 - CENTERWELL HOME HEALTH"

import os, sys, json, glob
ROOT = os.path.abspath("..")                       # project_alpha/
sys.path[:0] = [ROOT, f"{ROOT}/cqi_booklet/src", f"{ROOT}/branch_report", f"{ROOT}/agent"]

AS_OF = dbutils.widgets.get("as_of_date").strip() or dbutils.jobs.taskValues.get(taskKey="resolve_run_date", key="as_of_date", debugValue="2026-08-15")
SOURCE = dbutils.widgets.get("source")
BRANCHES = [b.strip() for b in dbutils.widgets.get("branches").split(",") if b.strip()]

BOOKLETS = "/Volumes/hsa/cqi/booklets"            # VERIFY: same Volume as 01_build_booklets OUT_DIR (without the date)
AGENT_OUT = "/Volumes/hsa/cqi/agent"               # VERIFY: PHI-approved Volume
BRANCH_OUT = "/Volumes/hsa/cqi/branch_reports"     # VERIFY
OPP_TABLE, BRANCH_TABLE = "hsa.cqi_care_opportunities", "hsa.cqi_branch_summary"   # VERIFY schema
os.environ["PROJECT_ALPHA_CONFIG"] = f"{ROOT}/config/project_alpha.yaml"

# COMMAND ----------

# MAGIC %md ## 1-2 · Registry and active-episode loader

# COMMAND ----------

from pyspark.sql import functions as F
from cqi_datasets import Datasets
import aggregate as A

ds = Datasets(f"{ROOT}/cqi_booklet/config/datasets.yaml", engine="spark", spark=spark, source=SOURCE)
asof = F.to_date(F.lit(AS_OF))
ep = ds.load("episodes").withColumn("episode_status", F.regexp_replace(F.upper(F.trim("episode_status")), "[ _]+", "-"))
keys = (ep.filter(F.col("episode_status").isin(*A.EPI_STATUSES))
          .filter((F.col("episode_start") <= asof) & (F.coalesce("episode_end", F.date_add("episode_start", 59)) >= asof)))
if BRANCHES:
    keys = keys.filter(F.col("branch_name").isin(BRANCHES))
keys = keys.select("patient_id", "episode_id").distinct().cache()
print("active episodes:", keys.count())

def load(name):
    """Spark (UC table / parquet / delta / csv) -> only active-episode rows -> pandas, canonical columns."""
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

# COMMAND ----------

# MAGIC %md ## 3 · CQI agent

# COMMAND ----------

import cqi_agent
stats = cqi_agent.run(AS_OF, load=load, out_root=AGENT_OUT)
print(json.dumps({k: stats[k] for k in ("episodes_scanned", "opportunities", "within_24h", "new", "resolved", "by_type")}, indent=1))
print(open(f"{AGENT_OUT}/{AS_OF}/agent_brief.md").read())

# COMMAND ----------

# MAGIC %md ## 4 · Branch summaries + PDFs

# COMMAND ----------

import build_branch
summaries = A.build_branch_summaries(AS_OF, json_dir=f"{BOOKLETS}/{AS_OF}/json", load=load)
os.makedirs(f"{BRANCH_OUT}/{AS_OF}", exist_ok=True)
for br, s in summaries.items():
    safe = s["branch_label"].replace(" ", "_")
    json.dump(s, open(f"{BRANCH_OUT}/{AS_OF}/{safe.lower()}_summary.json", "w"), indent=1, default=str)
    build_branch.build_pdf(s, f"{BRANCH_OUT}/{AS_OF}/{safe}_report.pdf")
print(f"{len(summaries)} branch reports -> {BRANCH_OUT}/{AS_OF}")

# COMMAND ----------

# MAGIC %md ## 5 · Delta tables (replace this run date)

# COMMAND ----------

opps = json.load(open(f"{AGENT_OUT}/{AS_OF}/opportunities.json"))
if opps:
    rows = [{**{k: o.get(k) for k in ("id", "priority", "when", "branch", "patient_id", "episode_id", "type", "discipline", "finding",
                                     "action", "owner", "status", "weeks_open", "first_seen", "risk_level", "risk_score", "rising", "days_to_end")},
             "sources": ", ".join(o.get("sources") or []), "evidence_json": json.dumps(o.get("evidence"), default=str)} for o in opps]
    (spark.createDataFrame(rows).withColumn("as_of_date", F.to_date(F.lit(AS_OF))).withColumn("published_ts", F.current_timestamp())
          .write.format("delta").mode("overwrite").option("replaceWhere", f"as_of_date = '{AS_OF}'").partitionBy("as_of_date").saveAsTable(OPP_TABLE))
brow = [(AS_OF, br, s["branch_label"], json.dumps(s, default=str)) for br, s in summaries.items()]
(spark.createDataFrame(brow, "as_of_date string, branch string, branch_label string, summary_json string")
      .withColumn("as_of_date", F.to_date("as_of_date")).withColumn("published_ts", F.current_timestamp())
      .write.format("delta").mode("overwrite").option("replaceWhere", f"as_of_date = '{AS_OF}'").partitionBy("as_of_date").saveAsTable(BRANCH_TABLE))
display(spark.table(OPP_TABLE).filter(F.col("as_of_date") == asof).orderBy(F.desc("priority")))
