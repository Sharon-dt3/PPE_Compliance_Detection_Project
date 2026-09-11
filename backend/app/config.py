"""Runtime configuration for the PPE Compliance Detection POC."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Load non-secret operational configuration from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_prefix="PPE_", extra="ignore")

    app_name: str = "PPE Compliance Detection POC"
    allowed_origins: str = "http://localhost:5173"
    max_upload_bytes: int = 104_857_600
    max_video_duration_seconds: int = 300
    max_frame_width: int = 3840
    max_frame_height: int = 2160
    max_video_fps: float = 60.0
    max_concurrent_jobs: int = 5
    evidence_retention_hours: int = 48
    raw_media_retention_hours: int = 1
    frame_observation_retention_hours: int = 24
    database_url: str = "sqlite:///./data/ppe_compliance.db"
    redis_url: str = "redis://localhost:6379/0"
    private_media_directory: str = "./data/private-media"
    private_evidence_directory: str = "./data/private-evidence"
    max_evidence_image_width: int = 1920
    max_evidence_image_height: int = 1080

    auth_mode: str = "demo"
    supabase_url: str = ""
    supabase_jwks_url: str = ""
    supabase_jwt_audience: str = "authenticated"
    supabase_jwt_algorithms: str = "RS256,ES256"

    detection_provider: str = "demo"
    demo_mode: bool = True
    hf_model_repository: str = "melihuzunoglu/ppe-detection"
    hf_model_filename: str = "best.pt"
    local_model_path: str = ""
    detection_confidence_threshold: float = 0.25

    face_detector_prototxt_path: str = ""
    face_detector_model_path: str = ""
    face_detector_confidence: float = 0.5
    face_blur_padding_ratio: float = 0.15
    face_blur_kernel_size: int = 31

    # Worker timeout and retry policy (Technology Decision 1's required configuration item).
    # soft_time_limit raises a catchable SoftTimeLimitExceeded inside process_media_job so
    # the job can still be marked FAILED safely; time_limit is a hard backstop that force-
    # kills the worker process if even that cleanup hangs (documented in OPERATIONS.md as a
    # known edge case: a hard-killed worker leaves the job stuck "processing" until an
    # administrator investigates, since no Python code runs to update its status).
    media_job_soft_time_limit_seconds: int = 240
    media_job_time_limit_seconds: int = 300

    def resolve_jwks_url(self) -> str:
        """Return the configured or provider-derived JWKS endpoint, or an empty string."""
        if self.supabase_jwks_url:
            return self.supabase_jwks_url
        if self.supabase_url:
            return f"{self.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json"
        return ""


settings = Settings()
