"""Pytest bootstrap for importing the backend package from the repository checkout."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

# Point any test that touches the real app at isolated, disposable storage instead of the
# developer's local ./data directory. Must run before the first `from app... import`, since
# Settings() and the SQLAlchemy engine are both bound once at first import.
_TEST_STATE_ROOT = Path(tempfile.gettempdir()) / "ppe-compliance-pytest"
_TEST_STATE_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("PPE_DATABASE_URL", f"sqlite:///{_TEST_STATE_ROOT / 'test.db'}")
os.environ.setdefault("PPE_PRIVATE_MEDIA_DIRECTORY", str(_TEST_STATE_ROOT / "private-media"))
os.environ.setdefault("PPE_PRIVATE_EVIDENCE_DIRECTORY", str(_TEST_STATE_ROOT / "private-evidence"))
