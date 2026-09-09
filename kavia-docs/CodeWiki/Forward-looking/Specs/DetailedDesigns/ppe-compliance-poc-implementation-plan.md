[CodeWiki](../../../index.md) / [Forward-looking](../../index.md) / [Specs](../index.md) / [Detailed designs](index.md)

# PPE Compliance Detection POC — Implementation Plan

## Purpose and boundaries

Build a five-day demonstrator that accepts uploaded CCTV stills or video clips, detects people and their PPE, evaluates zone-specific PPE requirements, creates reviewable non-compliance alerts, and displays aggregate compliance metrics.

The POC is a safety tool, not a productivity-monitoring tool. It must not identify named people or provide individual tracking. Live RTSP/VMS integration, on-site deployment, formal DPIA and works-council approvals, and production tuning on Renewi footage are deferred to Phase 2.

## Success criteria

The demonstrator is complete when it can:

1. Process an image or video clip end to end.
2. Detect `person`, `helmet`, `no_helmet`, and `vest` detections with confidence values and bounding boxes.
3. Attribute PPE items to each detected person and apply the configured requirement for the source camera or zone.
4. Suppress transient violations using multi-frame persistence.
5. Create a `PPE_NON_COMPLIANCE` event with an annotated, face-blurred evidence snapshot.
6. Allow an operator to acknowledge an alert and record an intervention note.
7. Show aggregate compliance rates by camera, zone, and shift, plus an alert log and time trend.
8. Present class-level validation metrics and known limitations, especially for no-helmet detections.

The primary business outcome is a defensible aggregate compliance rate that a safety manager can trend and act on, rather than surveillance of individual workers.

## Recommended technical approach

### POC inference path

Use the Hugging Face `melihuzunoglu/ppe-detection` YOLO model as the initial POC detector because it provides `Human`, `Helmet`, `No-Helmet`, and `Vest` classes aligned with the required experience. Benchmark it against the Roboflow Construction Site Safety hosted model and at least one alternate hosted YOLO PPE model before selecting the demonstrator default.

The POC may use AGPL-licensed model tooling only for an internal demonstration. The UI and application architecture must isolate inference behind a provider interface so the model implementation can be replaced without redesigning the workflow.

### Production path

Use a permissively licensed detector, initially YOLOX or another Apache-2.0 alternative, fine-tuned on commercial-use-cleared data. The proposed starting dataset is Safety Helmet Detection under CC0. Confirm the licences of framework code, base weights, fine-tuned weights, and every training dataset independently before production deployment.

Gloves and eye protection are stretch classes. Do not gate the POC on them because they are small, commonly occluded, and have lower expected accuracy.

## Proposed application architecture

```text
Browser operator UI
  ├─ Upload and source configuration
  ├─ Live processing status and annotated viewer
  ├─ Alert acknowledgement workflow
  └─ Aggregate dashboard
          │
Application API
  ├─ Upload/media service
  ├─ Detection-provider adapter
  ├─ Person-to-PPE association and compliance rules
  ├─ Event/evidence service
  ├─ Reporting query service
  └─ Retention and access-control enforcement
          │
Persistence
  ├─ Media and short-lived evidence snapshots
  ├─ Detection observations
  ├─ Compliance events and acknowledgement records
  └─ Aggregate metric rollups
```

### Major components

| Component | Responsibilities |
|---|---|
| Media ingestion | Validate uploaded image/video type and size, create processing jobs, retain original media only for the configured short POC window. |
| Detection provider | Present one stable interface for local YOLO, hosted Roboflow, or future YOLOX inference; normalize class names and coordinates. |
| Association engine | Associate helmets, no-helmet signals, and vests with a person bounding box using overlap, center position, and PPE-region heuristics. |
| Rules engine | Apply zone PPE policies, confidence thresholds, and persistence windows to calculate person-level compliance states. |
| Event service | Open, deduplicate, resolve, and acknowledge `PPE_NON_COMPLIANCE` alerts; record only operational intervention data. |
| Evidence service | Produce annotated snapshots, blur detected face regions before display/storage, and apply short retention. |
| Dashboard service | Compute rates and trends from aggregate observations without a person identity field. |
| Audit and access control | Record administrative and alert-review actions; restrict raw evidence and configuration access to authorized roles. |

## Data model

Use non-identifying records. Do not store names, biometric templates, face embeddings, worker identifiers, or cross-frame tracking identities.

