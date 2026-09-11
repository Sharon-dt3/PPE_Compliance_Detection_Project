[CodeWiki](../../../index.md) / [Forward-looking](../../index.md) / [Specs](../index.md) / [Detailed designs](index.md)

# PPE Compliance Detection — Phase 2 Pilot Scope Memo

## Purpose

The implementation plan's Day 5 exit criterion calls for a "Phase-2 scope memo" alongside the
recorded demo. This is that memo: what the POC actually proved, what a controlled 90-day
reference-site pilot at one Renewi location would need to add, and the governance path
(DPIA, works-council) that gates it — grounded in what was actually built and verified in
this repository, not a generic template. It does not authorize Phase 2; per the project
blueprint, that requires separate sign-off.

## What the POC actually proved (evidence, not assertion)

- **Detection works end-to-end** and is benchmarked, not just wired up: six candidate models
  scored on an identical 300-image held-out set (fixed seed), full per-class precision/
  recall/F1/confidence — not a single headline number. See
  [Model evaluation and licensing trail](ppe-compliance-model-evaluation.md).
- **A licence-clean production route exists and is evidenced, not proposed on paper**: an
  in-house YOLOX-Nano fine-tune (Apache 2.0 framework, CC0 training data) was actually
  trained and benchmarked, and currently has the second-best accuracy of every candidate
  tested (helmet F1 0.714, no_helmet F1 0.511) — the highest-accuracy candidate overall
  (`Hexmon/vyra-yolo-ppe-detection`, F1 0.900/0.815) carries an unresolved Ultralytics-AGPL
  licence dispute, so it is not the production recommendation despite the number.
- **The compliance-rules layer is real, not a stub**: person-anchored PPE attribution,
  frame-persistence to tolerate brief occlusion, explicit `no_helmet` precedence, and
  multi-requirement alerting are implemented and unit-tested (`app/decision.py`).
- **Privacy-by-design is built, not deferred**: mandatory fail-closed face-blur before any
  evidence is stored, viewed, or downloaded; a data-layer `CheckConstraint` prevents an
  unblurred row from ever existing even if application logic is bypassed; short, configurable
  evidence-retention windows; aggregate-only reporting with no individual tracking; alert
  lifecycle now genuinely supports `expired`/`cancelled`, not just `open`/`acknowledged`/
  `resolved`. This is materially ahead of the "Out of scope (Phase 2)" framing the original
  plan text used for face-blur specifically — it shipped as a mandatory Phase 7 requirement,
  not a deferred one.
- **The dashboard is genuinely live**: `MetricRollup` rows are written synchronously in the
  same transaction that completes a job (`app/processing.py`), and the frontend polls every
  3 seconds while any job is in flight (`frontend/src/main.tsx`) — verified by reading the
  actual code paths, not assumed from the UI looking right.
- **Video ingestion works end to end** against a real hosted PPE inference API (decode →
  frame-sample → Roboflow API → parsed detections, 10/10 frames succeeded) — using the exact
  same `cv2.VideoCapture` + frame-stride sampling the app's own pipeline uses.
- **RTSP/VMS live-camera capture is built**, not just designed: a configured
  `CameraSource.stream_url` is periodically pulled into an ordinary media job by a scheduled
  task, with no changes needed anywhere downstream (`app/live_capture.py`). Confirmed this
  environment's OpenCV build is genuinely FFmpeg-backed (real `rtsp://` support, not just
  local files); not yet validated against a live network stream end to end — see "What
  Phase 2 needs to add" below for exactly why.

## What Phase 2 needs to add

