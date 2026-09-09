[CodeWiki](../../../index.md) / [Forward-looking](../../index.md) / [Specs](../index.md) / [Detailed designs](index.md)

# PPE Compliance Detection — Project Blueprint

## 1. Blueprint purpose

This blueprint is the implementation-ready definition for the PPE Compliance Detection proof of concept (POC). It translates the implementation plan into the product behavior, technical design, operational controls, validation evidence, delivery work packages, and governance boundaries needed to build and demonstrate the solution.

The product is a **safety compliance decision-support tool**. It processes uploaded CCTV stills and video clips, identifies people and relevant PPE, evaluates configured zone requirements, raises reviewable non-compliance alerts, and reports aggregate compliance rates. It must never be positioned or implemented as employee productivity monitoring.

## 2. Product scope

### 2.1 POC objectives

The POC must provide a credible end-to-end demonstration of:

1. Uploading an image or video clip from a configured camera source.
2. Detecting `person`, `helmet`, `no_helmet`, and `vest` classes.
3. Associating PPE detections to individual persons within one frame.
4. Applying PPE policy by safety zone.
5. Differentiating `compliant`, `non_compliant`, and `unknown` states.
6. Suppressing transient violations through multi-frame persistence.
7. Creating one deduplicated `PPE_NON_COMPLIANCE` event for a sustained violation.
8. Capturing and displaying a face-blurred, annotated evidence snapshot.
9. Allowing a supervisor to acknowledge an alert and record an intervention.
10. Showing aggregate rates by camera, zone, shift, PPE rule, and time period.
11. Reporting model performance and uncertainty transparently.

### 2.2 Explicit POC exclusions

The following are out of scope and must not be implied as delivered:

- Live RTSP, VMS, or CCTV vendor integration.
- Always-on production camera processing.
- On-site deployment.
- Facial recognition, identity matching, person tracking, or worker profiling.
- Named-worker records or performance-management workflows.
- Production-grade face anonymisation certification.
- Final DPIA, legal approval, or works-council approval.
- Production model tuning using Renewi footage.
- Gloves, glasses, and other small PPE classes in the headline compliance rate.
- Autonomous escalation to disciplinary, HR, or access-control systems.

### 2.3 Phase-2 direction

The POC is intended to support a later, controlled 90-day reference-site pilot. Phase 2 may introduce live camera integration, validated site-specific models, strengthened access controls, formal retention controls, privacy approval, and works-council engagement. It is not authorized by this blueprint.

## 3. Principles and non-negotiable constraints

| Principle | Required implementation outcome |
|---|---|
| Safety before surveillance | UI copy, reports, and event labels describe safety observations and interventions, not employee performance. |
| Aggregate by default | Dashboards report zone, camera, shift, and time-window totals; they do not show individual histories. |
| No identity processing | Do not persist face embeddings, names, worker IDs, biometric templates, or persistent track IDs. |
| Privacy by design | Evidence is face-blurred before storage and display, uses short retention, and has role-limited access. |
| Unknown is safer than inferred absence | Low-confidence, occluded, or ambiguous observations must become `unknown`, not a violation. |
| Human review | An alert supports supervisor review and intervention; it is not a final enforcement decision. |
| Evidence transparency | Every rate reports its denominator, unknown/excluded observations, model version, and applicable policy. |
| Replaceable inference | The application communicates with models through a provider abstraction to support future model and licence changes. |
| Licence separation | Model framework, base weights, fine-tuned weights, datasets, and hosted-service terms are assessed independently. |
| Configuration over code | Thresholds, policies, retention windows, feature flags, and provider configuration are environment/configuration values rather than hard-coded business rules. |

## 4. Users, roles, and permissions

### 4.1 Roles

| Role | Purpose | Permitted actions |
|---|---|---|
| Safety supervisor | Reviews active safety events and coordinates responses. | View assigned camera/zone dashboards, inspect blurred evidence, acknowledge and resolve alerts, add intervention notes. |
| HSE manager | Reviews aggregate safety posture and trends. | View all aggregate dashboards and reports, configure reporting views, review alert metrics. |
| System administrator | Maintains platform configuration without becoming an evidence reviewer by default. | Manage users, roles, sources, zones, policies, retention settings, inference provider configuration, and audit review. |
| Model evaluator | Validates candidate inference models in the POC. | Run approved benchmark datasets, view evaluation outputs, manage model metadata. |
| Privacy/governance reviewer | Audits operating controls. | View audit records, retention status, aggregate exports, and governance documentation; evidence access must be explicitly granted only where required. |
| Demonstration viewer | Sees an approved POC demonstration. | View sanitized dashboard and pre-approved demo evidence only; cannot change data or configuration. |

### 4.2 Access requirements

- Enforce authenticated access for every non-public route and API.
- Authorize evidence access separately from dashboard access.
- Restrict source and zone configuration to administrators.
- Restrict model provider credentials and configuration to administrators.
- Record immutable audit entries for alert state changes, evidence access, exports, configuration changes, retention jobs, and permission changes.
- Never expose direct object-storage paths as public or guessable URLs.
- Do not include raw media or evidence links in notifications or exports.

## 5. Core user journeys

