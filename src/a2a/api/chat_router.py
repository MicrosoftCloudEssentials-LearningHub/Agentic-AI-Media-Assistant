"""
FastAPI Chat Router for A2A Protocol

This module provides FastAPI routers and endpoints that expose A2A protocol
functionality with proper streaming support and integration with the existing
chat application.
"""
import asyncio
import json
import uuid
import logging
import time
from typing import Any, Dict, List, Optional
from collections import defaultdict
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from datetime import datetime, timedelta

from ..agent.coordinator import EnhancedProductManagementAgent
from ..server import (
    DefaultRequestHandler, InMemoryTaskStore, InMemoryPushNotificationConfigStore,
    BasePushNotificationSender, get_global_event_queue
)
from ..types import ChatRequest, ChatResponse, TaskState, EventType
import httpx


logger = logging.getLogger(__name__)


# Request/Response Models
class ChatMessage(BaseModel):
    """Chat message model"""
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    context_id: Optional[str] = None
    streaming: bool = True
    metadata: Dict[str, Any] = {}


class ChatResponseModel(BaseModel):
    """Chat response model"""
    task_id: Optional[str] = None
    context_id: str
    agent_id: str
    content: str
    is_complete: bool = False
    requires_input: bool = False
    artifacts: List[Dict[str, Any]] = []
    metadata: Dict[str, Any] = {}