| Entity | Essential fields |
|---|---|
| `camera_source` | `id`, `name`, `zone_id`, `enabled`, `retention_policy` |
| `zone` | `id`, `name`, `required_ppe`, `active_shift_definition` |
| `media_job` | `id`, `source_id`, `media_type`, `status`, `submitted_at`, `completed_at`, `failure_reason` |
| `frame_observation` | `id`, `job_id`, `frame_timestamp`, `person_count`, `compliant_count`, `non_compliant_count` |
| `person_observation` | `id`, `frame_observation_id`, `ephemeral_frame_key`, `ppe_state`, `confidence_summary`; delete on retention schedule |
| `compliance_event` | `id`, `source_id`, `zone_id`, `event_type`, `opened_at`, `resolved_at`, `status`, `evidence_id` |
| `evidence_snapshot` | `id`, `event_id`, `annotated_location`, `blurred`, `expires_at`, `created_at` |
| `event_acknowledgement` | `id`, `event_id`, `acknowledged_at`, `intervention_note`, `operator_reference` |
| `metric_rollup` | `id`, `interval_start`, `source_id`, `zone_id`, `shift`, `observed_people`, `compliant_people`, `non_compliant_people` |

The rate is calculated as:

```text
compliance_rate = compliant_people / (compliant_people + non_compliant_people)
```

Frames or people with insufficient detector confidence must be counted as `unknown`, excluded from the denominator by default, and shown separately so the dashboard does not overstate confidence.

## Person-anchored compliance algorithm

1. Run inference for every selected video frame or uploaded still.
2. Normalize provider-specific labels to the application taxonomy.
3. Treat each `person` detection as an anchor.
4. Define a head region as the upper portion of that person box and a torso region as the middle portion.
5. Associate a PPE item only when it overlaps the appropriate person region and exceeds the source-specific confidence threshold.
6. Apply precedence: an explicit `no_helmet` signal overrides an inferred absent helmet; otherwise use `unknown` rather than calling absence from a weak detection.
7. Compare the associated PPE state with the zone policy.
8. Require the same violation for a configurable number of consecutive sampled frames before opening an alert.
9. Deduplicate an ongoing violation within a short time window to avoid repeated alerts for the same scene.
10. Generate a blurred evidence snapshot only after the alert threshold is reached.

Initial configuration defaults:

| Setting | Initial POC value |
|---|---|
| Detection confidence | 0.25, tuned per model and class |
| Video sampling rate | 2–5 FPS |
| Violation persistence | 3 consecutive sampled frames |
| Alert deduplication | 60 seconds per camera/zone |
| Evidence retention | 24–72 hours, configurable |
| Raw upload retention | Delete after processing unless explicitly retained for test evaluation |
| Helmet and vest requirements | Enabled in yard-zone example policy |
| Gloves and glasses | Experimental; excluded from headline compliance rate |

## UI scope

### Upload and processing view

- Upload still images and supported video clips.
- Select the camera source and zone policy before processing.
- Show job progress, failure reasons, and a playback/inspection view.
- Overlay people, PPE detections, compliance states, and confidence summaries.
- Make it clear that results are indicative POC output rather than production safety enforcement.

### Alert queue

- List open, acknowledged, resolved, and expired alerts.
- Display source, zone, timestamp, violated requirement, confidence summary, and blurred annotated evidence.
- Support acknowledgement and free-text intervention notes.
- Do not display a worker name, person profile, or persistent identity.

### Dashboard

- Headline compliance rate with explicit observation count and unknown count.
- Filters for date range, camera, zone, and shift.
- Time-series trend of aggregate rates.
- Compliance breakdown by PPE requirement.
- Alert volume and acknowledgement time.
- Evidence access limited to the alert inspector rather than exposed in aggregate reports.

## Five-day delivery sequence

### Day 1 — baseline flow

- Scaffold the application and media upload flow.
- Add the detector-provider adapter.
- Run the primary PPE model against sample stills and video.
- Render normalized detections in the inspection UI.
- Call the hosted Roboflow route as a benchmark cross-check.

**Exit criterion:** person, helmet/no-helmet, and vest detections run end to end in the application.

### Day 2 — model evaluation and licence evidence

- Assemble a small representative clip set and label a validation subset.
- Benchmark candidate models on the same inputs.
- Record per-class precision, recall, false-positive examples, and inference time.
- Prototype the permissive production-route baseline separately.
- Capture licence evidence for each POC and production candidate.

**Exit criterion:** selected POC model and a documented permissive production candidate, with known limitations.

### Day 3 — rules and eventing

- Build person-PPE association logic.
- Add zone policy configuration.
- Implement confidence thresholds, persistence, unknown state, and alert deduplication.
- Persist compliance observations and lifecycle-managed alerts.

