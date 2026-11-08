# Databricks notebook source
# MAGIC %md
# MAGIC # Project Alpha · CQI agent only (lightweight job)
# MAGIC Scans every active episode for care gaps and publishes the findings - **no PDFs, no AI libraries**.
# MAGIC Needs only what Databricks Runtime already ships (pandas) plus `pyyaml`.
# MAGIC
# MAGIC | Step | What happens |
# MAGIC |---|---|
# MAGIC | 1 | Dataset registry: Unity Catalog `table`, lake `path` (delta / parquet) or csv, per `cqi_booklet/config/datasets.yaml` |
# MAGIC | 2 | Last conference decision per patient (from `hsa.cqi_conference_decisions`, if loaded) |
# MAGIC | 3 | CQI agent: sense → detect → prioritise → explain (rules) → publish, with memory of last week |
# MAGIC | 4 | `hsa.cqi_care_opportunities` Delta table + files on the Volume |

# COMMAND ----------

dbutils.widgets.text("as_of_date", "")
dbutils.widgets.dropdown("source", "auto", ["auto", "csv", "table", "path"])
dbutils.widgets.text("branches", "")

import os, sys, json
ROOT = os.path.abspath("..")                       # project_alpha/
sys.path[:0] = [ROOT, f"{ROOT}/cqi_booklet/src", f"{ROOT}/branch_report", f"{ROOT}/agent", f"{ROOT}/databricks"]
os.environ["PROJECT_ALPHA_CONFIG"] = f"{ROOT}/config/project_alpha.yaml"

AS_OF = dbutils.widgets.get("as_of_date").strip() or dbutils.jobs.taskValues.get(taskKey="resolve_run_date", key="as_of_date", debugValue="2026-08-15")
SOURCE = dbutils.widgets.get("source")
BRANCHES = [b.strip() for b in dbutils.widgets.get("branches").split(",") if b.strip()]

AGENT_OUT = "/Volumes/hsa/cqi/agent"                       # VERIFY: PHI-approved Volume
OPP_TABLE = "hsa.cqi_care_opportunities"                   # VERIFY schema
DECISIONS_TABLE = "hsa.cqi_conference_decisions"           # VERIFY schema

# COMMAND ----------

from cqi_datasets import Datasets
from alpha_spark import active_episode_loader, write_opportunities, latest_decisions
import cqi_agent

ds = Datasets(f"{ROOT}/cqi_booklet/config/datasets.yaml", engine="spark", spark=spark, source=SOURCE)
load, keys = active_episode_loader(spark, ds, AS_OF, BRANCHES)
print("active episodes:", keys.count())

stats = cqi_agent.run(AS_OF, load=load, out_root=AGENT_OUT, explain=False, decisions=latest_decisions(spark, DECISIONS_TABLE))
print(json.dumps({k: stats[k] for k in ("episodes_scanned", "opportunities", "within_24h", "new", "resolved", "by_type")}, indent=1))
print(open(f"{AGENT_OUT}/{AS_OF}/agent_brief.md").read())

# COMMAND ----------

opps = json.load(open(f"{AGENT_OUT}/{AS_OF}/opportunities.json"))
print(write_opportunities(spark, opps, AS_OF, OPP_TABLE), "rows ->", OPP_TABLE)
display(spark.table(OPP_TABLE).filter(f"as_of_date = '{AS_OF}'").orderBy("priority", ascending=False))
