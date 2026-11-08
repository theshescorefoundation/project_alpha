# Project Alpha web app

Search-first Gradio app for the weekly CQI Patient Conference: you ask one question and the relevant answer appears below it. Styling follows clinician_copilot: the CenterWell blue gradient, the Inter font, lime and sky accents, and the leaf mark.

## Run (from the `project_alpha` folder)
```powershell
# once per CQI run: patient payloads + booklets (original pipeline)
cd cqi_booklet; ..\.venv\Scripts\python src\build_payloads.py --as-of 2026-08-15 --sample; cd ..
# the app
.venv\Scripts\python webapp\app.py          # http://localhost:7871
```
Set `GEMINI_API_KEY` to enable answers to typed, open questions. Without a key, everything else still works from the data.

## How questions are answered
| Question type | Answered by | Example |
|---|---|---|
| Recognised questions | `engine.py` rules, straight from the payloads and branch summaries (instant, cited) | "Who needs action today?", "Is 9000345 ready for discharge?", "Are her vitals stable?" |
| Open / judgement questions | `llm.py` → scope filter → Gemini Flash, grounded only in that patient's payload or the week's branch summaries | "What should the team worry about most for 9000123 before the weekend?" |
| Off-topic | Refused by the scope filter | "What's the weather?", "Write a Python script" |

Follow-up questions remember the patient, so "she" or "this patient" refers to the last patient asked about.

## Files
| File | Role |
|---|---|
| `app.py` | Layout, CSS and event wiring |
| `engine.py` | Intent routing, answers and readiness (booklet rules) |
| `views.py` | HTML cards, chips, tables and vitals sparklines |
| `llm.py` | Gemini / company gateway client, scope filter, grounding context, AI log |
| `data.py` | Loads payloads, branch summaries, PDFs and conference decisions |
| `project_alpha_ai_gateway.yaml` | Backend switch, model, filter prompts and logging |

## Outputs
Written to `webapp/output/<run>/decisions.csv` (conference decisions, using the booklet's form field names) and `webapp/output/ai_log.csv`. Both are git-ignored.
