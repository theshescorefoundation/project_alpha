# Branch report: spec (v1)

**Purpose:** one short, branded PDF per branch for each weekly CQI run, answering *how are our patients doing, and what does the branch need to do this week?* It sits beside the existing `_BRANCH_` packets, which are patient booklets stapled together, and does not replace them.

**Audience:** branch director, clinical manager, scheduling lead, and the clinical quality team at the weekly Patient Conference.

## Data: only what is in the zip
| Source | Scope | Used for |
|---|---|---|
| `cqi_booklet/output/<date>/json/*.json` (patient payloads) | CQI cohort (high, very high or rising risk) | Vitals trajectory, discharge window and recommendation, goals, meds, care network, data gaps, patient actions |
| `cqi_booklet/data/csv`: episodes, risk_scores | **All active episodes** in the branch | Census, risk mix, risk trend |
| orders, visits, sched | All active episodes | Visit delivery by discipline, missed visits by reason, unscheduled ordered visits |
| vs_alerts | All active episodes | Alerts with no physician notification |

An active episode has status CURRENT, RECERTIFIED or ON-HOLD and the run date falls within the episode. These are the same rules as the booklet.

## Definitions
- **Ordered to date:** visits ordered in fully completed episode weeks, before the current week. The order frequency (e.g. `3W1, 2W2`) is expanded week by week, the same way the booklet does it.
- **Delivered to date:** COMPLETED visits in those weeks. Delivery % = delivered ÷ ordered, capped at 100% per discipline-week, so extra visits in one week don't hide shortfalls in another.
- **Remaining:** visits ordered from the current episode week onward. This is the booklet's definition, so the numbers match the booklet's "7 ordered visits remain". Earlier shortfalls show up as missed or under-delivered, not as remaining. **Unscheduled** = remaining minus scheduled future visits, never below 0, per discipline.
- **Missed-visit category:** taken from `missed_reason`:
  - *Agency*: staffing / availability
  - *Clinical*: hospitalized
  - *Patient*: not home, reschedule, refused, other
- **Rising:** score up at least 0.05 since the prior run (booklet rule).

## Pages (letter, portrait, CenterWell styling to match the booklet)
1. **Branch at a glance.** Header band. Six KPI tiles:
   - active episodes
   - patients on the CQI list
   - rising risk
   - deteriorating vitals
   - nearing discharge
   - unscheduled ordered visits

   Below the tiles, "How the branch is doing" (rule-based findings with numbers) and the **branch action list**. Each action has an owner, an urgency and the patient IDs it concerns.
2. **Care delivery and safety.**
   - Delivery by discipline: ordered / delivered / % / missed / remaining / scheduled / unscheduled
   - Missed visits by category
   - Escalation and safety: alerts with no MD notification, high-risk meds with no recent understanding check, adverse events
   - Goal status mix
   - Care network and referral opportunities
3. **Patients on this week's list.** A roster sorted by risk: tier and score with its trend, vitals trajectory, days to episode end and recommendation, goals met, visits left / scheduled, data gaps, top action. Then a panel for each patient nearing discharge, showing the recommendation and its next steps.

## Branch actions (rule-based, one line each, only when the condition holds)
| Condition | Action | Owner | When |
|---|---|---|---|
| Vitals deteriorating | Same-day clinician review | Clinical manager / SN | Within 24 hrs |
| Vitals alert, MD not notified | Notify MD and document | Clinical manager | Within 24 hrs |
| Unscheduled ordered visits | Book the remaining ordered visits | Scheduling | Within 24 hrs if any patient is deteriorating, else this week |
| Missed visits for staffing | Review staffing / coverage by discipline | Branch director | This week |
| Goals missed or not started | Confirm orders and staffing for those disciplines | Clinical manager | This week |
| High-risk meds with no check in 14 days, or adherence concerns | Teach-back at next visit | SN | Next SN visit |
| Nearing discharge | Discharge / recert decision in conference | IDT conference | This conference |
| CW PCO / CW Rx "Refer", or no PCP | Offer referral / arrange primary care | Clinical manager | Before discharge |
| Data gaps | Resolve before the conference decision | Clinical quality | Before conference |

## Outputs
`branch_report/output/<date>/`:
- `<Branch>_report.pdf`
- `<branch>_summary.json`: the same numbers, for the Gradio app's Branch tab

## Limits (shown on the PDF's methods line)
- Synthetic data, with 1-2 cohort patients per branch and 2 risk runs. Trends are indicative only.
- AI-derived fields (goals, data gaps, actions) exist only for cohort patients.
- Rules are deliberately simple and listed here. Clinicians make every decision.
