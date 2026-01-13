import os
import logging
import json
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

# Load environment variables
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

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """Serve the main interface"""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/diagnostics")
async def diagnostics():
    """Expose orchestrator diagnostic details for troubleshooting."""
    diagnostics_info = {
        "orchestrator_initialized": orchestrator is not None,
        "initialization_error": orchestrator_error,
        "service_mode": "hybrid_agent_service",
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
                    
                    user_message = message_data.get("message", "")
                    image_data = message_data.get("image", None)
                    
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

