"""Private media storage boundary for local POC operation and future object storage replacement."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from app.config import settings


class PrivateMediaStorage:
    """Store uploads outside public paths without leaking filesystem references to API callers."""

    def __init__(self, root_directory: str | None = None) -> None:
        """Create a storage adapter rooted at a private application directory."""
        self._root = Path(root_directory or settings.private_media_directory)
        self._root.mkdir(parents=True, exist_ok=True)

    def save(self, content: bytes, original_filename: str) -> str:
        """Persist a file privately and return an opaque storage key."""
        suffix = Path(original_filename).suffix.lower()
        storage_key = f"{uuid4()}{suffix}"
        (self._root / storage_key).write_bytes(content)
        return storage_key

    def delete(self, storage_key: str) -> None:
        """Delete an opaque storage key without raising when it was already removed."""
        (self._root / storage_key).unlink(missing_ok=True)