**Exit criterion:** person-level events are stable enough that short occlusions do not create repeated alerts.

### Day 4 — privacy-first evidence and dashboard

- Generate annotated event snapshots.
- Blur faces before evidence display and storage.
- Implement acknowledgement and intervention-note workflow.
- Build camera, zone, and shift metric rollups, trend chart, and alert log.

**Exit criterion:** a processed clip updates the dashboard and creates reviewable, blurred evidence when a persistent violation occurs.

### Day 5 — validation and demonstration

- Run the full scenario as a simulated live stream.
- Capture a demo reel showing compliant and non-compliant cases.
- Document accuracy, excluded/unknown observations, model licence route, privacy design, and Phase-2 dependencies.
- Prepare the 90-day reference-site pilot outline.

**Exit criterion:** repeatable demo and a transparent handoff package for pilot planning.

## Validation plan

### Functional tests

- Valid image and video uploads create a processing job.
- Unsupported media and oversized uploads fail safely with a useful error.
- Detection adapter normalizes all supported provider labels.
- Helmet/vest-required zone creates an event only after persistence is met.
- Unknown PPE state does not create a false non-compliance event.
- Duplicate violation frames create one active event in the deduplication window.
- Acknowledgement records an intervention note and timestamp.
- Evidence is face-blurred before it can be viewed.
- Expired evidence is inaccessible.
- Dashboard totals reconcile with stored aggregate observations.

### Model evaluation

Label a small, held-out validation set from representative demo footage. Report:

- Precision, recall, and F1 for person, helmet, no-helmet, and vest.
- Confusion between `no_helmet`, occlusion, and low-confidence `unknown`.
- Per-camera-angle performance.
- Processing latency and sampling rate.
- Percentage of people excluded as unknown.

Do not report one headline accuracy value without class-level context.

### Security and privacy checks

- Role-check all media, evidence, and alert endpoints.
- Validate uploads by extension, MIME type, content signature, size, and duration.
- Prevent evidence URLs from being publicly enumerable.
- Apply encryption and short deletion schedules where platform facilities permit.
- Confirm dashboard exports contain aggregates only.
- Verify no face embedding, worker identifier, or long-lived track identifier is persisted.
- Document the DPIA and works-council approval as Phase-2 gates, not completed POC tasks.

## Phase-2 pilot prerequisites

A 90-day pilot at one reference site should proceed only after:

1. DPIA completion and legal/privacy approval.
2. Works-council engagement in the relevant country or countries.
3. Defined safety-zone policies and an intervention owner for each alert.
4. Approved camera feeds and controlled RTSP/VMS integration.
5. Evidence retention, access control, and deletion settings agreed with HSE and privacy stakeholders.
6. Site-footage data collection and annotation plan to address high-angle, wide-angle camera domain gaps.
7. Production licensing review confirming the detector, weights, data, dependencies, and deployment model are commercially acceptable.

## Risks and decisions

| Risk | Implementation response |
|---|---|
| GDPR and employee-monitoring concerns | Use aggregate reporting, no identities, blurred evidence, short retention, and formal governance before production. |
| High false positives from turns or occlusion | Use explicit no-helmet classes where available, `unknown` state, and frame persistence. |
| Public-model camera-angle domain gap | Treat the POC as indicative; fine-tune with approved site footage before pilot scale-up. |
| Secondary PPE classes underperform | Keep gloves and glasses outside the POC headline rate until validated. |
| AGPL and non-commercial dataset restrictions | Isolate inference, use such tooling only for the internal POC, and build the permissive YOLOX-plus-CC0 path for production. |
| Over-alerting | Deduplicate active violations and report interval-based rates rather than instantaneous counts. |

## Initial implementation backlog

1. Create application skeleton, authentication roles, and upload workflow.
2. Define source/zone policy configuration and non-identifying database schema.
3. Implement media validation and asynchronous job state handling.
4. Implement the inference-provider abstraction and primary model adapter.
5. Add detection normalization, person-PPE association, and unknown-state handling.
6. Implement persistence, deduplication, and alert lifecycle management.
7. Add face-blurred evidence generation and expiry enforcement.
8. Build the alert queue, acknowledgement flow, and intervention notes.
9. Build metric rollups, filters, trend visualization, and data-quality indicators.
10. Create labelled validation fixtures, functional tests, and benchmark report.
11. Add operational logging, audit records, error handling, and monitoring.
12. Produce demo content and Phase-2 governance/pilot handoff documentation.
