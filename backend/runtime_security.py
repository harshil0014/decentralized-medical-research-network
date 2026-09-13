from pathlib import Path
import fcntl
import os

from starlette.middleware.base import BaseHTTPMiddleware


RUNTIME_ROOT = Path(
    os.environ.get(
        "MEDICAL_REGISTRY_RUNTIME_DIR",
        "/root/.medical-registry",
    )
)

MUTATION_LOCK = RUNTIME_ROOT / "api-mutation.lock"


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)

        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        )

        return response


def require_mutation_lock():
    RUNTIME_ROOT.mkdir(
        parents=True,
        exist_ok=True,
        mode=0o700,
    )

    lock_file = open(
        MUTATION_LOCK,
        "a+",
    )

    os.chmod(
        MUTATION_LOCK,
        0o600,
    )

    try:
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_EX,
        )

        yield

    finally:
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_UN,
        )

        lock_file.close()