class A2AChatRouter:
    """
    FastAPI router for A2A protocol chat functionality.
    
    Provides endpoints for both traditional REST and WebSocket communication,
    with proper integration to the A2A protocol infrastructure.
    """
    
    def __init__(self, httpx_client: Optional[httpx.AsyncClient] = None):
        self.router = APIRouter(prefix="/a2a/chat", tags=["a2a-chat"])
        self.httpx_client = httpx_client or httpx.AsyncClient()
        
        # Initialize A2A components
        self.agent = EnhancedProductManagementAgent()
        self.event_queue = get_global_event_queue()
        self.task_store = InMemoryTaskStore()
        self.push_config_store = InMemoryPushNotificationConfigStore()
        self.push_sender = BasePushNotificationSender(
            self.httpx_client, 
            self.push_config_store
        )
        
        self.request_handler = DefaultRequestHandler(
            agent_executor=self.agent,
            task_store=self.task_store,
            push_config_store=self.push_config_store,
            push_sender=self.push_sender
        )
        
        # In-memory session storage (use Redis in production)
        self.active_sessions: Dict[str, Dict[str, Any]] = {}
        
        # Register routes
        self._register_routes()
        
        logger.info("A2A Chat Router initialized")
    
    def _register_routes(self):
        """Register all chat routes"""
        
        @self.router.post("/message", response_model=ChatResponseModel)
        async def send_message(chat_message: ChatMessage):
            """Send a message using A2A protocol (non-streaming)"""
            return await self._handle_message(chat_message, streaming=False)
        
        @self.router.post("/stream")
        async def stream_message(chat_message: ChatMessage):
            """Send a message using A2A protocol (streaming)"""
            return await self._handle_message(chat_message, streaming=True)
        
        @self.router.get("/sessions")
        async def get_active_sessions():
            """Get list of active chat sessions"""
            return {
                "active_sessions": list(self.active_sessions.keys()),
                "session_count": len(self.active_sessions)
            }
        
        @self.router.get("/sessions/{session_id}")
        async def get_session_info(session_id: str):
            """Get information about a specific session"""
            if session_id not in self.active_sessions:
                raise HTTPException(status_code=404, detail="Session not found")
            
            session_data = self.active_sessions[session_id]
            return {
                "session_id": session_id,
                "created_at": session_data.get("created_at"),
                "context_id": session_data.get("context_id"),
                "message_count": session_data.get("message_count", 0),
                "last_activity": session_data.get("last_activity")
            }
        
        @self.router.delete("/sessions/{session_id}")
        async def clear_session(session_id: str):
            """Clear a specific chat session"""
            if session_id not in self.active_sessions:
                raise HTTPException(status_code=404, detail="Session not found")
            
            session_data = self.active_sessions[session_id]
            context_id = session_data.get("context_id")
            
            # Clear context if it exists
            if context_id:
                await self.request_handler.clear_context(context_id)
            
            del self.active_sessions[session_id]
            
            return {"message": f"Session {session_id} cleared"}
        
        @self.router.get("/contexts/{context_id}/history")
        async def get_context_history(context_id: str, limit: int = 50):
            """Get conversation history for a context"""
            try:
                history = await self.request_handler.get_context_history(context_id, limit)
                return history
            except Exception as e:
                logger.error(f"Error getting context history: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.router.get("/tasks/{task_id}")
        async def get_task_status(task_id: str):
            """Get status of a specific task"""
            try:
                task = await self.task_store.get_task(task_id)
                if not task:
                    raise HTTPException(status_code=404, detail="Task not found")
                
                return {
                    "task_id": task.id,
                    "context_id": task.contextId,
                    "state": task.state,
                    "title": task.title,
                    "assigned_agent": task.assigned_agent,
                    "created_at": task.created_at.isoformat(),
                    "updated_at": task.updated_at.isoformat(),
                    "metadata": task.metadata
                }
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Error getting task status: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.router.get("/agent/capabilities")
        async def get_agent_capabilities():
            """Get capabilities of the A2A agent system"""
            try:
                capabilities = await self.agent.get_agent_capabilities()
                stats = self.agent.get_stats()
                
                return {
                    "capabilities": capabilities,
                    "stats": stats,
                    "available_domains": list(capabilities.keys()) if capabilities else []
                }
            except Exception as e:
                logger.error(f"Error getting agent capabilities: {e}")
                raise HTTPException(status_code=500, detail=str(e))
        
        @self.router.get("/stats")
        async def get_chat_stats():
            """Get chat system statistics"""
            return {
                "status": "healthy",
                "statistics": self._get_connection_stats(),
                "agents": {
                    "available": len(await self.agent.get_available_agents()),
                    "coordinator_stats": self.agent.get_stats()
                },
                "rate_limiting": {
                    "max_requests_per_minute": self.max_requests_per_minute,
                    "active_clients": len(self.request_counts)
                }
            }
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket endpoint for real-time A2A chat"""
            await self._handle_websocket(websocket)
    
    async def _handle_message(
        self, 
        chat_message: ChatMessage, 
        streaming: bool = True
    ):
        """Handle chat message with A2A protocol"""
        try:
            # Generate session ID if not provided
            session_id = chat_message.session_id or str(uuid.uuid4())
            
            # Update session tracking
            await self._update_session(session_id, chat_message.context_id)
            
            if streaming:
                return await self._handle_streaming_message(chat_message, session_id)
            else:
                return await self._handle_sync_message(chat_message, session_id)
                
        except Exception as e:
            logger.error(f"Error handling message: {e}")
            if streaming:
                async def error_stream():
                    yield f'data: {{"error": "{str(e)}"}}\n\n'
                
                return StreamingResponse(
                    error_stream(),
                    media_type="text/plain",
                    headers=self._get_sse_headers()
                )
            else:
                raise HTTPException(status_code=500, detail=str(e))
    
    async def _handle_sync_message(
        self, 
        chat_message: ChatMessage, 
        session_id: str
    ) -> ChatResponseModel:
        """Handle non-streaming message"""
        # Create request context
        request_context = await self.request_handler.handle_request(
            user_message=chat_message.message,
            session_id=session_id,
            user_id=chat_message.user_id,
            context_id=chat_message.context_id,
            additional_data=chat_message.metadata
        )
        
        # Wait for completion (with timeout)
        timeout_seconds = 30
        start_time = datetime.utcnow()
        
        while (datetime.utcnow() - start_time).total_seconds() < timeout_seconds:
            # Check for completed events
            events = await self.event_queue.get_events_for_context(
                request_context.task_context.id,
                event_types=[EventType.task_status_update],
                limit=10
            )
            
            for event in events:
                if (hasattr(event, 'final') and event.final and 
                    hasattr(event, 'status') and event.status):
                    
                    # Found final status
                    content = ""
                    if event.status.message:
                        content = event.status.message.content
                    
                    return ChatResponseModel(
                        task_id=event.taskId if hasattr(event, 'taskId') else None,
                        context_id=request_context.task_context.id,
                        agent_id=event.status.message.agent_id if event.status.message else "system",
                        content=content,
                        is_complete=event.status.state == TaskState.completed,
                        requires_input=event.status.state == TaskState.input_required,
                        metadata={"session_id": session_id}
                    )
            
            await asyncio.sleep(0.1)
        
        # Timeout
        return ChatResponseModel(
            context_id=request_context.task_context.id,
            agent_id="system",
            content="Request timed out. Please try again.",
            is_complete=False,
            requires_input=True,
            metadata={"session_id": session_id, "timeout": True}
        )
    
    async def _handle_streaming_message(
        self, 
        chat_message: ChatMessage, 
        session_id: str
    ):
        """Handle streaming message"""
        async def stream_generator():
            try:
                # Create request context
                request_context = await self.request_handler.handle_request(
                    user_message=chat_message.message,
                    session_id=session_id,
                    user_id=chat_message.user_id,
                    context_id=chat_message.context_id,
                    additional_data=chat_message.metadata
                )
                
                # Subscribe to events for this context
                events = []
                def event_callback(event):
                    events.append(event)
                
                subscription_id = self.event_queue.subscribe_to_context(
                    request_context.task_context.id,
                    event_callback
                )
                
                # Stream events as they arrive
                processed_events = set()
                timeout_counter = 0
                max_timeout = 300  # 30 seconds
                
                while timeout_counter < max_timeout:
                    # Check for new events
                    new_events = [e for e in events if e.id not in processed_events]
                    
                    for event in new_events:
                        processed_events.add(event.id)
                        
                        # Format event as SSE
                        event_data = await self._format_event_for_sse(
                            event, session_id, request_context.task_context.id
                        )
                        
                        if event_data:
                            yield f"data: {json.dumps(event_data)}\n\n"
                        
                        # Check if this is a final event
                        if (hasattr(event, 'final') and event.final and
                            hasattr(event, 'status') and 
                            event.status.state in [TaskState.completed, TaskState.failed]):
                            return
                    
                    await asyncio.sleep(0.1)
                    timeout_counter += 1
                
                # Timeout
                yield f'data: {{"error": "Request timeout", "session_id": "{session_id}"}}\n\n'
                
            except Exception as e:
                logger.error(f"Error in stream generator: {e}")
                yield f'data: {{"error": "{str(e)}", "session_id": "{session_id}"}}\n\n'
        
        return StreamingResponse(
            stream_generator(),
            media_type="text/plain",
            headers=self._get_sse_headers()
        )
    
    async def _handle_websocket(self, websocket: WebSocket):
        """Handle WebSocket connection for real-time chat"""
        await websocket.accept()
        session_id = str(uuid.uuid4())
        
        logger.info(f"WebSocket connection established: {session_id}")
        
        try:
            while True:
                # Receive message from client
                data = await websocket.receive_text()
                
                try:
                    message_data = json.loads(data)
                    user_message = message_data.get("message", "").strip()
                    
                    if not user_message:
                        continue
                    
                    # Create chat message
                    chat_message = ChatMessage(
                        message=user_message,
                        session_id=session_id,
                        user_id=message_data.get("user_id"),
                        context_id=message_data.get("context_id"),
                        metadata=message_data.get("metadata", {})
                    )
                    
                    # Process with A2A protocol
                    await self._process_websocket_message(websocket, chat_message, session_id)
                    
                except json.JSONDecodeError:
                    await websocket.send_text(json.dumps({
                        "error": "Invalid JSON format",
                        "session_id": session_id
                    }))
                except Exception as e:
                    logger.error(f"Error processing WebSocket message: {e}")
                    await websocket.send_text(json.dumps({
                        "error": str(e),
                        "session_id": session_id
                    }))
                    
        except WebSocketDisconnect:
            logger.info(f"WebSocket connection closed: {session_id}")
        except Exception as e:
            logger.error(f"WebSocket error: {e}")
        finally:
            # Clean up session
            if session_id in self.active_sessions:
                del self.active_sessions[session_id]
    
    async def _process_websocket_message(
        self, 
        websocket: WebSocket, 
        chat_message: ChatMessage, 
        session_id: str
    ):
        """Process WebSocket message using A2A protocol"""
        # Update session tracking
        await self._update_session(session_id, chat_message.context_id)
        
        # Create request context
        request_context = await self.request_handler.handle_request(
            user_message=chat_message.message,
            session_id=session_id,
            user_id=chat_message.user_id,
            context_id=chat_message.context_id,
            additional_data=chat_message.metadata
        )
        
        # Subscribe to events and stream to WebSocket
        events = []
        def event_callback(event):
            events.append(event)
        
        self.event_queue.subscribe_to_context(
            request_context.task_context.id,
            event_callback
        )
        
        # Stream events
        processed_events = set()
        timeout_counter = 0
        max_timeout = 300  # 30 seconds
        
        while timeout_counter < max_timeout:
            new_events = [e for e in events if e.id not in processed_events]
            
            for event in new_events:
                processed_events.add(event.id)
                
                event_data = await self._format_event_for_sse(
                    event, session_id, request_context.task_context.id
                )
                
                if event_data:
                    await websocket.send_text(json.dumps(event_data))
                
                # Check if final
                if (hasattr(event, 'final') and event.final and
                    hasattr(event, 'status') and 
                    event.status.state in [TaskState.completed, TaskState.failed]):
                    return
            
            await asyncio.sleep(0.1)
            timeout_counter += 1
        
        # Timeout
        await websocket.send_text(json.dumps({
            "error": "Request timeout",
            "session_id": session_id
        }))
    
    async def _format_event_for_sse(
        self, 
        event, 
        session_id: str, 
        context_id: str
    ) -> Optional[Dict[str, Any]]:
        """Format A2A event for SSE/WebSocket transmission"""
        event_data = {
            "session_id": session_id,
            "context_id": context_id,
            "type": event.type,
            "timestamp": event.timestamp.isoformat()
        }
        
        # Add event-specific data
        if hasattr(event, 'status') and event.status:
            event_data["status"] = event.status.state
            event_data["is_complete"] = getattr(event, 'final', False)
            
            if event.status.message:
                event_data["content"] = event.status.message.content
                event_data["agent"] = event.status.message.agent_id
        
        if hasattr(event, 'artifact') and event.artifact:
            event_data["artifact"] = {
                "name": event.artifact.name,
                "type": event.artifact.artifact_type,
                "content": event.artifact.content
            }
        
        if hasattr(event, 'taskId'):
            event_data["task_id"] = event.taskId
        
        return event_data
    
    async def _update_session(self, session_id: str, context_id: Optional[str] = None):
        """Update session tracking information"""
        current_time = datetime.utcnow()
        
        if session_id not in self.active_sessions:
            self.active_sessions[session_id] = {
                "created_at": current_time.isoformat(),
                "message_count": 0
            }
        
        session_data = self.active_sessions[session_id]
        session_data["last_activity"] = current_time.isoformat()
        session_data["message_count"] = session_data.get("message_count", 0) + 1
        
        if context_id:
            session_data["context_id"] = context_id
    
    def _get_sse_headers(self) -> Dict[str, str]:
        """Get headers for Server-Sent Events"""
        return {
            "Cache-Control": "no-cache",
            "Connection": "keep-alive", 
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "*",
            "Content-Type": "text/plain"
        }
    
    def get_router(self) -> APIRouter:
        """Get the configured FastAPI router"""
        return self.router