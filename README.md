# PPE Compliance Detection POC

A safety-first, non-identifying proof of concept for manual CCTV still/video processing. It demonstrates **detect → decide → alert → report** for versioned, zone-specific PPE requirements.

> This is safety decision support—not employee monitoring, facial recognition, worker tracking, or automated enforcement.

## Current implementation

- React and TypeScript dashboard for private JPEG, PNG, MP4, and MOV submission.
- FastAPI API with OpenAPI routes at `/docs`.
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
- Scheduled retention task for raw media and evidence.

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

Run the frontend in a separate terminal:

```bash
cd frontend
npm install
npm run dev
```

Open `http://localhost:5173`; API documentation is available at `http://localhost:8000/docs`.

## POC safety boundaries

- Live RTSP/VMS integration is not enabled.
- Real PPE model outputs are POC decision-support signals, never automated enforcement.
- Missing PPE is not inferred from a weak absence signal; ambiguous results remain `unknown`.
- The simple initial provider produces aggregate POC results and must be benchmarked before any pilot.
- No worker names, persistent person identifiers, face embeddings, or raw evidence URLs are exposed.
- Evidence expires automatically according to configured retention settings.

## Next increment

1. Add model-result person/PPE association, multi-frame persistence, and cross-job policy-aware deduplication.
2. Add database migrations, test coverage, audit events, durable model metadata, and controlled retry/cancellation.
3. Add Apache ECharts trend and breakdown visualizations with aggregate reporting filters.
4. Replace private local storage with managed object storage and configure PostgreSQL/Redis for shared deployment.

## Documentation

- [Implementation plan](kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-poc-implementation-plan.md)
- [Project blueprint](kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-project-blueprint.md)
- [Technology decisions](kavia-docs/CodeWiki/Forward-looking/Specs/DetailedDesigns/ppe-compliance-technology-decisions.md)
