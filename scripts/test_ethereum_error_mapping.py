"""Known contract reverts become bounded, sanitized API errors."""

from backend.ethereum_ledger import _http_error

for reason, status in (
    ("dataset missing", 404), ("request missing", 404),
    ("HE job missing", 404), ("request already terminal", 409),
    ("access no longer active", 403), ("invalid public dataType", 400),
):
    marker = "PRIVATE-RPC-DIAGNOSTIC"
    error = _http_error("test", RuntimeError(f"execution reverted: {reason}; {marker}"))
    assert error.status_code == status
    assert marker not in str(error.detail)

infrastructure = _http_error("test", RuntimeError("PRIVATE-RPC-DIAGNOSTIC timeout"))
assert infrastructure.status_code == 503
assert "PRIVATE-RPC-DIAGNOSTIC" not in str(infrastructure.detail)
print("ETHEREUM API REVERT/INFRASTRUCTURE ERROR MAPPING: PASS")
