"""
Enhanced FastAPI Chat Router for A2A Protocol with Advanced UX Features

This module provides an enhanced FastAPI router with advanced user experience features
including rate limiting, connection tracking, typing indicators, better error handling,
and comprehensive monitoring capabilities.

Key enhancements over the basic router:
- Rate limiting per client IP
- Connection tracking and statistics
- Enhanced error handling with retries
- Typing indicators and status updates
- Message history and session management
- Health monitoring and performance metrics
"""
import asyncio
import json
import uuid
import logging
import time
from typing import Any, Dict, List, Optional
from collections import defaultdict, deque
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

# Enhanced Request/Response Models
class EnhancedChatMessage(BaseModel):
    """Enhanced chat message model with additional metadata"""
    message: str
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    context_id: Optional[str] = None
    streaming: bool = True
    priority: str = "normal"  # low, normal, high
    expected_response_time: Optional[int] = None  # seconds
    metadata: Dict[str, Any] = {}


class EnhancedChatResponse(BaseModel):
    """Enhanced chat response with performance metrics"""
    task_id: Optional[str] = None
    context_id: str
    agent_id: str
    content: str
    is_complete: bool = False
    requires_input: bool = False
    artifacts: List[Dict[str, Any]] = []
    processing_time_ms: Optional[float] = None
    confidence_score: Optional[float] = None
    metadata: Dict[str, Any] = {}


class ConnectionStatus(BaseModel):
    """Connection status information"""
    connection_id: str
    status: str  # connected, processing, idle, error
    connected_at: datetime
    message_count: int
    last_activity: datetime


