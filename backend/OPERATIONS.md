# PPE Compliance Detection POC — Operations Guide

This local POC provides **indicative safety-support signals only**. It must not be used for employee performance monitoring, worker identification, facial recognition, persistent tracking, autonomous enforcement, or employment decisions.

## Scheduled retention task

Celery Beat dispatches `ppe.remove_expired_private_data` hourly (`3600` seconds). The task independently processes these categories so a failure in one category does not prevent the others from running:

| Data category | Expiry calculation | Cleanup outcome |
| --- | --- | --- |
| Raw approved upload | `MediaJob.expires_at`, set when the upload is accepted from `PPE_RAW_MEDIA_RETENTION_HOURS` | Deletes the private object and replaces the stored database reference with an opaque expired marker. |
| Face-blurred evidence | `EvidenceSnapshot.expires_at`, set from the active zone-policy `evidence_retention_hours` value (24–72 hours) | Deletes the private evidence object and marks the snapshot deleted. The API blocks expired evidence even if physical deletion needs a retry. |
| Frame observation summaries | `FrameObservation.expires_at`, set when processing persists a summary from `PPE_FRAME_OBSERVATION_RETENTION_HOURS` | Deletes only expired aggregate frame summaries; non-expired summaries are untouched. |
| Aggregate metric rollups | Governance-controlled | Not deleted by this task. |
| Audit records | Governance/security-controlled | Not deleted by this task. |

Frame summaries contain only job-scoped counts, frame index, and confidence ranges. They contain no worker names, biometric information, face data, persistent person IDs, or profiles.

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
- `retention.frame_summaries_deleted`; and
- `retention.executed`.

Failed category or object cleanup adds `retention.failed`. A partial run is recorded as `retention.completed_with_errors`; when all cleanup work fails, the aggregate run is recorded as `retention.failed` with a `failed` task status. Audit details include safe aggregate counts or high-level failure categories only—not raw paths, storage keys, public URLs, media contents, biometrics, worker identities, or HR data.

Authorized administrators and privacy/governance reviewers may inspect restricted records using `GET /api/v1/audit-events`.

## Failure recovery

1. Identify the high-level failed category from the task result (`media_deletion`, `evidence_deletion`, or `frame_summary_deletion`).
2. Inspect the aggregate task result and corresponding restricted audit records.
3. Confirm the relevant private storage or database service is available without downloading, viewing, copying, or exposing unnecessary raw media or evidence.
4. Allow the next scheduled execution to retry. Failed raw-media and evidence records retain their original opaque key so they remain eligible; expired evidence remains unavailable through the API.
5. Escalate persistent failures through the approved privacy, security, and platform operations process. Pause uploads if required by approved retention governance.
6. Never make private media public, create direct storage links, restore expired evidence for convenience, or add identity-level metadata to diagnose cleanup.

## POC limitations

The default adapter uses local private filesystem storage and does not provide production object-storage lifecycle administration. Before any live employee/CCTV deployment, validate retention behavior, access controls, applicable legal requirements, DPIA/privacy impact assessment obligations, model performance, managed secret handling, production storage, monitoring, and incident response controls.
