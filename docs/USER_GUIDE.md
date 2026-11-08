# Project Alpha: user guide
**CenterWell Home Health · Clinical Quality Intelligence (CQI) · Weekly Patient Conference**

Project Alpha answers one question: *which patient needs care today, before it's too late?*

Every week the **CQI agent** scans operational data (visits, vitals, OASIS, goals, medications, wounds, alerts) for every active episode and lists the care gaps it finds. In the conference you ask Project Alpha questions in plain English. It answers from that week's data and shows where each answer came from.

> Proof of concept on **sample data**. Findings are AI-assisted drafts for clinical review. Clinicians and physicians make every decision.

---

## 1. Opening the app
1. Your coordinator starts Project Alpha on the conference laptop.
2. Open **http://localhost:7871** in Edge or Chrome.
3. A short **intro screen** plays: the CQI agent, care gaps, prioritisation, discharge readiness, Ask Alpha. It ends with this week's numbers. **Click anywhere to skip it.**

## 2. The home screen
| Area | What it shows |
|---|---|
| **Header** (top left) | CenterWell **Project Alpha**, "powered by the CQI agent – Alpha for CWHH Clinical Quality Team", and the "Proof of concept" badge |
| **💡 Guide** (top right) | Opens this user guide inside the app; close it with ✕ or by clicking outside it |
| **Agent status** (green pill) | What the agent found this run, e.g. *"scanned 4 episodes · 20 care opportunities · 8 within 24 h · 5 new"* |
| **Search box** | Type any question about this week's patients or branches, then press **Enter** or the arrow |
| **Suggested questions** | One-click starting points |
| **Status line** | Number of patients and branches on this week's list, "AI-assisted · answers only from CQI data" |
| **Footer** (bottom of every screen) | The CQI run date and conference week the data comes from |

## 3. Asking questions
Ask the way you would ask a colleague. For a question about one patient, include the patient's **MRN** as it appears in your records. You can write it on its own (*9000123*) or with a label (*MRN 9000123*, *patient #9000123*). The examples below use sample MRNs.

| You want to know | Try asking |
|---|---|
| What needs doing first | *Who needs action today?* |
| What the agent found | *What did the CQI agent find this week?* |
| Who could go home | *Who is ready for discharge?* · *Is 9000345 ready for discharge?* |
| One patient's picture | *Tell me about 9000123*, or just type the MRN |
| Vitals | *Are 9000123's vitals stable?* |
| Goals | *Which goals aren't met for 9000123?* |
| Why they're on the list | *Why is 9000123 on the list?* |
| Visits | *Why are visits being missed?* · *Were visits missed for 9000123?* |
| Medications | *What medications is 9000123 on?* |
| Wounds and function | *Wounds for 9000123* · *Has function improved for 9000345?* |
| Falls, ED visits, alerts | *Any falls or ED visits for 9000123?* |
| After discharge | *Who follows 9000345 after discharge?* |
| Data quality | *What data gaps exist for 9000345?* |
| A branch | *How is Branch 0412 doing?* · *How are the branches doing?* |
| Judgement questions | *What should the team worry about most for 9000123 before the weekend?* |

**Follow-ups remember the patient.** After asking about 9000123 you can ask *"Are her vitals stable?"* or *"What about this patient's goals?"*.

## 4. Reading an answer
After your first question the search box moves to the top, and the answer appears below it.

1. **Answer card.** The badge tells you where the answer came from:
   - **● From the CQI data:** read straight from the data. Instant, and the same every time.
   - **✦ AI answer · grounded in CQI data:** written by AI for open questions, using only this patient's or this week's data. Check it against the evidence below it.
   - **Outside Project Alpha's scope:** the question wasn't about this conference's patients or data, so nothing was answered.
2. **Sources.** The grey tags under every answer name the data behind it, such as `vitals`, `llm_goals` or `orders`.
3. **Follow-up questions.** Click one to continue. **⌂ Start over** returns to the home screen.
4. **Evidence cards** below the answer, depending on the question:
   - **Patient card:** risk tier and trend, vitals trend, episode day, "Nearing discharge", the patient's own goal in quotes, what CQI found.
   - **Discharge readiness:** the system's recommendation, *Supports discharge* (green) vs. *Supports recertification* (red), *Check first* data caveats, and *Next steps if the team agrees*.
   - **Vitals charts:** weight, SpO₂, pulse and systolic BP against the patient's limits (dashed lines). Red means outside a limit or a weight gain of 5 lb or more.
   - **Tables** for goals, visits (ordered / delivered / remaining / booked / not booked), OASIS (start of care → latest), wounds, medications, events and referral indicators.
   - **CQI agent findings:** when to act (red = within 24 hours), the gap, the finding, the action and owner, whether it's **New** or has been open for several weeks, and the last conference decision.

