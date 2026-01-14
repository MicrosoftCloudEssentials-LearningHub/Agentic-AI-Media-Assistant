import os
import logging
import json
import asyncio
import traceback
from typing import Any, Dict, Optional
from datetime import datetime
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File
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

# Load environment variables
if not is_running_in_azure():
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
if os.path.exists(static_dir) and os.listdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    logger.info(f"Static files mounted from {static_dir}")

# Mount templates
templates = Jinja2Templates(directory="app/templates")

orchestrator_error: Optional[str] = None
direct_model_service: Optional[DirectModelService] = None

try:
    # Use Azure AI Agents - let them handle everything
    logger.info("Initializing Hybrid Agent Service (Azure AI Agents + Fallback)")
    orchestrator = HybridAgentService()
    
    if orchestrator.orchestrator_agent:
        logger.info("✅ Agent service initialized successfully")
        logger.info(f"  - Orchestrator Agent ID: {orchestrator.orchestrator_agent.id}")
        logger.info("  - Orchestrator handles all routing with AI-powered decision making")
    else:
        logger.warning("⚠️ Agent service initialized with fallback mode")
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
    # Fast path: only attempt JSON parse when it looks like an object
    if not (candidate.lstrip().startswith("{") and candidate.rstrip().endswith("}")):
        return None
    try:
        obj = json.loads(candidate)
    except Exception:
        return None
    if isinstance(obj, dict) and obj.get("action"):
        return obj
    return None


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


async def _ensure_direct_model_service() -> DirectModelService:
    global direct_model_service
    if direct_model_service is None:
        direct_model_service = DirectModelService()
    return direct_model_service