| Area | Gap | Why it's Phase 2, not a POC oversight |
| --- | --- | --- |
| Live camera integration | **Built 2026-09-12**, not just deferred: `CameraSource.stream_url` + a scheduled capture task turn a configured RTSP/VMS feed into an ordinary media job through the unchanged detect/decide/alert pipeline (`app/live_capture.py`, `POST /api/v1/sources/{id}/capture-now` for on-demand testing). What remains open is validation only — no real network RTSP connection has been exercised end to end; the two public demo streams tried during development were dead (DNS) or blocked (403), both third-party issues | Real validation needs either Renewi's own cameras (DPIA-gated) or a freshly provisioned, currently-live RTSP test server — this is a data/access gap now, not a missing capability |
| Site-specific model validation | Every benchmark used publicly available imagery (eye-level construction/industrial photography), never Renewi's own camera angle, height, or lighting | PPE accuracy is angle-sensitive; the plan's own domain-gap warning is borne out directly in this evaluation — see "Reading these results" in the model-evaluation doc | 
| Production `DetectionProvider` adapter | The fine-tuned YOLOX checkpoint is benchmarked but not wired into `app/detection.py` — today's adapter is Ultralytics-only | Deliberately scoped out: swapping the live model needs a new adapter implementation and its own full production-approval pass, not just a config change |
| Class coverage beyond helmet | Gloves/glasses have no benchmark coverage at all (the held-out set only labels helmet/no_helmet); the licence-clean CC0 training data used for YOLOX covers helmet/person/head only | SH17 (the richest class-coverage dataset) is CC BY-NC-SA — usable for benchmarking reference only, never for training shippable weights |
| Full DPIA and works-council approval (NL + BE) | Not started as an engineering deliverable — deliberately, per the blueprint | This POC processes synthetic/public test data only; Renewi's own footage requires the governance process first, not after |
| Real site footage | Zero Renewi footage used anywhere in this evaluation | Explicitly deferred to the same DPIA-gated channel per the plan's own instruction — not something to request informally |
| Auth hardening | Backend already verifies Supabase JWTs via JWKS (not a shared secret); a production pilot still needs the demo-mode role selector fully retired as a login path, not just gated behind an env var | POC intentionally keeps demo mode available for stand-up demos without a live IdP dependency |

## Two things this memo should say plainly

1. **Accuracy on public benchmark imagery does not transfer directly to Renewi's cameras.**
   The single sharpest evidence for this in the whole evaluation: the Roboflow candidate's
   own published aggregate numbers (84.1% mAP@50, 92.7% precision, 77.4% recall) collapsed to
   4.1% recall on `no_helmet` against this evaluation's held-out set — a domain-gap
   demonstration on data that isn't even Renewi's own. A pilot without real site footage in
   the loop is not a smaller version of a validated system; it is an unvalidated one.
2. **The licence-cleanest option is not the highest-accuracy option today**, and that trade-off
   should be made explicitly by whoever approves Phase 2, not silently defaulted. The
   fine-tuned YOLOX route has no licence exposure and second-best accuracy; the top-accuracy
   candidate (`Hexmon`) carries an unresolved AGPL dispute. Recommend continuing the YOLOX
   production route and treating a longer, full-dataset, GPU-accelerated fine-tune (this
   POC's run used 750 of 5,000 available CC0 images, 20 epochs, CPU-only, mosaic augmentation
   disabled) as the natural next step to close the remaining accuracy gap without touching
   the licence question at all.

## Recommended 90-day pilot scope (one reference site)

- **Weeks 1–4 — governance and data access, run in parallel, not sequentially**: initiate the
  DPIA; engage the NL/BE works councils; request a small set of anonymised or access-
  controlled Renewi camera clips through that same governance channel (per the plan's own
  instruction, this is not something to source informally).
- **Weeks 3–6 — model validation on real footage**: re-run this evaluation's exact
  methodology (fixed seed, held-out split, per-class precision/recall/F1, no headline number)
  against real site clips once available; expect the fine-tuned model's accuracy to move,
  possibly substantially, and treat that as the real go/no-go signal rather than this POC's
  public-imagery numbers.
- **Weeks 5–8 — live ingestion**: the RTSP/VMS capture path is already built
  (`app/live_capture.py`) — this window is for connecting it to Renewi's actual reference
  cameras and validating it against a real network stream for the first time, not building
  it from scratch; no change needed to the rules, alerting, or dashboard layers, which are
  camera-source-agnostic already.
- **Weeks 6–10 — production adapter and hardening**: wire the (by then re-validated) YOLOX
  checkpoint into a new `DetectionProvider` implementation; retire demo-mode auth as a
  production login path; extend the fixed-class coverage if gloves/glasses remain in scope
  for the pilot site.
- **Weeks 10–12 — controlled go-live and review**: single reference site, safety-supervisor
  and HSE-manager roles only to start, explicit compliance-rate reporting cadence agreed with
  site management up front (per the plan's own "success metric to agree... up front" — this
  memo recommends leading with the compliance-rate framing, not per-class detection accuracy,
  for exactly the reason the plan gives: it is the actual deliverable a safety function can
  act on and trend).

## Section 8 checklist, verified against the running code (2026-09-12)

The plan's own "After the POC — path to a pilot" section lists five Phase-2 items. Two of
them turn out to already be built, one is only partially built with a real gap worth naming
precisely, and two are correctly still open.

