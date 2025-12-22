"""
A2A Protocol Core Types and Models

This module defines the core types and models used in the Agent-to-Agent (A2A) protocol.
These models enable structured communication between AI agents and provide a foundation
for task coordination, event handling, and agent discovery.

Key frameworks and libraries used:
- Pydantic: Data validation library that uses Python type annotations to validate,
  serialize, and deserialize data with automatic error handling and documentation
- Python Enums: Built-in enumeration support for defining sets of named constants
- UUID: Universally Unique Identifier library for generating unique task and session IDs
- DateTime: Python's built-in date and time handling for timestamps and scheduling
- Typing: Python's type hinting system for better code documentation and IDE support
"""
import uuid
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union
from pydantic import BaseModel, Field


# === Agent Capabilities and Skills ===

class AgentCapabilities(BaseModel):
    """Defines the capabilities of an agent"""
    streaming: bool = True
    multimodal: bool = False
    function_calling: bool = True
    memory_persistent: bool = False
    handoff_supported: bool = True
    context_sharing: bool = True


class AgentSkill(BaseModel):
    """Represents a specific skill that an agent possesses"""
    id: str
    name: str
    description: str
    tags: List[str] = []
    examples: List[str] = []
    input_types: List[str] = ["text"]
    output_types: List[str] = ["text"]
    confidence_level: float = Field(default=0.9, ge=0.0, le=1.0)


class AgentCard(BaseModel):
    """Agent card containing metadata and capabilities"""
    name: str
    description: str
    url: str
    version: str = "1.0.0"
    agent_id: Optional[str] = None
    defaultInputModes: List[str] = ["text"]
    defaultOutputModes: List[str] = ["text"]
    capabilities: AgentCapabilities
    skills: List[AgentSkill]
    metadata: Dict[str, Any] = {}


# === Task Management ===

class TaskState(str, Enum):
    """States that a task can be in"""
    created = "created"
    assigned = "assigned"
    working = "working"
    input_required = "input_required"
    waiting_for_handoff = "waiting_for_handoff"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class TaskPriority(str, Enum):
    """Task priority levels"""
    low = "low"
    normal = "normal" 
    high = "high"
    urgent = "urgent"


class AgentMessage(BaseModel):
    """Message from an agent to user or another agent"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    content: str
    agent_id: str
    task_id: str
    context_id: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    message_type: Literal["text", "json", "markdown", "error", "info"] = "text"
    metadata: Dict[str, Any] = {}


class TaskContext(BaseModel):
    """Context information for task execution"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None
    session_id: str
    conversation_history: List[Dict[str, str]] = []
    shared_data: Dict[str, Any] = {}
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class Task(BaseModel):
    """Represents a task in the A2A system"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    contextId: str
    title: str
    description: str
    state: TaskState = TaskState.created
    priority: TaskPriority = TaskPriority.normal
    assigned_agent: Optional[str] = None
    created_by: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = {}
    artifacts: List["TaskArtifact"] = []
    
    def update_state(self, new_state: TaskState, message: Optional[str] = None):
        """Update task state with optional message"""
        self.state = new_state
        self.updated_at = datetime.utcnow()
        if message:
            self.metadata["last_message"] = message


class TaskStatus(BaseModel):
    """Current status of a task"""
    state: TaskState
    message: Optional[AgentMessage] = None
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    estimated_completion: Optional[datetime] = None
    error_details: Optional[str] = None


class TaskArtifact(BaseModel):
    """Artifacts generated during task execution"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    task_id: str
    name: str
    description: str
    artifact_type: Literal["text", "json", "image", "file", "url"] = "text"
    content: Union[str, Dict[str, Any]]
    size: Optional[int] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = {}


# === Events ===

