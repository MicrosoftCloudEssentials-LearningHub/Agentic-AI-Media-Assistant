"""
Combined ASGI entrypoint that serves the media assistant app and the A2A
automation endpoints within the same container. This removes the need for
Terraform local-exec automation steps and keeps everything containerized.
"""
import os
from fastapi import FastAPI
from main import app as media_app
from a2a.automated_main import AutomatedA2ASystem

# Build the A2A automation app once so its lifespan hooks start inside the
# container process instead of via Terraform PowerShell scripts.
automation = AutomatedA2ASystem(
    host=os.getenv("A2A_HOST", "0.0.0.0"),
    port=int(os.getenv("A2A_PORT", "8000")),
)
a2a_app = automation.create_app()

# Mount the A2A API under /a2a while keeping the original media app routes at /
media_app.mount("/a2a", a2a_app)

# Export the combined ASGI application for uvicorn/gunicorn
app: FastAPI = media_app


@app.get("/healthz")
async def healthz():
    """Basic health for the combined server (keeps original /health intact)."""
    return {"status": "healthy", "service": "Zava Media AI Assistant + A2A"}
