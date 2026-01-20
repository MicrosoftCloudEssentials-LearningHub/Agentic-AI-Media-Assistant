"""Smoke-test for 429 retry logic.

This does NOT call Azure.
It monkeypatches requests.post to return 429 once, then 200, and asserts we retry.

Run:
  python scripts/smoke_retry_429.py
"""

import os
import sys

# Ensure we can import from src/
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_ROOT = os.path.join(REPO_ROOT, "src")
if SRC_ROOT not in sys.path:
    sys.path.insert(0, SRC_ROOT)

from services.direct_model_service import DirectModelService  # noqa: E402
import services.direct_model_service as dms  # noqa: E402


class _FakeResponse:
    def __init__(self, status_code: int, json_obj=None, text: str = "", headers=None):
        self.status_code = status_code
        self._json_obj = json_obj
        self.text = text
        self.headers = headers or {"content-type": "application/json"}

    def json(self):
        if self._json_obj is None:
            raise ValueError("No JSON")
        return self._json_obj


def main() -> int:
    # Make retries fast and deterministic
    os.environ["AZURE_OPENAI_IMAGES_MAX_RETRIES"] = "3"
    os.environ["AZURE_OPENAI_IMAGES_RETRY_BASE_SECONDS"] = "0.01"
    os.environ["AZURE_OPENAI_IMAGES_RETRY_MAX_SECONDS"] = "0.02"
    os.environ["AZURE_OPENAI_IMAGES_CONCURRENCY_LIMIT"] = "2"

    svc = DirectModelService()

    # Avoid real token acquisition
    svc._get_access_token = lambda: "fake-token"  # type: ignore[assignment]

    calls = {"count": 0, "urls": []}

    def fake_post(url, headers=None, json=None, timeout=None):
        calls["count"] += 1
        calls["urls"].append(url)
        if calls["count"] == 1:
            # First call rate-limited; immediate retry allowed
            return _FakeResponse(
                429,
                json_obj={"error": {"code": "RateLimitReached", "message": "too many"}},
                text='{"error":{"code":"RateLimitReached","message":"too many"}}',
                headers={"content-type": "application/json", "Retry-After": "0"},
            )
        return _FakeResponse(
            200,
            json_obj={"data": [{"url": "https://example.invalid/image.png"}]},
            text='{"data":[{"url":"https://example.invalid/image.png"}]}',
            headers={"content-type": "application/json"},
        )

    # Monkeypatch requests.post used inside the module
    dms.requests.post = fake_post  # type: ignore[assignment]

    # Use a dummy endpoint; URL correctness doesn't matter for this smoke test
    svc.flux2_inference = "https://unit.test"

    result = svc.generate_image_flux2("an apple", size="1024x1024")

    if result.get("status") != "success":
        print("FAIL: expected success, got:", result)
        return 2

    if calls["count"] < 2:
        print("FAIL: expected >=2 POST attempts, got", calls["count"])
        return 3

    print("OK: retried after 429 and succeeded")
    print("POST attempts:", calls["count"])
    print("First URL:", calls["urls"][0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