### 5.1 Upload and process media

1. An authorized operator opens the upload workspace.
2. The operator selects a registered camera source and its associated zone policy.
3. The operator uploads an approved image or video file.
4. The system validates filename extension, MIME type, content signature, file size, duration, and frame dimensions.
5. The system creates a media-processing job and returns a visible processing state.
6. The worker samples frames, runs model inference, normalizes detections, evaluates person-level compliance, and stores only approved records.
7. The operator sees job completion, failure, or partial-completion status.
8. The operator can inspect annotated frames and the aggregated processing outcome.

### 5.2 Persistent non-compliance event

1. A sampled frame contains a valid person anchor.
2. The association engine evaluates helmet and vest evidence within that person’s expected regions.
3. A zone requirement is not met with sufficient confidence.
4. The same violation persists through the configured number of sampled frames.
5. The event service checks for an active equivalent event in the configured deduplication window.
6. If no equivalent active event exists, the system creates a `PPE_NON_COMPLIANCE` event.
7. The evidence service creates an annotated frame and applies face blurring before it is stored or displayed.
8. The event appears in the alert queue and dashboard aggregates.

### 5.3 Supervisor review and acknowledgement

1. A safety supervisor opens an active alert.
2. The interface displays its source, zone, policy version, timestamp, detected requirement failure, model version, confidence summary, and blurred evidence.
3. The supervisor selects **Acknowledge**, writes an intervention note, and optionally marks the event resolved after intervention.
4. The system records the action timestamp, operator reference, prior state, next state, and note in the audit trail.
5. The system updates alert lifecycle metrics such as acknowledgement time.

### 5.4 Aggregate reporting

1. An HSE manager selects a date range, zone, source, and shift.
2. The dashboard retrieves rollups and data-quality counts.
3. The dashboard shows a compliance rate, observed population, unknown count, active/resolved alerts, and trend.
4. A user can export aggregate, non-identifying data only if the role permits it.
5. Every view labels the data as POC/indicative unless the model and governance gates have been approved for production.

## 6. Functional requirements

### 6.1 Media ingestion

| ID | Requirement |
|---|---|
| FR-ING-01 | Accept configured image and video formats only. Initial recommended formats are JPEG, PNG, MP4, and MOV, subject to platform capability. |
| FR-ING-02 | Reject files with mismatched extension, MIME type, or content signature. |
| FR-ING-03 | Enforce configurable limits for upload size, duration, resolution, frame rate, and concurrent jobs. |
| FR-ING-04 | Store each upload as a job with queued, validating, processing, completed, failed, cancelled, or expired status. |
| FR-ING-05 | Delete raw media according to the configured POC retention policy, with test-media retention requiring explicit approval. |
| FR-ING-06 | Return actionable validation and processing failure messages without exposing internal credentials or storage details. |

### 6.2 Detection and normalization

| ID | Requirement |
|---|---|
| FR-DET-01 | Invoke inference through a provider-neutral adapter interface. |
| FR-DET-02 | Normalize provider labels to `person`, `helmet`, `no_helmet`, `vest`, `gloves`, `glasses`, or `unknown_label`. |
| FR-DET-03 | Store provider name, model version, inference timestamp, and per-class confidence metadata for every processed job. |
| FR-DET-04 | Ignore unsupported labels in compliance calculation while preserving them in controlled technical evaluation output if needed. |
| FR-DET-05 | Permit source- and class-specific threshold configuration without code deployment. |
| FR-DET-06 | Fail a job safely when the provider is unavailable, times out, returns malformed detections, or exceeds its configured retry policy. |

### 6.3 Association and rules

| ID | Requirement |
|---|---|
| FR-RULE-01 | Use a detected person as the anchor for PPE attribution. |
| FR-RULE-02 | Evaluate helmet evidence in a configurable upper-person region and vest evidence in a configurable torso region. |
| FR-RULE-03 | Associate PPE only when containment, overlap, relative position, and confidence satisfy policy thresholds. |
| FR-RULE-04 | Apply explicit `no_helmet` precedence when supported by the selected model. |
| FR-RULE-05 | Produce `unknown` when evidence is ambiguous, occluded, too small, or below threshold. |
| FR-RULE-06 | Evaluate compliance against the policy version active for the selected source and zone. |
| FR-RULE-07 | Require a configurable number of consecutive sampled frames before opening a violation. |
| FR-RULE-08 | Deduplicate equivalent active violations for the configured source, zone, rule, and time period. |
| FR-RULE-09 | Exclude `unknown` observations from headline compliance-rate denominators and expose them as a separate data-quality measure. |

### 6.4 Alerts and evidence

| ID | Requirement |
|---|---|
| FR-ALERT-01 | Create `PPE_NON_COMPLIANCE` events for sustained non-compliance only. |
| FR-ALERT-02 | Support `open`, `acknowledged`, `resolved`, `expired`, and `cancelled` event states. |
| FR-ALERT-03 | Support acknowledgement and optional resolution notes without collecting worker identity. |
| FR-ALERT-04 | Generate a visual annotation of relevant people, PPE, and policy result. |
| FR-ALERT-05 | Blur face regions before evidence can be persisted, rendered, or downloaded. |
| FR-ALERT-06 | Enforce evidence expiry and make expired evidence inaccessible. |
| FR-ALERT-07 | Show confidence and uncertainty context to reviewers. |

