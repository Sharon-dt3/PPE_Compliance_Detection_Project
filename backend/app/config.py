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
    evidence_retention_hours: int = 48
    raw_media_retention_hours: int = 1
    frame_observation_retention_hours: int = 24
    database_url: str = "sqlite:///./data/ppe_compliance.db"
    redis_url: str = "redis://localhost:6379/0"
    private_media_directory: str = "./data/private-media"
    private_evidence_directory: str = "./data/private-evidence"

    auth_mode: str = "demo"
    supabase_jwt_secret: str = ""
    supabase_jwt_audience: str = "authenticated"

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


settings = Settings()
