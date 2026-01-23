import os
import logging
import json
import asyncio
import traceback
import re
from typing import Any, Dict, Optional
from datetime import datetime
import uuid
from pathlib import Path
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import orjson

# OpenTelemetry imports
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter

from services.hybrid_agent_service import HybridAgentService
from services.direct_model_service import DirectModelService
from services.env_utils import get_env, get_env_with_source, is_running_in_azure
from services.file_service import FileService
from services.document_extractor import DocumentExtractor

# Load environment variables
if not is_running_in_azure():
    try:
        repo_root = Path(__file__).resolve().parent.parent
        load_dotenv(dotenv_path=repo_root / ".env")
    except Exception:
        load_dotenv()

# Configure comprehensive logging
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for detailed logging
    format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
)
logger = logging.getLogger(__name__)

# Configure Azure Monitor if connection string is available
appinsights_connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
if appinsights_connection_string:
    try:
        logger.info("Configuring Azure Monitor OpenTelemetry...")
        # Set up tracer provider
        trace.set_tracer_provider(TracerProvider())
        
        # Create Azure Monitor exporter
        exporter = AzureMonitorTraceExporter(connection_string=appinsights_connection_string)
        span_processor = BatchSpanProcessor(exporter)
        trace.get_tracer_provider().add_span_processor(span_processor)
        
        logger.info("Azure Monitor OpenTelemetry configured successfully")
    except Exception as e:
        logger.error(f"Failed to configure Azure Monitor: {e}", exc_info=True)
else:
    logger.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set - Azure Monitor disabled")

# Get tracer
tracer = trace.get_tracer(__name__)

logger.info("Starting Zava Media AI Assistant...")
logger.info(f"Current working directory: {os.getcwd()}")
logger.info(f"Files in current directory: {os.listdir('.')}")

# Log all environment variables (excluding sensitive values)
logger.debug("Environment variables:")
for key in sorted(os.environ.keys()):
    if any(secret in key.upper() for secret in ["KEY", "SECRET", "PASSWORD", "TOKEN"]):
        logger.debug(f"  {key}=***REDACTED***")
    else:
        logger.debug(f"  {key}={os.environ[key]}")

# Initialize FastAPI app
app = FastAPI(title="Zava Media AI Assistant")

# Add CORS middleware to allow frontend communication
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for Azure App Service
    allow_credentials=False,  # Changed to False for wildcard origins
    allow_methods=["*"],
    allow_headers=["*"],
)

# Instrument FastAPI with OpenTelemetry
FastAPIInstrumentor.instrument_app(app)
logger.info("FastAPI instrumented with OpenTelemetry")

# Health check endpoint (required for App Service)
@app.get("/health")
async def health_check():
    with tracer.start_as_current_span("health_check"):
        logger.debug("Health check requested")
        return {"status": "healthy", "service": "Zava Media AI Assistant", "timestamp": datetime.utcnow().isoformat()}

# Mount static files only if directory exists and has content
static_dir = "app/static"
Path(static_dir).mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")
logger.info(f"Static files mounted from {static_dir}")

# Mount templates
templates = Jinja2Templates(directory="app/templates")

orchestrator_error: Optional[str] = None
direct_model_service: Optional[DirectModelService] = None
file_service = FileService()
document_extractor = DocumentExtractor()

# Concurrency limiter for direct image generation to avoid Azure rate limits.
_flux_semaphore: Optional[asyncio.Semaphore] = None


def _get_flux_semaphore() -> asyncio.Semaphore:
    global _flux_semaphore
    if _flux_semaphore is None:
        try:
            limit = int(os.getenv("AZURE_OPENAI_FLUX_MAX_CONCURRENCY", "1"))
        except Exception:
            limit = 1
        if limit <= 0:
            limit = 1
        _flux_semaphore = asyncio.Semaphore(limit)
    return _flux_semaphore

try:
    # Use Azure AI Agents - let them handle everything
    logger.info("Initializing Hybrid Agent Service (Azure AI Agents + Fallback)")
    orchestrator = HybridAgentService()
    
    if orchestrator.orchestrator_agent:
        logger.info("Agent service initialized successfully")
        logger.info(f"  - Orchestrator Agent ID: {orchestrator.orchestrator_agent.id}")
        logger.info("  - Orchestrator handles all routing with AI-powered decision making")
    else:
        logger.warning("Agent service initialized with fallback mode")
        logger.warning("  - Azure AI Agents unavailable, using local fallback responses")
        orchestrator_error = "Azure AI Agents unavailable - using fallback mode"

except Exception as e:
    orchestrator_error = str(e)
    logger.error(f"Failed to initialize orchestrator: {e}", exc_info=True)
    # Create a simple fallback app that can at least respond to health checks
    orchestrator = None

# Fast JSON serialization
def fast_json_dumps(obj):
    return orjson.dumps(obj).decode("utf-8")


def _strip_json_fences(text: str) -> str:
    """Remove common markdown code-fences around JSON."""
    if not text:
        return text
    stripped = text.strip()
    if stripped.startswith("```"):
        # Remove leading ``` or ```json
        lines = stripped.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        # Remove trailing ```
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    return stripped


def _try_parse_action_dict(text: str) -> Optional[Dict[str, Any]]:
    """Parse action JSON payloads like {"action":"call_flux",...} from plain text."""
    if not text:
        return None
    candidate = _strip_json_fences(text)
    if not candidate:
        return None
    decoder = json.JSONDecoder()

    # Fast path: whole-string JSON object
    stripped = candidate.strip()
    if stripped.startswith("{"):
        try:
            obj = json.loads(stripped)
            if isinstance(obj, dict) and obj.get("action"):
                return obj
        except Exception:
            pass

    # Robust path: find the first decodable JSON object within surrounding text.
    # This enables parsing when the agent returns an action JSON plus extra explanation.
    max_scan = min(len(candidate), 2000)
    scan_text = candidate[:max_scan]
    starts: list[int] = [i for i, ch in enumerate(scan_text) if ch == "{"]
    for start in starts[:10]:
        try:
            obj, _end = decoder.raw_decode(candidate[start:])
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("action"):
            return obj
    return None


def _try_infer_action_from_user_message(text: str) -> Optional[Dict[str, Any]]:
    """Infer a tool/action payload from plain English requests.

    Goal: make image/video generation work implicitly from the UI without requiring
    explicit action JSON or special commands.

    Heuristic is intentionally conservative to avoid accidental tool calls.
    """
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None

    lowered = raw.lower()

    # Don't double-handle explicit JSON action payloads.
    if lowered.lstrip().startswith("{") and lowered.rstrip().endswith("}"):
        return None

    # Require an explicit generation verb to reduce false positives.
    has_gen_verb = any(v in lowered for v in ["generate", "create", "make", "produce", "render"])
    if not has_gen_verb:
        return None

    # Video intent
    if any(k in lowered for k in [" video", "video ", " video.", "sora"]):
        payload: Dict[str, Any] = {"action": "call_sora", "prompt": raw}
        inferred = _infer_video_duration_seconds_from_prompt(raw)
        if inferred is not None:
            payload["duration"] = inferred
        return payload

    # Image intent (thumbnail/logo/etc.)
    image_keywords = [" image", "image ", " image.", "thumbnail", "poster", "banner", "logo", "cover"]
    if any(k in lowered for k in image_keywords):
        # If user mentions kontext/context explicitly, route to kontext model.
        if any(k in lowered for k in ["kontext", "context", "document", "pdf"]):
            return {"action": "call_flux_kontext", "prompt": raw}
        return {"action": "call_flux", "prompt": raw}

    return None


def _infer_video_duration_seconds_from_prompt(text: str) -> Optional[int]:
    """Infer an integer duration (seconds) from a free-form prompt.

    Supported examples:
      - "a 5 second video"
      - "a 5-second clip"
      - "make it 5s"
      - "for 7 seconds"
    """

    t = (text or "").strip().lower()
    if not t:
        return None

    # Prefer explicit seconds units; keep it conservative to avoid false matches.
    patterns = [
        r"\b(\d{1,3})(?:\.\d+)?\s*(?:seconds?|secs?|s)\b",
        r"\b(\d{1,3})(?:\.\d+)?\s*-\s*(?:seconds?|secs?)\b",
        r"\b(\d{1,3})(?:\.\d+)?-second\b",
    ]
    for pat in patterns:
        m = re.search(pat, t)
        if not m:
            continue
        try:
            return int(float(m.group(1)))
        except Exception:
            continue

    return None


def _normalize_video_duration_seconds(seconds: Optional[int], *, default: int = 3) -> int:
    try:
        value = int(seconds) if seconds is not None else int(default)
    except Exception:
        value = int(default)

    # Keep a reasonable guardrail; Sora/worker backends may enforce their own limits.
    if value <= 0:
        value = int(default)
    return int(max(1, min(value, 20)))


def _extract_first_media_url_or_data_uri(model_result: Dict[str, Any]) -> Optional[str]:
    """Extract a usable URL or data-uri from the DirectModelService response."""
    data_items = model_result.get("data") or []
    if not isinstance(data_items, list) or not data_items:
        return None
    first = data_items[0] or {}
    if isinstance(first, dict):
        if url := first.get("url"):
            return str(url)
        if b64_json := first.get("b64_json"):
            return f"data:image/png;base64,{b64_json}"
    return None


def _format_standard_reply(answer: str, actions: list[str], next_steps: list[str], assumptions: list[str]) -> str:
    def _bullets(items: list[str]) -> str:
        cleaned = [str(i).strip() for i in (items or []) if str(i).strip()]
        if not cleaned:
            return "- None"
        return "\n".join([f"- {i}" for i in cleaned[:3]])

    summary = (answer or "").strip()
    if not summary:
        summary = "(No summary provided.)"

    return (
        f"**{summary}**\n\n"
        "---\n\n"
        "## What I did\n\n"
        f"{_bullets(actions)}\n\n"
        "## Next steps\n\n"
        f"{_bullets(next_steps)}\n\n"
        "## Assumptions\n\n"
        f"{_bullets(assumptions)}"
    )


def _append_explain_answer_section(message: str, bullets: list[str]) -> str:
    cleaned = [str(b).strip() for b in (bullets or []) if str(b).strip()]
    if len(cleaned) != 3:
        return message
    return (
        f"{(message or '').rstrip()}\n\n"
        "Explain answer (3 bullets)\n"
        + "\n".join([f"- {b}" for b in cleaned])
    )


def _standard_format_instructions_for_model(wants_explain_answer: bool) -> str:
    base = (
        "---\n"
        "RESPONSE FORMAT (use exactly):\n\n"
        "**<One short summary paragraph>**\n\n"
        "---\n\n"
        "## What I did\n\n"
        "- <0-3 bullets; if none, write '- None'>\n\n"
        "## Next steps\n\n"
        "- <0-3 bullets; if none, write '- None'>\n\n"
        "## Assumptions\n\n"
        "- <0-3 bullets; if none, write '- None'>\n\n"
        "Rules:\n"
        "- Do not add other headings.\n"
        "- Do not use markdown code fences.\n"
        "- Do not use emojis.\n"
        "- Keep bullets short; one line each.\n"
    )

    if not wants_explain_answer:
        return base

    return (
        base
        + "\nAlso append a section titled 'Explain answer (3 bullets)' AFTER the sections above.\n"
        + "Rules for that section:\n"
        + "- This is NOT internal chain-of-thought. Do not reveal hidden reasoning steps.\n"
        + "- Output exactly 3 bullets starting with '- '. Each bullet must be <= 20 words.\n"
        + "- Do not output any content after those 3 bullets.\n"
    )


def _vision_message_for_empty_prompt() -> str:
    return "Describe what you see in this image and call out any important details." 


