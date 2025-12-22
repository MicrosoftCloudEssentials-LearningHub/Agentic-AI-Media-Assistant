"""
Utility functions for the A2A protocol implementation
"""
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from .types import (
    AgentMessage, Task, TaskArtifact, TaskContext, TaskState, 
    TaskPriority, EventType, BaseEvent
)


def generate_id() -> str:
    """Generate a unique ID"""
    return str(uuid.uuid4())


def new_task(
    message: AgentMessage,
    title: Optional[str] = None,
    priority: TaskPriority = TaskPriority.normal
) -> Task:
    """Create a new task from an agent message"""
    return Task(
        id=generate_id(),
        contextId=message.context_id,
        title=title or f"Task for: {message.content[:50]}...",
        description=message.content,
        priority=priority,
        created_by=message.agent_id,
        metadata={"original_message_id": message.id}
    )


def new_agent_text_message(
    content: str,
    context_id: str,
    task_id: str,
    agent_id: str = "system"
) -> AgentMessage:
    """Create a new text message from an agent"""
    return AgentMessage(
        content=content,
        agent_id=agent_id,
        task_id=task_id,
        context_id=context_id,
        message_type="text"
    )


def new_text_artifact(
    name: str,
    description: str,
    text: str,
    task_id: Optional[str] = None
) -> TaskArtifact:
    """Create a new text artifact"""
    return TaskArtifact(
        task_id=task_id or generate_id(),
        name=name,
        description=description,
        artifact_type="text",
        content=text,
        size=len(text.encode('utf-8'))
    )


def new_json_artifact(
    name: str,
    description: str,
    data: Dict[str, Any],
    task_id: Optional[str] = None
) -> TaskArtifact:
    """Create a new JSON artifact"""
    import json
    content_str = json.dumps(data)
    return TaskArtifact(
        task_id=task_id or generate_id(),
        name=name,
        description=description,
        artifact_type="json",
        content=data,
        size=len(content_str.encode('utf-8'))
    )


def new_context(
    session_id: str,
    user_id: Optional[str] = None,
    initial_data: Optional[Dict[str, Any]] = None
) -> TaskContext:
    """Create a new task context"""
    return TaskContext(
        session_id=session_id,
        user_id=user_id,
        shared_data=initial_data or {}
    )


def update_context_data(
    context: TaskContext,
    key: str,
    value: Any
) -> TaskContext:
    """Update shared data in task context"""
    context.shared_data[key] = value
    context.updated_at = datetime.utcnow()
    return context


def merge_context_data(
    context: TaskContext,
    data: Dict[str, Any]
) -> TaskContext:
    """Merge data into task context"""
    context.shared_data.update(data)
    context.updated_at = datetime.utcnow()
    return context


def extract_cart_from_context(context: TaskContext) -> List[Dict[str, Any]]:
    """Extract shopping cart from context"""
    return context.shared_data.get("cart", [])


def update_cart_in_context(
    context: TaskContext, 
    cart: List[Dict[str, Any]]
) -> TaskContext:
    """Update shopping cart in context"""
    return update_context_data(context, "cart", cart)


def extract_customer_data_from_context(context: TaskContext) -> Dict[str, Any]:
    """Extract customer data from context"""
    return context.shared_data.get("customer", {})


def update_customer_data_in_context(
    context: TaskContext,
    customer_data: Dict[str, Any]
) -> TaskContext:
    """Update customer data in context"""
    return update_context_data(context, "customer", customer_data)


def format_conversation_history(
    context: TaskContext,
    limit: int = 10
) -> List[Dict[str, str]]:
    """Format conversation history for agent consumption"""
    history = context.conversation_history[-limit:] if limit > 0 else context.conversation_history
    return [
        {
            "role": msg.get("role", "user"),
            "content": msg.get("content", "")
        }
        for msg in history
        if msg.get("content")
    ]


def add_to_conversation_history(
    context: TaskContext,
    role: str,
    content: str
) -> TaskContext:
    """Add a message to conversation history"""
    context.conversation_history.append({
        "role": role,
        "content": content,
        "timestamp": datetime.utcnow().isoformat()
    })
    context.updated_at = datetime.utcnow()
    return context


def sanitize_agent_response(response: str) -> str:
    """Sanitize and clean agent response text"""
    # Remove common JSON artifacts
    text = response.strip()
    
    # If it looks like JSON, try to extract meaningful content
    if text.startswith('{') and text.endswith('}'):
        try:
            import json
            data = json.loads(text)
            
            # Look for common response fields
            if isinstance(data, dict):
                for field in ['answer', 'response', 'message', 'content', 'result']:
                    if field in data and isinstance(data[field], str):
                        return data[field].strip()
                        
                # If no standard field, return the first string value found
                for value in data.values():
                    if isinstance(value, str) and len(value.strip()) > 0:
                        return value.strip()
        except:
            pass
    
    return text


def format_error_message(error: Exception, context: str = "") -> str:
    """Format error message for user consumption"""
    base_msg = "I apologize, but I encountered an issue while processing your request."
    
    if context:
        base_msg = f"I apologize, but I encountered an issue while {context}."
    
    # In production, you might want to log the actual error details
    # but only show user-friendly messages to the client
    return f"{base_msg} Please try again or rephrase your request."


def create_handoff_context(
    from_agent: str,
    to_agent: str,
    task: Task,
    reason: str,
    additional_data: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Create context data for agent handoffs"""
    return {
        "handoff": {
            "from_agent": from_agent,
            "to_agent": to_agent,
            "task_id": task.id,
            "reason": reason,
            "timestamp": datetime.utcnow().isoformat(),
            "additional_data": additional_data or {}
        },
        "task_summary": {
            "id": task.id,
            "title": task.title,
            "description": task.description,
            "state": task.state,
            "created_at": task.created_at.isoformat()
        }
    }


def validate_agent_id(agent_id: str) -> bool:
    """Validate agent ID format"""
    if not agent_id or not isinstance(agent_id, str):
        return False
    
    # Agent IDs should be non-empty strings
    # You can add more specific validation rules here
    return len(agent_id.strip()) > 0


def validate_session_id(session_id: str) -> bool:
    """Validate session ID format"""
    if not session_id or not isinstance(session_id, str):
        return False
    
    # Session IDs should be non-empty strings
    # You can add more specific validation rules here (UUID format, etc.)
    return len(session_id.strip()) > 0


def calculate_confidence_score(
    agent_response: str,
    expected_indicators: List[str],
    negative_indicators: List[str] = None
) -> float:
    """Calculate confidence score based on response content"""
    if not agent_response:
        return 0.0
    
    response_lower = agent_response.lower()
    positive_score = 0.0
    negative_score = 0.0
    
    # Check for positive indicators
    for indicator in expected_indicators:
        if indicator.lower() in response_lower:
            positive_score += 1.0
    
    # Check for negative indicators
    if negative_indicators:
        for indicator in negative_indicators:
            if indicator.lower() in response_lower:
                negative_score += 1.0
    
    # Calculate final score (0.0 to 1.0)
    total_indicators = len(expected_indicators)
    if total_indicators == 0:
        return 0.5  # Default confidence if no indicators
    
    base_score = positive_score / total_indicators
    penalty = negative_score * 0.2  # Reduce confidence for negative indicators
    
    return max(0.0, min(1.0, base_score - penalty))