"""
A2A Coordinator Agent

This module implements the coordinator agent that manages handoffs and routing
between multiple agents using the A2A protocol. The coordinator acts as the
central orchestrator for multi-agent conversations.
"""
import asyncio
import logging
from typing import Any, Dict, List, Optional, Type
from datetime import datetime

from ..server.agent_execution import BaseAgentExecutor, RequestContext
from ..server.events.event_queue import EventQueue
from ..types import (
    TaskState, TaskStatus, TaskStatusUpdateEvent, TaskArtifactUpdateEvent,
    AgentHandoffEvent, EventType, IntentClassification
)
from ..utils import (
    new_task, new_agent_text_message, new_text_artifact,
    add_to_conversation_history
)

# Import agent adapters
from .agent_adapters import (
    OrchestratorAgentAdapter, CroppingAgentAdapter, BackgroundAgentAdapter,
    ThumbnailGeneratorAdapter, VideoAgentAdapter
)


# Import existing handoff service
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services.handoff_service import HandoffService


logger = logging.getLogger(__name__)


class A2ACoordinatorAgent(BaseAgentExecutor):
    """
    Coordinator agent that manages handoffs and routing between multiple agents.
    
    This agent acts as the central orchestrator, receiving requests, classifying
    intent, routing to appropriate agents, and managing handoffs between agents.
    """
    
    def __init__(self):
        super().__init__(
            agent_name="A2ACoordinator",
            supported_domains=["coordination", "routing", "handoff"]
        )
        
        # Initialize agent adapters
        self.agents: Dict[str, BaseAgentExecutor] = {
            "orchestrator": OrchestratorAgentAdapter(),
            "cropping_agent": CroppingAgentAdapter(), 
            "background_agent": BackgroundAgentAdapter(),
            "thumbnail_generator": ThumbnailGeneratorAdapter(),
            "video_agent": VideoAgentAdapter()
        }
        
        # Initialize handoff service for intent classification
        self.handoff_service: Optional[HandoffService] = None
        self._initialize_handoff_service()
        
        # Track active handoffs
        self.active_handoffs: Dict[str, str] = {}  # task_id -> current_agent
        
        # Subscribe to handoff events
        self._handoff_subscriptions: List[str] = []
    
    def _initialize_handoff_service(self) -> None:
        """Initialize the handoff service for intent classification"""
        try:
            self.handoff_service = HandoffService()
            logger.info("Handoff service initialized successfully")
        except Exception as e:
            logger.warning(f"Failed to initialize handoff service: {e}")
            self.handoff_service = None
    
    async def _execute_impl(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """Execute coordination logic with intent classification and routing"""
        # Subscribe to handoff events for this context
        self._subscribe_to_handoffs(event_queue, context.task_context.id)
        
        # Get or create task
        task = context.current_task
        if not task:
            task = new_task(context.message)
            await event_queue.enqueue_event(task)
            context.current_task = task
        
        # Add user message to conversation history
        context.task_context = add_to_conversation_history(
            context.task_context, "user", context.get_user_input()
        )
        
        try:
            # Classify intent to determine target agent
            classification = await self._classify_intent(
                context.get_user_input(),
                context.get_conversation_history()
            )
            
            logger.info(f"Intent classified as: {classification['domain']} (confidence: {classification['confidence']})")
            
            # Send classification status
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.working,
                        message=new_agent_text_message(
                            f"Routing to {classification['domain']} agent...",
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
            
            # Route to appropriate agent
            await self._route_to_agent(
                classification["domain"],
                context,
                event_queue,
                task
            )
            
        except Exception as e:
            logger.error(f"Error in coordinator execution: {e}")
            await self._handle_execution_error(context, event_queue, e)
    
    async def _classify_intent(
        self,
        user_message: str,
        conversation_history: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """Classify user intent to determine target agent"""
        if self.handoff_service:
            try:
                return self.handoff_service.classify_intent(
                    user_message=user_message,
                    conversation_history=conversation_history
                )
            except Exception as e:
                logger.warning(f"Handoff service classification failed: {e}")
        
        # Fallback to simple keyword-based classification
        return self._simple_classification(user_message)
    
    def _simple_classification(self, user_message: str) -> Dict[str, Any]:
        """Simple keyword-based classification fallback"""
        message_lower = user_message.lower()
        
        # Define keyword patterns for each domain
        domain_patterns = {
            "interior_design": [
                "design", "color", "paint", "room", "style", "decor", 
                "furniture", "interior", "aesthetic"
            ],
            "inventory": [
                "stock", "available", "inventory", "in store", "quantity",
                "do you have", "is there"
            ],
            "customer_loyalty": [
                "discount", "loyalty", "points", "member", "reward",
                "savings", "deal"
            ],
            "cart_management": [
                "cart", "add", "remove", "purchase", "buy", "checkout",
                "order", "item"
            ],
            "cora": [
                "help", "information", "question", "what is", "tell me about"
            ]
        }
        
        # Score each domain based on keyword matches
        scores = {}
        for domain, keywords in domain_patterns.items():
            score = sum(1 for keyword in keywords if keyword in message_lower)
            if score > 0:
                scores[domain] = score
        
        # Determine best domain
        if scores:
            best_domain = max(scores, key=scores.get)
            confidence = min(0.8, scores[best_domain] * 0.2)  # Max 0.8 confidence
        else:
            best_domain = "product_management"  # Default to product management
            confidence = 0.3
        
        return {
            "domain": best_domain,
            "confidence": confidence,
            "reasoning": f"Keyword-based classification: {scores}"
        }
    
    async def _route_to_agent(
        self,
        domain: str,
        context: RequestContext,
        event_queue: EventQueue,
        task
    ) -> None:
        """Route request to the appropriate agent"""
        if domain not in self.agents:
            logger.warning(f"Unknown domain: {domain}, falling back to product_management")
            domain = "product_management"
        
        target_agent = self.agents[domain]
        self.active_handoffs[task.id] = domain
        
        logger.info(f"Routing task {task.id} to {domain} agent")
        
        # Update task assignment
        task.assigned_agent = domain
        task.state = TaskState.assigned
        
        # Execute the target agent
        try:
            await target_agent.execute(context, event_queue)
        except Exception as e:
            logger.error(f"Error executing {domain} agent: {e}")
            # Remove from active handoffs on error
            if task.id in self.active_handoffs:
                del self.active_handoffs[task.id]
            raise
        finally:
            # Clean up tracking
            if task.id in self.active_handoffs:
                del self.active_handoffs[task.id]
    
    def _subscribe_to_handoffs(self, event_queue: EventQueue, context_id: str) -> None:
        """Subscribe to handoff events for managing agent-to-agent transfers"""
        subscription_id = event_queue.subscribe_to_event_type(
            EventType.agent_handoff,
            self._handle_handoff_event
        )
        self._handoff_subscriptions.append(subscription_id)
    
    async def _handle_handoff_event(self, event: AgentHandoffEvent) -> None:
        """Handle handoff events between agents"""
        logger.info(f"Handling handoff from {event.from_agent} to {event.to_agent} for task {event.taskId}")
        
        try:
            # Update tracking
            self.active_handoffs[event.taskId] = event.to_agent
            
            # Here you would implement the logic to transfer the task to the new agent
            # For now, we'll just log the handoff
            logger.info(f"Task {event.taskId} handed off from {event.from_agent} to {event.to_agent}")
            
        except Exception as e:
            logger.error(f"Error handling handoff event: {e}")
    
    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """Cancel coordination and any active agent executions"""
        task_id = context.current_task.id if context.current_task else "unknown"
        
        # Cancel any active agent execution
        if task_id in self.active_handoffs:
            domain = self.active_handoffs[task_id]
            target_agent = self.agents.get(domain)
            
            if target_agent:
                try:
                    await target_agent.cancel(context, event_queue)
                except Exception as e:
                    logger.error(f"Error cancelling {domain} agent: {e}")
            
            del self.active_handoffs[task_id]
        
        # Call parent cancellation
        await super().cancel(context, event_queue)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get coordinator statistics"""
        base_stats = super().get_stats()
        
        agent_stats = {}
        for domain, agent in self.agents.items():
            if hasattr(agent, 'get_stats'):
                agent_stats[domain] = agent.get_stats()
        
        return {
            **base_stats,
            "coordinator_stats": {
                "active_handoffs": len(self.active_handoffs),
                "available_agents": list(self.agents.keys()),
                "handoff_service_available": self.handoff_service is not None
            },
            "agent_stats": agent_stats
        }
    
    async def get_agent_capabilities(self) -> Dict[str, Any]:
        """Get capabilities of all managed agents"""
        capabilities = {}
        
        for domain, agent in self.agents.items():
            capabilities[domain] = {
                "agent_name": agent.get_agent_name(),
                "supported_domains": agent.get_supported_domains(),
                "available": True  # Could check actual availability
            }
        
        return capabilities


class EnhancedProductManagementAgent(BaseAgentExecutor):
    """
    Enhanced Product Management Agent that integrates with A2A coordinator.
    
    This is the main entry point agent that uses the coordinator for
    multi-agent orchestration while providing a single interface.
    """
    
    def __init__(self):
        super().__init__(
            agent_name="EnhancedProductManagementAgent",
            supported_domains=["product_management", "shopping", "assistance"]
        )
        self.coordinator = A2ACoordinatorAgent()
    
    async def _execute_impl(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """Execute using the coordinator for intelligent routing"""
        logger.info("Enhanced Product Management Agent delegating to coordinator")
        
        # Delegate to coordinator
        await self.coordinator.execute(context, event_queue)
    
    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """Cancel execution via coordinator"""
        await self.coordinator.cancel(context, event_queue)
    
    def get_stats(self) -> Dict[str, Any]:
        """Get combined statistics"""
        base_stats = super().get_stats()
        coordinator_stats = self.coordinator.get_stats()
        
        return {
            **base_stats,
            "coordinator": coordinator_stats
        }
    
    async def get_agent_capabilities(self) -> Dict[str, Any]:
        """Get capabilities from coordinator"""
        return await self.coordinator.get_agent_capabilities()