### Already built, not Phase-2 work

The plan lists **"Implement privacy-by-design properly: face blurring, short retention,
aggregate reporting, access control on evidence"** as a Phase-2 deliverable. All four exist
today, verified directly rather than assumed:

| Sub-item | Verified state |
| --- | --- |
| Face blurring | Mandatory, fail-closed OpenCV DNN face-blur before any evidence is stored, viewed, or downloaded; enforced at the data layer by a `CheckConstraint`, not only application logic (Phase 7) |
| Short retention | `raw_media_retention_hours`, `frame_observation_retention_hours`, and per-policy `evidence_retention_hours` (24–72h, API-enforced) are all real, administrator-editable, and actually swept by a scheduled retention task — including alerts themselves now (`expired`/`cancelled` states added this session) |
| Aggregate reporting | `GET /api/v1/reports/compliance`, `.../trend`, `.../alerts` return only aggregate, non-identifying counts with an explicit POC disclaimer; exports are aggregate-only rows with no evidence, filenames, or identities (FR-RPT-05) |
| Access control on evidence | `GET /api/v1/alerts/{id}/evidence` is role-gated separately from dashboard access (`Role.SUPERVISOR`, or `Role.DEMO_VIEWER` only after explicit reviewer approval) — matches the blueprint's own instruction to "authorize evidence access separately from dashboard access" |

Same pattern as the Section 5 face-blur finding: this is a case where the external plan text
is stale relative to what has actually shipped, not a real Phase-2 gap. Nothing to build here.

### The response loop, all three parts now built (2026-09-12)

The plan asks Phase 2 to **"define the response loop — who receives an alert, what
intervention follows, and how it is logged for the HSE record."** Checked against the actual
code:

- **"What intervention follows" — built.** Acknowledge/resolve/cancel all capture a
  supervisor-authored note (optional per FR-ALERT-03) against the specific alert.
- **"How it is logged" — built.** Every action is durably recorded in `EventAcknowledgement`
  (timestamp, actor reference, actor role, prior state, next state, note) and mirrored to the
  audit-event log; average acknowledgement/resolution time is already computed for reporting.
- **"Who receives an alert" — built as an in-app indicator**, a deliberate choice over
  email/webhook: `GET /api/v1/alerts/open-count` plus a persistent frontend poll (independent
  of whether a media job happens to be in flight) surfaces a live badge on the Safety Alerts
  nav item for every role that can see alerts, without needing SMTP credentials or an
  external push target this environment doesn't have. Verified live in the browser (a real
  open alert produced a visible "1" badge) and by 3 automated tests. This is a real,
  deliberate product decision, not a default: an in-app pull-with-a-visible-badge is weaker
  than a page/SMS/email escalation for a safety-critical alert (it still requires the
  reviewer's client to have the app open somewhere), and a Phase 2 pilot should revisit
  whether that guarantee is strong enough for a real site, or whether it needs a genuine push
  channel (email/webhook) — deferred here specifically because both need real infrastructure
  (SMTP credentials, a webhook target) this POC environment doesn't have.

### Correctly still open

- **Fine-tuning on Renewi footage** — covered above under "What Phase 2 needs to add,"
  correctly gated behind DPIA/works-council approval; no real Renewi footage exists to train
  on regardless of engineering effort.
- **DPIA and works-council engagement** — correctly not started as an engineering deliverable;
  nothing for this session to verify or build here.
- **RTSP/VMS live camera integration is now built** (see the updated row above), which
  narrows this item to validation against a real network stream — genuinely still open, but
  no longer a missing capability.

### Outside this session's ability to verify

The plan's closing claim — **"shares the Kavia.ai stack and YOLO model family with the fire
& smoke and hazmat builds"** — describes the Kavia.ai platform's cross-use-case state. This
Claude Code session works against this repository only; the user also builds this project
directly on Kavia.ai in parallel (a separate agent, separate history). Whether the fire/smoke
and hazmat demonstrators are still on a compatible stack is not something this session's
tooling can check. Verify on the Kavia.ai platform itself before repeating this claim in a
pilot proposal.

## What this memo is not

It is not a DPIA, not works-council material, and not an authorization to begin Phase 2 — per
the project blueprint, engineering work beyond this POC "is not authorized by this blueprint."
It is the scope memo that Day 5 of the implementation plan calls for, to accompany the
recorded demo when Phase 2 approval is sought.