### 6.5 Dashboards and reports

| ID | Requirement |
|---|---|
| FR-RPT-01 | Calculate aggregate compliance rate by camera, zone, shift, time interval, and PPE rule. |
| FR-RPT-02 | Display observed, compliant, non-compliant, and unknown counts beside every percentage. |
| FR-RPT-03 | Display alert volume, alert state, acknowledgement time, and resolution time. |
| FR-RPT-04 | Support filtering by permitted data scope and date range. |
| FR-RPT-05 | Restrict exports to aggregate, non-identifying records. |
| FR-RPT-06 | Mark POC model outputs as indicative and unsuitable for autonomous enforcement. |

## 7. Compliance decision design

### 7.1 Normalized PPE taxonomy

| Canonical class | Meaning | POC use |
|---|---|---|
| `person` | Human anchor detection. | Required |
| `helmet` | Positive hard-hat signal. | Required |
| `no_helmet` | Explicit hard-hat absence signal. | Required where model supports it |
| `vest` | Positive high-visibility vest signal. | Required |
| `gloves` | Protective-glove signal. | Experimental |
| `glasses` | Eye-protection signal. | Experimental |
| `unknown` | Insufficient confidence or evidence. | Required safety state |

### 7.2 Zone policy model

Each safety zone has an independently versioned policy:

```text
Zone policy
  - zone identifier
  - effective start and optional end time
  - required PPE rules
  - per-class confidence threshold
  - video sampling rate
  - persistence-frame count
  - alert deduplication period
  - evidence retention duration
  - enabled/disabled state
```

Example POC policy:

| Attribute | Yard zone example |
|---|---|
| Required PPE | Helmet and vest |
| Person threshold | 0.25 |
| Helmet/no-helmet threshold | 0.25, tuned after validation |
| Vest threshold | 0.25, tuned after validation |
| Sampling rate | 2–5 frames per second |
| Persistence | 3 consecutive sampled frames |
| Deduplication | 60 seconds |
| Evidence retention | 24–72 hours |
| Experimental classes | Gloves and glasses excluded from headline rate |

### 7.3 Person-PPE association algorithm

For each frame:

1. Filter detections below the applicable per-class confidence thresholds.
2. Select valid `person` detections as anchor candidates.
3. Create a normalized bounding box for each anchor.
4. Define relative regions:
   - head region: upper portion of the person box;
   - torso region: central/middle portion of the person box.
5. Search for eligible PPE detections within each region.
6. Score candidates using confidence, region overlap, PPE-center location, and proximity to the person anchor.
7. Associate at most one strongest matching state per PPE rule unless a reviewed model strategy supports multiple valid items.
8. Apply rule precedence:
   - explicit `no_helmet` with sufficient confidence results in helmet failure;
   - valid `helmet` results in helmet pass;
   - ambiguous/missing evidence becomes `unknown`;
   - do not infer no-helmet merely from absent helmet detection.
9. Combine individual rule states into a person state:
   - `non_compliant` when any mandatory rule has reliable failure evidence;
   - `compliant` when all mandatory rules have reliable pass evidence;
   - `unknown` when no reliable failure exists but one or more mandatory rules cannot be established.
10. Pass the result into persistence and event-deduplication processing.

### 7.4 Persistence state machine

```text
unknown / compliant
  └─ reliable violation detected → candidate_violation(frame_count=1)

candidate_violation
  ├─ equivalent violation in next sampled frame → increment count
  ├─ count reaches policy persistence threshold → active_violation
  ├─ compliant or unknown result → reset candidate
  └─ end of job → reset candidate

active_violation
  ├─ equivalent event exists in deduplication window → update observation summary
  ├─ no equivalent event → create PPE_NON_COMPLIANCE event
  ├─ reliably compliant observations continue → eligible for resolution
  └─ event reaches evidence expiry → expired evidence, retained aggregate metrics only
```

The POC must use ephemeral, job/frame-scoped correlation only. It must not use a persistent person identity to join people across jobs or long durations.

## 8. System architecture

### 8.1 Logical architecture

```text
Operator Browser
  ├─ Upload workspace
  ├─ Processing viewer
  ├─ Alert queue and evidence inspector
  ├─ Compliance dashboard
  └─ Administration pages
          │
          ▼
Application API
  ├─ Authentication and authorization
  ├─ Source, zone, and policy service
  ├─ Upload/media service
  ├─ Job orchestration service
  ├─ Alert and evidence service
  ├─ Reporting service
  └─ Audit service
          │
          ▼
Asynchronous processing worker
  ├─ Frame sampler
  ├─ Detection provider adapter
  ├─ Detection normalization
  ├─ Person-PPE association engine
  ├─ Compliance rules engine
  ├─ Event deduplication
  ├─ Annotation and face blurring
  └─ Metric rollup writer
          │
          ├───────────────► Configured inference provider
          │                  (POC YOLO / hosted benchmark / future permissive model)
          ▼
Persistence services
  ├─ Relational application database
  ├─ Private media/evidence object storage
  ├─ Job queue
  └─ Audit/event logs
```