async def _handle_action(action_payload: Dict[str, Any]) -> Dict[str, Any]:
    """Execute a tool-like action payload and return a websocket response dict."""
    action = (action_payload.get("action") or "").strip()
    prompt = (action_payload.get("prompt") or "").strip()

    if not action:
        return {
            "type": "error",
            "message": "Missing 'action' in request payload.",
            "diagnostics": {"action_payload": action_payload},
        }

    if action in {"call_flux", "call_flux_kontext", "call_sora"} and not prompt:
        return {
            "type": "error",
            "message": f"Missing 'prompt' for action '{action}'.",
            "diagnostics": {"action_payload": action_payload},
        }

    # Optional parameters
    size = (action_payload.get("size") or "1024x1024").strip()
    duration = action_payload.get("duration")
    resolution = (action_payload.get("resolution") or "1920x1080").strip()

    svc = await _ensure_direct_model_service()

    try:
        if action == "call_flux":
            # FLUX.2-pro (West US 3)
            result = await asyncio.to_thread(svc.generate_image_flux2, prompt, size)
            image_data = _extract_first_media_url_or_data_uri(result)
            if not image_data:
                return {
                    "type": "error",
                    "message": "FLUX did not return an image.",
                    "diagnostics": {"action": action, "result": result},
                }
            return {
                "type": "agent_response",
                "agent": "FLUX.2-pro",
                "message": "Generated image with FLUX.2-pro.",
                "image_data": image_data,
                "diagnostics": {"action": action, "model": result.get("model"), "size": size},
            }

        if action == "call_flux_kontext":
            # FLUX.1-Kontext-pro (Sweden Central)
            result = await asyncio.to_thread(svc.generate_image_flux1, prompt, size)
            image_data = _extract_first_media_url_or_data_uri(result)
            if not image_data:
                return {
                    "type": "error",
                    "message": "FLUX Kontext did not return an image.",
                    "diagnostics": {"action": action, "result": result},
                }
            return {
                "type": "agent_response",
                "agent": "FLUX.1-Kontext-pro",
                "message": "Generated image with FLUX.1-Kontext-pro.",
                "image_data": image_data,
                "diagnostics": {"action": action, "model": result.get("model"), "size": size},
            }

        if action == "call_sora":
            # Sora video generation (UI currently doesn't render video, so return URL in message)
            resolved_duration = 10
            if isinstance(duration, (int, float)) and duration > 0:
                resolved_duration = int(duration)
            result = await asyncio.to_thread(svc.generate_video_sora, prompt, resolved_duration, resolution)
            media_url = _extract_first_media_url_or_data_uri(result)
            message = "Generated video with Sora." if media_url else "Sora request completed, but no URL was returned."
            if media_url:
                message += f"\nURL: {media_url}"
            return {
                "type": "agent_response",
                "agent": "Sora",
                "message": message,
                "diagnostics": {"action": action, "model": result.get("model"), "resolution": resolution, "duration": resolved_duration},
            }

        return {
            "type": "error",
            "message": f"Unknown action: {action}",
            "diagnostics": {"action_payload": action_payload},
        }

    except Exception as e:
        logger.error(f"Action execution failed ({action}): {e}", exc_info=True)
        return {
            "type": "error",
            "message": f"Action execution failed: {str(e)}",
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
async def upload_image(file: UploadFile = File(...)):
    """Handle image upload"""
    try:
        contents = await file.read()
        # In a real app, save to disk or cloud storage
        # For now, we'll just return success and maybe a base64 representation or ID
        import base64
        encoded = base64.b64encode(contents).decode("utf-8")
        return JSONResponse(content={"status": "success", "image_data": f"data:{file.content_type};base64,{encoded}"})
    except Exception as e:
        return JSONResponse(content={"status": "error", "message": str(e)}, status_code=500)

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
            
            while True:
                try:
                    # Receive message from client
                    data = await websocket.receive_text()
                    logger.debug(f"[{connection_id}] Received data: {data[:200]}...")
                    
                    message_data = json.loads(data)

                    # Support both the standard UI payload and tool/action payloads
                    user_message = message_data.get("message", "")
                    image_data = message_data.get("image", None)

                    # If client sent an explicit action payload, handle it directly
                    if isinstance(message_data, dict) and message_data.get("action"):
                        logger.info(f"[{connection_id}] Handling direct action: {message_data.get('action')}")
                        action_response = await _handle_action(message_data)
                        await websocket.send_text(fast_json_dumps(action_response))
                        continue

                    # If the user typed/pasted an action JSON blob into the chat, handle it
                    if isinstance(user_message, str):
                        parsed_action = _try_parse_action_dict(user_message)
                        if parsed_action:
                            logger.info(f"[{connection_id}] Handling action from user message: {parsed_action.get('action')}")
                            action_response = await _handle_action(parsed_action)
                            await websocket.send_text(fast_json_dumps(action_response))
                            continue
                    
                    logger.info(f"[{connection_id}] User message: {user_message[:100]}...")
                    if image_data:
                        logger.info(f"[{connection_id}] Image data received: {len(image_data)} chars")
                    
                    if not user_message and not image_data:
                        logger.warning(f"[{connection_id}] Empty message received, skipping")
                        continue
                    
                    # Check if orchestrator is available
                    if not orchestrator:
                        error_msg = {
                            "type": "error",
                            "message": f"Agent service is currently unavailable. Please check configuration.\n\n[DEBUG]\nError: {orchestrator_error}",
                            "details": orchestrator_error,
                            "diagnostics": {
                                "orchestrator_initialized": False,
                                "error": orchestrator_error
                            }
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
                            
                            # Process with agent service (pass context)
                            result = orchestrator.process_request(user_message, request_context)
                            
                            logger.info(f"[{connection_id}] Processing complete")
                            logger.info(f"[{connection_id}] Agent result: {str(result)[:200]}...")
                            
                            # Extract response from agent
                            response_text = result.get("text", "I processed your request but got an empty response.")
                            agent_name = result.get("agent", "Zava Media Assistant")

                            # If the agent responded with an action payload, execute it
                            if isinstance(response_text, str):
                                parsed_action = _try_parse_action_dict(response_text)
                                if parsed_action:
                                    logger.info(f"[{connection_id}] Handling action from agent response: {parsed_action.get('action')}")
                                    action_response = await _handle_action(parsed_action)
                                    await websocket.send_text(fast_json_dumps(action_response))
                                    continue
                            
                            # Send response back to client
                            response = {
                                "type": "agent_response",
                                "agent": agent_name,
                                "message": response_text,
                                "diagnostics": result.get("diagnostics"),
                                "image_data": result.get("image_data")
                            }
                            
                            logger.info(f"[{connection_id}] Sending response: {response_text[:100]}...")
                            await websocket.send_text(fast_json_dumps(response))
                            logger.info(f"[{connection_id}] Response sent successfully")
                            
                        except Exception as e:
                            error_msg = f"Error processing request: {str(e)}"
                            logger.error(f"[{connection_id}] {error_msg}", exc_info=True)
                            logger.error(f"[{connection_id}] Traceback: {traceback.format_exc()}")
                            
                            error_response = {
                                "type": "error",
                                "message": error_msg,
                                "details": traceback.format_exc()
                            }
                            
                            await websocket.send_text(fast_json_dumps(error_response))
                
                except json.JSONDecodeError as e:
                    logger.error(f"[{connection_id}] JSON decode error: {e}", exc_info=True)
                    await websocket.send_text(fast_json_dumps({
                        "type": "error",
                        "message": f"Invalid JSON: {str(e)}"
                    }))
                except Exception as e:
                    logger.error(f"[{connection_id}] Unexpected error in message loop: {e}", exc_info=True)
                    await websocket.send_text(fast_json_dumps({
                        "type": "error",
                        "message": f"Unexpected error: {str(e)}"
                    }))
                    
    except WebSocketDisconnect:
        logger.info(f"[{connection_id}] WebSocket disconnected normally")
    except Exception as e:
        logger.error(f"[{connection_id}] WebSocket error: {e}", exc_info=True)
        try:
            await websocket.close()
        except:
            pass

