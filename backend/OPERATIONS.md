# PPE Compliance Detection POC — Operations Guide

This local POC provides **indicative safety-support signals only**. It must not be used for employee performance monitoring, worker identification, facial recognition, persistent tracking, autonomous enforcement, or employment decisions.

## Face-detector privacy-gate readiness

`GET /health/face-detector` reports whether the mandatory OpenCV DNN face-detector model
(`deploy.prototxt` + `res10_300x300_ssd_iter_140000_fp16.caffemodel`) is configured, loads,
and successfully runs a forward pass — independent of whether any evidence has been
processed yet. The same check runs once at application startup and logs its result.

| `status` | Meaning | Operational action |
| --- | --- | --- |
| `ready` | The configured model loaded and produced a valid inference result. | None; evidence generation may proceed normally. |
| `not_configured` | `PPE_FACE_DETECTOR_PROTOTXT_PATH` / `PPE_FACE_DETECTOR_MODEL_PATH` are unset. | Expected in local development. Configure both paths before enabling any evidence-generating deployment. |
| `unavailable` | Paths are set, but the files are missing, unreadable, or the model failed to load or run. | Re-provision the approved model assets; every evidence attempt will fail closed until this is resolved. |

This check never blocks the application from starting: a `not_configured` or
`unavailable` result only means evidence generation will fail closed (Phase 7), consistent
with `create_annotated_blurred_evidence` never producing an unblurred image.

The model files bundled at `backend/models/face_detector/` (point
`PPE_FACE_DETECTOR_PROTOTXT_PATH` / `PPE_FACE_DETECTOR_MODEL_PATH` at them to enable
evidence generation) are not committed without a licence trail: see
[`backend/models/face_detector/README.md`](../backend/models/face_detector/README.md) for
their upstream source, licence, and a checksum verified against OpenCV's own manifest.

## Policy effective time window

A zone policy version may optionally carry `effective_start` and `effective_end` UTC
timestamps via `PATCH /api/v1/zones/{zone_id}/policy`. Both are optional; when unset, the
policy version is effective for as long as it remains `active`. When both are supplied,
`effective_end` must be strictly after `effective_start` or the request is rejected with a
422 response.