### 8.2 Component responsibilities

| Component | Responsibilities |
|---|---|
| Web application | Provides role-specific workflows, explains POC boundaries, and never presents an alert as an automated personnel decision. |
| API service | Validates requests, applies authorization, manages durable application records, and exposes documented endpoints. |
| Job queue | Separates upload requests from potentially long-running video analysis and provides recoverable job state. |
| Processing worker | Samples media, invokes inference, applies normalized decision logic, and creates observation/event outputs. |
| Detection adapter | Encapsulates provider-specific authentication, request format, label mapping, coordinate format, timeout behavior, and errors. |
| Rules engine | Applies versioned zone policy and produces explainable per-person results. |
| Evidence service | Creates annotated, face-blurred snapshots and applies lifecycle/authorization controls. |
| Reporting service | Produces aggregate metrics and data-quality measures. |
| Database | Stores configuration, job state, aggregate observations, event lifecycle, acknowledgement, and audit records. |
| Object storage | Holds encrypted, private raw media and evidence only for approved retention periods. |
| Scheduler | Executes retention deletion, rollup reconciliation, and health checks. |

### 8.3 Technology constraints

The repository contains no implementation or established application stack. The implementation team must choose a Kavia.ai-supported frontend, backend, persistence, queue, and object-storage stack before coding. The chosen stack must support:

- asynchronous media processing;
- authenticated and role-authorized APIs;
- private object storage with expiry/lifecycle capability;
- relational data persistence;
- structured logs and audit records;
- an inference client or hosted-inference integration;
- image processing capable of annotation and face blurring;
- dashboard visualizations;
- environment-based configuration without secrets in source control.

## 9. Backend interface blueprint

All public API routes must be authenticated, authorized, documented through OpenAPI/Swagger metadata, and grouped by capability. The actual base path is implementation-defined; `/api/v1` is the recommended versioned prefix.

### 9.1 Sources and zones

| Method | Route | Purpose | Required role |
|---|---|---|---|
| `GET` | `/api/v1/sources` | List permitted camera sources. | Supervisor or above |
| `POST` | `/api/v1/sources` | Create a camera source. | Administrator |
| `GET` | `/api/v1/sources/{sourceId}` | Retrieve source configuration. | Authorized scope |
| `PATCH` | `/api/v1/sources/{sourceId}` | Update a source. | Administrator |
| `GET` | `/api/v1/zones` | List zones and current policies. | Supervisor or above |
| `POST` | `/api/v1/zones` | Create a zone. | Administrator |
| `PATCH` | `/api/v1/zones/{zoneId}/policy` | Create or activate a versioned zone policy. | Administrator |

### 9.2 Media jobs

| Method | Route | Purpose | Required role |
|---|---|---|---|
| `POST` | `/api/v1/media-jobs` | Validate and create an upload processing job. | Supervisor or above |
| `GET` | `/api/v1/media-jobs/{jobId}` | Retrieve job status and summary. | Authorized scope |
| `GET` | `/api/v1/media-jobs/{jobId}/frames` | Retrieve approved frame/observation summaries. | Authorized scope |
| `POST` | `/api/v1/media-jobs/{jobId}/cancel` | Request cancellation of queued/processing job. | Job creator or administrator |

### 9.3 Alerts and evidence

| Method | Route | Purpose | Required role |
|---|---|---|---|
| `GET` | `/api/v1/alerts` | Filter permitted alerts. | Supervisor or above |
| `GET` | `/api/v1/alerts/{alertId}` | Get alert metadata and lifecycle history. | Authorized scope |
| `POST` | `/api/v1/alerts/{alertId}/acknowledgements` | Acknowledge an event with intervention note. | Supervisor |
| `POST` | `/api/v1/alerts/{alertId}/resolve` | Resolve an event with optional outcome note. | Supervisor |
| `GET` | `/api/v1/alerts/{alertId}/evidence` | Retrieve time-limited blurred evidence view. | Evidence-authorized role |

### 9.4 Reporting and administration

| Method | Route | Purpose | Required role |
|---|---|---|---|
| `GET` | `/api/v1/reports/compliance` | Retrieve aggregate compliance metrics. | HSE manager or above |
| `GET` | `/api/v1/reports/alerts` | Retrieve aggregate alert metrics. | HSE manager or above |
| `POST` | `/api/v1/reports/exports` | Create aggregate-only export. | HSE manager or above |
| `GET` | `/api/v1/model-evaluations` | List benchmark/evaluation runs. | Model evaluator or above |
| `POST` | `/api/v1/model-evaluations` | Create approved evaluation run metadata. | Model evaluator |
| `GET` | `/api/v1/audit-events` | Query security and operating audit records. | Administrator or governance reviewer |

### 9.5 Request and response requirements

- Validate route parameters, query values, uploaded media, and request bodies with explicit schemas.
- Document all fields, response codes, and error conditions.
- Include correlation/request IDs in responses and structured logs.
- Return `400` for invalid requests, `401` for missing authentication, `403` for denied authorization, `404` for unavailable resources within permitted scope, `409` for conflicting state transitions, `413` for oversized uploads, `415` for unsupported media, `422` for semantic validation failure, and `5xx` only for unexpected failures.
- Do not return provider credentials, internal object-store paths, raw model errors, or sensitive configuration in API output.

