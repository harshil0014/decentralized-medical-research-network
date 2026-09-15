#!/usr/bin/env bash
set -euo pipefail

if [ ! -f backend/app.py ] || [ ! -f backend/runtime_security.py ]; then
  echo "Run this from the project root."
  exit 1
fi

cp backend/app.py /tmp/app.py.before_frontend
cp backend/runtime_security.py /tmp/runtime_security.py.before_frontend

python - <<'PY'
from pathlib import Path

# Patch app.py
p = Path("backend/app.py")
text = p.read_text()

import_line = "from backend.frontend_ui import router as frontend_router\n"
if import_line not in text:
    marker = "from backend.runtime_security import ("
    pos = text.index(marker)
    text = text[:pos] + import_line + text[pos:]

include = "app.include_router(frontend_router)"
if include not in text:
    marker = '''app.add_middleware(
    SecurityHeadersMiddleware
)'''
    if marker not in text:
        raise SystemExit("SecurityHeadersMiddleware block not found")
    text = text.replace(
        marker,
        marker + "\n\n" + include,
        1,
    )

p.write_text(text)

# Relax CSP only enough for same-origin frontend assets/API.
p = Path("backend/runtime_security.py")
text = p.read_text()
old = '''        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; "
            "frame-ancestors 'none'; "
            "base-uri 'none'"
        )'''
new = '''        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "connect-src 'self'; "
            "img-src 'self' data:; "
            "frame-ancestors 'none'; "
            "base-uri 'none'; "
            "form-action 'self'"
        )'''

if old in text:
    text = text.replace(old, new, 1)
elif new not in text:
    raise SystemExit("CSP block not found")

p.write_text(text)
print("FRONTEND BACKEND PATCH: PASS")
PY

source backend/.venv/bin/activate
python -m py_compile \
  backend/app.py \
  backend/frontend_ui.py \
  backend/runtime_security.py

echo "PYTHON COMPILE: PASS"

./scripts/start_secure_api.sh

curl -fsS \
  --cacert /root/.medical-registry/tls/ca.crt \
  https://localhost:8443/ \
  | grep -q "Medical Research Network"

echo "FRONTEND ONLINE: PASS"
echo
echo "Open: https://localhost:8443/"
echo
git status --short