The window is enforced, not only recorded: `GET /api/v1/zones` and `POST /api/v1/media-jobs`
both resolve a zone's governing policy through `_currently_effective_policy`, which treats
the `active` policy as unusable outside its own `effective_start`/`effective_end` window.
This lets an administrator stage a future policy change in advance (create it `active` with
a future `effective_start`) without it governing zone display or new jobs early, and lets a
policy intentionally lapse at `effective_end` without a manual follow-up deactivation. When
the `active` policy is outside its window, the zone is treated exactly as if it had no
active policy at all: it is omitted from `GET /api/v1/zones`, and a new upload against one
of its sources is rejected with `409 Conflict` ("The selected source has no active safety
policy") until an operator activates a policy version that is currently in effect.

## Maximum video frame rate

`PPE_MAX_VIDEO_FPS` (default `60.0`) bounds the accepted native frame rate for uploaded
MP4/MOV clips, alongside the existing frame-dimension and duration checks. An upload whose
detected FPS exceeds this limit is rejected with a 422 response before it is queued for
processing, and its already-saved private copy is deleted.

## Concurrent media-job limit

`PPE_MAX_CONCURRENT_JOBS` (default `5`) bounds how many media jobs may be simultaneously
`queued` or `processing` at once. `POST /api/v1/media-jobs` returns `429 Too Many Requests`
once this limit is reached; callers should retry after a short delay rather than treating
this as a permanent failure.

## Test-media retention approval workflow

An upload may be marked `is_test_media=true` (an optional `POST /api/v1/media-jobs` query
parameter) to identify it as non-operational test or demonstration content rather than real
CCTV footage. Only a job flagged this way at upload time is eligible for
`POST /api/v1/media-jobs/{job_id}/approve-test-retention`, restricted to the administrator
role, which requires an explicit `retention_hours` (1-720) and a `justification` note and
extends that job's private raw-media expiry accordingly. Attempting this workflow against a
job not flagged as test media returns `409 Conflict` — real operational media can never have
its retention extended through this endpoint.

## Scheduled retention task

Celery Beat dispatches `ppe.remove_expired_private_data` hourly (`3600` seconds). The task independently processes these categories so a failure in one category does not prevent the others from running:

| Data category | Expiry calculation | Cleanup outcome |
| --- | --- | --- |
| Raw approved upload | `MediaJob.expires_at`, set when the upload is accepted from `PPE_RAW_MEDIA_RETENTION_HOURS` | Deletes the private object and replaces the stored database reference with an opaque expired marker. |
| Face-blurred evidence | `EvidenceSnapshot.expires_at`, set from the active zone-policy `evidence_retention_hours` value (24–72 hours) | Deletes the private evidence object and marks the snapshot deleted. The API blocks expired evidence even if physical deletion needs a retry. |
| Frame observation summaries | `FrameObservation.expires_at`, set when processing persists a summary from `PPE_FRAME_OBSERVATION_RETENTION_HOURS` | Deletes only expired aggregate frame summaries; non-expired summaries are untouched. |
| Person observation summaries | `PersonObservation.expires_at`, set alongside its parent frame summary from `PPE_FRAME_OBSERVATION_RETENTION_HOURS` | Deletes only expired per-person frame states; non-expired summaries are untouched. |
| Aggregate metric rollups | Governance-controlled | Not deleted by this task. |
| Audit records | Governance/security-controlled | Not deleted by this task. |

Frame summaries contain only job-scoped counts, frame index, and confidence ranges. Person summaries contain only a frame-scoped state (`compliant`/`non_compliant`/`unknown`), the failed requirement, and a confidence value; `person_index` is only that frame's detection ordinal, never a tracking key. Neither table contains worker names, biometric information, face data, persistent person IDs, or profiles.

## Configuration

Use the platform environment-management workflow for non-secret configuration. Never commit a populated `.env` file, storage credentials, or private paths.

| Variable | Default | Purpose |
| --- | ---: | --- |
| `PPE_RAW_MEDIA_RETENTION_HOURS` | `1` | Duration before a private raw upload expires. |
| `PPE_FRAME_OBSERVATION_RETENTION_HOURS` | `24` | Duration before a persisted, non-identifying frame summary expires. |
| `PPE_EVIDENCE_RETENTION_HOURS` | `48` | Default POC evidence setting; the active zone-policy version ultimately sets individual evidence expiry and is API-limited to 24–72 hours. |

## Starting scheduled cleanup

Start a worker and scheduler against the configured Redis service:

```bash
cd backend
. .venv/bin/activate
celery -A app.worker.celery_app worker --loglevel=INFO
celery -A app.worker.celery_app beat --loglevel=INFO
```

Do not expose private storage directories publicly as part of any retention operation.

## Monitoring result

The task returns a JSON-serializable aggregate payload suitable for a monitoring system:

```json
{
  "status": "completed",
  "expired_media_deleted": 0,
  "expired_evidence_deleted": 0,
  "expired_frame_summaries_deleted": 0,
  "expired_person_summaries_deleted": 0,
  "failures": 0,
  "failure_categories": []
}
```

`status` is one of:

- `completed`: every category completed without a recorded cleanup failure;
- `completed_with_errors`: at least one cleanup category failed, while another category completed work;
- `failed`: cleanup categories recorded failures and none completed deletion work.

The payload intentionally excludes storage keys, raw or signed URLs, credentials, evidence contents, worker information, faces, and personal data.

## Audit evidence

Successful outcomes add append-only audit events including:

- `retention.media_deleted`;
- `retention.evidence_deleted`;
- `retention.frame_summaries_deleted`;
- `retention.person_summaries_deleted`; and
- `retention.executed`.

Failed category or object cleanup adds `retention.failed`. A partial run is recorded as `retention.completed_with_errors`; when all cleanup work fails, the aggregate run is recorded as `retention.failed` with a `failed` task status. Audit details include safe aggregate counts or high-level failure categories only—not raw paths, storage keys, public URLs, media contents, biometrics, worker identities, or HR data.

Authorized administrators and privacy/governance reviewers may inspect restricted records using `GET /api/v1/audit-events`.

## Failure recovery

1. Identify the high-level failed category from the task result (`media_deletion`, `evidence_deletion`, `frame_summary_deletion`, or `person_summary_deletion`).
2. Inspect the aggregate task result and corresponding restricted audit records.
3. Confirm the relevant private storage or database service is available without downloading, viewing, copying, or exposing unnecessary raw media or evidence.
4. Allow the next scheduled execution to retry. Failed raw-media and evidence records retain their original opaque key so they remain eligible; expired evidence remains unavailable through the API.
5. Escalate persistent failures through the approved privacy, security, and platform operations process. Pause uploads if required by approved retention governance.
6. Never make private media public, create direct storage links, restore expired evidence for convenience, or add identity-level metadata to diagnose cleanup.

## POC limitations

The default adapter uses local private filesystem storage and does not provide production object-storage lifecycle administration. Before any live employee/CCTV deployment, validate retention behavior, access controls, applicable legal requirements, DPIA/privacy impact assessment obligations, model performance, managed secret handling, production storage, monitoring, and incident response controls.