## 10. Data blueprint

### 10.1 Data classification

| Classification | Examples | Control |
|---|---|---|
| Configuration | Zones, policies, source names, thresholds. | Role-limited change access and audit logs. |
| Operational personal-data-adjacent media | Uploaded footage and evidence snapshots. | Private storage, encryption, short retention, face blur, restricted access. |
| Non-identifying observation data | Counts, class states, frame-scoped keys, confidence summaries. | Retention-controlled and access-limited. |
| Aggregate safety data | Compliance rates and rollups. | Wider HSE access; exclude identities and image links. |
| Audit data | Access, configuration, lifecycle changes. | Append-only or tamper-evident controls, restricted access. |
| Secrets | Provider keys, storage credentials, signing keys. | Environment/secret manager only; never database plaintext, browser, code, logs, or documentation. |

### 10.2 Core relational entities

| Entity | Essential fields | Notes |
|---|---|---|
| `user` | `id`, `role`, `status`, `created_at` | Do not replicate unnecessary HR data. |
| `camera_source` | `id`, `name`, `zone_id`, `enabled`, `created_at` | Camera/source metadata only. |
| `zone` | `id`, `name`, `description`, `enabled` | Safety geography/configuration. |
| `zone_policy_version` | `id`, `zone_id`, `version`, `requirements`, `thresholds`, `effective_from`, `effective_to` | Immutable after activation except controlled supersession. |
| `media_job` | `id`, `source_id`, `policy_version_id`, `status`, `media_type`, `submitted_at`, `completed_at`, `expires_at`, `failure_code` | Holds workflow state, not raw media path in public responses. |
| `frame_observation` | `id`, `job_id`, `timestamp`, `person_count`, `compliant_count`, `non_compliant_count`, `unknown_count` | Supports validation and reconciliation. |
| `person_observation` | `id`, `frame_observation_id`, `ephemeral_frame_key`, `state`, `rule_results`, `confidence_summary`, `expires_at` | No name, face template, or persistent identity. |
| `compliance_event` | `id`, `source_id`, `zone_id`, `policy_version_id`, `type`, `status`, `opened_at`, `resolved_at`, `expires_at` | `type` is initially `PPE_NON_COMPLIANCE`. |
| `event_rule_result` | `id`, `event_id`, `rule_key`, `result`, `confidence_summary` | Explains why the alert was created. |
| `evidence_snapshot` | `id`, `event_id`, `storage_ref`, `blurred`, `created_at`, `expires_at`, `deleted_at` | `blurred` must be true before availability. |
| `event_acknowledgement` | `id`, `event_id`, `operator_reference`, `note`, `acknowledged_at` | Operator reference is access/audit metadata, not subject identity. |
| `metric_rollup` | `id`, `interval_start`, `interval_end`, `source_id`, `zone_id`, `shift`, `rule_key`, `observed`, `compliant`, `non_compliant`, `unknown` | Retain aggregate records longer than media subject to approved policy. |
| `model_version` | `id`, `provider`, `name`, `version`, `licence_status`, `approved_for`, `activated_at` | Supports reproducibility and licence review. |
| `audit_event` | `id`, `actor_reference`, `action`, `entity_type`, `entity_id`, `timestamp`, `metadata` | Avoid raw media or sensitive free-text duplication. |

### 10.3 Retention and deletion requirements

| Artifact | POC retention target | Required action |
|---|---|---|
| Raw uploaded media | Delete after processing unless explicitly retained for controlled evaluation. | Automated deletion with logged outcome. |
| Blurred evidence snapshot | 24–72 hours, configurable by approved policy. | Make inaccessible at expiry and delete from storage. |
| Person observations | Short-lived; align with evidence/operational need. | Automatically purge; retain aggregate counts. |
| Processing logs | Minimum needed for technical operation; avoid image content and PII. | Apply log retention policy. |
| Aggregate metric rollups | Retain for demonstration/reporting subject to governance decision. | No evidence links or identities. |
| Audit records | Retain per security/governance requirements. | Tamper-evident restricted store. |

Retention schedules are configuration and governance decisions. They must be implemented as automated lifecycle jobs and verified through tests and operational monitoring.

## 11. Frontend blueprint

### 11.1 Application navigation

```text
Dashboard
  ├─ Compliance overview
  ├─ Trend analysis
  └─ Data quality / unknown observations

Media processing
  ├─ New upload
  ├─ Job list
  └─ Processing inspector

Alerts
  ├─ Open and acknowledged alerts
  ├─ Alert details
  └─ Blurred evidence inspector

Administration
  ├─ Sources
  ├─ Zones and PPE policies
  ├─ Retention settings
  ├─ Model metadata
  └─ Access/audit review
```

### 11.2 Required interface states

Every meaningful page must support loading, empty, success, validation-error, authorization-error, service-error, and expired-data states.

