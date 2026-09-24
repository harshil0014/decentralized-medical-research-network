from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from backend.secure_temp import require_staging_capacity


with patch("backend.secure_temp.os.statvfs", return_value=SimpleNamespace(f_bavail=100, f_frsize=1024)):
    require_staging_capacity(50 * 1024, root=Path("/unused"))
    try:
        require_staging_capacity(50 * 1024 + 1, root=Path("/unused"))
    except ValueError as exc:
        assert "RAM-backed staging" in str(exc)
    else:
        raise AssertionError("Insufficient tmpfs capacity was accepted")

print("RAM-BACKED STAGING CAPACITY: PASS")
