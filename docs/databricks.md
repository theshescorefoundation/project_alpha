# Running Project Alpha with Databricks

## The short version
| Piece | Where it runs | Why |
|---|---|---|
| **Data prep**: patient payloads + booklets (original pipeline), **CQI agent**, branch reports | **Databricks job**, weekly (`databricks.yml`) | Reads Unity Catalog tables / lake files directly; same code as the laptop |
| **Gradio app** (Project Alpha) | **Your laptop** for the demo; Databricks Apps later, if your workspace enables it | Your company can't host web apps; a notebook can't serve a UI to other people safely |

All the logic is shared Python, so nothing has to be rewritten to move from CSV to tables.

## 1. Data: CSV today, Unity Catalog tables or parquet tomorrow
Every dataset is declared once in `cqi_booklet/config/datasets.yaml` (the original pipeline's registry). Each one can come from:

```yaml
visits:
  source: table                           # csv | table | path | auto
  table: lighthouse.lvl3.client_visits    # Unity Catalog: catalog.schema.table
  # or a folder of parquet / delta files:
  path: /Volumes/lighthouse/lvl3/client_visits
  format: parquet
  rename: {visit_dt: VISIT_DATE, ...}     # source column -> canonical name (VERIFY items)
```

- `source: auto` picks the table if one is configured, else the path, else the CSV.
- **Switch every dataset at once** with the job parameter `source`, or env `CQI_SOURCE=table`.
- Downstream code only ever sees canonical column names, so the booklet builder, branch roll-up, agent and app don't change.
- **Laptop:** `data.source: csv` in `config/project_alpha.yaml`, reading `cqi_booklet/data/csv`.
- **Databricks:** the notebooks build `Datasets(..., engine="spark", spark=spark, source=...)` and pass a `load(name)` function to the agent and the branch roll-up. It keeps only active-episode rows before converting to pandas.

## 2. The weekly job (agentic data prep)
`databricks.yml` is a Databricks Asset Bundle. It defines one job, **"Project Alpha · weekly CQI agent"**, scheduled for Mondays at 06:00 and starting **PAUSED**:

```
resolve_run_date  ->  build_booklets (original 01 notebook, unchanged)  ->  cqi_agent_and_branch_reports
```

| Task | Notebook | Output |
|---|---|---|
| resolve_run_date | `databricks/00_resolve_run_date.py` | Picks the latest `risk_scores` date unless `as_of_date` is given |
| build_booklets | `cqi_booklet/notebooks/01_build_booklets.py` | Payload JSON + booklets on the Volume; `hsa.cqi_booklet_payload` |
| cqi_agent_and_branch_reports | `databricks/02_project_alpha_weekly.py` | Agent findings, branch PDFs; `hsa.cqi_care_opportunities`, `hsa.cqi_branch_summary` |

**What makes it an agent** (`agent/cqi_agent.py`). Each week it does:

1. **Sense:** load the operational datasets for every active episode, not just the CQI list.
2. **Detect:** run eleven care-gap detectors:
   - vitals outside limits or weight gain
   - monitoring gaps
   - alerts not escalated to the MD
   - ordered visits not booked
   - missed visits
   - high-risk medications without a teach-back check
   - wounds not reassessed
   - goals behind plan
   - discharge/recert window
   - rising risk
   - no PCP
3. **Prioritise:** urgency + risk tier + rising risk.
4. **Explain:** a branch brief, rule-based by default. Set `agent.explain_with_llm: true` to have the configured model write it.
5. **Publish + remember:** each finding is compared with last week's run as new / ongoing (weeks open) / resolved. Outputs: `opportunities.json/.csv`, `agent_brief.md`, `agent_run.json`.

The detector thresholds and urgency weights live in `config/project_alpha.yaml -> agent`.

**Deploy:**
```bash
databricks auth login --host https://<your-workspace>
databricks bundle validate
databricks bundle deploy -t dev
databricks bundle run project_alpha_weekly -t dev
```

**Before the first run, check every `VERIFY`:**
- **Volumes and schemas.** Defaults are `/Volumes/hsa/cqi/{booklets,agent,branch_reports}` and `hsa.*` tables. Use PHI-approved locations only.
- **`ROOT` in the original `01_build_booklets.py`.** It is hard-coded to `/Workspace/Shared/cqi/cqi_booklet`. Point it at the bundle's `cqi_booklet` folder.
- **Runtime:** DBR **16.x (Python 3.12+)**. The original pipeline uses Python 3.12 f-string syntax (`cqi_payload.py`), so DBR 15.x (Python 3.11) fails to import it.
- **Cluster policy, node type, and the notification email.**
- The source-column `rename`s in `datasets.yaml`.

## 3. The app: demo on your laptop, fed by Databricks
Gradio can't be shared from a notebook without Databricks Apps. Running `share=True` from a notebook opens a public tunnel, which isn't acceptable for PHI. So the app runs on your machine and reads the job's outputs:

1. **Copy one run's outputs to your laptop.** Synthetic or approved data only.
   ```bash
   databricks fs cp -r dbfs:/Volumes/hsa/cqi/booklets/2026-08-15 data/synced/booklets/2026-08-15
   ```
   ```bash
   databricks fs cp -r dbfs:/Volumes/hsa/cqi/agent/2026-08-15 data/synced/agent/2026-08-15
   ```
2. **Point the app at the copy** in `config/project_alpha.yaml`:
   ```yaml
   data:
     output_root: data/synced/booklets
     agent_root:  data/synced/agent
   ```
3. **Run the app:**
   ```bash
   .venv\Scripts\python webapp\app.py
   ```
   Then open http://localhost:7871.

The branch roll-up in the app also reads the operational datasets. With `data.source: csv` it uses `cqi_booklet/data/csv`, so also copy a CSV snapshot. `Datasets.snapshot_to_csv()` in the original registry writes one.

**Later: Databricks Apps.** If your workspace turns on Databricks Apps, the same `webapp/app.py` runs there with Unity Catalog permissions. The app would read the Volume paths directly (set `output_root` to the Volume), and no data leaves the workspace.

## 4. Changing the AI model
Everything is in `config/project_alpha.yaml -> llm`. To change the model, change one word:

```yaml
llm:
  provider: anthropic        # gemini | anthropic | gateway | rules
```

| Provider | Uses | Set |
|---|---|---|
| `gemini` | Gemini Flash (`gemini-2.5-flash`) | `GEMINI_API_KEY` |
| `anthropic` | **Claude Opus 5.5** (`claude-opus-5-5`) via the official `anthropic` SDK | `ANTHROPIC_API_KEY` (or `ant auth login`); optional `llm.anthropic.base_url` for a gateway that proxies the Anthropic API |
| `gateway` | Any OpenAI-compatible endpoint: company AI gateway, **Databricks Model Serving**, vLLM | `llm.gateway.base_url` (or `base_url_env`), `model`, `auth.token_env`, extra headers, CA bundle |
| `rules` | No model; recognised questions only | — |

- **Databricks Model Serving** as the gateway: `base_url: https://<workspace>/serving-endpoints`, `model: <endpoint-name>`, `auth.token_env: DATABRICKS_TOKEN`.
- Keys are never stored in the file. The file only names the environment variable.
- `approved_for_phi` stays `false` until your security team approves the endpoint. While it's false, AI answers are switched off for non-sample data.
- With Claude, the app asks for short answers (`effort: low`) and turns on server-side refusal fallback. Set `refusal_fallback: false` if your gateway rejects it.
- The app re-reads the config file when it changes, so you can switch providers between questions without restarting.
