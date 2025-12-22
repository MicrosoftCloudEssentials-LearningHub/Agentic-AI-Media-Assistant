import os
import logging
import json
from typing import Any, Dict, Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, UploadFile, File
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv
import orjson

from app.agents.agent_processor import AgentProcessor

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

logger.info("Starting Zava Media AI Assistant...")
logger.info(f"Current working directory: {os.getcwd()}")
logger.info(f"Files in current directory: {os.listdir('.')}")

# Initialize FastAPI app
app = FastAPI(title="Zava Media AI Assistant")

# Health check endpoint (required for App Service)
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "Zava Media AI Assistant"}

# Mount static files only if directory exists and has content
static_dir = "app/static"
if os.path.exists(static_dir) and os.listdir(static_dir):
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Mount templates
templates = Jinja2Templates(directory="app/templates")

try:
    # Initialize Orchestrator - Use real Azure AI agents
    # AgentProcessor will use AGENT_ORCHESTRATOR_ID from environment by default
    orchestrator = AgentProcessor()
    logger.info("Orchestrator initialized successfully")

except Exception as e:
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
    await websocket.accept()
    logger.info("WebSocket connection established")
    
    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()
            message_data = json.loads(data)
            
            user_message = message_data.get("message", "")
            image_data = message_data.get("image", None)
            
            if not user_message and not image_data:
                continue
            
            # Check if orchestrator is available
            if not orchestrator:
                await websocket.send_text(fast_json_dumps({
                    "type": "error",
                    "message": "Agent service is currently unavailable. Please check configuration."
                }))
                continue
                
            # Process with Orchestrator
            try:
                # Build context with image data if provided
                additional_context = {}
                if image_data:
                    additional_context["image_data"] = image_data
                
                # Stream response from agent
                response_text = ""
                for chunk in orchestrator.run_conversation_with_text_stream(
                    user_message=user_message,
                    additional_context=additional_context if additional_context else None
                ):
                    response_text += chunk
                
                # Send complete response back
                response = {
                    "type": "agent_response",
                    "agent": "Zava Media Assistant",
                    "message": response_text,
                    "image_data": None  # Image processing would happen via agents
                }
                await websocket.send_text(fast_json_dumps(response))
                
            except Exception as e:
                logger.error(f"Error processing request: {e}", exc_info=True)
                await websocket.send_text(fast_json_dumps({
                    "type": "error",
                    "message": f"An error occurred: {str(e)}"
                }))
                
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close()
        except:
            pass