| Screen | Essential behavior |
|---|---|
| Dashboard | Shows aggregate rate, denominator, unknown count, filters, trend, and a POC disclaimer. |
| Upload form | Requires source and zone context; explains accepted formats and applicable policy. |
| Job list | Shows status, submitted time, source, outcome count, and safe failure information. |
| Job inspector | Renders annotated result frames with labels, confidence summaries, and non-identifying per-frame findings. |
| Alert queue | Supports filters and clearly distinguishes open, acknowledged, resolved, and expired evidence. |
| Alert detail | Shows why it was flagged, what policy was applied, blurred evidence, lifecycle actions, and intervention note history. |
| Policy configuration | Lets an administrator manage versioned helmet/vest requirements, thresholds, persistence, and retention. |
| Model evaluation | Shows model/version, evaluation data source, class metrics, latency, limitations, and approval state. |
| Audit view | Allows permitted reviewers to query access and configuration events without exposing raw evidence in the event list. |

### 11.3 Accessibility and usability

- Use clear safety terminology and avoid language implying worker ranking or surveillance.
- Provide keyboard-accessible forms, tables, dialogs, filters, and image controls.
- Ensure status is not communicated by color alone.
- Use descriptive error messages and recovery guidance.
- Label confidence, uncertainty, and `unknown` states in plain language.
- Make evidence expiry visible and avoid showing unavailable evidence as a broken image.
- Provide responsive layouts appropriate for supervisors reviewing alerts on a tablet or desktop.

## 12. Security and privacy design

### 12.1 Threat controls

| Risk | Required control |
|---|---|
| Unauthorized evidence access | Authentication, role authorization, private storage, time-limited access, audit events. |
| Public/guessable evidence URLs | Never expose permanent public object paths; proxy access through authorized API checks. |
| Malicious media upload | Signature/MIME validation, size/duration limits, isolated processing, safe file naming, malware scanning where platform services permit. |
| Excessive media retention | Scheduled deletion, expirations stored with artifacts, deletion monitoring and audit records. |
| Identity leakage in evidence | Face blur before persistence/display; no original unblurred evidence in reviewer routes. |
| Worker profiling | No user/person identity model, no persistent cross-job person key, no individual dashboard. |
| False enforcement action | Display POC caveat, unknown state, confidence evidence, persistence thresholds, and human-review workflow. |
| Model or licence drift | Store model metadata, approval state, licence review result, and activated version. |
| Credential exposure | Use platform secret management/environment configuration; prohibit secrets in source, logs, client bundles, or export data. |
| Privilege escalation | Least-privilege roles, server-side authorization, audit role changes, and secure session handling. |

### 12.2 Privacy gates

The POC is a technical demonstrator and does not complete the governance process. Before any live operational pilot, require:

1. Data protection impact assessment (DPIA).
2. Legal/privacy assessment for personal-data processing.
3. Works-council engagement in applicable jurisdictions.
4. Written safety purpose and non-productivity-monitoring rules.
5. Approved retention, access, deletion, and evidence handling policy.
6. A documented human intervention and escalation process.
7. Model-quality acceptance criteria using site-representative footage.
8. Approved production licensing across code, models, weights, datasets, and services.

## 13. Model and licence strategy

### 13.1 POC route

The POC begins with the `melihuzunoglu/ppe-detection` YOLO model because it maps well to the required initial classes. It must be accessed behind a provider adapter and labeled as an internal demonstration route. Candidate hosted alternatives, including the Roboflow Construction Site Safety model, may be evaluated through the same interface.

The selected POC model must not be silently represented as production-cleared. Its model card, licence, source, version/checksum where available, date acquired, known classes, and test results must be recorded.

### 13.2 Production route

The initial production direction is a permissively licensed detector such as YOLOX or another Apache-2.0 alternative, fine-tuned on commercially cleared data. Safety Helmet Detection with CC0 licensing is the proposed starting dataset for helmet-focused capability.

A production approval package must separately confirm:

- framework/source-code licence;
- model architecture/source licence;
- base-weight licence;
- fine-tuned-weight ownership and licence;
- training and validation dataset licence;
- annotation-service terms;
- hosted-inference or deployment-service terms;
- dependency licences;
- intended commercial/network-service distribution model.

### 13.3 Evaluation policy

Benchmark all candidates on a common, labeled, held-out clip/image set. Report per class:

- true positives, false positives, false negatives;
- precision, recall, and F1;
- confidence calibration observations;
- no-helmet vs. occlusion/unknown confusion;
- per-camera-angle results;
- inference latency and effective sampling rate;
- unknown/excluded-person proportion;
- representative failure cases;
- model and dataset licence status.

Do not use a single headline accuracy figure as evidence of PPE compliance fitness.

## 14. Non-functional requirements

