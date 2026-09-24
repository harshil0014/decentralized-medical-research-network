from pathlib import Path
import fcntl
import hashlib
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
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "connect-src 'self'; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "form-action 'self'"
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


def require_job_lock(job_id: str):
    """Serialize one HE job without blocking unrelated Hospital mutations."""
    lock_root = RUNTIME_ROOT / "job-locks"
    lock_root.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock_name = hashlib.sha256(job_id.encode("utf-8")).hexdigest() + ".lock"
    path = lock_root / lock_name
    with open(path, "a+") as lock_file:
        os.chmod(path, 0o600)
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
