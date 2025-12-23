"""
Enhanced Zava Shopping Assistant with A2A Protocol

This is the main application entry point that integrates the A2A protocol
with the existing Zava shopping assistant, providing both legacy and
enhanced multi-agent capabilities.

Key frameworks and technologies used:
- FastAPI: Modern, fast web framework for building APIs with Python, featuring
  automatic OpenAPI documentation, dependency injection, and async support
- Pydantic: Data validation and serialization library using Python type annotations
- Uvicorn: Lightning-fast ASGI server implementation for serving Python web applications
- AsyncIO: Python's built-in library for writing concurrent code using async/await syntax
- A2A Protocol: Agent-to-Agent communication protocol enabling multi-agent coordination
- WebSockets: Real-time bidirectional communication protocol for live chat features
- CORS: Cross-Origin Resource Sharing middleware for secure web API access
"""
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import Dict, Any

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import httpx
import uvicorn

# Add project root to path for imports
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from a2a.config import get_global_config, ServerMode, setup_logging
from a2a.api import A2AChatRouter, A2AServerRouter
from a2a.server import A2AStarletteApplication, DefaultRequestHandler
from a2a.agent import EnhancedProductManagementAgent
from a2a.types import AgentCard, AgentCapabilities, AgentSkill

# Import legacy components for hybrid mode
try:
    from chat_app_multi_agent import app as legacy_app
    from chat_app_multi_agent import websocket_endpoint as legacy_websocket
    LEGACY_AVAILABLE = True
except ImportError:
    LEGACY_AVAILABLE = False
    legacy_app = None
    legacy_websocket = None


logger = logging.getLogger(__name__)


@asynccontextmanager
async def app_lifespan(app: FastAPI):
    """Application lifespan manager"""
    config = get_global_config()
    logger.info(f"Starting Enhanced Zava Shopping Assistant in {config.a2a.mode} mode")
    
    # Log configuration summary
    logger.info(f"Enabled agents: {config.get_effective_agents()}")
    logger.info(f"Server endpoint: http://{config.a2a.host}:{config.a2a.port}")
    
    # Setup global HTTP client
    app.state.httpx_client = httpx.AsyncClient()
    
    yield
    
    # Cleanup
    logger.info("Shutting down Enhanced Zava Shopping Assistant")
    await app.state.httpx_client.aclose()