| Area | Requirement |
|---|---|
| Reliability | A failed job must remain inspectable with a safe failure reason and retry eligibility where appropriate. |
| Performance | Establish a measured POC target after selecting infrastructure; report processing time per frame, clip, and job. |
| Scalability | Decouple web requests from inference through a queue/worker architecture. |
| Observability | Emit structured logs, job metrics, inference latency, queue depth, provider failures, retention outcomes, and audit events. |
| Recoverability | Job processing must be idempotent or safely retryable without duplicate alerts. |
| Maintainability | Keep provider adapters, policy evaluation, persistence, media processing, and UI concerns separated. |
| Explainability | Preserve policy version, rule result, confidence summary, and model version for each event. |
| Data quality | Surface unknown observations and model limitations rather than silently discarding them. |
| Configuration | Use environment/configuration management; no runtime secrets or deployment-specific values in code. |
| Documentation | Provide API documentation, setup instructions, configuration reference, architecture overview, privacy operating guide, and demo procedure. |

## 15. Delivery work breakdown

### Work package 1 — Application foundation

- Select the Kavia.ai-compatible stack.
- Create frontend, API, worker, queue, database, storage, and authentication foundations.
- Create environment example/configuration documentation without committing secrets.
- Add OpenAPI metadata and documented, versioned API patterns.
- Establish roles, authorization guards, error handling, and structured logging.

**Exit:** authenticated skeleton can show a protected dashboard and submit a mock processing job.

### Work package 2 — Configuration and media ingestion

- Implement sources, zones, and versioned policies.
- Implement upload validation and private object storage.
- Create job lifecycle records and status views.
- Add cancellation and safe retry design.

**Exit:** approved media creates a tracked processing job; invalid media is rejected safely.

### Work package 3 — Inference integration

- Define the provider interface.
- Implement initial PPE provider adapter.
- Normalize classes and coordinates.
- Store model/version metadata.
- Add timeout, retry, and malformed-result handling.

**Exit:** processed image/video frames produce normalized person, helmet/no-helmet, and vest detections.

### Work package 4 — Compliance intelligence

- Implement person anchor detection and relative-region association.
- Implement configurable policy evaluation and `unknown` state.
- Implement persistence and event deduplication.
- Store frame and person observation summaries.

**Exit:** sustained helmet/vest violations create stable, explainable, non-duplicated events.

### Work package 5 — Evidence and alert operations

- Implement event state transitions.
- Generate annotations and blur face regions before storage/display.
- Implement acknowledgement and resolution workflows.
- Enforce evidence authorization and expiration.

**Exit:** supervisor can review blurred evidence, acknowledge an event, and record intervention.

### Work package 6 — Aggregate dashboard

- Implement metric rollups and reconciliation.
- Build dashboard cards, trend chart, filter controls, alert metrics, and unknown-data indicator.
- Implement aggregate-only export.

**Exit:** a processed clip updates source/zone/shift metrics with transparent denominators.

### Work package 7 — Validation and demonstrator

- Build labeled fixture set and evaluation workflow.
- Execute functional, privacy, security, and model tests.
- Benchmark model candidates and document results.
- Prepare simulated-live demonstration script and Phase-2 handoff.

**Exit:** repeatable demo, benchmark evidence, known limitations, and pilot prerequisites are documented.

## 16. Five-day POC schedule

| Day | Deliverables | Exit criterion |
|---|---|---|
| Day 1 | Application skeleton, upload flow, job state, primary model adapter, basic annotated inspection. | Person, helmet/no-helmet, and vest detections run end to end. |
| Day 2 | Candidate model benchmark, initial validation set, licence register, production-route baseline research/prototype. | POC candidate and permissive production direction are documented with class-level results. |
| Day 3 | Person-PPE association, zone policy, unknown state, persistence, deduplication, event records. | Persistent person-level non-compliance events are stable and explainable. |
| Day 4 | Blurred evidence, alert acknowledgement, intervention notes, aggregate dashboard and trend reporting. | Processed media creates reviewable alert evidence and updates metrics. |
| Day 5 | End-to-end validation, simulated live demonstration, demo reel, pilot/guidance package. | Demo is repeatable and its technical/governance limitations are explicit. |

## 17. Quality strategy and acceptance criteria

### 17.1 Automated tests

| Layer | Required tests |
|---|---|
| Unit | Label normalization, bounding-box geometry, association scoring, rule precedence, unknown handling, persistence, deduplication, rate calculations, retention eligibility. |
| API | Authentication, authorization, request validation, upload limits, state transitions, error responses, aggregate-only exports. |
| Integration | Queue/job lifecycle, provider adapter success/failure, object storage access restriction, evidence blur pipeline, scheduled expiry/deletion. |
| UI | Upload validation feedback, job status changes, dashboard denominators, alert acknowledgement, evidence expiration state, role-based route access. |
| Security | Unauthorized evidence request denial, public URL prevention, malicious/mismatched upload rejection, sensitive fields absent from export/API output. |
| Data reconciliation | Dashboard rollups match underlying compliant/non-compliant/unknown aggregate observations. |

### 17.2 POC acceptance checklist

The POC is accepted only when all applicable checks are evidenced:

