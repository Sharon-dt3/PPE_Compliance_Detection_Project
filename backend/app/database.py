"""Database setup, migrations, and POC configuration seeding."""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.migrations import apply_migrations
from app.models import Base, CameraSource, Zone, ZonePolicy

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