### What the urgency colours mean
| Label | Meaning |
|---|---|
| **Within 24 hrs** (red) | Act today, e.g. vitals outside limits, an alert never escalated to the MD, unbooked visits for a deteriorating patient |
| **This conference** (amber) | Decide in today's meeting, e.g. the discharge/recert window or rising risk |
| **This week** (blue) | Plan this week, e.g. missed visits, wounds not reassessed, goals behind plan |
| **Next visit** | At the next SN visit, e.g. teach-back on high-risk medications |
| **Before discharge** (green) | Before the episode ends, e.g. referrals or arranging a PCP |

## 5. Discharge readiness
Ask *"Who is ready for discharge?"* for the whole list, soonest episode end first, or *"Is 9000345 ready for discharge?"* for one patient.

- **Within 14 days of episode end:** you see the **system recommendation** (Discharge, Discharge with referrals, Recertify or Clinical review), how strong the signal is, and the evidence on each side. This is the same rule as the booklet's "Discharge or recertification" page.
- **Not yet in the window:** you see a **readiness snapshot** of the same signals, labelled *Leaning discharge*, *Not ready* or *Mixed signals*, so the team can plan ahead.

Signals include goals met, vitals trend, wound healing, risk tier, OASIS function change, adverse events, prognosis and rehab potential. **The team and physician decide.** The system only lays out the evidence.

## 6. Recording the conference decision
When you've asked about a patient, a **Conference decision · patient …** section appears at the bottom.
1. Click it to open it.
2. Choose **Agree with CQI**, **Agree with changes**, **Disagree** or **Escalate to MD**.
3. Add the **action owner**, **due date**, **notes** and **reviewed by**.
4. Click **Save decision**. A green tick confirms it.

Decisions are saved on the conference laptop, and every save is kept. The patient card then shows the latest decision, and next week the CQI agent shows "last conference: …" beside that patient's findings.

## 7. Downloading PDFs
Download buttons appear under the answer when they apply:
- **Full booklet (PDF):** the patient's fillable CQI booklet.
- **3-page brief:** a short summary for that patient.
- **Branch report:** a 3-page branch summary covering how the patients are doing, delivery by discipline, and branch actions.

Every PDF page ends with *"Report generated <date> from Project Alpha – Clinical Quality Intelligence"*.

## 8. A suggested conference flow (about 5 minutes per branch)
1. **What did the CQI agent find this week?** See what's urgent and what's new.
2. **Who needs action today?** Assign the 24-hour items.
3. For each patient on the list: **Tell me about 9000123** → follow-ups (vitals, goals, why on the list) → **record the decision**.
4. **Who is ready for discharge?** Decide discharge or recert for patients in the 14-day window.
5. **How is Branch 0412 doing?** Review delivery, missed visits and staffing. Download the branch report.

## 9. What Project Alpha will and won't do
- **Answers only from this week's CQI data.** If something isn't in the data, such as caregiver availability, labs or pain scores, it says **"Not in the CQI data"** and names what's missing.
- **Stays on topic.** It doesn't answer questions about other subjects (news, coding, general medical advice not tied to these patients). You'll see a short message instead.
- **Doesn't change any record.** It reads data; the only thing it writes is the conference decision you save.
- **AI answers are drafts.** Check them against the evidence cards and the booklet.

## 10. Troubleshooting
| You see | What to do |
|---|---|
| The page doesn't load | Ask the coordinator to start the app, then refresh |
| "AI answer unavailable" | The AI service is off or unreachable. Recognised questions and the suggested ones still work from the data |
| "The CQI agent hasn't run for this date" | The weekly agent hasn't run for this CQI date yet. Ask the coordinator |
| An MRN isn't recognised | Check the MRN for typos. Only patients on this week's CQI list are covered |
| Numbers look wrong | Note it in the decision notes. The booklet's feedback page and the CQI team use this to improve the data |

---

### For the coordinator (setup)
From the `project_alpha` folder, run each week:
1. **Booklets and payloads (original pipeline):**
   ```bash
   cd cqi_booklet
   ```
   ```bash
   ..\.venv\Scripts\python src\build_payloads.py --as-of 2026-08-15 --sample
   ```
   Then go back to the `project_alpha` folder:
   ```bash
   cd ..
   ```
2. **CQI agent:**
   ```bash
   .venv\Scripts\python agent\cqi_agent.py --as-of 2026-08-15
   ```
3. **The app** (then open http://localhost:7871):
   ```bash
   .venv\Scripts\python webapp\app.py
   ```

Settings, including the data location and AI model, are in `config/project_alpha.yaml`. Databricks setup is in `docs/databricks.md`.