- [ ] Valid images and videos create and complete media jobs.
- [ ] Invalid, oversized, unsupported, or spoofed uploads fail safely.
- [ ] The selected provider returns normalized classes and documented model metadata.
- [ ] Zone policy controls helmet and vest requirements.
- [ ] Ambiguous observations become `unknown` rather than violations.
- [ ] A violation requires configured multi-frame persistence.
- [ ] Equivalent active violations are deduplicated.
- [ ] Each created alert records source, zone, policy version, model version, rule results, and timestamps.
- [ ] Evidence is annotated and face-blurred before any reviewer view.
- [ ] Evidence access is role-checked, audited, and expires.
- [ ] Alert acknowledgement and intervention note are stored and audited.
- [ ] Dashboard rates show numerator, denominator, and unknown counts.
- [ ] Export output contains aggregate, non-identifying data only.
- [ ] Class-level benchmark metrics and limitations are documented.
- [ ] The demonstration clearly states that outputs are safety-support signals, not autonomous enforcement.
- [ ] Phase-2 governance gates are included in the handoff material.

## 18. Operational readiness

### 18.1 Monitoring

Track and alert on:

- upload validation failures;
- queued, processing, failed, cancelled, and expired jobs;
- job duration and processing latency;
- frame sampling throughput;
- inference timeout/error rate by provider and model version;
- alert creation/deduplication rate;
- evidence blur failures;
- evidence access denials;
- retention/deletion success and failure;
- dashboard rollup reconciliation errors;
- authentication and authorization failures;
- configuration/policy changes;
- storage consumption and object lifecycle outcomes.

### 18.2 Incident response expectations

| Scenario | Initial response |
|---|---|
| Inference provider outage | Mark job retryable or failed with safe explanation; do not fabricate results. |
| Evidence blur failure | Block evidence availability, record technical failure, and alert an administrator. |
| Retention job failure | Preserve no longer than required by approved policy; investigate immediately and log remediation. |
| Unauthorized evidence-access attempt | Deny request, audit it, and notify security per operating procedure. |
| High false-alert rate | Pause affected policy/model route if necessary, review benchmark/threshold settings, and document the change. |
| Privacy concern | Restrict relevant evidence access, preserve necessary audit records, and escalate to designated privacy/governance owner. |

### 18.3 Demo runbook

1. Verify environment health, private storage, model availability, and permitted user accounts.
2. Confirm the selected source and policy demonstrate helmet and vest requirements.
3. Upload an approved prepared clip containing compliant and non-compliant examples.
4. Show job status progression and normalized detections.
5. Show person-anchored policy result and frame persistence behavior.
6. Open the single deduplicated alert and show blurred evidence.
7. Acknowledge the alert and add a sample safety intervention note.
8. Show aggregate rate, observed count, unknown count, trend, and alert status.
9. Explain model limitations, camera-angle risks, privacy controls, POC licensing route, and Phase-2 governance gates.
10. Confirm that the solution does not identify workers or make autonomous disciplinary decisions.

## 19. Risks and mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| GDPR/employee-monitoring concerns | Pilot delay or unacceptable deployment design. | Safety framing, no identity tracking, aggregation, short retention, face blurring, DPIA, and works-council engagement. |
| High-angle/wide-angle domain gap | Missed PPE or false alerts on site cameras. | Use POC outputs as indicative; collect approved site footage and fine-tune before pilot scale-up. |
| Occlusion/crowding | Unstable associations and duplicate alerts. | Person-anchored rules, `unknown`, multi-frame persistence, and deduplication. |
| Weak no-helmet model output | Incorrect non-compliance assertion. | Prefer explicit no-helmet class; require confidence; classify ambiguity as unknown; validate with labelled samples. |
| Secondary PPE inaccuracy | Misleading headline metrics. | Limit headline rate to validated helmet and vest rules. |
| AGPL/non-commercial licence restrictions | Production legal exposure. | Separate POC and production paths; preserve licence register; use permissive model/data stack for production. |
| Evidence retention drift | Privacy/control failure. | Automated lifecycle deletion, deletion audits, monitoring, and periodic verification. |
| Over-alerting | Supervisor fatigue and poor trust. | Persistence, deduplication, threshold tuning, and rates reported over intervals. |
| Misuse as HR surveillance | Governance and employee-relations harm. | Scope controls, role limits, no identity data, safety-only policy, and governance review. |

## 20. Phase-2 pilot handoff package

Before a reference-site pilot begins, produce and approve:

1. DPIA and privacy assessment.
2. Works-council engagement plan and outcome.
3. Site safety-zone register and PPE-policy owners.
4. Live camera/VMS/RTSP integration design.
5. Approved evidence retention, deletion, encryption, access-control, and audit policy.
6. Site-footage collection and annotation plan.
7. Model validation protocol and measurable minimum acceptance criteria.
8. Commercial licensing register for code, models, weights, training data, evaluation data, and services.
9. Operational response playbook identifying who receives, reviews, and closes alerts.
10. Pilot success measures focused on aggregate safety compliance reporting and intervention value.
11. Rollback/deactivation procedure for the inference pipeline and evidence collection.
12. Training materials for supervisors, HSE managers, administrators, and governance reviewers.

## 21. Definition of done

The PPE Compliance Detection POC is complete when it delivers a working, secure, privacy-aware technical demonstration of the detect → decide → alert → aggregate-report workflow; includes a transparent class-level evaluation; prevents identity-based surveillance behavior; retains evidence only briefly and accessibly only to authorized roles; and packages all unresolved production, governance, model, and licensing dependencies for Phase 2.
