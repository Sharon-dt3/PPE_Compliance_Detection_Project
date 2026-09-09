# PPE Compliance Detection POC

A safety-first, non-identifying proof of concept for manual CCTV still/video processing. It demonstrates **detect → decide → alert → report** for versioned, zone-specific PPE requirements.

> This is safety decision support—not employee monitoring, facial recognition, worker tracking, or automated enforcement.

## Current implementation

- Role-aware React and TypeScript POC workflows for private JPEG, PNG, MP4, and MOV submission, alert review, aggregate reporting, configuration, model evaluation, and restricted audit review.
- FastAPI API with OpenAPI routes at `/docs`.
- JWKS-verified Supabase authentication with server-side role assignment (`/api/v1/users`, `/api/v1/me`); a local demo role selector remains for zero-setup development.
- Durable zones, policy versions, sources, media jobs, alerts, private evidence metadata, and aggregate metrics.
- SQLite default for zero-cost local development; configurable PostgreSQL URL for shared environments.
- Celery/Redis asynchronous processing boundary.
- Private raw-media and evidence storage adapters.
- Configurable POC policies for vehicle yard, sorting hall, and visitor walkway.
- Explicit detector boundary:
  - deterministic demo mode for workflow testing;
  - configured Hugging Face/Ultralytics PPE model for real POC inference.
- Mandatory fail-closed OpenCV DNN ResNet SSD face detection and Gaussian blurring before evidence becomes available.
- Role-gated evidence API that serves only current, face-blurred snapshots.
- Scheduled, independently failure-tolerant, auditable retention task for raw media, face-blurred evidence, and frame summaries with explicit configuration-derived expiry timestamps.
- JSON-safe Celery retention monitoring results with status, aggregate deletion counts, and high-level failure categories only.

## Required configuration for real processing

Real inference is deliberately disabled by default. Configure the platform-managed variables below before setting `PPE_DEMO_MODE=false`:

- `PPE_DETECTION_PROVIDER=ultralytics`
- `PPE_HF_MODEL_REPOSITORY` and `PPE_HF_MODEL_FILENAME`, or `PPE_LOCAL_MODEL_PATH`
- `PPE_FACE_DETECTOR_PROTOTXT_PATH`
- `PPE_FACE_DETECTOR_MODEL_PATH`

The configured face assets must be the approved OpenCV DNN ResNet SSD Caffe files:

- `deploy.prototxt`
- `res10_300x300_ssd_iter_140000_fp16.caffemodel`

If either asset is absent, unreadable, or errors during processing, the alert remains available as non-identifying metadata but **no evidence is stored or displayed**.

## Authentication

Local development defaults to `PPE_AUTH_MODE=demo` on the backend and `VITE_AUTH_MODE=demo` on the
frontend: the UI shows a labeled, non-production role selector and the API trusts an `X-Demo-Role`
header. This must never be enabled outside local development.

For a shared deployment, set `PPE_AUTH_MODE=supabase` and `VITE_AUTH_MODE=supabase`. The backend
verifies every bearer token's signature against the identity provider's published JWKS endpoint
(`PPE_SUPABASE_URL`, or an explicit `PPE_SUPABASE_JWKS_URL`) — never a shared secret, so the backend
only ever holds public key material. A verified token still grants nothing on its own: the caller's
role is looked up server-side from the `application_users` table, never trusted from a client-supplied
JWT claim. An administrator must create that row first, through `POST /api/v1/users`, using the new
identity's provider subject (JWT `sub`); until then, a real sign-in resolves to `403`.

## Run locally

Create environment values from `backend/.env.example`, then start the API:

```bash
cd backend
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Run the worker after Redis is available:

```bash
cd backend
. .venv/bin/activate
celery -A app.worker.celery_app worker --loglevel=INFO
```

For hourly retention cleanup in local or deployed environments, also run Celery beat:

```bash
cd backend
. .venv/bin/activate
celery -A app.worker.celery_app beat --loglevel=INFO
```

Run the frontend in a separate terminal (create environment values from `frontend/.env.example` for a real Supabase sign-in; the defaults need no configuration):

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`; API documentation is available at `http://localhost:8000/docs`.

## POC safety boundaries

- Live RTSP/VMS integration is not enabled.
- No route trusts a client-supplied role claim; every role is resolved server-side against the application's own role-assignment table, and only an administrator can create or change one.
- Real PPE model outputs are POC decision-support signals, never automated enforcement.
- Missing PPE is not inferred from a weak absence signal; ambiguous results remain `unknown`.
- The simple initial provider produces aggregate POC results and must be benchmarked before any pilot.
- No worker names, persistent person identifiers, face embeddings, or raw evidence URLs are exposed.
- Evidence expires automatically according to configured retention settings.
- Frame-summary observations are short-lived and deleted only after their explicit configured expiry; they never contain persistent person IDs, worker identities, or biometric data.
- Retention outcomes and deletion failures are recorded as restricted audit events; expired evidence remains inaccessible even when physical deletion must be retried.
- This is not production-ready: managed storage, production identity controls, DPIA/legal validation, and deployment controls require validation before live CCTV use.

## Next increment

1. Benchmark the configured PPE model against approved, representative POC datasets before any pilot.
2. Validate policy thresholds, alert-review workflows, privacy controls, and retention operations through a governed POC evaluation.
3. Replace private local storage with managed object storage and configure PostgreSQL/Redis for any shared deployment.
4. Complete DPIA/legal review, production identity controls, monitoring, incident response, and deployment hardening before considering live CCTV use.

## Documentation

- [Implementation plan](kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-poc-implementation-plan.md)
- [Project blueprint](kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-project-blueprint.md)
- [Technology decisions](kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-technology-decisions.md)
- [Model evaluation and licensing trail](kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-model-evaluation.md)
- [Operations guide](backend/OPERATIONS.md)
