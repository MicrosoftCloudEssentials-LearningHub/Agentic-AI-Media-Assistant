"""
A2A Starlette Application

This module provides the Starlette application implementation for the A2A protocol.
It handles agent cards, routing, and HTTP request processing.
"""
import json
import logging
from typing import Any, Dict, Optional
from starlette.applications import Starlette
from starlette.routing import Route, Mount
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.requests import Request
from starlette.middleware.cors import CORSMiddleware
from starlette.staticfiles import StaticFiles

from .request_handlers import DefaultRequestHandler
from ..types import AgentCard


logger = logging.getLogger(__name__)


class A2AStarletteApplication:
    """
    Starlette application wrapper for A2A protocol.
    
    Provides HTTP endpoints for agent interaction and discovery.
    """
    
    def __init__(
        self,
        agent_card: AgentCard,
        http_handler: DefaultRequestHandler,
        static_dir: Optional[str] = None,
        cors_enabled: bool = True
    ):
        self.agent_card = agent_card
        self.http_handler = http_handler
        self.static_dir = static_dir
        self.cors_enabled = cors_enabled
        self._app = None
    
    def build(self) -> Starlette:
        """Build and return the Starlette application"""
        if self._app is not None:
            return self._app
        
        # Define routes
        routes = [
            Route("/", self._agent_card_endpoint, methods=["GET"]),
            Route("/health", self._health_endpoint, methods=["GET"]),
            Route("/tasks/send", self._send_task_endpoint, methods=["POST"]),
            Route("/tasks/stream", self._stream_task_endpoint, methods=["POST"]),
            Route("/tasks/{task_id}", self._get_task_endpoint, methods=["GET"]),
            Route("/contexts/{context_id}", self._get_context_endpoint, methods=["GET"]),
            Route("/contexts/{context_id}", self._clear_context_endpoint, methods=["DELETE"]),
            Route("/stats", self._stats_endpoint, methods=["GET"]),
        ]
        
        # Add static files if directory provided
        if self.static_dir:
            routes.append(Mount("/static", StaticFiles(directory=self.static_dir), name="static"))
        
        # Create Starlette app
        self._app = Starlette(routes=routes, debug=True)
        
        # Add CORS middleware if enabled
        if self.cors_enabled:
            self._app.add_middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )
        
        logger.info(f"A2A Starlette application built for agent: {self.agent_card.name}")
        return self._app
    
    async def _agent_card_endpoint(self, request: Request) -> JSONResponse:
        """Return the agent card information"""
        return JSONResponse({
            "agent_card": self.agent_card.model_dump(),
            "timestamp": None,  # Add current timestamp if needed
            "version": "1.0.0"
        })
    
    async def _health_endpoint(self, request: Request) -> JSONResponse:
        """Health check endpoint"""
        return JSONResponse({
            "status": "healthy",
            "agent": self.agent_card.name,
            "version": self.agent_card.version,
            "timestamp": None,  # Add current timestamp if needed
            "active_requests": len(await self.http_handler.get_active_requests())
        })
    
    async def _send_task_endpoint(self, request: Request) -> JSONResponse:
        """Handle task send requests (non-streaming)"""
        try:
            data = await request.json()
            
            # Extract request parameters
            message = data.get("message", "")
            session_id = data.get("session_id", "default")
            user_id = data.get("user_id")
            context_id = data.get("context_id")
            additional_data = data.get("additional_data", {})
            
            if not message:
                return JSONResponse(
                    {"error": "Message is required"},
                    status_code=400
                )
            
            # Handle request
            request_context = await self.http_handler.handle_request(
                user_message=message,
                session_id=session_id,
                user_id=user_id,
                context_id=context_id,
                additional_data=additional_data
            )
            
            # Wait for completion (with timeout)
            # Note: In a real implementation, you might want to implement
            # a proper mechanism to wait for task completion
            import asyncio
            await asyncio.sleep(0.1)  # Brief wait to allow processing
            
            return JSONResponse({
                "task_id": request_context.current_task.id if request_context.current_task else None,
                "context_id": request_context.task_context.id,
                "status": "accepted",
                "message": "Task has been queued for processing"
            })
            
        except Exception as e:
            logger.error(f"Error in send task endpoint: {e}")
            return JSONResponse(
                {"error": f"Failed to process request: {str(e)}"},
                status_code=500
            )
    
    async def _stream_task_endpoint(self, request: Request) -> PlainTextResponse:
        """Handle streaming task requests"""
        try:
            data = await request.json()
            
            # Extract request parameters
            message = data.get("message", "")
            session_id = data.get("session_id", "default")
            user_id = data.get("user_id")
            context_id = data.get("context_id")
            additional_data = data.get("additional_data", {})
            
            if not message:
                return PlainTextResponse(
                    "data: {\"error\": \"Message is required\"}\n\n",
                    status_code=400
                )
            
            async def stream_generator():
                """Generate streaming response"""
                try:
                    # Handle request
                    request_context = await self.http_handler.handle_request(
                        user_message=message,
                        session_id=session_id,
                        user_id=user_id,
                        context_id=context_id,
                        additional_data=additional_data
                    )
                    
                    # Set up event subscription for this context
                    events = []
                    
                    def event_callback(event):
                        events.append(event)
                    
                    # Subscribe to events for this context
                    self.http_handler.event_queue.subscribe_to_context(
                        request_context.task_context.id,
                        event_callback
                    )
                    
                    # Stream events as they arrive
                    processed_events = set()
                    timeout_counter = 0
                    max_timeout = 300  # 30 seconds (300 * 0.1s)
                    
                    while timeout_counter < max_timeout:
                        # Check for new events
                        new_events = [e for e in events if e.id not in processed_events]
                        
                        for event in new_events:
                            processed_events.add(event.id)
                            
                            # Format event as SSE
                            event_data = {
                                "type": event.type,
                                "context_id": event.contextId,
                                "timestamp": event.timestamp.isoformat()
                            }
                            
                            # Add event-specific data
                            if hasattr(event, 'status') and event.status:
                                event_data["status"] = event.status.state
                                if event.status.message:
                                    event_data["content"] = event.status.message.content
                                    event_data["is_complete"] = event.final if hasattr(event, 'final') else False
                            
                            if hasattr(event, 'artifact') and event.artifact:
                                event_data["artifact"] = {
                                    "name": event.artifact.name,
                                    "type": event.artifact.artifact_type,
                                    "content": event.artifact.content
                                }
                            
                            yield f"data: {json.dumps(event_data)}\n\n"
                            
                            # Check if this is a final event
                            if hasattr(event, 'final') and event.final:
                                return
                        
                        # Wait briefly before checking again
                        import asyncio
                        await asyncio.sleep(0.1)
                        timeout_counter += 1
                    
                    # Timeout reached
                    yield f"data: {{\"error\": \"Request timeout\"}}\n\n"
                    
                except Exception as e:
                    logger.error(f"Error in stream generator: {e}")
                    yield f"data: {{\"error\": \"{str(e)}\"}}\n\n"
            
            return PlainTextResponse(
                stream_generator(),
                media_type="text/plain",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Access-Control-Allow-Origin": "*",
                }
            )
            
        except Exception as e:
            logger.error(f"Error in stream task endpoint: {e}")
            return PlainTextResponse(
                f"data: {{\"error\": \"{str(e)}\"}}\n\n",
                status_code=500
            )
    
    async def _get_task_endpoint(self, request: Request) -> JSONResponse:
        """Get information about a specific task"""
        task_id = request.path_params["task_id"]
        
        try:
            task = await self.http_handler.task_store.get_task(task_id)
            
            if not task:
                return JSONResponse(
                    {"error": "Task not found"},
                    status_code=404
                )
            
            return JSONResponse({
                "task": {
                    "id": task.id,
                    "context_id": task.contextId,
                    "title": task.title,
                    "description": task.description,
                    "state": task.state,
                    "priority": task.priority,
                    "assigned_agent": task.assigned_agent,
                    "created_at": task.created_at.isoformat(),
                    "updated_at": task.updated_at.isoformat(),
                    "metadata": task.metadata
                }
            })
            
        except Exception as e:
            logger.error(f"Error getting task {task_id}: {e}")
            return JSONResponse(
                {"error": f"Failed to get task: {str(e)}"},
                status_code=500
            )
    
    async def _get_context_endpoint(self, request: Request) -> JSONResponse:
        """Get context information and history"""
        context_id = request.path_params["context_id"]
        limit = int(request.query_params.get("limit", "50"))
        
        try:
            context_data = await self.http_handler.get_context_history(context_id, limit)
            
            if "error" in context_data:
                return JSONResponse(
                    {"error": context_data["error"]},
                    status_code=404
                )
            
            return JSONResponse(context_data)
            
        except Exception as e:
            logger.error(f"Error getting context {context_id}: {e}")
            return JSONResponse(
                {"error": f"Failed to get context: {str(e)}"},
                status_code=500
            )
    
    async def _clear_context_endpoint(self, request: Request) -> JSONResponse:
        """Clear a context and all associated data"""
        context_id = request.path_params["context_id"]
        
        try:
            success = await self.http_handler.clear_context(context_id)
            
            if not success:
                return JSONResponse(
                    {"error": "Context not found"},
                    status_code=404
                )
            
            return JSONResponse({
                "message": f"Context {context_id} cleared successfully"
            })
            
        except Exception as e:
            logger.error(f"Error clearing context {context_id}: {e}")
            return JSONResponse(
                {"error": f"Failed to clear context: {str(e)}"},
                status_code=500
            )
    
    async def _stats_endpoint(self, request: Request) -> JSONResponse:
        """Get agent and system statistics"""
        try:
            # Get agent stats
            agent_stats = self.http_handler.agent_executor.get_stats() if hasattr(self.http_handler.agent_executor, 'get_stats') else {}
            
            # Get event queue stats
            event_stats = await self.http_handler.event_queue.get_queue_stats()
            
            # Get active requests
            active_requests = await self.http_handler.get_active_requests()
            
            return JSONResponse({
                "agent": {
                    "name": self.agent_card.name,
                    "version": self.agent_card.version,
                    "stats": agent_stats
                },
                "system": {
                    "active_requests": len(active_requests),
                    "event_queue": event_stats
                }
            })
            
        except Exception as e:
            logger.error(f"Error getting stats: {e}")
            return JSONResponse(
                {"error": f"Failed to get stats: {str(e)}"},
                status_code=500
            )