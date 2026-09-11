[CodeWiki](../../../index.md) / [Forward-looking](../../index.md) / [Specs](../index.md) / [Detailed designs](index.md)

# PPE Compliance Detection — DPIA Input Document

## What this is, and what it is not

The Phase 2 plan says the DPIA "gates everything else and should start now, not after the
POC." This document is a **starting point for that DPIA** — a factual description of the
system, its data flows, and its built-in safeguards, assembled from what has actually been
verified in this codebase this session (not assumed or templated). **It is not a completed
DPIA.** A real DPIA under GDPR Art. 35 requires a qualified Data Protection Officer or
privacy counsel to assess necessity/proportionality, consult data subjects or their
representatives (the works councils), and sign off on residual risk. Nothing here
substitutes for that. Its purpose is to give whoever performs the real assessment an
accurate running start instead of a blank page — every claim below is something checked
directly against the code, not asserted.

## 1. Nature and purpose of the processing

**Purpose:** continuous, automated visual monitoring of Personal Protective Equipment (PPE)
compliance (helmets, hi-vis vests) at Renewi sites, to support safety oversight — explicitly
**not** individual performance monitoring, disciplinary evidence gathering, or productivity
tracking. See "Framing this correctly" in the project's own plan text and the project
blueprint's Principle: "The POC is a safety tool, not a productivity-monitoring tool."

**What the system does:** ingests a video clip or still image from a configured camera
source, detects people and their PPE state (helmet/no-helmet, vest/no-vest) using a YOLO
object-detection model, applies a per-zone PPE policy, and raises a non-compliance alert
with a retained, face-blurred evidence snapshot when non-compliance is sustained across
multiple frames (not a single momentary detection).

**Legal basis (for the DPO/legal team to confirm, not assumed here):** most likely
"legitimate interests" (health and safety, duty of care) under GDPR Art. 6(1)(f), balanced
against the intrusiveness of continuous visual monitoring of employees — this balancing
test is exactly what the DPIA needs to perform. If the processing is later found to involve
special-category data (see §3), an Art. 9 exception would also need to be identified.

## 2. Data flows, verified against the actual code

| Stage | What happens | What is stored, and for how long |
| --- | --- | --- |
| Ingestion | A video/image file is uploaded (or, as of 2026-09-12, periodically pulled from a configured RTSP/VMS camera feed — see `app/live_capture.py`) and saved to private, non-public storage | Raw media: `raw_media_retention_hours` (default 1h, admin-configurable), deleted by a scheduled retention task |
| Detection | The configured YOLO model detects `person`, `helmet`, `no_helmet`, `vest`, `no_vest`, `gloves`, `glasses` bounding boxes per sampled frame | Per-class confidence and counts only — no image data — in `FrameObservation`, retained `frame_observation_retention_hours` (default 24h) |
| Person-level state | Each detected person in a frame gets a short-lived, non-identifying compliance state | `PersonObservation.person_index` is explicitly documented and enforced in code as never a cross-frame or cross-job identity key — it cannot be used to track one individual across time; not exposed by any API endpoint |
| Alerting | Sustained (multi-frame) non-compliance raises a `ComplianceAlert` | Zone, camera, timestamp, confidence, failed requirement — no name, employee ID, or biometric identifier of any kind exists anywhere in this schema |
| Evidence | One annotated frame is generated for reviewer context | **Every face region is detected and Gaussian-blurred before the image can be stored, viewed, or downloaded — enforced twice: in application logic (fail-closed: if the face detector cannot run, no evidence is produced at all) and at the database layer by a `CheckConstraint` that makes an unblurred row impossible to persist even if application code is bypassed.** Evidence retention is policy-configured, API-bounded to 24–72 hours |
| Human review | A supervisor acknowledges/resolves/cancels an alert with an optional free-text note | The note is free text — see §4's residual risk about what a reviewer could type into it |
| Reporting | Aggregate-only compliance rates by camera/zone/shift/time window | No underlying image, evidence, or per-person record is ever included in a report or export |

## 3. Special-category data assessment

GDPR Art. 9 covers biometric data used "for the purpose of uniquely identifying a natural
person." This system's own architecture is designed specifically to avoid that:

- Face detection exists **only** to locate regions to blur, never to identify or verify who
  someone is — no facial recognition, no embedding, no comparison against any reference set
  exists anywhere in this codebase.
