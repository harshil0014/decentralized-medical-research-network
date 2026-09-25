import json
import os
import tempfile
from pathlib import Path

from fastapi import HTTPException


with tempfile.TemporaryDirectory() as temporary:
    root = Path(temporary)
    root.chmod(0o700)
    os.environ["MEDICAL_REGISTRY_AUTH_DIR"] = str(root)
    hospital_token = "a" * 64
    researcher_token = "b" * 64
    (root / "hospital_api.token").write_text(hospital_token)
    (root / "researcher.token").write_text(researcher_token)
    for path in root.iterdir():
        path.chmod(0o600)
    registry = root / "researchers.json"
    record = {
        "id": "researcher-a",
        "walletAddress": "0x0000000000000000000000000000000000000001",
        "tokenFile": "researcher.token",
        "enabled": True,
    }

    def save() -> None:
        registry.write_text(json.dumps({"schemaVersion": 2, "researchers": [record]}))
        registry.chmod(0o600)

    save()
    from backend.api_auth import authenticated_identity

    assert authenticated_identity(f"Bearer {researcher_token}").wallet_address.lower() == record["walletAddress"].lower()
    record["enabled"] = False
    save()
    try:
        authenticated_identity(f"Bearer {researcher_token}")
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("Disabled researcher still authenticated")

    record["enabled"] = True
    save()
    (root / "researcher.token").unlink()
    try:
        authenticated_identity(f"Bearer {researcher_token}")
    except HTTPException as exc:
        assert exc.status_code == 401
    else:
        raise AssertionError("Removed token still authenticated")

print("RESEARCHER CREDENTIAL LIFECYCLE: PASS")
