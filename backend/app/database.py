"""Database setup, migrations, and POC configuration seeding."""

import json

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.migrations import apply_migrations
from app.models import Base, CameraSource, ModelEvaluation, Zone, ZonePolicy

_engine_options: dict[str, object] = {"future": True}
if settings.database_url.startswith("sqlite"):
    _engine_options["connect_args"] = {"check_same_thread": False}

engine = create_engine(settings.database_url, **_engine_options)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_session():
    """Yield one database session per request and always close it afterwards."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def initialise_database() -> None:
    """Create missing tables, apply additive migrations, and seed fixed POC examples."""
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        apply_migrations(connection)

    with SessionLocal() as session:
        _seed_zones_sources_and_policies(session)
        _seed_model_evaluations(session)


def _seed_zones_sources_and_policies(session: Session) -> None:
    """Seed the fixed example zones, policies, and sources once, on a first run only."""
    if session.scalar(select(Zone.id).limit(1)):
        return

    yard = Zone(name="Vehicle yard", description="Vehicle and heavy-plant operating area.")
    sorting = Zone(name="Sorting hall", description="Indoor materials sorting area.")
    walkway = Zone(name="Visitor walkway", description="Non-operational route with no POC PPE alert rule.")
    session.add_all([yard, sorting, walkway])
    session.flush()

    session.add_all(
        [
            ZonePolicy(zone_id=yard.id, version=1, helmet_required=True, vest_required=True),
            ZonePolicy(zone_id=sorting.id, version=1, helmet_required=False, vest_required=True),
            ZonePolicy(zone_id=walkway.id, version=1, helmet_required=False, vest_required=False),
        ]
    )
    session.add_all(
        [
            CameraSource(name="Yard gate camera", zone_id=yard.id),
            CameraSource(name="Sorting line camera", zone_id=sorting.id),
            CameraSource(name="Visitor entrance camera", zone_id=walkway.id),
        ]
    )
    session.commit()


def _seed_model_evaluations(session: Session) -> None:
    """Seed the real, held-out-set benchmark results recorded for Phase 5.

    Ensures a fresh deployment never silently treats a candidate model as production-cleared:
    the recorded benchmark and its poc_only approval state are present from first boot, not
    only after someone remembers to call the API. See the full methodology, licence trail, and
    reading notes in ppe-compliance-model-evaluation.md.
    """
    if session.scalar(select(ModelEvaluation.id).limit(1)):
        return

    dataset_reference = (
        "keremberke/hard-hat-detection (Hugging Face Datasets, Roboflow export, COCO format), "
        "test split, n=300 random sample (seed 20260909) of 1974 eligible images; "
        "ground truth covers helmet/no_helmet only, IoU>=0.5, conf>=0.25"
    )
    session.add_all(
        [
            ModelEvaluation(
                provider="huggingface",
                model_name="melihuzunoglu/ppe-detection",
                model_version="best.pt (Ultralytics YOLOv11)",
                dataset_reference=dataset_reference,
                licence_status="agpl-3.0 (explicit in model card); training-data licence unverified (declared only as 'custom')",
                approval_state="poc_only",
                class_metrics_json=json.dumps(
                    {
                        "helmet": {"tp": 271, "fp": 221, "fn": 401, "precision": 0.551, "recall": 0.403, "f1": 0.466},
                        "no_helmet": {"tp": 78, "fp": 177, "fn": 70, "precision": 0.306, "recall": 0.527, "f1": 0.387},
                    },
                    sort_keys=True,
                ),
                latency_ms=70.1,
                effective_sampling_rate=None,
                limitations=(
                    "Configured POC default. Benchmark covers helmet/no_helmet only (dataset has no vest/"
                    "gloves/glasses ground truth). Both classes under 0.5 F1 on this held-out, cross-dataset "
                    "sample; mean false-positive confidence (0.572-0.635) sits close to mean true-positive "
                    "confidence, so a confidence floor alone cannot substitute for evaluation against "
                    "representative footage. No Renewi camera-angle/height data represented. Requires "
                    "Phase-2 fine-tuning on real site footage before any pilot use."
                ),
            ),
            ModelEvaluation(
                provider="huggingface",
                model_name="Hansung-Cho/yolov8-ppe-detection",
                model_version="best.pt (Ultralytics YOLOv8n)",
                dataset_reference=dataset_reference,
                licence_status="Disputed: card declares mit, but Ultralytics AGPL-3.0 may extend to trained weights (unconfirmed)",
                approval_state="poc_only",
                class_metrics_json=json.dumps(
                    {
                        "helmet": {"tp": 379, "fp": 271, "fn": 293, "precision": 0.583, "recall": 0.564, "f1": 0.573},
                        "no_helmet": {"tp": 9, "fp": 198, "fn": 139, "precision": 0.043, "recall": 0.061, "f1": 0.051},
                    },
                    sort_keys=True,
                ),
                latency_ms=69.5,
                effective_sampling_rate=None,
                limitations=(
                    "Cross-check candidate, rejected. Near-unusable no_helmet detection (F1 0.051, precision "
                    "0.043) despite a better helmet F1 (0.573) than the primary candidate - this application "
                    "treats no_helmet as the authoritative violation signal (explicit-negative precedence), "
                    "so this failure mode outweighs the helmet-class improvement. Training-data licence also "
                    "unverified ('public PPE/construction datasets, licence follows the data provider'). Same "
                    "dataset-coverage and domain-gap limitations as the primary candidate apply."
                ),
            ),
        ]
    )
    session.commit()