class EventType(str, Enum):
    """Types of events in the A2A system"""
    task_created = "task_created"
    task_status_update = "task_status_update"
    task_artifact_update = "task_artifact_update"
    agent_handoff = "agent_handoff"
    agent_registration = "agent_registration"
    agent_heartbeat = "agent_heartbeat"
    system_error = "system_error"


class BaseEvent(BaseModel):
    """Base class for all A2A events"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    type: EventType
    contextId: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    source_agent: Optional[str] = None
    metadata: Dict[str, Any] = {}


class TaskStatusUpdateEvent(BaseEvent):
    """Event fired when task status changes"""
    type: Literal[EventType.task_status_update] = EventType.task_status_update
    taskId: str
    status: TaskStatus
    final: bool = False


class TaskArtifactUpdateEvent(BaseEvent):
    """Event fired when task artifacts are updated"""
    type: Literal[EventType.task_artifact_update] = EventType.task_artifact_update
    taskId: str
    artifact: TaskArtifact
    append: bool = True
    lastChunk: bool = False


class AgentHandoffEvent(BaseEvent):
    """Event fired when task is handed off between agents"""
    type: Literal[EventType.agent_handoff] = EventType.agent_handoff
    taskId: str
    from_agent: str
    to_agent: str
    handoff_reason: str
    handoff_data: Dict[str, Any] = {}


class AgentRegistrationEvent(BaseEvent):
    """Event fired when agent registers with the system"""
    type: Literal[EventType.agent_registration] = EventType.agent_registration
    agent_card: AgentCard


# === Agent Communication ===

class HandoffRequest(BaseModel):
    """Request to hand off a task to another agent"""
    task_id: str
    target_agent: str
    reason: str
    context_data: Dict[str, Any] = {}
    priority_boost: bool = False


class IntentClassification(BaseModel):
    """Result of intent classification for routing"""
    domain: str
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str
    suggested_agent: Optional[str] = None
    alternate_agents: List[str] = []


class AgentResponse(BaseModel):
    """Standardized response from an agent"""
    task_id: str
    agent_id: str
    content: Union[str, Dict[str, Any]]
    status: TaskState
    requires_input: bool = False
    handoff_request: Optional[HandoffRequest] = None
    artifacts: List[TaskArtifact] = []
    metadata: Dict[str, Any] = {}


# === Error Handling ===

class A2AError(BaseModel):
    """A2A protocol error"""
    code: str
    message: str
    details: Optional[Dict[str, Any]] = None
    recoverable: bool = True
    suggested_action: Optional[str] = None


class ValidationError(A2AError):
    """Validation error in A2A protocol"""
    code: Literal["validation_error"] = "validation_error"


class AgentNotFoundError(A2AError):
    """Agent not found error"""
    code: Literal["agent_not_found"] = "agent_not_found"


class TaskExecutionError(A2AError):
    """Task execution error"""
    code: Literal["task_execution_error"] = "task_execution_error"


# === Request/Response Models ===

class ChatRequest(BaseModel):
    """Request model for chat interactions"""
    message: str
    session_id: Optional[str] = None
    context_id: Optional[str] = None
    user_id: Optional[str] = None
    preferred_agent: Optional[str] = None
    streaming: bool = True
    metadata: Dict[str, Any] = {}


class ChatResponse(BaseModel):
    """Response model for chat interactions"""
    task_id: str
    context_id: str
    agent_id: str
    content: str
    is_complete: bool = False
    requires_input: bool = False
    artifacts: List[TaskArtifact] = []
    handoff_suggestion: Optional[HandoffRequest] = None
    metadata: Dict[str, Any] = {}


# === System Configuration ===

class A2AConfig(BaseModel):
    """Configuration for A2A system"""
    host: str = "localhost"
    port: int = 8001
    max_concurrent_tasks: int = 100
    task_timeout_seconds: int = 300
    agent_discovery_enabled: bool = True
    event_queue_size: int = 1000
    debug_mode: bool = False
    cors_enabled: bool = True
    allowed_origins: List[str] = ["*"]