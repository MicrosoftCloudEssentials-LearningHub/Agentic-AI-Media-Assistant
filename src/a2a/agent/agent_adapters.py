"""
Enhanced Agent Adapters for A2A Protocol

This module provides adapters that wrap existing Zava agents to work with the A2A protocol.
Each adapter translates between the legacy agent interface and the A2A protocol requirements.
"""
import json
import logging
from typing import Any, Dict, List, Optional
from datetime import datetime

from ..server.agent_execution import BaseAgentExecutor, RequestContext
from ..server.events.event_queue import EventQueue
from ..types import (
    TaskState, TaskStatus, TaskStatusUpdateEvent, TaskArtifactUpdateEvent,
    AgentHandoffEvent, HandoffRequest
)
from ..utils import (
    new_task, new_agent_text_message, new_text_artifact, new_json_artifact,
    sanitize_agent_response, add_to_conversation_history
)

# Import existing agents
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.agents.agent_processor import AgentProcessor
from app.agents.local_agent_processor import LocalAgentProcessor
from services.handoff_service import HandoffService


logger = logging.getLogger(__name__)


class ZavaAgentAdapter(BaseAgentExecutor):
    """
    Base adapter for Zava agents to work with A2A protocol.
    
    Provides common functionality for wrapping existing agents and translating
    their responses to A2A protocol format.
    """
    
    def __init__(
        self,
        agent_domain: str,
        agent_name: str,
        supported_domains: List[str] = None
    ):
        super().__init__(agent_name, supported_domains or [agent_domain])
        self.agent_domain = agent_domain
        self._agent_processor: Optional[AgentProcessor] = None
        self._local_agent_processor: Optional[LocalAgentProcessor] = None
        self._use_remote = False
    
    def _initialize_agent(self) -> None:
        """Initialize the appropriate agent processor (remote or local)"""
        if self._agent_processor is not None or self._local_agent_processor is not None:
            return
        
        # Get agent configuration
        agent_id_map = {
            "orchestrator": os.getenv("orchestrator"),
            "cropping_agent": os.getenv("cropping_agent"), 
            "background_agent": os.getenv("background_agent"),
            "thumbnail_generator": os.getenv("thumbnail_generator"),
            "video_agent": os.getenv("video_agent")
        }
        
        agent_id = agent_id_map.get(self.agent_domain)
        remote_endpoint = os.getenv("AZURE_AI_AGENT_ENDPOINT") or os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        
        # Try remote first if available
        if (remote_endpoint and agent_id and 
            agent_id.startswith("asst_") and 
            not agent_id.startswith("asst_local_")):
            try:
                self._agent_processor = AgentProcessor(
                    agent_id=agent_id,
                    project_endpoint=remote_endpoint
                )
                self._use_remote = True
                logger.info(f"Initialized remote agent processor for {self.agent_domain}")
                return
            except Exception as e:
                logger.warning(f"Failed to initialize remote agent for {self.agent_domain}: {e}")
        
        # Fall back to local processor
        self._local_agent_processor = LocalAgentProcessor(
            agent_id=agent_id or f"asst_local_{self.agent_domain}",
            domain=self.agent_domain
        )
        self._use_remote = False
        logger.info(f"Initialized local agent processor for {self.agent_domain}")
    
    async def _execute_impl(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """Execute the wrapped agent with A2A protocol integration"""
        # Initialize agent if needed
        self._initialize_agent()
        
        # Get or create task
        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
            context.current_task = task
        
        # Prepare agent context
        additional_context = {
            "cart": context.get_cart(),
            "customer": context.get_customer_data()
        }
        
        # Add conversation history for context-aware agents
        if self.agent_domain == "cart_management":
            additional_context["conversation_history"] = context.get_conversation_history()
        
        try:
            # Send working status
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.working,
                        message=new_agent_text_message(
                            f"Processing your request with {self.agent_name}...",
                            context.task_context.id,
                            task.id,
                            self.agent_name
                        )
                    ),
                    final=False,
                    contextId=context.task_context.id,
                    taskId=task.id
                )
            )
            
            # Execute agent
            response_text = ""
            processor = self._agent_processor if self._use_remote else self._local_agent_processor
            
            if processor:
                # Stream response from agent
                async for chunk in self._stream_agent_response(
                    processor, 
                    context.get_user_input(),
                    context.get_conversation_history(),
                    additional_context
                ):
                    response_text += chunk
            else:
                raise RuntimeError(f"No agent processor available for {self.agent_domain}")
            
            # Process and parse response
            await self._process_agent_response(
                response_text, context, event_queue, task
            )
            
        except Exception as e:
            logger.error(f"Error executing {self.agent_name}: {e}")
            await self._handle_execution_error(context, event_queue, e)
    
    async def _stream_agent_response(
        self,
        processor,
        user_message: str,
        conversation_history: List[Dict[str, str]],
        additional_context: Dict[str, Any]
    ):
        """Stream response from the agent processor"""
        try:
            if hasattr(processor, 'run_conversation_with_text_stream'):
                # Remote agent processor
                for chunk in processor.run_conversation_with_text_stream(
                    user_message=user_message,
                    conversation_history=conversation_history,
                    additional_context=additional_context
                ):
                    yield chunk
            elif hasattr(processor, 'process_message'):
                # Local agent processor
                response = processor.process_message(
                    message=user_message,
                    conversation_history=conversation_history,
                    additional_context=additional_context
                )
                yield response
            else:
                raise AttributeError(f"Processor does not have expected methods")
                
        except Exception as e:
            logger.error(f"Error streaming from agent processor: {e}")
            yield f"Error: {str(e)}"
    
    async def _process_agent_response(
        self,
        response_text: str,
        context: RequestContext,
        event_queue: EventQueue,
        task
    ) -> None:
        """Process the agent's response and generate appropriate A2A events"""
        try:
            # Clean and parse response
            cleaned_response = sanitize_agent_response(response_text)
            
            # Try to parse as JSON for structured responses
            structured_data = None
            try:
                if response_text.strip().startswith('{'):
                    structured_data = json.loads(response_text.strip())
            except json.JSONDecodeError:
                pass
            
            # Update context with any returned data
            if structured_data and isinstance(structured_data, dict):
                await self._update_context_from_response(
                    structured_data, context, event_queue
                )
            
            # Check for handoff requests
            handoff_request = self._check_for_handoff(structured_data, cleaned_response)
            
            if handoff_request:
                # Agent is requesting a handoff
                await event_queue.enqueue_event(
                    AgentHandoffEvent(
                        taskId=task.id,
                        contextId=context.task_context.id,
                        from_agent=self.agent_name,
                        to_agent=handoff_request["to_agent"],
                        handoff_reason=handoff_request["reason"],
                        handoff_data=handoff_request.get("data", {})
                    )
                )
                
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.waiting_for_handoff,
                            message=new_agent_text_message(
                                f"Handing off to {handoff_request['to_agent']}: {handoff_request['reason']}",
                                context.task_context.id,
                                task.id,
                                self.agent_name
                            )
                        ),
                        final=True,
                        contextId=context.task_context.id,
                        taskId=task.id
                    )
                )
            else:
                # Normal completion
                # Create response artifact
                if cleaned_response:
                    artifact = new_text_artifact(
                        name=f"{self.agent_name}_response",
                        description=f"Response from {self.agent_name}",
                        text=cleaned_response,
                        task_id=task.id
                    )
                    
                    await event_queue.enqueue_event(
                        TaskArtifactUpdateEvent(
                            append=False,
                            contextId=context.task_context.id,
                            taskId=task.id,
                            lastChunk=True,
                            artifact=artifact
                        )
                    )
                
                # Mark task as completed
                await event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.completed,
                            message=new_agent_text_message(
                                cleaned_response,
                                context.task_context.id,
                                task.id,
                                self.agent_name
                            )
                        ),
                        final=True,
                        contextId=context.task_context.id,
                        taskId=task.id
                    )
                )
            
        except Exception as e:
            logger.error(f"Error processing agent response: {e}")
            raise
    
    async def _update_context_from_response(
        self,
        structured_data: Dict[str, Any],
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """Update context with data from agent response"""
        # Update cart if present
        if "cart" in structured_data and isinstance(structured_data["cart"], list):
            context.set_cart(structured_data["cart"])
        
        # Update customer data if present
        if "discount_percentage" in structured_data:
            customer_data = context.get_customer_data()
            customer_data["discount_percentage"] = structured_data["discount_percentage"]
            context.set_customer_data(customer_data)
        
        # Store any additional metadata
        if "metadata" in structured_data:
            for key, value in structured_data["metadata"].items():
                context.set_shared_data(f"agent_{self.agent_name}_{key}", value)
    
    def _check_for_handoff(
        self,
        structured_data: Optional[Dict[str, Any]],
        response_text: str
    ) -> Optional[Dict[str, Any]]:
        """Check if the agent is requesting a handoff to another agent"""
        # Look for explicit handoff in structured data
        if structured_data and "handoff" in structured_data:
            return structured_data["handoff"]
        
        # Look for handoff indicators in response text
        response_lower = response_text.lower()
        
        # Simple keyword-based handoff detection
        handoff_patterns = {
            "cropping_agent": ["crop", "cut out", "focus on", "resize"],
            "background_agent": ["background", "remove background", "replace background", "transparent"],
            "thumbnail_generator": ["thumbnail", "cover image", "youtube cover"],
            "video_agent": ["video", "movie", "animate", "motion"],
            "orchestrator": ["help", "start over", "general"]
        }
        
        for agent_domain, keywords in handoff_patterns.items():
            if agent_domain != self.agent_domain:  # Don't handoff to self
                if any(keyword in response_lower for keyword in keywords):
                    return {
                        "to_agent": agent_domain,
                        "reason": f"Detected request for {agent_domain} functionality",
                        "data": {"trigger_keywords": keywords}
                    }
        
        return None
    
    def get_confidence_for_task(self, user_input: str) -> float:
        """Get confidence score based on domain expertise"""
        user_lower = user_input.lower()
        
        # Domain-specific confidence scoring
        domain_keywords = {
            "cropping_agent": ["crop", "cut", "resize", "focus", "coordinates"],
            "background_agent": ["background", "remove", "replace", "transparent", "scene"],
            "thumbnail_generator": ["thumbnail", "cover", "text", "overlay", "clickbait"],
            "video_agent": ["video", "movie", "animate", "motion", "sora"],
            "orchestrator": ["help", "hello", "start", "general"]
        }
        
        keywords = domain_keywords.get(self.agent_domain, [])
        matches = sum(1 for keyword in keywords if keyword in user_lower)
        
        if matches == 0:
            return 0.1  # Low confidence for no matches
        elif matches >= 2:
            return 0.9  # High confidence for multiple matches
        else:
            return 0.6  # Medium confidence for single match


# Specific agent adapters

class OrchestratorAgentAdapter(ZavaAgentAdapter):
    """Adapter for the Orchestrator Agent"""
    
    def __init__(self):
        super().__init__(
            agent_domain="orchestrator",
            agent_name="OrchestratorAgent",
            supported_domains=["orchestrator", "general", "routing"]
        )


class CroppingAgentAdapter(ZavaAgentAdapter):
    """Adapter for the Cropping Specialist"""
    
    def __init__(self):
        super().__init__(
            agent_domain="cropping_agent",
            agent_name="CroppingAgent", 
            supported_domains=["cropping_agent", "crop", "resize"]
        )


class BackgroundAgentAdapter(ZavaAgentAdapter):
    """Adapter for the Background Specialist"""
    
    def __init__(self):
        super().__init__(
            agent_domain="background_agent",
            agent_name="BackgroundAgent",
            supported_domains=["background_agent", "background", "remove_bg"]
        )


class ThumbnailGeneratorAdapter(ZavaAgentAdapter):
    """Adapter for the Thumbnail Generator"""
    
    def __init__(self):
        super().__init__(
            agent_domain="thumbnail_generator", 
            agent_name="ThumbnailGenerator",
            supported_domains=["thumbnail_generator", "thumbnail", "cover"]
        )


class VideoAgentAdapter(ZavaAgentAdapter):
    """Adapter for the Video Specialist"""
    
    def __init__(self):
        super().__init__(
            agent_domain="video_agent",
            agent_name="VideoAgent",
            supported_domains=["video_agent", "video", "animation"]
        )