def _looks_like_generation_request(text: str) -> bool:
    lowered = (text or "").lower()
    has_gen_verb = any(v in lowered for v in ["generate", "create", "make", "produce", "render"])
    if not has_gen_verb:
        return False
    return any(k in lowered for k in ["image", "thumbnail", "poster", "banner", "logo", "cover", "video", "sora"])


def _looks_like_background_removal_request(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    return any(
        k in lowered
        for k in [
            "remove background",
            "remove the background",
            "transparent background",
            "make background transparent",
            "cut out",
            "cutout",
            "bg remove",
            "background removal",
        ]
    )


def _looks_like_blur_background_request(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    return any(k in lowered for k in ["blur background", "background blur", "portrait mode", "bokeh", "defocus background"]) and not _looks_like_background_removal_request(text)


def _looks_like_blur_faces_request(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    return any(k in lowered for k in ["blur face", "blur faces", "anonymize", "hide face", "privacy blur", "pixelate face", "censor face"])


def _infer_filter_name(text: str) -> Optional[str]:
    lowered = (text or "").lower()
    if not lowered.strip():
        return None
    if any(k in lowered for k in ["edge detect", "edge-detect", "edges", "canny", "outline"]):
        return "edge"
    if any(k in lowered for k in ["sharpen", "sharper"]):
        return "sharpen"
    if any(k in lowered for k in ["denoise", "reduce noise", "remove noise"]):
        return "denoise"
    if any(k in lowered for k in ["cartoon", "toon"]):
        return "cartoon"
    if any(k in lowered for k in ["posterize", "posterise", "poster"]):
        return "posterize"
    return None


def _looks_like_filter_request(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    return (_infer_filter_name(text) is not None) or any(k in lowered for k in ["apply filter", "filter this image"])


def _looks_like_thumbnail_request(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    return any(
        k in lowered
        for k in [
            "thumbnail",
            "make a thumbnail",
            "create a thumbnail",
            "youtube thumbnail",
            "avatar",
            "profile picture",
            "profile photo",
        ]
    )


def _infer_thumbnail_size(text: str) -> str:
    lowered = (text or "").lower()
    args = _infer_transform_args(text)
    w = args.get("width")
    h = args.get("height")
    try:
        if w and h:
            return f"{int(w)}x{int(h)}"
    except Exception:
        pass

    aspect = str(args.get("aspect") or "").strip().lower()

    if "youtube" in lowered or "16:9" in lowered or aspect in {"16:9", "16x9"}:
        return "1280x720"
    if "story" in lowered or "9:16" in lowered or aspect in {"9:16", "9x16"}:
        return "1080x1920"
    if "instagram" in lowered and ("post" in lowered or "square" in lowered or aspect in {"1:1", "1x1"}):
        return "1080x1080"
    if aspect in {"1:1", "1x1"}:
        return "1024x1024"
    if aspect in {"4:5", "4x5"}:
        return "1080x1350"
    return "1024x1024"


def _looks_like_enhance_request(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    return any(
        k in lowered
        for k in [
            "enhance",
            "improve quality",
            "make this look better",
            "increase clarity",
            "sharpen",
            "sharper",
            "more vibrant",
        ]
    )


def _infer_transform_args(text: str) -> Dict[str, Any]:
    raw = (text or "")
    lowered = raw.lower()
    out: Dict[str, Any] = {}

    if any(k in lowered for k in ["pad", "letterbox", "add padding"]):
        out["mode"] = "pad"
    elif any(k in lowered for k in ["crop", "center crop", "trim"]):
        out["mode"] = "crop"

    # Common aspect ratios
    if any(k in lowered for k in ["square", "1:1", "1x1"]):
        out["aspect"] = "1:1"
    else:
        import re

        m = re.search(r"(\d+)\s*[:x]\s*(\d+)", lowered)
        if m:
            a = int(m.group(1))
            b = int(m.group(2))
            if a > 0 and b > 0 and a <= 32 and b <= 32:
                out["aspect"] = f"{a}:{b}"

    # Target size WxH
    try:
        import re

        m2 = re.search(r"(\d{2,4})\s*x\s*(\d{2,4})", lowered)
        if m2:
            out["width"] = int(m2.group(1))
            out["height"] = int(m2.group(2))
    except Exception:
        pass

    return out


def _looks_like_transform_request(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    if any(k in lowered for k in ["resize", "crop", "pad", "letterbox", "aspect ratio", "16:9", "9:16", "4:5", "1:1", "square"]):
        return True
    return False


def _allowed_image_sizes_from_env() -> list[str]:
    raw = (os.getenv("AZURE_OPENAI_ALLOWED_IMAGE_SIZES") or "").strip()
    if raw:
        sizes = [s.strip() for s in raw.split(",") if s.strip()]
        if sizes:
            return sorted(set(sizes))
    return ["1024x1024", "1024x1792", "1792x1024"]


def _is_allowed_image_sizes_question(text: str) -> bool:
    lowered = (text or "").lower()
    if not lowered.strip():
        return False
    wants_sizes = any(k in lowered for k in ["size", "sizes", "resolution", "dimensions", "dimension"]) and any(
        k in lowered for k in ["image", "flux", "thumbnail", "poster", "banner", "logo", "cover"]
    )
    is_question = "?" in lowered or any(k in lowered for k in ["what", "which", "allowed", "available", "supported"])
    return bool(wants_sizes and is_question)


def _infer_prompt_clarifying_questions(
    *,
    action: str,
    prompt: str,
    action_payload: Dict[str, Any],
) -> list[str]:
    """Return up to 3 targeted questions when a media prompt is underspecified."""
    prompt_text = (prompt or "").strip()
    prompt_l = prompt_text.lower()
    questions: list[str] = []

    # Basic tokenization for "subject exists" heuristic.
    words = re.findall(r"[a-zA-Z0-9']+", prompt_l)
    stop = {
        "a",
        "an",
        "the",
        "of",
        "to",
        "and",
        "or",
        "in",
        "on",
        "with",
        "for",
        "please",
        "make",
        "create",
        "generate",
        "draw",
        "build",
        "design",
        "image",
        "picture",
        "photo",
        "thumbnail",
        "poster",
        "banner",
        "logo",
        "cover",
        "video",
        "clip",
    }
    meaningful = [w for w in words if w not in stop]
    has_subject_signal = len(meaningful) >= 2 or any(w in prompt_l for w in ["apple", "cat", "dog", "person", "people", "product", "logo", "car", "house"])

    if not has_subject_signal:
        questions.append("What’s the main subject? (e.g., ‘a green apple’, ‘a dog wearing sunglasses’, ‘company logo for X’)")

    # Style
    style_terms = [
        "photorealistic",
        "photo",
        "realistic",
        "cinematic",
        "studio",
        "vector",
        "flat",
        "minimal",
        "illustration",
        "cartoon",
        "anime",
        "watercolor",
        "oil painting",
        "3d",
        "render",
    ]
    if not any(t in prompt_l for t in style_terms):
        questions.append("What style do you want? (photorealistic, illustration, vector/flat, cinematic, etc.)")

    # Background / setting
    bg_terms = ["background", "on a", "in a", "studio", "outdoors", "plain", "solid", "transparent", "white background", "black background"]
    if not any(t in prompt_l for t in bg_terms):
        questions.append("What background/setting? (plain white, transparent, studio, outdoors, on a table, etc.)")

    # Subject-specific detail: apple color is a common ambiguity.
    if "apple" in prompt_l and not any(c in prompt_l for c in ["green", "red", "yellow", "golden", "granny smith"]):
        questions.insert(0, "What color should the apple be? (green/red/yellow)")

    # Format knobs: only ask if the user didn’t explicitly set them.
    if action in {"call_flux", "call_flux_kontext"}:
        prompt_mentions_format = any(k in prompt_l for k in ["thumbnail", "banner", "poster", "cover", "widescreen", "16:9", "9:16"])
        if prompt_mentions_format and "size" not in action_payload:
            questions.append("What image size/aspect? (1024x1024, 1792x1024, 1024x1792)")

    if action == "call_sora":
        inferred_duration = None
        if "duration" not in action_payload:
            inferred_duration = _infer_video_duration_seconds_from_prompt(action_payload.get("prompt") or user_text or "")

        if "duration" not in action_payload and inferred_duration is None:
            questions.append("How long should the video be (seconds)?")
        if "resolution" not in action_payload and not any(k in prompt_l for k in ["1080", "720", "4k", "1920x1080", "1280x720"]):
            questions.append("What resolution/aspect? (e.g., 1920x1080, 1080x1920)")

    # Keep it lightweight: max 3.
    out: list[str] = []
    for q in questions:
        if q not in out:
            out.append(q)
        if len(out) >= 3:
            break
    return out


def _should_prompt_for_media_clarifications(
    *,
    action: str,
    user_text: Optional[str],
    action_payload: Dict[str, Any],
) -> bool:
    """Only ask clarifying questions when the user explicitly wants refinement.

    This keeps first-time generations fast, and uses questions as an optional
    accuracy tool when the user asks to improve/regenerate.
    """

    if bool(action_payload.get("clarify") or action_payload.get("ask_questions")):
        return True

    text = (user_text or action_payload.get("prompt") or "").strip().lower()
    if not text:
        return False

    # Explicit refinement / dissatisfaction signals.
    triggers = [
        "improve",
        "improved",
        "make it better",
        "better",
        "not happy",
        "unhappy",
        "wrong",
        "fix",
        "refine",
        "tweak",
        "adjust",
        "change it",
        "try again",
        "regenerate",
        "redo",
        "another version",
        "variation",
        "ask questions",
        "ask me",
    ]
    return any(t in text for t in triggers)


async def _ensure_direct_model_service() -> DirectModelService:
    global direct_model_service
    if direct_model_service is None:
        direct_model_service = DirectModelService()
    return direct_model_service


async def _handle_action(action_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool-like action payload and return a websocket response dict."""
    action = (action_payload.get("action") or "").strip()
    prompt = (action_payload.get("prompt") or "").strip()
    wants_explain = bool(action_payload.get("explain", False))
    wants_explain_answer = bool(action_payload.get("explain_answer", False))
    include_oss = bool(action_payload.get("include_oss", True))

    if not action:
        return {
            "type": "error",
            "message": _format_standard_reply(
                answer="I couldn’t run a tool action because the request is missing the 'action' field.",
                actions=["Validated tool/action payload"],
                next_steps=["Send an action payload with an 'action' key (e.g., call_flux)", "Try again"],
                assumptions=["The client intended to call a tool via an action payload"],
            ),
            "diagnostics": {"action_payload": action_payload},
        }

    if action in {"call_flux", "call_flux_kontext", "call_sora"} and not prompt:
        return {
            "type": "error",
            "message": _format_standard_reply(
                answer=f"I couldn’t run '{action}' because the request is missing the 'prompt' field.",
                actions=[f"Validated payload for {action}"],
                next_steps=["Provide a non-empty prompt", "Try again"],
                assumptions=["This action requires a text prompt"],
            ),
            "diagnostics": {"action_payload": action_payload},
        }

    if action in {
        "remove_background",
        "blur_background",
        "blur_faces",
        "apply_filter",
        "transform_image",
        "generate_thumbnail",
        "enhance_image",
    } and not (action_payload.get("image_data") or action_payload.get("image")):
        return {
            "type": "error",
            "message": _format_standard_reply(
                answer="I couldn’t edit the image because the request is missing 'image_data'.",
                actions=[f"Validated payload for {action}"],
                next_steps=["Attach an image and try again"],
                assumptions=["The client intended to edit an attached image"],
            ),
            "diagnostics": {"action_payload": action_payload},
        }

    # Optional parameters
    size = (action_payload.get("size") or "1024x1024").strip()
    duration = action_payload.get("duration")
    resolution = (action_payload.get("resolution") or "1920x1080").strip()

    svc = await _ensure_direct_model_service()

    try:
        if action in {
            "remove_background",
            "blur_background",
            "blur_faces",
            "apply_filter",
            "transform_image",
            "generate_thumbnail",
            "enhance_image",
        } and not include_oss:
            return {
                "type": "error",
                "message": _format_standard_reply(
                    answer="Open-source edits are disabled (OSS toggle is off).",
                    actions=["Checked OSS toggle"],
                    next_steps=["Enable the OSS checkbox and try again"],
                    assumptions=["You only want OSS media edits when explicitly enabled"],
                ),
                "diagnostics": {"action": action, "include_oss": include_oss},
            }

        if action == "remove_background":
            image_input = action_payload.get("image_data") or action_payload.get("image")
            oss_result = await asyncio.to_thread(svc.remove_background_open_source, image_input)
            oss_ok = oss_result.get("status") == "success"
            edited = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            if not oss_ok or not edited:
                return {
                    "type": "error",
                    "message": _format_standard_reply(
                        answer="Background removal failed.",
                        actions=["Attempted open-source background removal"],
                        next_steps=["Try a different image", "Try a higher-contrast subject"],
                        assumptions=["The image data is valid"],
                    ),
                    "diagnostics": {"action": action, "open_source": oss_result},
                }

            return {
                "type": "agent_response",
                "agent": "OSS Image Edit",
                "message": _format_standard_reply(
                    answer="Removed the background and returned a transparent PNG.",
                    actions=["Ran open-source background removal"],
                    next_steps=["Download the PNG", "Try another edit request"],
                    assumptions=["The image contains a reasonably separable foreground subject"],
                ),
                "image_data": [edited],
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "baseline", "value": oss_result.get("model", "oss")},
                ],
                "explanation": (
                    "High-level explanation (not internal chain-of-thought):\n"
                    "1) I detected a background-removal edit request.\n"
                    "2) I ran a local open-source segmentation baseline.\n"
                    "3) I returned a transparent PNG."
                )
                if wants_explain
                else None,
                "diagnostics": {"action": action, "open_source": oss_result},
            }

        if action == "blur_background":
            image_input = action_payload.get("image_data") or action_payload.get("image")
            oss_result = await asyncio.to_thread(svc.blur_background_open_source, image_input)
            oss_ok = oss_result.get("status") == "success"
            edited = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            if not oss_ok or not edited:
                return {
                    "type": "error",
                    "message": _format_standard_reply(
                        answer="Background blur failed.",
                        actions=["Attempted open-source background blur"],
                        next_steps=["Try a different image", "Ensure the subject is centered and clear"],
                        assumptions=["The image data is valid"],
                    ),
                    "diagnostics": {"action": action, "open_source": oss_result},
                }

            return {
                "type": "agent_response",
                "agent": "OSS Image Edit",
                "message": _format_standard_reply(
                    answer="Blurred the background while keeping the subject sharp.",
                    actions=["Ran open-source background blur"],
                    next_steps=["Download the image", "Try another edit request"],
                    assumptions=["Foreground/background separation is reasonably clear"],
                ),
                "image_data": [edited],
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "baseline", "value": oss_result.get("model", "oss")},
                ],
                "diagnostics": {"action": action, "open_source": oss_result},
            }

        if action == "blur_faces":
            image_input = action_payload.get("image_data") or action_payload.get("image")
            oss_result = await asyncio.to_thread(svc.blur_faces_open_source, image_input)
            oss_ok = oss_result.get("status") == "success"
            edited = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            if not oss_ok or not edited:
                return {
                    "type": "error",
                    "message": _format_standard_reply(
                        answer="Face blurring failed.",
                        actions=["Attempted open-source face blurring"],
                        next_steps=["Try a clearer face image", "Try a higher-resolution photo"],
                        assumptions=["The image data is valid"],
                    ),
                    "diagnostics": {"action": action, "open_source": oss_result},
                }

            face_count = oss_result.get("faces")
            return {
                "type": "agent_response",
                "agent": "OSS Image Edit",
                "message": _format_standard_reply(
                    answer=f"Blurred detected faces ({face_count} found).",
                    actions=["Ran open-source face detection + blur"],
                    next_steps=["Download the image", "If a face was missed, try a closer crop"],
                    assumptions=["Faces are frontal enough for Haar detection"],
                ),
                "image_data": [edited],
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "baseline", "value": oss_result.get("model", "oss")},
                ],
                "diagnostics": {"action": action, "open_source": oss_result},
            }

        if action == "apply_filter":
            image_input = action_payload.get("image_data") or action_payload.get("image")
            filter_name = (action_payload.get("filter") or "").strip()
            if not filter_name:
                return {
                    "type": "error",
                    "message": _format_standard_reply(
                        answer="I couldn’t apply a filter because the request is missing 'filter'.",
                        actions=["Validated payload for apply_filter"],
                        next_steps=["Choose one: edge, sharpen, denoise, cartoon, posterize"],
                        assumptions=["The client intended to apply a known filter"],
                    ),
                    "diagnostics": {"action_payload": action_payload},
                }

            oss_result = await asyncio.to_thread(svc.apply_filter_open_source, image_input, filter_name)
            oss_ok = oss_result.get("status") == "success"
            edited = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            if not oss_ok or not edited:
                return {
                    "type": "error",
                    "message": _format_standard_reply(
                        answer=f"Filter '{filter_name}' failed.",
                        actions=["Attempted open-source filter"],
                        next_steps=["Try a different filter", "Check supported filters"],
                        assumptions=["The image data is valid"],
                    ),
                    "diagnostics": {"action": action, "open_source": oss_result},
                }

            return {
                "type": "agent_response",
                "agent": "OSS Image Edit",
                "message": _format_standard_reply(
                    answer=f"Applied filter: {filter_name}.",
                    actions=["Ran open-source filter"],
                    next_steps=["Download the image", "Try another filter"],
                    assumptions=["The selected filter matches your intent"],
                ),
                "image_data": [edited],
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "baseline", "value": oss_result.get("model", "oss")},
                ],
                "diagnostics": {"action": action, "open_source": oss_result},
            }

        if action == "transform_image":
            image_input = action_payload.get("image_data") or action_payload.get("image")
            args = {}
            for k in ("mode", "aspect", "width", "height"):
                if k in action_payload and action_payload.get(k) is not None:
                    args[k] = action_payload.get(k)

            oss_result = await asyncio.to_thread(svc.transform_image_open_source, image_input, **args)
            oss_ok = oss_result.get("status") == "success"
            edited = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            if not oss_ok or not edited:
                return {
                    "type": "error",
                    "message": _format_standard_reply(
                        answer="Image transform failed.",
                        actions=["Attempted open-source transform"],
                        next_steps=["Try a different target aspect/size"],
                        assumptions=["The image data is valid"],
                    ),
                    "diagnostics": {"action": action, "open_source": oss_result},
                }

            transform_info = oss_result.get("transform") or {}
            return {
                "type": "agent_response",
                "agent": "OSS Image Edit",
                "message": _format_standard_reply(
                    answer=f"Transformed image ({transform_info.get('src_size')} → {transform_info.get('dst_size')}).",
                    actions=["Ran open-source crop/resize/pad transform"],
                    next_steps=["Download the image", "Tell me your exact target size/aspect if needed"],
                    assumptions=["Center crop/pad is acceptable"],
                ),
                "image_data": [edited],
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "baseline", "value": oss_result.get("model", "oss")},
                ],
                "diagnostics": {"action": action, "open_source": oss_result},
            }

        if action == "generate_thumbnail":
            image_input = action_payload.get("image_data") or action_payload.get("image")
            mode = (action_payload.get("mode") or "crop").strip().lower()
            if mode not in {"crop", "pad"}:
                mode = "crop"
            enhance = bool(action_payload.get("enhance", True))
            oss_result = await asyncio.to_thread(
                svc.generate_thumbnail_open_source,
                image_input,
                size,
                mode=mode,
                enhance=enhance,
            )
            oss_ok = oss_result.get("status") == "success"
            edited = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            if not oss_ok or not edited:
                return {
                    "type": "error",
                    "message": _format_standard_reply(
                        answer="Thumbnail creation failed.",
                        actions=["Attempted open-source thumbnail generation"],
                        next_steps=["Try a different image", "Try specifying an exact size like 1280x720"],
                        assumptions=["The image data is valid"],
                    ),
                    "diagnostics": {"action": action, "open_source": oss_result},
                }

            return {
                "type": "agent_response",
                "agent": "OSS Image Edit",
                "message": _format_standard_reply(
                    answer=f"Created a thumbnail from your uploaded image ({size}).",
                    actions=["Generated OSS thumbnail (crop/resize + enhancement)"],
                    next_steps=["Download the image", "Tell me the exact target platform if you want a different aspect"],
                    assumptions=["Center-crop thumbnail is acceptable"],
                ),
                "image_data": [edited],
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "baseline", "value": oss_result.get("model", "oss")},
                ],
                "diagnostics": {"action": action, "open_source": oss_result},
            }

        if action == "enhance_image":
            image_input = action_payload.get("image_data") or action_payload.get("image")
            strength = (action_payload.get("strength") or "auto").strip()
            oss_result = await asyncio.to_thread(svc.enhance_image_open_source, image_input, strength=strength)
            oss_ok = oss_result.get("status") == "success"
            edited = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            if not oss_ok or not edited:
                return {
                    "type": "error",
                    "message": _format_standard_reply(
                        answer="Image enhancement failed.",
                        actions=["Attempted open-source enhancement"],
                        next_steps=["Try a different image", "Try asking for 'denoise' or 'sharpen' explicitly"],
                        assumptions=["The image data is valid"],
                    ),
                    "diagnostics": {"action": action, "open_source": oss_result},
                }

            return {
                "type": "agent_response",
                "agent": "OSS Image Edit",
                "message": _format_standard_reply(
                    answer="Enhanced your uploaded image (contrast/color/sharpness).",
                    actions=["Ran OSS enhancement"],
                    next_steps=["Download the image", "If you want more/less, say 'enhance strong' or 'enhance light'"],
                    assumptions=["A light enhancement is desirable"],
                ),
                "image_data": [edited],
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "baseline", "value": oss_result.get("model", "oss")},
                ],
                "diagnostics": {"action": action, "open_source": oss_result},
            }

        if action == "call_flux":
            # FLUX.2-pro (West US 3)
            image_input = action_payload.get("image_data") or action_payload.get("image")

            def _run_oss_flux_baseline() -> Dict[str, Any]:
                if image_input and (
                    _looks_like_thumbnail_request(prompt)
                    or _looks_like_transform_request(prompt)
                    or _looks_like_enhance_request(prompt)
                ):
                    return svc.generate_thumbnail_open_source(image_input, size)
                return svc.generate_image_open_source(prompt, size)

            oss_task = (
                asyncio.create_task(asyncio.to_thread(_run_oss_flux_baseline))
                if include_oss
                else None
            )
            async with _get_flux_semaphore():
                flux_result = await asyncio.to_thread(svc.generate_image_flux2, prompt, size)
            oss_result = (
                (await oss_task)
                if oss_task is not None
                else {"status": "skipped", "model": "oss"}
            )

            flux_ok = flux_result.get("status") == "success"
            oss_ok = include_oss and oss_result.get("status") == "success"

            flux_image = _extract_first_media_url_or_data_uri(flux_result) if flux_ok else None
            oss_image = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            image_payload: Any = None
            if include_oss:
                labeled: list[dict] = []
                if flux_image:
                    labeled.append({"label": "LLM/Cloud output (FLUX.2-pro)", "src": flux_image})
                if oss_image:
                    labeled.append({"label": "Open-source output", "src": oss_image})
                if len(labeled) == 1:
                    image_payload = labeled[0]
                elif len(labeled) > 1:
                    image_payload = labeled
            else:
                flat: list[str] = []
                if flux_image:
                    flat.append(flux_image)
                if len(flat) == 1:
                    image_payload = flat[0]
                elif len(flat) > 1:
                    image_payload = flat

            if not flux_ok and oss_ok:
                message = _format_standard_reply(
                    answer="The FLUX image generation request failed, but I generated an open-source baseline image.",
                    actions=["Attempted image generation with FLUX.2-pro", "Generated baseline image using open-source backend"],
                    next_steps=["Compare outputs", "Retry FLUX later"],
                    assumptions=["Your prompt is valid and the open-source backend is reachable"],
                )
                if include_oss:
                    message += (
                        "\n\n---\n\n## Open-source output\n\n"
                        f"- Backend: {oss_result.get('model', 'oss')}\n"
                        "- Status: success\n"
                        "- Note: This is a baseline result for comparison"
                    )
                return {
                    "type": "agent_response",
                    "agent": "FLUX + OSS",
                    "message": message,
                    "image_data": image_payload,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "FLUX.2-pro"},
                        *(
                            [{"type": "baseline", "value": oss_result.get("model", "oss")}]
                            if include_oss
                            else []
                        ),
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "explanation": (
                        "High-level explanation (not internal chain-of-thought):\n"
                        "1) I ran FLUX.2-pro and an open-source backend.\n"
                        "2) FLUX failed, but the baseline succeeded.\n"
                        "3) I returned the baseline image and included FLUX diagnostics."
                    )
                    if wants_explain
                    else None,
                    "diagnostics": {"action": action, "flux": flux_result, "open_source": oss_result},
                }

            if not flux_ok:
                status_code = flux_result.get("status_code")
                message = _format_standard_reply(
                    answer="The FLUX image generation request failed.",
                    actions=["Attempted image generation with FLUX.2-pro"],
                    next_steps=[
                        "Retry the request",
                        "Check server diagnostics for details",
                    ],
                    assumptions=["Your prompt is valid and the model endpoint is reachable"],
                )
                if wants_explain_answer:
                    message = _append_explain_answer_section(
                        message,
                        [
                            "I routed your request to the FLUX tool.",
                            f"The service returned HTTP {status_code} for image generation.",
                            "Check diagnostics for endpoint/deployment configuration and transient failures.",
                        ],
                    )
                return {
                    "type": "error",
                    "message": message,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "FLUX.2-pro"},
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "explanation": (
                        "High-level explanation (not internal chain-of-thought):\n"
                        "1) I detected an image-generation intent.\n"
                        "2) I invoked the FLUX.2-pro tool handler.\n"
                        f"3) The tool call failed with HTTP {status_code}.\n"
                        "4) I returned the diagnostics so you can verify endpoint/deployment/api-version."
                    )
                    if wants_explain
                    else None,
                    "diagnostics": {"action": action, "flux": flux_result, "open_source": oss_result},
                }
            if not flux_image:
                message = _format_standard_reply(
                    answer="The FLUX request completed, but no image data was returned.",
                    actions=["Attempted image generation with FLUX.2-pro"],
                    next_steps=["Retry the request", "Check server diagnostics for the model response"],
                    assumptions=["The model response should include an image URL or data URI"],
                )
                if include_oss:
                    message += "\n\n---\n\n## Open-source output\n\n- Status: " + (
                        "success" if oss_ok else "unavailable"
                    )
                if wants_explain_answer:
                    message = _append_explain_answer_section(
                        message,
                        [
                            "The FLUX tool request completed successfully.",
                            "No image URL or base64 payload was present.",
                            "Retry or inspect diagnostics to confirm response shape.",
                        ],
                    )
                return {
                    "type": "error",
                    "message": message,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "FLUX.2-pro"},
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "explanation": (
                        "High-level explanation (not internal chain-of-thought):\n"
                        "1) I invoked FLUX.2-pro to generate an image.\n"
                        "2) The tool returned success but without an image artifact.\n"
                        "3) I returned diagnostics so you can inspect the raw model result."
                    )
                    if wants_explain
                    else None,
                    "diagnostics": {"action": action, "flux": flux_result, "open_source": oss_result},
                }
            effective_size = ((flux_result.get("request") or {}).get("size") or size)
            allowed_sizes = _allowed_image_sizes_from_env()
            size_note = ""
            if str(effective_size).strip() and str(size).strip() and str(effective_size).strip() != str(size).strip():
                size_note = f" (Used {effective_size} because {size} isn’t allowed. Allowed: {', '.join(allowed_sizes)})"

            message = _format_standard_reply(
                answer=f"I generated an image from your prompt.{size_note}",
                actions=["Generated image with FLUX.2-pro"]
                + (["Generated baseline image using open-source backend"] if oss_ok else []),
                next_steps=[
                    "Download the image",
                    "Compare against the open-source baseline"
                    if oss_ok
                    else "Tell me if you want a different style or size",
                ],
                assumptions=["Your prompt describes the desired thumbnail/image"],
            )
            if include_oss:
                message += (
                    "\n\n---\n\n## Open-source output\n\n"
                    + ("- Status: success\n" if oss_ok else "- Status: unavailable\n")
                    + (f"- Backend: {oss_result.get('model', 'oss')}\n" if oss_ok else "")
                )
            if wants_explain_answer:
                message = _append_explain_answer_section(
                    message,
                    [
                        "I routed your request to FLUX.2-pro.",
                        "The model returned an image artifact.",
                        "You can refine style, size, or composition with follow-ups.",
                    ],
                )
            return {
                "type": "agent_response",
                "agent": "FLUX.2-pro",
                "message": message,
                "image_data": image_payload,
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "model", "value": "FLUX.2-pro"},
                    {"type": "prompt_chars", "value": len(prompt)},
                    {"type": "artifact", "value": "image_data"},
                ],
                "explanation": (
                    "High-level explanation (not internal chain-of-thought):\n"
                    "1) I detected an image-generation request.\n"
                    "2) I invoked FLUX.2-pro via the tool handler.\n"
                    "3) The tool returned image data, which I attached to the response."
                )
                if wants_explain
                else None,
                "diagnostics": {
                    "action": action,
                    "model": flux_result.get("model"),
                    "size": ((flux_result.get("request") or {}).get("size") or size),
                    "open_source": {"model": oss_result.get("model"), "status": oss_result.get("status")},
                },
            }

        if action == "call_flux_kontext":
            # FLUX.1-Kontext-pro (Sweden Central)
            image_input = action_payload.get("image_data") or action_payload.get("image")

            def _run_oss_kontext_baseline() -> Dict[str, Any]:
                if image_input and (
                    _looks_like_thumbnail_request(prompt)
                    or _looks_like_transform_request(prompt)
                    or _looks_like_enhance_request(prompt)
                ):
                    return svc.generate_thumbnail_open_source(image_input, size)
                return svc.generate_image_open_source(prompt, size)

            oss_task = (
                asyncio.create_task(asyncio.to_thread(_run_oss_kontext_baseline))
                if include_oss
                else None
            )
            async with _get_flux_semaphore():
                flux_result = await asyncio.to_thread(svc.generate_image_flux1, prompt, size)
            oss_result = (
                (await oss_task)
                if oss_task is not None
                else {"status": "skipped", "model": "oss"}
            )

            flux_ok = flux_result.get("status") == "success"
            oss_ok = include_oss and oss_result.get("status") == "success"

            flux_image = _extract_first_media_url_or_data_uri(flux_result) if flux_ok else None
            oss_image = _extract_first_media_url_or_data_uri(oss_result) if oss_ok else None

            image_payload: Any = None
            if include_oss:
                labeled: list[dict] = []
                if flux_image:
                    labeled.append({"label": "LLM/Cloud output (FLUX.1-Kontext-pro)", "src": flux_image})
                if oss_image:
                    labeled.append({"label": "Open-source output", "src": oss_image})
                if len(labeled) == 1:
                    image_payload = labeled[0]
                elif len(labeled) > 1:
                    image_payload = labeled
            else:
                flat: list[str] = []
                if flux_image:
                    flat.append(flux_image)
                if len(flat) == 1:
                    image_payload = flat[0]
                elif len(flat) > 1:
                    image_payload = flat

            if not flux_ok and oss_ok:
                message = _format_standard_reply(
                    answer="The FLUX Kontext image generation request failed, but I generated an open-source baseline image.",
                    actions=["Attempted image generation with FLUX.1-Kontext-pro", "Generated baseline image using open-source backend"],
                    next_steps=["Compare outputs", "Retry FLUX Kontext later"],
                    assumptions=["Your prompt is valid and the open-source backend is reachable"],
                )
                if include_oss:
                    message += (
                        "\n\n---\n\n## Open-source output\n\n"
                        f"- Backend: {oss_result.get('model', 'oss')}\n"
                        "- Status: success\n"
                        "- Note: This is a baseline result for comparison"
                    )
                return {
                    "type": "agent_response",
                    "agent": "FLUX Kontext + OSS",
                    "message": message,
                    "image_data": image_payload,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "FLUX.1-Kontext-pro"},
                        *(
                            [{"type": "baseline", "value": oss_result.get("model", "oss")}]
                            if include_oss
                            else []
                        ),
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "diagnostics": {"action": action, "flux": flux_result, "open_source": oss_result},
                }

            if not flux_ok:
                status_code = flux_result.get("status_code")
                message = _format_standard_reply(
                    answer="The FLUX Kontext image generation request failed.",
                    actions=["Attempted image generation with FLUX.1-Kontext-pro"],
                    next_steps=[
                        "Retry the request",
                        "Check server diagnostics for details",
                    ],
                    assumptions=["Your prompt is valid and the model endpoint is reachable"],
                )
                if wants_explain_answer:
                    message = _append_explain_answer_section(
                        message,
                        [
                            "I routed your request to the FLUX Kontext tool.",
                            f"The service returned HTTP {status_code} for image generation.",
                            "Check diagnostics for endpoint/deployment configuration and transient failures.",
                        ],
                    )
                return {
                    "type": "error",
                    "message": message,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "FLUX.1-Kontext-pro"},
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "explanation": (
                        "High-level explanation (not internal chain-of-thought):\n"
                        "1) I detected an image-generation intent.\n"
                        "2) I invoked the FLUX.1-Kontext-pro tool handler.\n"
                        f"3) The tool call failed with HTTP {status_code}.\n"
                        "4) I returned diagnostics so you can verify endpoint/deployment/api-version."
                    )
                    if wants_explain
                    else None,
                    "diagnostics": {"action": action, "flux": flux_result, "open_source": oss_result},
                }
            if not flux_image:
                message = _format_standard_reply(
                    answer="The FLUX Kontext request completed, but no image data was returned.",
                    actions=["Attempted image generation with FLUX.1-Kontext-pro"],
                    next_steps=["Retry the request", "Check server diagnostics for the model response"],
                    assumptions=["The model response should include an image URL or data URI"],
                )
                if include_oss:
                    message += "\n\n---\n\n## Open-source output\n\n- Status: " + (
                        "success" if oss_ok else "unavailable"
                    )
                if wants_explain_answer:
                    message = _append_explain_answer_section(
                        message,
                        [
                            "The FLUX Kontext tool request completed successfully.",
                            "No image URL or base64 payload was present.",
                            "Retry or inspect diagnostics to confirm response shape.",
                        ],
                    )
                return {
                    "type": "error",
                    "message": message,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "FLUX.1-Kontext-pro"},
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "explanation": (
                        "High-level explanation (not internal chain-of-thought):\n"
                        "1) I invoked FLUX.1-Kontext-pro to generate an image.\n"
                        "2) The tool returned success but without an image artifact.\n"
                        "3) I returned diagnostics so you can inspect the raw model result."
                    )
                    if wants_explain
                    else None,
                    "diagnostics": {"action": action, "flux": flux_result, "open_source": oss_result},
                }
            effective_size = ((flux_result.get("request") or {}).get("size") or size)
            allowed_sizes = _allowed_image_sizes_from_env()
            size_note = ""
            if str(effective_size).strip() and str(size).strip() and str(effective_size).strip() != str(size).strip():
                size_note = f" (Used {effective_size} because {size} isn’t allowed. Allowed: {', '.join(allowed_sizes)})"

            message = _format_standard_reply(
                answer=f"I generated an image from your prompt.{size_note}",
                actions=["Generated image with FLUX.1-Kontext-pro"] + (["Generated baseline image using open-source backend"] if oss_ok else []),
                next_steps=["Download the image", "Compare against the open-source baseline" if oss_ok else "Tell me if you want a different style or size"],
                assumptions=["Your prompt describes the desired thumbnail/image"],
            )
            if include_oss:
                message += (
                    "\n\n---\n\n## Open-source output\n\n"
                    + ("- Status: success\n" if oss_ok else "- Status: unavailable\n")
                    + (f"- Backend: {oss_result.get('model', 'oss')}\n" if oss_ok else "")
                )
            if wants_explain_answer:
                message = _append_explain_answer_section(
                    message,
                    [
                        "I routed your request to FLUX.1-Kontext-pro.",
                        "The model returned an image artifact.",
                        "You can refine style, size, or composition with follow-ups.",
                    ],
                )
            return {
                "type": "agent_response",
                "agent": "FLUX.1-Kontext-pro",
                "message": message,
                "image_data": image_payload,
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "model", "value": "FLUX.1-Kontext-pro"},
                    {"type": "prompt_chars", "value": len(prompt)},
                    {"type": "artifact", "value": "image_data"},
                ],
                "explanation": (
                    "High-level explanation (not internal chain-of-thought):\n"
                    "1) I detected an image-generation request.\n"
                    "2) I invoked FLUX.1-Kontext-pro via the tool handler.\n"
                    "3) The tool returned image data, which I attached to the response."
                )
                if wants_explain
                else None,
                "diagnostics": {
                    "action": action,
                    "model": flux_result.get("model"),
                    "size": ((flux_result.get("request") or {}).get("size") or size),
                    "open_source": {"model": oss_result.get("model"), "status": oss_result.get("status")},
                },
            }

        if action == "call_sora":
            # Sora video generation (v1 async jobs + mp4 content download)
            resolved_duration = _normalize_video_duration_seconds(
                int(duration) if isinstance(duration, (int, float)) else _infer_video_duration_seconds_from_prompt(prompt),
                default=3,
            )

            oss_task = (
                asyncio.create_task(
                    asyncio.to_thread(svc.generate_video_open_source, prompt, resolved_duration, resolution)
                )
                if include_oss
                else None
            )
            result = await asyncio.to_thread(svc.generate_video_sora, prompt, resolved_duration, resolution)
            oss_result = (
                (await oss_task)
                if oss_task is not None
                else {"status": "skipped", "model": "oss:video"}
            )

            sora_ok = result.get("status") == "success"
            oss_ok = include_oss and oss_result.get("status") == "success"

            if not sora_ok and oss_ok:
                # Save OSS video (if bytes) to /static so the UI can download/play it.
                oss_video_url = None
                oss_bytes = oss_result.get("content_bytes")
                if isinstance(oss_bytes, (bytes, bytearray)) and oss_bytes:
                    oss_filename = f"oss_video_{uuid.uuid4().hex}.mp4"
                    oss_output_path = Path(static_dir) / oss_filename
                    oss_output_path.write_bytes(bytes(oss_bytes))
                    oss_video_url = f"/static/{oss_filename}"
                else:
                    oss_video_url = oss_result.get("video_url")

                message = _format_standard_reply(
                    answer="The Sora request failed, but I generated an open-source baseline video you can play/download below.",
                    actions=["Attempted video generation with Sora", "Generated baseline video using open-source backend"],
                    next_steps=["Compare outputs", "Retry Sora later"],
                    assumptions=["Your prompt is valid and the open-source backend is reachable"],
                )
                if include_oss:
                    message += (
                        "\n\n---\n\n## Open-source output\n\n"
                        f"- Backend: {oss_result.get('model', 'oss:video')}\n"
                        "- Status: success\n"
                        "- Note: This is a baseline result for comparison"
                    )
                return {
                    "type": "agent_response",
                    "agent": "Sora + OSS",
                    "message": message,
                    "video_url": {"label": "Open-source output", "src": oss_video_url} if oss_video_url else None,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "sora"},
                        {"type": "baseline", "value": oss_result.get("model", "oss:video")},
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "diagnostics": {"action": action, "sora": result, "open_source": oss_result},
                }

            if not sora_ok:
                message = _format_standard_reply(
                    answer="The Sora video generation request failed.",
                    actions=["Attempted video generation with Sora"],
                    next_steps=["Retry the request", "Check server diagnostics for the model response"],
                    assumptions=["Your prompt is valid and the model endpoint is reachable"],
                )
                if include_oss:
                    oss_status = (oss_result or {}).get("status") or ("success" if oss_ok else "unavailable")
                    message += "\n\n---\n\n## Open-source output\n\n- Status: " + str(oss_status)
                    oss_error = (oss_result or {}).get("error")
                    if oss_error:
                        message += "\n- Error: " + str(oss_error)
                if wants_explain_answer:
                    status_code = result.get("status_code")
                    message = _append_explain_answer_section(
                        message,
                        [
                            "I routed your request to the Sora tool.",
                            f"The service returned HTTP {status_code} during video generation.",
                            "Verify endpoint, deployment, and API version settings.",
                        ],
                    )
                return {
                    "type": "error",
                    "message": message,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "sora"},
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "explanation": (
                        "High-level explanation (not internal chain-of-thought):\n"
                        "1) I detected a video-generation request.\n"
                        "2) I invoked the Sora tool handler.\n"
                        f"3) The tool call failed with HTTP {result.get('status_code')}.\n"
                        "4) I returned diagnostics so you can verify endpoint/deployment/api-version."
                    )
                    if wants_explain
                    else None,
                    "diagnostics": {"action": action, "sora": result, "open_source": oss_result},
                }

            content_bytes = result.get("content_bytes")
            if not isinstance(content_bytes, (bytes, bytearray)) or not content_bytes:
                message = _format_standard_reply(
                    answer="The Sora request completed, but no video bytes were returned.",
                    actions=["Attempted video generation with Sora"],
                    next_steps=["Retry the request", "Check server diagnostics for returned content"],
                    assumptions=["The model response should include mp4 bytes"],
                )
                if wants_explain_answer:
                    message = _append_explain_answer_section(
                        message,
                        [
                            "The Sora job completed successfully.",
                            "No mp4 bytes were included in the response.",
                            "Retry or inspect diagnostics for job output references.",
                        ],
                    )
                return {
                    "type": "error",
                    "message": message,
                    "references": [
                        {"type": "tool_action", "value": action},
                        {"type": "model", "value": "sora"},
                        {"type": "prompt_chars", "value": len(prompt)},
                    ],
                    "explanation": (
                        "High-level explanation (not internal chain-of-thought):\n"
                        "1) I invoked Sora via the tool handler.\n"
                        "2) The call completed but no mp4 bytes were returned.\n"
                        "3) I returned diagnostics so you can inspect the raw model result."
                    )
                    if wants_explain
                    else None,
                    "diagnostics": {"action": action, "result": result},
                }

            # Save to /static so the UI can download/play it
            filename = f"sora_{result.get('generation_id') or uuid.uuid4().hex}.mp4"
            output_path = Path(static_dir) / filename
            output_path.write_bytes(bytes(content_bytes))
            video_url = f"/static/{filename}"

            oss_video_url = None
            if oss_ok:
                oss_bytes = oss_result.get("content_bytes")
                if isinstance(oss_bytes, (bytes, bytearray)) and oss_bytes:
                    oss_filename = f"oss_video_{uuid.uuid4().hex}.mp4"
                    oss_output_path = Path(static_dir) / oss_filename
                    oss_output_path.write_bytes(bytes(oss_bytes))
                    oss_video_url = f"/static/{oss_filename}"
                else:
                    oss_video_url = oss_result.get("video_url")

            message = _format_standard_reply(
                answer=f"I generated a {resolved_duration}-second video. You can play it and download it below.",
                actions=["Generated video with Sora", "Saved artifact to /static for playback/download"],
                next_steps=["Play or download the video", "Ask for a different duration, resolution, or style"],
                assumptions=["Your prompt describes the desired motion/scene"],
            )
            if include_oss:
                oss_status = (oss_result or {}).get("status") or ("success" if oss_ok else "unavailable")
                oss_error = (oss_result or {}).get("error")
                message += (
                    "\n\n---\n\n## Open-source output\n\n"
                    + (f"- Status: {oss_status}\n")
                    + (f"- Backend: {(oss_result or {}).get('model', 'oss:video')}\n")
                    + (f"- Error: {oss_error}\n" if oss_error else "")
                )
            if wants_explain_answer:
                message = _append_explain_answer_section(
                    message,
                    [
                        "I routed your request to Sora.",
                        "The model returned a video artifact.",
                        "You can adjust duration, resolution, or style in a follow-up.",
                    ],
                )

            video_payload: Any = video_url
            if include_oss:
                labeled_videos: list[dict] = [{"label": "LLM/Cloud output (Sora)", "src": video_url}]
                if oss_video_url and oss_video_url != video_url:
                    labeled_videos.append({"label": "Open-source output", "src": oss_video_url})
                video_payload = labeled_videos[0] if len(labeled_videos) == 1 else labeled_videos
            elif oss_video_url and oss_video_url != video_url:
                video_payload = [video_url, oss_video_url]

            return {
                "type": "agent_response",
                "agent": "Sora",
                "message": message,
                "video_url": video_payload,
                "references": [
                    {"type": "tool_action", "value": action},
                    {"type": "model", "value": "sora"},
                    {"type": "prompt_chars", "value": len(prompt)},
                    {"type": "artifact", "value": "video_url"},
                    {"type": "artifact_path", "value": video_url},
                ],
                "explanation": (
                    "High-level explanation (not internal chain-of-thought):\n"
                    "1) I detected a video-generation request.\n"
                    "2) I invoked Sora via the tool handler.\n"
                    "3) I saved the returned video bytes to /static and returned the URL."
                )
                if wants_explain
                else None,
                "diagnostics": {
                    "action": action,
                    "model": result.get("model"),
                    "resolution": resolution,
                    "duration": resolved_duration,
                    "job_id": result.get("job_id"),
                    "generation_id": result.get("generation_id"),
                    "open_source": {"model": oss_result.get("model"), "status": oss_result.get("status")},
                },
            }

        return {
            "type": "error",
            "message": _format_standard_reply(
                answer=f"I don’t recognize the requested action: {action}",
                actions=["Validated tool/action payload"],
                next_steps=["Use one of: call_flux, call_flux_kontext, call_sora"],
                assumptions=["The client is sending an action payload for a supported tool"],
            ),
            "diagnostics": {"action_payload": action_payload},
        }

    except Exception as e:
        logger.error(f"Action execution failed ({action}): {e}", exc_info=True)
        return {
            "type": "error",
            "message": _format_standard_reply(
                answer="Tool execution failed due to an internal error.",
                actions=[f"Attempted to execute action: {action or 'unknown'}"],
                next_steps=["Retry the request", "Check server logs for details"],
                assumptions=["This is a transient error or a configuration issue"],
            ),
            "diagnostics": {"action": action, "exception": str(e)},
        }

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main interface"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/diagnostics")
async def diagnostics():
    """Expose orchestrator diagnostic details for troubleshooting."""
    project_endpoint, project_endpoint_source = get_env_with_source("AZURE_AI_PROJECT_ENDPOINT")
    project_endpoint_sweden, project_endpoint_sweden_source = get_env_with_source("AZURE_AI_PROJECT_ENDPOINT_SWEDEN")
    project_name, project_name_source = get_env_with_source("AZURE_AI_PROJECT_NAME")

    diagnostics_info = {
        "orchestrator_initialized": orchestrator is not None,
        "initialization_error": orchestrator_error,
        "service_mode": "hybrid_agent_service",
        "env": {
            "RUNNING_IN_AZURE": is_running_in_azure(),
            "WEBSITE_SITE_NAME": get_env("WEBSITE_SITE_NAME"),
            "AZURE_AI_PROJECT_ENDPOINT": project_endpoint,
            "AZURE_AI_PROJECT_ENDPOINT__SOURCE": project_endpoint_source,
            "AZURE_AI_PROJECT_ENDPOINT_SWEDEN": project_endpoint_sweden,
            "AZURE_AI_PROJECT_ENDPOINT_SWEDEN__SOURCE": project_endpoint_sweden_source,
            "AZURE_AI_PROJECT_NAME": project_name,
            "AZURE_AI_PROJECT_NAME__SOURCE": project_name_source,
            "AZURE_AI_AGENT_ENDPOINT": get_env("AZURE_AI_AGENT_ENDPOINT"),
            "AZURE_AI_FOUNDRY_ENDPOINT": get_env("AZURE_AI_FOUNDRY_ENDPOINT"),

            # Direct model generation settings (useful for debugging FLUX/Sora)
            "AZURE_AI_INFERENCE_ENDPOINT_SWEDEN": get_env("AZURE_AI_INFERENCE_ENDPOINT_SWEDEN"),
            "AZURE_AI_INFERENCE_ENDPOINT_WESTUS3": get_env("AZURE_AI_INFERENCE_ENDPOINT_WESTUS3"),
            "AZURE_OPENAI_ENDPOINT_FLUX": get_env("AZURE_OPENAI_ENDPOINT_FLUX"),
            "AZURE_OPENAI_ENDPOINT_SORA": get_env("AZURE_OPENAI_ENDPOINT_SORA"),
            "AZURE_OPENAI_IMAGES_API_VERSION": get_env("AZURE_OPENAI_IMAGES_API_VERSION"),
            "AZURE_OPENAI_SORA_API_VERSION": get_env("AZURE_OPENAI_SORA_API_VERSION"),
            "AZURE_OPENAI_DEPLOYMENT_FLUX1": get_env("AZURE_OPENAI_DEPLOYMENT_FLUX1"),
            "AZURE_OPENAI_DEPLOYMENT_FLUX2": get_env("AZURE_OPENAI_DEPLOYMENT_FLUX2"),
            "AZURE_OPENAI_DEPLOYMENT_SORA": get_env("AZURE_OPENAI_DEPLOYMENT_SORA"),

            # Vision analysis settings
            "AZURE_OPENAI_VISION_DEPLOYMENT": get_env("AZURE_OPENAI_VISION_DEPLOYMENT"),
            "AZURE_OPENAI_DEPLOYMENT_GPT4O_VISION": get_env("AZURE_OPENAI_DEPLOYMENT_GPT4O_VISION"),

            # File/OCR settings
            "MAX_UPLOAD_BYTES": get_env("MAX_UPLOAD_BYTES"),
            "MAX_DOCUMENT_CHARS": get_env("MAX_DOCUMENT_CHARS"),
            "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT": get_env("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"),
            "AZURE_DOCUMENT_INTELLIGENCE_API_VERSION": get_env("AZURE_DOCUMENT_INTELLIGENCE_API_VERSION"),
        },
    }
    
    if orchestrator:
        diagnostics_info.update({
            "agent_available": orchestrator.orchestrator_agent is not None,
            "agent_client_initialized": orchestrator.agent_client is not None,
            "endpoint": orchestrator.sweden_endpoint,
        })
        
        if orchestrator.orchestrator_agent:
            diagnostics_info["agent_id"] = orchestrator.orchestrator_agent.id
            diagnostics_info["agent_name"] = orchestrator.orchestrator_agent.name
            diagnostics_info["status"] = "fully_operational"
        else:
            diagnostics_info["status"] = "fallback_mode"
    else:
        diagnostics_info["status"] = "failed"
    
    return diagnostics_info

@app.post("/upload")
async def upload_file(file: UploadFile = File(...), inline_image: bool = False):
    """Handle file upload (supports large files)."""
    max_bytes = int(get_env("MAX_UPLOAD_BYTES", str(200 * 1024 * 1024)))
    try:
        stored_name, dest_path, total = await file_service.save_upload(file, max_bytes=max_bytes)
    except ValueError as e:
        raise HTTPException(status_code=413, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Upload failed: {e}")

    is_img = file_service.is_image(file.content_type, file.filename)
    url = f"/static/uploads/{stored_name}"

    # Optional: return inline base64 only when explicitly requested AND file is small.
    image_data = None
    if inline_image and is_img and total <= 3 * 1024 * 1024:
        try:
            import base64

            b = dest_path.read_bytes()
            image_data = f"data:{file.content_type or 'image/png'};base64,{base64.b64encode(b).decode('utf-8')}"
        except Exception:
            image_data = None

    return JSONResponse(
        content={
            "status": "success",
            "file": {
                "name": file.filename,
                "content_type": file.content_type,
                "size_bytes": total,
                "url": url,
                "stored_name": stored_name,
                "is_image": is_img,
            },
            "image_data": image_data,
        }
    )

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for real-time chat"""
    connection_id = datetime.utcnow().isoformat()
    logger.info(f"[{connection_id}] WebSocket connection attempt...")
    
    try:
        await websocket.accept()
        logger.info(f"[{connection_id}] WebSocket connection established")
        
        with tracer.start_as_current_span("websocket_session") as span:
            span.set_attribute("connection_id", connection_id)

            pending_media_action: Optional[Dict[str, Any]] = None
            pending_media_questions: Optional[list[str]] = None
            
            while True:
                try:
                    # Receive message from client
                    data = await websocket.receive_text()
                    logger.debug(f"[{connection_id}] Received data: {data[:200]}...")
                    
                    message_data = json.loads(data)

                    # Support both the standard UI payload and tool/action payloads
                    user_message = message_data.get("message", "")
                    image_data = message_data.get("image", None)
                    file_meta = message_data.get("file", None)
                    wants_stream = bool(message_data.get("stream", False))
                    wants_explain = bool(message_data.get("explain", False))
                    wants_explain_answer = bool(message_data.get("explain_answer", False))
                    include_oss = bool(message_data.get("include_oss", True))

                    # If we previously asked clarifying questions for a media generation request,
                    # treat the next user message as clarifications (unless they send a new action).
                    if pending_media_action and isinstance(user_message, str) and user_message.strip():
                        lowered = user_message.strip().lower()
                        if any(k in lowered for k in ["cancel", "never mind", "nevermind", "stop"]):
                            pending_media_action = None
                            pending_media_questions = None
                            await websocket.send_text(
                                fast_json_dumps(
                                    {
                                        "type": "agent_response",
                                        "agent": "System",
                                        "message": _format_standard_reply(
                                            answer="Okay — canceled the pending image/video generation.",
                                            actions=["Canceled pending clarification"],
                                            next_steps=["Send a new prompt when ready"],
                                            assumptions=["You want to start over"],
                                        ),
                                    }
                                )
                            )
                            continue

                        # Allow skipping clarifications.
                        if any(k in lowered for k in ["just generate", "skip", "no preference", "surprise me"]):
                            action_payload = pending_media_action
                            pending_media_action = None
                            pending_media_questions = None
                            action_payload.setdefault("include_oss", include_oss)
                            action_payload.setdefault("explain", wants_explain)
                            action_payload.setdefault("explain_answer", wants_explain_answer)
                            action_response = await _handle_action(action_payload)
                            await websocket.send_text(fast_json_dumps(action_response))
                            continue

                        # Use the user's message as clarifications and proceed.
                        action_payload = pending_media_action
                        pending_media_action = None
                        pending_media_questions = None
                        base_prompt = str(action_payload.get("prompt") or "").strip()
                        clar = user_message.strip()
                        action_payload["prompt"] = f"{base_prompt}\n\nUser clarifications: {clar}".strip()
                        action_payload.setdefault("include_oss", include_oss)
                        action_payload.setdefault("explain", wants_explain)
                        action_payload.setdefault("explain_answer", wants_explain_answer)
                        action_response = await _handle_action(action_payload)
                        await websocket.send_text(fast_json_dumps(action_response))
                        continue

                    # If the UI uploaded a file and is only sending metadata, map it into the existing
                    # routing paths (image analysis uses image_url; docs are extracted server-side).
                    extracted_doc_text: Optional[str] = None
                    extracted_doc_kind: Optional[str] = None
                    extracted_doc_used_ocr: Optional[bool] = None
                    if isinstance(file_meta, dict):
                        stored_name = file_meta.get("stored_name")
                        file_url = file_meta.get("url")
                        file_name = file_meta.get("name")
                        file_content_type = file_meta.get("content_type")
                        file_is_image = bool(file_meta.get("is_image"))

                        if file_is_image and not image_data and isinstance(file_url, str) and file_url.strip():
                            image_data = file_url.strip()

                        if (not file_is_image) and stored_name and isinstance(stored_name, str):
                            # Basic path safety: stored_name must be a basename without separators.
                            if ("/" in stored_name) or ("\\" in stored_name) or (Path(stored_name).name != stored_name):
                                logger.warning(f"[{connection_id}] Rejecting unsafe stored_name: {stored_name}")
                            else:
                                local_path = Path(file_service.uploads_dir) / stored_name
                                if not local_path.exists():
                                    logger.warning(f"[{connection_id}] Uploaded file not found on disk: {local_path}")
                                else:
                                    try:
                                        extracted = await asyncio.to_thread(
                                            document_extractor.extract,
                                            local_path,
                                            file_content_type,
                                        )
                                        if extracted:
                                            extracted_doc_text = extracted.text
                                            extracted_doc_kind = extracted.kind
                                            extracted_doc_used_ocr = bool(extracted.used_ocr)
                                    except Exception as e:
                                        logger.warning(f"[{connection_id}] Document extraction failed: {e}")

                    # If a doc was attached without a message, default to a simple instruction.
                    if (not user_message) and extracted_doc_text and extracted_doc_text.strip():
                        user_message = "Summarize the attached document."

                    # If client sent an explicit action payload, handle it directly
                    if isinstance(message_data, dict) and message_data.get("action"):
                        logger.info(f"[{connection_id}] Handling direct action: {message_data.get('action')}")
                        action_payload = dict(message_data)
                        action_payload.setdefault("explain", wants_explain)
                        action_payload.setdefault("explain_answer", wants_explain_answer)
                        action_payload.setdefault("include_oss", include_oss)

                        # If a media generation request is underspecified, ask clarifying questions first.
                        try:
                            action_name = str(action_payload.get("action") or "").strip()
                            if action_name in {"call_flux", "call_flux_kontext", "call_sora"}:
                                if _should_prompt_for_media_clarifications(
                                    action=action_name,
                                    user_text=user_message if isinstance(user_message, str) else None,
                                    action_payload=action_payload,
                                ):
                                    qs = _infer_prompt_clarifying_questions(
                                        action=action_name,
                                        prompt=str(action_payload.get("prompt") or ""),
                                        action_payload=action_payload,
                                    )
                                    if qs:
                                        pending_media_action = action_payload
                                        pending_media_questions = qs
                                        msg = "To generate a more accurate result, quick questions:\n" + "\n".join(
                                            [f"{i+1}) {q}" for i, q in enumerate(qs)]
                                        )
                                        msg += "\n\nReply with your answers in one message (or say 'just generate' to skip)."
                                        await websocket.send_text(
                                            fast_json_dumps(
                                                {
                                                    "type": "agent_response",
                                                    "agent": "System",
                                                    "message": msg,
                                                    "diagnostics": {"clarification": {"action": action_name, "questions": qs}},
                                                }
                                            )
                                        )
                                        continue
                        except Exception:
                            pass

                        # If an image is attached in the UI payload, allow action payloads to
                        # omit image_data/image and still operate on the attachment.
                        try:
                            action_name = str(action_payload.get("action") or "").strip()
                            if action_name in {
                                "remove_background",
                                "blur_background",
                                "blur_faces",
                                "apply_filter",
                                "transform_image",
                            } and not (action_payload.get("image_data") or action_payload.get("image")):
                                if image_data:
                                    action_payload["image_data"] = image_data
                        except Exception:
                            pass

                        action_response = await _handle_action(action_payload)
                        await websocket.send_text(fast_json_dumps(action_response))
                        continue

                    # If the user typed/pasted an action JSON blob into the chat, handle it
                    if isinstance(user_message, str):
                        parsed_action = _try_parse_action_dict(user_message)
                        if parsed_action:
                            logger.info(f"[{connection_id}] Handling action from user message: {parsed_action.get('action')}")
                            parsed_action.setdefault("explain", wants_explain)
                            parsed_action.setdefault("explain_answer", wants_explain_answer)
                            parsed_action.setdefault("include_oss", include_oss)

                            # Same convenience: if the user typed an action JSON but also attached
                            # an image via the UI, auto-wire the attachment into the action.
                            try:
                                action_name = str(parsed_action.get("action") or "").strip()
                                if action_name in {
                                    "remove_background",
                                    "blur_background",
                                    "blur_faces",
                                    "apply_filter",
                                    "transform_image",
                                } and not (parsed_action.get("image_data") or parsed_action.get("image")):
                                    if image_data:
                                        parsed_action["image_data"] = image_data
                            except Exception:
                                pass

                            action_response = await _handle_action(parsed_action)
                            await websocket.send_text(fast_json_dumps(action_response))
                            continue

                    # Implicit tool routing for clear image/video generation requests
                    if isinstance(user_message, str):
                        if _is_allowed_image_sizes_question(user_message):
                            sizes = _allowed_image_sizes_from_env()
                            msg = _format_standard_reply(
                                answer=f"Allowed image sizes: {', '.join(sizes)}",
                                actions=["Reported configured/allowed image sizes"],
                                next_steps=[
                                    "Use one of these sizes when calling FLUX",
                                    "If you need different sizes, set AZURE_OPENAI_ALLOWED_IMAGE_SIZES",
                                ],
                                assumptions=["You are generating images with FLUX via the app"],
                            )
                            await websocket.send_text(
                                fast_json_dumps(
                                    {
                                        "type": "agent_response",
                                        "agent": "System",
                                        "message": msg,
                                        "diagnostics": {"allowed_image_sizes": sizes},
                                    }
                                )
                            )
                            continue

                        inferred_action = _try_infer_action_from_user_message(user_message)
                        if inferred_action:
                            logger.info(
                                f"[{connection_id}] Inferred action from user message: {inferred_action.get('action')}"
                            )
                            inferred_action.setdefault("explain", wants_explain)
                            inferred_action.setdefault("explain_answer", wants_explain_answer)
                            inferred_action.setdefault("include_oss", include_oss)

                            # Ask clarifying questions before generating media if needed.
                            try:
                                action_name = str(inferred_action.get("action") or "").strip()
                                if action_name in {"call_flux", "call_flux_kontext", "call_sora"}:
                                    if _should_prompt_for_media_clarifications(
                                        action=action_name,
                                        user_text=user_message,
                                        action_payload=inferred_action,
                                    ):
                                        qs = _infer_prompt_clarifying_questions(
                                            action=action_name,
                                            prompt=str(inferred_action.get("prompt") or ""),
                                            action_payload=inferred_action,
                                        )
                                        if qs:
                                            pending_media_action = inferred_action
                                            pending_media_questions = qs
                                            msg = "To generate a more accurate result, quick questions:\n" + "\n".join(
                                                [f"{i+1}) {q}" for i, q in enumerate(qs)]
                                            )
                                            msg += "\n\nReply with your answers in one message (or say 'just generate' to skip)."
                                            await websocket.send_text(
                                                fast_json_dumps(
                                                    {
                                                        "type": "agent_response",
                                                        "agent": "System",
                                                        "message": msg,
                                                        "diagnostics": {"clarification": {"action": action_name, "questions": qs}},
                                                    }
                                                )
                                            )
                                            continue
                            except Exception:
                                pass

                            action_response = await _handle_action(inferred_action)
                            await websocket.send_text(fast_json_dumps(action_response))
                            continue

                        # Local OSS image edit routing: "remove background" for an attached image.
                        # Do this before Vision analysis so the user sees an actual edited image.
                        if include_oss and image_data and isinstance(image_data, str) and image_data.strip() and isinstance(user_message, str):
                            if _looks_like_thumbnail_request(user_message):
                                transform_args = _infer_transform_args(user_message)
                                action_payload = {
                                    "action": "generate_thumbnail",
                                    "image_data": image_data,
                                    "prompt": user_message,
                                    "include_oss": True,
                                    "explain": wants_explain,
                                    "explain_answer": wants_explain_answer,
                                    "size": _infer_thumbnail_size(user_message),
                                    "mode": transform_args.get("mode", "crop"),
                                    "enhance": True,
                                }
                                action_response = await _handle_action(action_payload)
                                await websocket.send_text(fast_json_dumps(action_response))
                                continue

                            if _looks_like_enhance_request(user_message):
                                strength = "auto"
                                lowered = user_message.lower()
                                if any(k in lowered for k in ["strong", "more", "extra"]):
                                    strength = "strong"
                                elif any(k in lowered for k in ["light", "subtle", "less"]):
                                    strength = "light"

                                action_response = await _handle_action(
                                    {
                                        "action": "enhance_image",
                                        "image_data": image_data,
                                        "prompt": user_message,
                                        "strength": strength,
                                        "include_oss": True,
                                        "explain": wants_explain,
                                        "explain_answer": wants_explain_answer,
                                    }
                                )
                                await websocket.send_text(fast_json_dumps(action_response))
                                continue

                            if _looks_like_background_removal_request(user_message):
                                action_response = await _handle_action(
                                    {
                                        "action": "remove_background",
                                        "image_data": image_data,
                                        "prompt": user_message,
                                        "include_oss": True,
                                        "explain": wants_explain,
                                        "explain_answer": wants_explain_answer,
                                    }
                                )
                                await websocket.send_text(fast_json_dumps(action_response))
                                continue

                            if _looks_like_blur_background_request(user_message):
                                action_response = await _handle_action(
                                    {
                                        "action": "blur_background",
                                        "image_data": image_data,
                                        "prompt": user_message,
                                        "include_oss": True,
                                        "explain": wants_explain,
                                        "explain_answer": wants_explain_answer,
                                    }
                                )
                                await websocket.send_text(fast_json_dumps(action_response))
                                continue

                            if _looks_like_blur_faces_request(user_message):
                                action_response = await _handle_action(
                                    {
                                        "action": "blur_faces",
                                        "image_data": image_data,
                                        "prompt": user_message,
                                        "include_oss": True,
                                        "explain": wants_explain,
                                        "explain_answer": wants_explain_answer,
                                    }
                                )
                                await websocket.send_text(fast_json_dumps(action_response))
                                continue

                            filter_name = _infer_filter_name(user_message)
                            if filter_name and _looks_like_filter_request(user_message):
                                action_response = await _handle_action(
                                    {
                                        "action": "apply_filter",
                                        "image_data": image_data,
                                        "prompt": user_message,
                                        "filter": filter_name,
                                        "include_oss": True,
                                        "explain": wants_explain,
                                        "explain_answer": wants_explain_answer,
                                    }
                                )
                                await websocket.send_text(fast_json_dumps(action_response))
                                continue

                            if _looks_like_transform_request(user_message):
                                transform_args = _infer_transform_args(user_message)
                                if transform_args:
                                    action_payload = {
                                        "action": "transform_image",
                                        "image_data": image_data,
                                        "prompt": user_message,
                                        "include_oss": True,
                                        "explain": wants_explain,
                                        "explain_answer": wants_explain_answer,
                                    }
                                    action_payload.update(transform_args)
                                    action_response = await _handle_action(action_payload)
                                    await websocket.send_text(fast_json_dumps(action_response))
                                    continue

                    # If an image is attached, route to GPT-4o Vision for analysis by default.
                    # Do not intercept explicit generation requests (those are handled above).
                    if image_data and isinstance(image_data, str) and image_data.strip():
                        message_text = user_message if isinstance(user_message, str) else ""
                        if not _looks_like_generation_request(message_text):
                            if not orchestrator or not getattr(orchestrator, "agent_client", None):
                                error_msg = {
                                    "type": "error",
                                    "message": _format_standard_reply(
                                        answer="I can’t analyze the image right now because the agent client is not initialized.",
                                        actions=["Checked vision analysis availability"],
                                        next_steps=["Check /diagnostics", "Verify Azure AI Project configuration"],
                                        assumptions=["The server is running but the AI client initialization failed"],
                                    ),
                                    "diagnostics": {
                                        "orchestrator_initialized": bool(orchestrator),
                                        "agent_client_initialized": bool(getattr(orchestrator, "agent_client", None)),
                                        "orchestrator_error": orchestrator_error,
                                    },
                                }
                                await websocket.send_text(fast_json_dumps(error_msg))
                                continue

                            vision_model = (
                                get_env("AZURE_OPENAI_VISION_DEPLOYMENT")
                                or get_env("AZURE_OPENAI_DEPLOYMENT_GPT4O_VISION")
                                or "gpt-4o"
                            )
                            openai_client = orchestrator.agent_client.get_openai_client()

                            prompt_text = message_text.strip() or _vision_message_for_empty_prompt()
                            user_message_to_send = (
                                f"{prompt_text}\n\n{_standard_format_instructions_for_model(wants_explain_answer)}"
                            )

                            # Build multimodal input for the Responses API.
                            vision_input = [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "input_text", "text": user_message_to_send},
                                        {"type": "input_image", "image_url": image_data},
                                    ],
                                }
                            ]

                            message_id = uuid.uuid4().hex
                            if wants_stream:
                                await websocket.send_text(
                                    fast_json_dumps(
                                        {
                                            "type": "agent_start",
                                            "message_id": message_id,
                                            "agent": "GPT-4o Vision",
                                        }
                                    )
                                )

                            try:
                                if wants_stream and hasattr(openai_client, "responses") and hasattr(openai_client.responses, "stream"):
                                    full_text_parts: list[str] = []
                                    streamed_any_delta = False
                                    with openai_client.responses.stream(model=vision_model, input=vision_input) as stream:
                                        for event in stream:
                                            event_type = getattr(event, "type", None)
                                            delta = getattr(event, "delta", None)
                                            if event_type and isinstance(event_type, str) and event_type.endswith(".delta") and delta:
                                                d = str(delta)
                                                if d:
                                                    streamed_any_delta = True
                                                    full_text_parts.append(d)
                                                    await websocket.send_text(
                                                        fast_json_dumps(
                                                            {
                                                                "type": "agent_delta",
                                                                "message_id": message_id,
                                                                "delta": d,
                                                            }
                                                        )
                                                    )

                                        final_response = stream.get_final_response()
                                        response_text = getattr(final_response, "output_text", None)
                                        if not response_text and hasattr(orchestrator, "_extract_response_text"):
                                            response_text = orchestrator._extract_response_text(final_response)
                                    final_text = ("".join(full_text_parts).strip() or str(response_text or "").strip())
                                else:
                                    resp = openai_client.responses.create(model=vision_model, input=vision_input)
                                    response_text = getattr(resp, "output_text", None)
                                    if not response_text and hasattr(orchestrator, "_extract_response_text"):
                                        response_text = orchestrator._extract_response_text(resp)
                                    final_text = str(response_text or "").strip()

                                if not final_text:
                                    final_text = _format_standard_reply(
                                        answer="I analyzed the image, but the model returned no text.",
                                        actions=["Ran GPT-4o Vision analysis"],
                                        next_steps=["Try a more specific question about the image"],
                                        assumptions=["The image data was valid"],
                                    )

                                response = {
                                    "type": "agent_response",
                                    "message_id": message_id,
                                    "agent": "GPT-4o Vision",
                                    "message": final_text if not (wants_stream and 'streamed_any_delta' in locals() and streamed_any_delta) else final_text,
                                    "references": [
                                        {"type": "service", "value": "VisionAnalysis"},
                                        {"type": "model", "value": vision_model},
                                        {"type": "artifact", "value": "image_input"},
                                    ],
                                    "diagnostics": {
                                        "vision_model": vision_model,
                                    },
                                    "explanation": (
                                        "High-level explanation (not internal chain-of-thought):\n"
                                        "1) I detected an attached image.\n"
                                        "2) I routed the request to GPT-4o Vision for analysis.\n"
                                        "3) I returned the model’s response in the app’s standard format."
                                    )
                                    if wants_explain
                                    else None,
                                }

                                await websocket.send_text(fast_json_dumps(response))
                                continue
                            except Exception as e:
                                logger.error(f"[{connection_id}] Vision analysis failed: {e}", exc_info=True)
                                error_response = {
                                    "type": "error",
                                    "message": _format_standard_reply(
                                        answer="Image analysis failed.",
                                        actions=["Attempted GPT-4o Vision analysis"],
                                        next_steps=["Retry", "Check /diagnostics"],
                                        assumptions=["The vision model deployment is configured and reachable"],
                                    ),
                                    "diagnostics": {
                                        "vision_model": vision_model,
                                        "exception": str(e),
                                    },
                                }
                                await websocket.send_text(fast_json_dumps(error_response))
                                continue
                    
                    logger.info(f"[{connection_id}] User message: {user_message[:100]}...")
                    if image_data:
                        logger.info(f"[{connection_id}] Image data received: {len(image_data)} chars")
                    
                    if not user_message and not image_data and not isinstance(file_meta, dict):
                        logger.warning(f"[{connection_id}] Empty message received, skipping")
                        continue
                    
                    # Check if orchestrator is available
                    if not orchestrator:
                        error_msg = {
                            "type": "error",
                            "message": _format_standard_reply(
                                answer="The agent service is currently unavailable.",
                                actions=["Checked orchestrator availability"],
                                next_steps=[
                                    "Verify Azure/agent configuration and environment variables",
                                    "Check /diagnostics for more details",
                                ],
                                assumptions=["The server is running but the orchestrator failed to initialize"],
                            ),
                            "details": orchestrator_error,
                            "diagnostics": {
                                "orchestrator_initialized": False,
                                "error": orchestrator_error,
                            },
                        }
                        logger.error(f"[{connection_id}] Orchestrator not available: {orchestrator_error}")
                        await websocket.send_text(fast_json_dumps(error_msg))
                        continue
                        
                    # Process with Orchestrator
                    logger.info(f"[{connection_id}] Starting orchestrator processing...")
                    
                    with tracer.start_as_current_span("process_message") as process_span:
                        process_span.set_attribute("message_length", len(user_message))
                        process_span.set_attribute("has_image", bool(image_data))
                        
                        try:
                            # Use Hybrid Agent Service
                            logger.info(f"[{connection_id}] Calling hybrid orchestrator...")
                            
                            # Build request context
                            request_context = {}
                            if image_data:
                                request_context["image_data"] = image_data
                                logger.debug(f"[{connection_id}] Added image data to context")
                            if isinstance(file_meta, dict):
                                request_context["file_name"] = file_meta.get("name")
                                request_context["file_content_type"] = file_meta.get("content_type")
                                request_context["file_size_bytes"] = file_meta.get("size_bytes")
                                request_context["file_url"] = file_meta.get("url")
                                request_context["file_is_image"] = bool(file_meta.get("is_image"))
                            if extracted_doc_text is not None:
                                max_doc_chars = int(get_env("MAX_DOCUMENT_CHARS", "40000"))
                                doc_text = extracted_doc_text
                                if isinstance(doc_text, str) and len(doc_text) > max_doc_chars:
                                    doc_text = doc_text[:max_doc_chars]
                                request_context["document_text"] = doc_text
                                request_context["document_kind"] = extracted_doc_kind
                                request_context["document_used_ocr"] = extracted_doc_used_ocr
                            if wants_explain_answer:
                                request_context["explain_answer"] = True
                            
                            # Process with agent service (pass context)
                            message_id = uuid.uuid4().hex
                            if wants_stream:
                                # Notify client we started generating a response
                                await websocket.send_text(
                                    fast_json_dumps(
                                        {
                                            "type": "agent_start",
                                            "message_id": message_id,
                                            "agent": "Zava Media Orchestrator",
                                        }
                                    )
                                )

                            # Stream deltas when available; fall back to one-shot response.
                            full_text_parts: list[str] = []
                            agent_name: str = "Zava Media Assistant"
                            diagnostics: Any = None
                            result: Dict[str, Any] = {}
                            streamed_any_delta = False

                            if wants_stream and hasattr(orchestrator, "stream_request"):
                                async for event in orchestrator.stream_request(user_message, request_context):
                                    if not isinstance(event, dict):
                                        continue
                                    if event.get("type") == "delta":
                                        delta = str(event.get("delta") or "")
                                        if delta:
                                            full_text_parts.append(delta)
                                            streamed_any_delta = True
                                            await websocket.send_text(
                                                fast_json_dumps(
                                                    {
                                                        "type": "agent_delta",
                                                        "message_id": message_id,
                                                        "delta": delta,
                                                    }
                                                )
                                            )
                                    elif event.get("type") == "final":
                                        result = event.get("result") or {}
                                        agent_name = result.get("agent", agent_name)
                                        diagnostics = result.get("diagnostics")
                            else:
                                result = orchestrator.process_request(user_message, request_context)
                                diagnostics = result.get("diagnostics")
                                agent_name = result.get("agent", agent_name)
                                response_text = str(result.get("text") or "")
                                full_text_parts.append(response_text)

                            result_text = "".join(full_text_parts).strip() or result.get(
                                "text", "I processed your request but got an empty response."
                            )
                            
                            logger.info(f"[{connection_id}] Processing complete")
                            logger.info(f"[{connection_id}] Agent result: {str(result)[:200]}...")
                            
                            # Extract response from agent
                            response_text = result_text

                            # If the agent responded with an action payload, execute it immediately
                            # so parsing remains reliable and the UI can render the returned artifacts.
                            if isinstance(response_text, str):
                                parsed_action = _try_parse_action_dict(response_text)
                                if parsed_action:
                                    logger.info(
                                        f"[{connection_id}] Handling action from agent response: {parsed_action.get('action')}"
                                    )
                                    parsed_action.setdefault("explain", wants_explain)
                                    parsed_action.setdefault("explain_answer", wants_explain_answer)
                                    parsed_action.setdefault("include_oss", include_oss)
                                    action_response = await _handle_action(parsed_action)
                                    await websocket.send_text(fast_json_dumps(action_response))
                                    continue
                            
                            # Send response back to client
                            response = {
                                "type": "agent_response",
                                "message_id": message_id,
                                "agent": agent_name,
                                "diagnostics": diagnostics,
                                "image_data": result.get("image_data")
                            }

                            mode = "streamed" if (wants_stream and streamed_any_delta) else "non-streamed"
                            references: list[Dict[str, Any]] = [
                                {"type": "service", "value": "HybridAgentService"},
                                {"type": "agent", "value": agent_name},
                                {"type": "generation_mode", "value": mode},
                            ]
                            if isinstance(user_message, str):
                                references.append({"type": "user_message_chars", "value": len(user_message)})
                            if image_data:
                                references.append({"type": "user_image", "value": "attached"})
                            if isinstance(result, dict) and result.get("response_id"):
                                references.append({"type": "response_id", "value": result.get("response_id")})
                            if response.get("image_data"):
                                references.append({"type": "artifact", "value": "image_data"})
                            response["references"] = references

                            if wants_explain:
                                mode = "streamed" if (wants_stream and streamed_any_delta) else "non-streamed"
                                has_image = bool(image_data)
                                msg_len = len(user_message) if isinstance(user_message, str) else None
                                response["explanation"] = (
                                    "High-level explanation (not internal chain-of-thought):\n"
                                    f"1) I received your message ({msg_len} chars).\n"
                                    f"2) I detected an attached image: {'yes' if has_image else 'no'}.\n"
                                    "3) I routed the request to the Hybrid Agent Service (Azure AI Agent orchestrator).\n"
                                    f"4) I generated the assistant reply in {mode} mode and sent it back.\n"
                                    "\nIf you want a content-focused explanation too, ask for 'Explain the answer in 3 bullets'."
                                )

                            # Always include the final message body, even when deltas were streamed.
                            response["message"] = response_text
                            
                            logger.info(f"[{connection_id}] Sending response: {response_text[:100]}...")
                            await websocket.send_text(fast_json_dumps(response))
                            logger.info(f"[{connection_id}] Response sent successfully")
                            
                        except Exception as e:
                            logger.error(f"[{connection_id}] Error processing request: {e}", exc_info=True)
                            logger.error(f"[{connection_id}] Traceback: {traceback.format_exc()}")
                            
                            error_response = {
                                "type": "error",
                                "message": _format_standard_reply(
                                    answer="I hit an error while processing your request.",
                                    actions=["Attempted to process your message with the orchestrator"],
                                    next_steps=["Retry the request", "If it persists, check server logs/diagnostics"],
                                    assumptions=["This may be a transient error or configuration issue"],
                                ),
                                "details": traceback.format_exc(),
                            }
                            
                            await websocket.send_text(fast_json_dumps(error_response))
                
                except json.JSONDecodeError as e:
                    logger.error(f"[{connection_id}] JSON decode error: {e}", exc_info=True)
                    await websocket.send_text(fast_json_dumps({
                        "type": "error",
                        "message": _format_standard_reply(
                            answer="Invalid JSON payload.",
                            actions=["Parsed inbound WebSocket message"],
                            next_steps=["Send valid JSON", "Check for trailing commas/quotes"],
                            assumptions=["The client is sending JSON text frames"],
                        ),
                        "details": str(e),
                    }))
                except Exception as e:
                    logger.error(f"[{connection_id}] Unexpected error in message loop: {e}", exc_info=True)
                    await websocket.send_text(fast_json_dumps({
                        "type": "error",
                        "message": _format_standard_reply(
                            answer="An unexpected server error occurred while handling your message.",
                            actions=["Handled inbound WebSocket message"],
                            next_steps=["Retry the request", "Check server logs for details"],
                            assumptions=["This is an internal error"],
                        ),
                        "details": str(e),
                    }))
                    
    except WebSocketDisconnect:
        logger.info(f"[{connection_id}] WebSocket disconnected normally")
    except Exception as e:
        logger.error(f"[{connection_id}] WebSocket error: {e}", exc_info=True)
        try:
            await websocket.close()
        except:
            pass

