# Databricks notebook source
# MAGIC %md
# MAGIC # Project Alpha · weekly CQI agent + branch reports
# MAGIC Runs after `cqi_booklet/notebooks/01_build_booklets` (patient payloads + booklets on the Volume).
# MAGIC
# MAGIC | Step | What happens |
# MAGIC |---|---|
# MAGIC | 1 | Dataset registry (`cqi_booklet/config/datasets.yaml`): each dataset from a Unity Catalog `table`, a lake `path` (delta / parquet) or csv |
# MAGIC | 2 | Active episodes for the run date (optionally pilot branches); only their rows go to pandas |
# MAGIC | 3 | **CQI agent**: sense → detect care gaps → prioritise → explain → publish, with memory of last week and each patient's last conference decision |
# MAGIC | 4 | Branch summaries + branded branch PDFs (same code as the laptop) |
# MAGIC | 5 | Delta tables for Power BI / the app: `cqi_care_opportunities`, `cqi_branch_summary` |
# MAGIC
# MAGIC Cluster libraries: `reportlab matplotlib pyyaml` (+ `anthropic` or `google-genai` only if `agent.explain_with_llm: true`).

# COMMAND ----------

dbutils.widgets.text("as_of_date", "")
dbutils.widgets.dropdown("source", "auto", ["auto", "csv", "table", "path"])
dbutils.widgets.text("branches", "")              # optional pilot list, e.g. "BRANCH 0412 - CENTERWELL HOME HEALTH"

import os, sys, json, glob
from pyspark.sql import functions as F
ROOT = os.path.abspath("..")                       # project_alpha/
sys.path[:0] = [ROOT, f"{ROOT}/cqi_booklet/src", f"{ROOT}/branch_report", f"{ROOT}/agent", f"{ROOT}/databricks"]

AS_OF = dbutils.widgets.get("as_of_date").strip() or dbutils.jobs.taskValues.get(taskKey="resolve_run_date", key="as_of_date", debugValue="2026-08-15")
SOURCE = dbutils.widgets.get("source")
BRANCHES = [b.strip() for b in dbutils.widgets.get("branches").split(",") if b.strip()]

BOOKLETS = "/Volumes/hsa/cqi/booklets"            # VERIFY: same Volume as 01_build_booklets OUT_DIR (without the date)
AGENT_OUT = "/Volumes/hsa/cqi/agent"               # VERIFY: PHI-approved Volume
BRANCH_OUT = "/Volumes/hsa/cqi/branch_reports"     # VERIFY
OPP_TABLE, BRANCH_TABLE = "hsa.cqi_care_opportunities", "hsa.cqi_branch_summary"   # VERIFY schema
DECISIONS_TABLE = "hsa.cqi_conference_decisions"                                   # VERIFY schema
os.environ["PROJECT_ALPHA_CONFIG"] = f"{ROOT}/config/project_alpha.yaml"

# COMMAND ----------

# MAGIC %md ## 1-2 · Registry, active-episode loader, last conference decisions

# COMMAND ----------

from cqi_datasets import Datasets
from alpha_spark import active_episode_loader, write_opportunities, latest_decisions
import aggregate as A

ds = Datasets(f"{ROOT}/cqi_booklet/config/datasets.yaml", engine="spark", spark=spark, source=SOURCE)
load, keys = active_episode_loader(spark, ds, AS_OF, BRANCHES)
print("active episodes:", keys.count())
decisions = latest_decisions(spark, DECISIONS_TABLE)
print("patients with a recorded conference decision:", len(decisions))

# COMMAND ----------

# MAGIC %md ## 3 · CQI agent

# COMMAND ----------

import cqi_agent
stats = cqi_agent.run(AS_OF, load=load, out_root=AGENT_OUT, decisions=decisions)
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
print(write_opportunities(spark, opps, AS_OF, OPP_TABLE), "rows ->", OPP_TABLE)
brow = [(AS_OF, br, s["branch_label"], json.dumps(s, default=str)) for br, s in summaries.items()]
(spark.createDataFrame(brow, "as_of_date string, branch string, branch_label string, summary_json string")
      .withColumn("as_of_date", F.to_date("as_of_date")).withColumn("published_ts", F.current_timestamp())
      .write.format("delta").mode("overwrite").option("replaceWhere", f"as_of_date = '{AS_OF}'").partitionBy("as_of_date").saveAsTable(BRANCH_TABLE))
display(spark.table(OPP_TABLE).filter(f"as_of_date = '{AS_OF}'").orderBy(F.desc("priority")))