- No employee identity, badge ID, or name is captured, stored, or inferable from any table.
- `PersonObservation.person_index` is an ordinal position within one frame's detection
  output only — verified in code to carry no meaning across frames, jobs, or time.

**For the DPO to confirm, not assumed here:** whether processing raw camera footage of
identifiable people — even briefly, even before blurring — itself constitutes personal data
processing under GDPR (almost certainly yes) and whether any argument could be made that
this rises to special-category processing despite the above (the system's own design intent
is that it should not, but that is an assessment for the DPIA, not a foregone conclusion).

## 4. Risks to data subjects, and what already mitigates them

| Risk | Built-in mitigation (verified) | Residual risk for the DPIA to weigh |
| --- | --- | --- |
| Function creep into productivity/performance monitoring | No individual tracking capability exists in the schema or API at all (see §2); aggregate-only reporting; explicit non-productivity framing in the project blueprint | A future feature request to "see who was non-compliant" would need to be refused or escalated — this is a governance/process risk, not a current technical one |
| Facial identification from evidence | Fail-closed, mandatory, DB-enforced face blur (§2) | None identified from the code; the DPIA should independently confirm the face-detector model's own false-negative rate (a face it fails to detect is a face it cannot blur) |
| Evidence retained too long or accessed too broadly | Short, policy-bounded retention (24–72h); evidence access is role-gated separately from dashboard access; a `demonstration_viewer` role sees evidence only after explicit supervisor approval | Retention windows and role grants are all administrator-editable at runtime — a misconfiguration is a process risk, not something the code prevents outright |
| A reviewer's free-text note naming a worker | Notes are optional (not required) and there is no structured "worker name" field | The note field is free text with no content filtering — nothing stops a supervisor from typing a name into it. **Worth a DPIA recommendation**: reviewer training/guidance on this, since the code cannot enforce it |
| Live camera capture (new, 2026-09-12) pulling footage more broadly than intended | Capture duration and interval are both explicitly bounded and administrator-configured, not continuous unbounded recording | Not yet validated against a real camera; the DPIA's data-minimization assessment should be revisited once real capture parameters for an actual site are chosen |
| Cross-border/vendor data exposure | All inference in this POC runs against publicly hosted models (Hugging Face weights downloaded once, run locally) or Roboflow's hosted API (a live third-party service call for one benchmarked candidate only — not the configured POC default) | If a hosted third-party inference API is ever used in production rather than a locally-run model, that vendor's own data-processing terms need their own review — not assessed here |

## 5. Consultation obligations (NL + BE works councils)

The plan is explicit that works-council engagement is required in both jurisdictions before
any live deployment, and that the rules differ by country — this document does not attempt
to state what NL/BE works-council law requires; that needs local legal input. What this
document can usefully hand to that consultation: the full data-flow table in §2, the
special-category assessment in §3, and the residual-risk table in §4, so the consultation is
grounded in what the system actually does rather than a general description.

## 6. What must happen before this can become a real DPIA

1. Qualified DPO/legal review of §1's legal-basis question and §3's special-category
   question — both are flagged above as "for the DPO to confirm," not resolved here.
2. A formal necessity/proportionality assessment: is continuous visual monitoring
   proportionate to the safety benefit, compared to less intrusive alternatives (e.g.
   periodic manual spot-checks, worker self-reporting)? This is the core of Art. 35 and is
   explicitly not attempted in this document.
3. NL and BE works-council consultation using this document as a factual starting point.
4. A decision on retention periods, evidence-access roles, and the free-text note risk in
   §4, formally recorded as part of the DPIA's mitigation plan.
5. Re-assessment once real Renewi camera footage and real capture parameters are known —
   this document is written against the POC's synthetic/public-benchmark data flows, and a
   pilot's actual data flows (real employees, real cameras) is a materially different
   processing activity that the DPIA must cover on its own terms, not by extension from this
   document.

## Related documents

- [Phase 2 pilot scope memo](ppe-compliance-phase-2-pilot-scope.md) — what a 90-day pilot needs, including this DPIA
- [PPE compliance model evaluation](ppe-compliance-model-evaluation.md) — model/data licence trail (a separate concern from personal-data processing, but relevant to the same governance review)
- [PPE compliance project blueprint](ppe-compliance-project-blueprint.md) — the POC's own stated non-negotiable privacy constraints