def create_app() -> FastAPI:
    """Create and configure the FastAPI application"""
    config = get_global_config()
    
    # Create FastAPI app
    app = FastAPI(
        title="Enhanced Zava Shopping Assistant",
        description="Multi-agent shopping assistant using A2A protocol",
        version="1.0.0",
        debug=config.a2a.debug,
        lifespan=app_lifespan
    )
    
    # Add CORS middleware
    if config.a2a.cors_enabled:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.a2a.allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    # Initialize HTTP client for dependency injection
    httpx_client = httpx.AsyncClient()
    
    # Setup A2A components if enabled
    if config.a2a.mode in [ServerMode.A2A, ServerMode.HYBRID]:
        logger.info("Setting up A2A protocol endpoints")
        
        # Initialize A2A routers
        chat_router = A2AChatRouter()
        server_router = A2AServerRouter()
        
        # Add A2A routers
        app.include_router(chat_router.get_router())
        app.include_router(server_router.get_router())
        
        # Store references for lifecycle management
        app.state.chat_router = chat_router
        app.state.server_router = server_router
        
        logger.info("Enhanced A2A routers initialized with advanced UX features")
        
        # Add A2A WebSocket endpoint (handled by enhanced router)
        # The enhanced router already includes WebSocket support
    
    # Setup legacy endpoints if enabled
    if config.a2a.mode in [ServerMode.LEGACY, ServerMode.HYBRID] and LEGACY_AVAILABLE:
        logger.info("Setting up legacy endpoints")
        
        # Mount legacy WebSocket endpoint
        @app.websocket(config.a2a.legacy_websocket_path)
        async def legacy_websocket_endpoint(websocket: WebSocket):
            """Legacy WebSocket endpoint"""
            await legacy_websocket(websocket)
        
        # Add legacy health endpoint if not conflicting
        if config.a2a.mode == ServerMode.LEGACY:
            @app.get("/health")
            async def legacy_health():
                return {
                    "status": "healthy",
                    "mode": "legacy",
                    "service": "Zava AI Shopping Assistant"
                }
    
    # Add root endpoint
    @app.get("/")
    async def root():
        """Root endpoint with mode information"""
        config = get_global_config()
        
        if config.a2a.mode == ServerMode.A2A:
            # Enhanced A2A mode with advanced UX features
            return {
                "message": "Enhanced Zava Shopping Assistant (A2A Mode)",
                "features": [
                    "Agent-to-Agent Protocol",
                    "Multi-Agent Coordination", 
                    "Real-time Streaming",
                    "Enhanced UX with Typing Indicators",
                    "Rate Limiting & Connection Management",
                    "Performance Monitoring",
                    "Message History & Session Management"
                ],
                "endpoints": {
                    "chat": "/a2a/chat/message",
                    "streaming": "/a2a/chat/stream", 
                    "websocket": "/a2a/chat/ws",
                    "stats": "/a2a/chat/stats",
                    "health": "/a2a/chat/health",
                    "api_docs": "/a2a/api/docs"
                }
            }
        elif config.a2a.mode == ServerMode.LEGACY:
            return {
                "message": "Zava AI Shopping Assistant (Legacy Mode)",
                "websocket_endpoint": config.a2a.legacy_websocket_path,
                "health_endpoint": "/health"
            }
        else:  # HYBRID
            return {
                "message": "Enhanced Zava Shopping Assistant (Hybrid Mode)",
                "features": [
                    "Legacy Zava Agents",
                    "Enhanced A2A Protocol Support", 
                    "Advanced UX Features",
                    "Intelligent Agent Routing",
                    "Performance Monitoring",
                    "Real-time Communication"
                ],
                "endpoints": {
                    "legacy_chat": config.a2a.legacy_websocket_path,
                    "enhanced_a2a_chat": "/a2a/chat/message",
                    "streaming": "/a2a/chat/stream",
                    "websocket": "/a2a/chat/ws", 
                    "stats": "/a2a/chat/stats",
                    "connections": "/a2a/chat/connections",
                    "api_docs": "/a2a/api/docs"
                }
            }
    
    # Add combined health endpoint for hybrid mode
    if config.a2a.mode == ServerMode.HYBRID:
        @app.get("/health")
        async def hybrid_health():
            return {
                "status": "healthy",
                "mode": "hybrid",
                "services": {
                    "a2a": "available",
                    "legacy": "available" if LEGACY_AVAILABLE else "unavailable"
                },
                "endpoints": {
                    "a2a_chat": "/a2a/chat/",
                    "a2a_server": "/a2a/server/",
                    "a2a_websocket": "/a2a/ws",
                    "legacy_websocket": config.a2a.legacy_websocket_path
                }
            }
    
    # Setup static files if enabled
    if config.a2a.static_files_enabled:
        static_path = config.a2a.get_static_files_path()
        if os.path.exists(static_path):
            app.mount("/static", StaticFiles(directory=static_path), name="static")
            logger.info(f"Mounted static files from: {static_path}")
        else:
            logger.warning(f"Static files directory not found: {static_path}")
    
    # Setup templates if available
    template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
    if os.path.exists(template_dir):
        templates = Jinja2Templates(directory=template_dir)
        
        @app.get("/ui", response_class=HTMLResponse)
        async def ui_endpoint(request):
            """Serve UI template"""
            return templates.TemplateResponse("index.html", {"request": request})
    
    logger.info(f"FastAPI application created in {config.a2a.mode} mode")
    return app


def main():
    """Main entry point for the application"""
    # Load configuration
    config = get_global_config()
    
    # Create app
    app = create_app()
    
    # Run server
    uvicorn.run(
        app,
        host=config.a2a.host,
        port=config.a2a.port,
        log_level=config.a2a.log_level.lower(),
        reload=config.a2a.debug,
        access_log=config.a2a.debug
    )


if __name__ == "__main__":
    main()


# Export for external usage
__all__ = ["create_app", "main"]