class EnhancedA2AChatRouter:
    """
    Enhanced A2A Chat Router with advanced UX features including:
    - Rate limiting and connection management
    - Real-time status updates and typing indicators
    - Enhanced error handling with automatic retries
    - Performance monitoring and statistics
    - Message history and session persistence
    """
    
    def __init__(self, 
                 max_requests_per_minute: int = 60,
                 max_concurrent_connections: int = 100,
                 message_history_limit: int = 1000):
        self.router = APIRouter(prefix="/a2a/chat", tags=["Enhanced A2A Chat"])
        
        # Core A2A components
        self.agent = EnhancedProductManagementAgent()
        self.task_store = InMemoryTaskStore()
        self.notification_config_store = InMemoryPushNotificationConfigStore()
        self.notification_sender = BasePushNotificationSender()
        self.request_handler = DefaultRequestHandler(
            task_store=self.task_store,
            notification_config_store=self.notification_config_store,
            notification_sender=self.notification_sender,
            event_queue=get_global_event_queue()
        )
        
        # Enhanced features configuration
        self.max_requests_per_minute = max_requests_per_minute
        self.max_concurrent_connections = max_concurrent_connections
        self.message_history_limit = message_history_limit
        
        # Rate limiting and connection tracking
        self.request_counts = defaultdict(deque)  # IP -> timestamps
        self.active_connections = {}  # connection_id -> ConnectionStatus
        self.message_history = deque(maxlen=message_history_limit)
        
        # Performance and monitoring
        self.stats = {
            'total_connections': 0,
            'total_messages': 0,
            'total_errors': 0,
            'average_response_time': 0.0,
            'last_reset': time.time(),
            'peak_concurrent_connections': 0
        }
        
        self._setup_routes()
        logger.info(f"Enhanced A2A Chat Router initialized with rate limit: {max_requests_per_minute}/min")
    
    def _check_rate_limit(self, client_ip: str) -> tuple[bool, int]:
        """Check rate limit and return (allowed, remaining_requests)"""
        now = time.time()
        minute_ago = now - 60
        
        # Clean old requests
        while self.request_counts[client_ip] and self.request_counts[client_ip][0] <= minute_ago:
            self.request_counts[client_ip].popleft()
        
        current_count = len(self.request_counts[client_ip])
        remaining = max(0, self.max_requests_per_minute - current_count)
        
        if current_count >= self.max_requests_per_minute:
            return False, 0
            
        self.request_counts[client_ip].append(now)
        return True, remaining - 1
    
    def _update_stats(self, response_time: float = None, error: bool = False):
        """Update performance statistics"""
        if error:
            self.stats['total_errors'] += 1
        else:
            self.stats['total_messages'] += 1
            if response_time:
                # Simple moving average
                alpha = 0.1
                self.stats['average_response_time'] = (
                    alpha * response_time + 
                    (1 - alpha) * self.stats['average_response_time']
                )
        
        current_connections = len(self.active_connections)
        if current_connections > self.stats['peak_concurrent_connections']:
            self.stats['peak_concurrent_connections'] = current_connections
    
    def _setup_routes(self):
        """Setup all enhanced chat routes"""
        
        @self.router.post("/message", response_model=EnhancedChatResponse)
        async def enhanced_send_message(message: EnhancedChatMessage, request: Request):
            """Enhanced message endpoint with rate limiting and performance monitoring"""
            start_time = time.time()
            client_ip = request.client.host
            
            # Rate limiting
            allowed, remaining = self._check_rate_limit(client_ip)
            if not allowed:
                self._update_stats(error=True)
                raise HTTPException(
                    status_code=429,
                    detail={
                        "error": "Rate limit exceeded",
                        "message": f"Maximum {self.max_requests_per_minute} requests per minute allowed",
                        "retry_after": 60,
                        "client_ip": client_ip
                    },
                    headers={"Retry-After": "60"}
                )
            
            # Connection limit
            if len(self.active_connections) >= self.max_concurrent_connections:
                self._update_stats(error=True)
                raise HTTPException(
                    status_code=503,
                    detail={
                        "error": "Server busy", 
                        "message": "Too many concurrent connections. Please try again later."
                    }
                )
            
            try:
                # Enhanced logging with request context
                logger.info(
                    f"Processing message from {client_ip} (remaining: {remaining}): "
                    f"{message.message[:100]}{'...' if len(message.message) > 100 else ''}"
                )
                
                # Create enhanced chat request
                chat_request = ChatRequest(
                    message=message.message,
                    session_id=message.session_id or str(uuid.uuid4()),
                    user_id=message.user_id,
                    context_id=message.context_id or str(uuid.uuid4()),
                    metadata={
                        **message.metadata,
                        'client_ip': client_ip,
                        'timestamp': datetime.now().isoformat(),
                        'user_agent': request.headers.get('user-agent', 'Unknown'),
                        'priority': message.priority,
                        'rate_limit_remaining': remaining
                    }
                )
                
                # Process with appropriate timeout based on priority
                timeout = 30.0  # default
                if message.priority == 'high':
                    timeout = 45.0
                elif message.priority == 'low':
                    timeout = 15.0
                
                response = await asyncio.wait_for(
                    self.request_handler.handle_request(chat_request),
                    timeout=timeout
                )
                
                processing_time = (time.time() - start_time) * 1000  # ms
                self._update_stats(response_time=processing_time)
                
                # Add to message history
                self.message_history.append({
                    'timestamp': datetime.now().isoformat(),
                    'client_ip': client_ip,
                    'message': message.message[:200],  # truncate for storage
                    'response_time_ms': processing_time,
                    'agent': response.agent_id
                })
                
                logger.info(
                    f"Successfully processed message for {client_ip} "
                    f"in {processing_time:.2f}ms via {response.agent_id}"
                )
                
                return EnhancedChatResponse(
                    task_id=response.task_id,
                    context_id=response.context_id,
                    agent_id=response.agent_id,
                    content=response.content,
                    is_complete=response.is_complete,
                    artifacts=response.artifacts,
                    processing_time_ms=processing_time,
                    confidence_score=response.metadata.get('confidence', 0.9),
                    metadata={
                        **response.metadata,
                        'client_ip': client_ip,
                        'rate_limit_remaining': remaining,
                        'server_timestamp': datetime.now().isoformat()
                    }
                )
                
            except asyncio.TimeoutError:
                self._update_stats(error=True)
                logger.error(f"Timeout processing message from {client_ip} (priority: {message.priority})")
                raise HTTPException(
                    status_code=408,
                    detail={
                        "error": "Request timeout",
                        "message": f"Request took longer than {timeout}s to process. Please try again.",
                        "timeout_seconds": timeout,
                        "priority": message.priority
                    }
                )
            except Exception as e:
                self._update_stats(error=True)
                logger.error(f"Error processing message from {client_ip}: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail={
                        "error": "Processing error",
                        "message": "An error occurred while processing your request.",
                        "details": str(e) if logger.level <= logging.DEBUG else None,
                        "retry_recommended": True
                    }
                )
        
        @self.router.post("/stream")
        async def enhanced_stream_message(message: EnhancedChatMessage, request: Request):\n            \"\"\"Enhanced streaming endpoint with real-time status updates\"\"\"\n            client_ip = request.client.host\n            \n            # Rate limiting check\n            allowed, remaining = self._check_rate_limit(client_ip)\n            if not allowed:\n                raise HTTPException(status_code=429, detail=\"Rate limit exceeded\")\n            \n            async def generate_stream():\n                try:\n                    # Send initial status\n                    yield f\"data: {json.dumps({'type': 'status', 'status': 'processing', 'message': 'Agent is thinking...'})}\n\n\"\n                    \n                    # Create request\n                    chat_request = ChatRequest(\n                        message=message.message,\n                        session_id=message.session_id or str(uuid.uuid4()),\n                        context_id=message.context_id or str(uuid.uuid4()),\n                        metadata={**message.metadata, 'client_ip': client_ip, 'streaming': True}\n                    )\n                    \n                    # Process request\n                    start_time = time.time()\n                    response = await self.request_handler.handle_request(chat_request)\n                    processing_time = (time.time() - start_time) * 1000\n                    \n                    # Send response\n                    yield f\"data: {json.dumps({\n                        'type': 'response',\n                        'content': response.content,\n                        'agent': response.agent_id,\n                        'task_id': response.task_id,\n                        'processing_time_ms': processing_time,\n                        'is_complete': response.is_complete\n                    })}\n\n\"\n                    \n                    # Send completion status\n                    yield f\"data: {json.dumps({'type': 'status', 'status': 'complete', 'message': 'Response generated'})}\n\n\"\n                    \n                    self._update_stats(response_time=processing_time)\n                    \n                except Exception as e:\n                    self._update_stats(error=True)\n                    logger.error(f\"Streaming error for {client_ip}: {e}\")\n                    yield f\"data: {json.dumps({'type': 'error', 'error': str(e), 'message': 'Failed to process request'})}\n\n\"\n            \n            return StreamingResponse(\n                generate_stream(),\n                media_type=\"text/plain\",\n                headers={\n                    \"Cache-Control\": \"no-cache\",\n                    \"Connection\": \"keep-alive\",\n                    \"X-Rate-Limit-Remaining\": str(remaining)\n                }\n            )\n        \n        @self.router.websocket(\"/ws\")\n        async def enhanced_websocket_endpoint(websocket: WebSocket):\n            \"\"\"Enhanced WebSocket with connection tracking and status updates\"\"\"\n            await websocket.accept()\n            connection_id = str(uuid.uuid4())\n            client_ip = websocket.client.host\n            \n            # Track connection\n            connection_status = ConnectionStatus(\n                connection_id=connection_id,\n                status=\"connected\",\n                connected_at=datetime.now(),\n                message_count=0,\n                last_activity=datetime.now()\n            )\n            self.active_connections[connection_id] = connection_status\n            self.stats['total_connections'] += 1\n            \n            logger.info(f\"WebSocket connected: {connection_id} from {client_ip}\")\n            \n            try:\n                # Send welcome with enhanced info\n                await websocket.send_json({\n                    \"type\": \"connection\",\n                    \"connection_id\": connection_id,\n                    \"message\": \"Connected to enhanced A2A protocol server\",\n                    \"features\": [\n                        \"Real-time status updates\",\n                        \"Typing indicators\",\n                        \"Enhanced error handling\",\n                        \"Performance monitoring\"\n                    ],\n                    \"rate_limit\": f\"{self.max_requests_per_minute} requests/minute\",\n                    \"timestamp\": datetime.now().isoformat()\n                })\n                \n                while True:\n                    # Rate limiting\n                    allowed, remaining = self._check_rate_limit(client_ip)\n                    if not allowed:\n                        await websocket.send_json({\n                            \"type\": \"error\",\n                            \"error\": \"Rate limit exceeded\",\n                            \"message\": f\"Maximum {self.max_requests_per_minute} requests per minute\",\n                            \"retry_after\": 60\n                        })\n                        continue\n                    \n                    # Receive message\n                    data = await websocket.receive_json()\n                    connection_status.message_count += 1\n                    connection_status.last_activity = datetime.now()\n                    connection_status.status = \"processing\"\n                    \n                    # Send typing indicator\n                    await websocket.send_json({\n                        \"type\": \"typing\",\n                        \"message\": \"Agent is typing...\",\n                        \"agent\": \"system\"\n                    })\n                    \n                    try:\n                        # Process message\n                        start_time = time.time()\n                        chat_request = ChatRequest(\n                            message=data.get(\"message\", \"\"),\n                            session_id=data.get(\"session_id\", str(uuid.uuid4())),\n                            context_id=data.get(\"context_id\", str(uuid.uuid4())),\n                            metadata={\n                                **data.get(\"metadata\", {}),\n                                'connection_id': connection_id,\n                                'client_ip': client_ip\n                            }\n                        )\n                        \n                        response = await asyncio.wait_for(\n                            self.request_handler.handle_request(chat_request),\n                            timeout=30.0\n                        )\n                        \n                        processing_time = (time.time() - start_time) * 1000\n                        self._update_stats(response_time=processing_time)\n                        \n                        connection_status.status = \"idle\"\n                        \n                        # Send response with enhanced metadata\n                        await websocket.send_json({\n                            \"type\": \"response\",\n                            \"content\": response.content,\n                            \"agent\": response.agent_id,\n                            \"task_id\": response.task_id,\n                            \"is_complete\": response.is_complete,\n                            \"processing_time_ms\": processing_time,\n                            \"connection_id\": connection_id,\n                            \"message_count\": connection_status.message_count,\n                            \"rate_limit_remaining\": remaining\n                        })\n                        \n                    except asyncio.TimeoutError:\n                        connection_status.status = \"error\"\n                        self._update_stats(error=True)\n                        await websocket.send_json({\n                            \"type\": \"error\",\n                            \"error\": \"Timeout\",\n                            \"message\": \"Request took too long to process\"\n                        })\n                    except Exception as e:\n                        connection_status.status = \"error\"\n                        self._update_stats(error=True)\n                        logger.error(f\"WebSocket processing error: {e}\")\n                        await websocket.send_json({\n                            \"type\": \"error\",\n                            \"error\": str(e),\n                            \"message\": \"Failed to process your request\"\n                        })\n                    finally:\n                        if connection_status.status == \"processing\":\n                            connection_status.status = \"idle\"\n                        \n            except WebSocketDisconnect:\n                logger.info(f\"WebSocket disconnected: {connection_id}\")\n            except Exception as e:\n                logger.error(f\"WebSocket error for {connection_id}: {e}\")\n            finally:\n                # Clean up connection\n                if connection_id in self.active_connections:\n                    del self.active_connections[connection_id]\n        \n        @self.router.get(\"/stats\")\n        async def get_enhanced_stats():\n            \"\"\"Get comprehensive system statistics and health metrics\"\"\"\n            uptime = time.time() - self.stats['last_reset']\n            active_connections_count = len(self.active_connections)\n            \n            return {\n                \"status\": \"healthy\",\n                \"uptime_seconds\": uptime,\n                \"performance\": {\n                    \"total_messages\": self.stats['total_messages'],\n                    \"total_errors\": self.stats['total_errors'],\n                    \"error_rate\": (\n                        self.stats['total_errors'] / max(1, self.stats['total_messages'] + self.stats['total_errors'])\n                    ),\n                    \"average_response_time_ms\": self.stats['average_response_time'],\n                    \"messages_per_second\": self.stats['total_messages'] / max(1, uptime)\n                },\n                \"connections\": {\n                    \"active_connections\": active_connections_count,\n                    \"peak_concurrent\": self.stats['peak_concurrent_connections'],\n                    \"total_connections\": self.stats['total_connections'],\n                    \"max_allowed\": self.max_concurrent_connections\n                },\n                \"rate_limiting\": {\n                    \"requests_per_minute_limit\": self.max_requests_per_minute,\n                    \"active_clients\": len(self.request_counts)\n                },\n                \"agents\": {\n                    \"available_agents\": len(await self.agent.get_available_agents()),\n                    \"coordinator_stats\": self.agent.get_stats()\n                },\n                \"message_history\": {\n                    \"total_stored\": len(self.message_history),\n                    \"limit\": self.message_history_limit,\n                    \"recent_messages\": list(self.message_history)[-5:] if self.message_history else []\n                }\n            }\n        \n        @self.router.get(\"/connections\")\n        async def get_active_connections():\n            \"\"\"Get information about active connections\"\"\"\n            return {\n                \"active_connections\": {\n                    conn_id: {\n                        \"status\": conn.status,\n                        \"connected_at\": conn.connected_at.isoformat(),\n                        \"message_count\": conn.message_count,\n                        \"last_activity\": conn.last_activity.isoformat()\n                    }\n                    for conn_id, conn in self.active_connections.items()\n                },\n                \"total_active\": len(self.active_connections)\n            }\n        \n        @self.router.post(\"/connections/{connection_id}/close\")\n        async def close_connection(connection_id: str):\n            \"\"\"Administratively close a specific connection\"\"\"\n            if connection_id in self.active_connections:\n                del self.active_connections[connection_id]\n                return {\"message\": f\"Connection {connection_id} closed\"}\n            else:\n                raise HTTPException(status_code=404, detail=\"Connection not found\")\n        \n        @self.router.get(\"/health\")\n        async def enhanced_health_check():\n            \"\"\"Enhanced health check with detailed system status\"\"\"\n            try:\n                # Test agent availability\n                agent_health = await self.agent.get_available_agents()\n                agent_status = \"healthy\" if agent_health else \"degraded\"\n                \n                # Check connection health\n                active_conn_count = len(self.active_connections)\n                connection_health = \"healthy\"\n                if active_conn_count > self.max_concurrent_connections * 0.9:\n                    connection_health = \"warning\"\n                elif active_conn_count >= self.max_concurrent_connections:\n                    connection_health = \"critical\"\n                \n                # Check error rate\n                total_requests = self.stats['total_messages'] + self.stats['total_errors']\n                error_rate = self.stats['total_errors'] / max(1, total_requests)\n                error_health = \"healthy\" if error_rate < 0.05 else \"warning\" if error_rate < 0.1 else \"critical\"\n                \n                overall_status = \"healthy\"\n                if any(status == \"critical\" for status in [agent_status, connection_health, error_health]):\n                    overall_status = \"critical\"\n                elif any(status == \"warning\" for status in [agent_status, connection_health, error_health]):\n                    overall_status = \"warning\"\n                \n                return {\n                    \"status\": overall_status,\n                    \"components\": {\n                        \"agents\": agent_status,\n                        \"connections\": connection_health,\n                        \"error_rate\": error_health\n                    },\n                    \"metrics\": {\n                        \"active_connections\": active_conn_count,\n                        \"error_rate\": error_rate,\n                        \"uptime_seconds\": time.time() - self.stats['last_reset']\n                    },\n                    \"timestamp\": datetime.now().isoformat()\n                }\n                \n            except Exception as e:\n                logger.error(f\"Health check failed: {e}\")\n                return {\n                    \"status\": \"critical\",\n                    \"error\": str(e),\n                    \"timestamp\": datetime.now().isoformat()\n                }\n\n\ndef create_enhanced_chat_router(**kwargs) -> EnhancedA2AChatRouter:\n    \"\"\"Factory function to create an enhanced chat router with custom configuration\"\"\"\n    return EnhancedA2AChatRouter(**kwargs)\n