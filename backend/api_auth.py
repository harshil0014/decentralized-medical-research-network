from pathlib import Path
import hmac
import os

from fastapi import (
    Depends,
    Header,
    HTTPException,
)


AUTH_ROOT = Path(
    os.environ.get(
        "MEDICAL_REGISTRY_AUTH_DIR",
        "/root/.medical-registry",
    )
)

HOSPITAL_TOKEN_PATH = (
    AUTH_ROOT / "hospital_api.token"
)

RESEARCHER_TOKEN_PATH = (
    AUTH_ROOT / "researcher_api.token"
)


def _load_token(
    path: Path,
) -> str:
    try:
        token = path.read_text().strip()
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"API token file is missing: {path}"
        ) from exc

    if (
        len(token) != 64
        or any(
            ch not in "0123456789abcdef"
            for ch in token
        )
    ):
        raise RuntimeError(
            f"API token file is malformed: {path}"
        )

    return token


# Loaded when the API process starts.
# Changing a token file requires an API restart.
_HOSPITAL_TOKEN = _load_token(
    HOSPITAL_TOKEN_PATH
)

_RESEARCHER_TOKEN = _load_token(
    RESEARCHER_TOKEN_PATH
)


def authenticated_role(
    authorization: str | None = Header(
        default=None,
        alias="Authorization",
    ),
) -> str:

    if not authorization:
        raise HTTPException(
            status_code=401,
            detail="Bearer authentication required",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    scheme, separator, token = (
        authorization.partition(" ")
    )

    if (
        not separator
        or scheme.lower() != "bearer"
        or not token.strip()
    ):
        raise HTTPException(
            status_code=401,
            detail="Invalid Authorization header",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    token = token.strip()

    # Perform both comparisons instead of returning
    # immediately after the first comparison.
    hospital_match = hmac.compare_digest(
        token,
        _HOSPITAL_TOKEN,
    )

    researcher_match = hmac.compare_digest(
        token,
        _RESEARCHER_TOKEN,
    )

    if hospital_match:
        return "hospital"

    if researcher_match:
        return "researcher"

    raise HTTPException(
        status_code=401,
        detail="Invalid bearer token",
        headers={
            "WWW-Authenticate": "Bearer",
        },
    )


def require_authenticated(
    role: str = Depends(
        authenticated_role
    ),
) -> str:
    return role


def require_hospital(
    role: str = Depends(
        authenticated_role
    ),
) -> str:

    if role != "hospital":
        raise HTTPException(
            status_code=403,
            detail="Hospital role required",
        )

    return role


def require_researcher(
    role: str = Depends(
        authenticated_role
    ),
) -> str:

    if role != "researcher":
        raise HTTPException(
            status_code=403,
            detail="Researcher role required",
        )

    return role
