"""
Agent Execution Framework for A2A Protocol

This module provides the base classes and infrastructure for executing agents
within the A2A protocol framework.
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional
from datetime import datetime

from ..types import Task, TaskContext, AgentMessage
from .events.event_queue import EventQueue


logger = logging.getLogger(__name__)


class RequestContext:
    """
    Context for agent request execution.
    
    Contains all the information needed for an agent to process a request
    including the current task, conversation history, and shared data.
    """
    
    def __init__(
        self,
        message: AgentMessage,
        task_context: TaskContext,
        current_task: Optional[Task] = None,
        additional_data: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.task_context = task_context
        self.current_task = current_task
        self.additional_data = additional_data or {}
        self.start_time = datetime.utcnow()
        self._user_input = None
    
    def get_user_input(self) -> str:
        """Get the user input from the message"""
        if self._user_input is None:
            self._user_input = self.message.content.strip()
        return self._user_input
    
    def get_conversation_history(self, limit: int = 10) -> list:
        """Get conversation history with optional limit"""
        history = self.task_context.conversation_history
        if limit > 0:
            return history[-limit:]
        return history
    
    def get_shared_data(self, key: str, default: Any = None) -> Any:
        """Get shared data from task context"""
        return self.task_context.shared_data.get(key, default)
    
    def set_shared_data(self, key: str, value: Any) -> None:
        """Set shared data in task context"""
        self.task_context.shared_data[key] = value
        self.task_context.updated_at = datetime.utcnow()
    
    def get_cart(self) -> list:
        """Get shopping cart from shared data"""
        return self.get_shared_data("cart", [])
    
    def set_cart(self, cart: list) -> None:
        """Set shopping cart in shared data"""
        self.set_shared_data("cart", cart)
    
    def get_customer_data(self) -> dict:
        """Get customer data from shared data"""
        return self.get_shared_data("customer", {})
    
    def set_customer_data(self, customer_data: dict) -> None:
        """Set customer data in shared data"""
        self.set_shared_data("customer", customer_data)


class AgentExecutor(ABC):
    """
    Abstract base class for agent executors.
    
    Agent executors are responsible for processing requests and generating responses
    within the A2A protocol framework. Each agent should implement this interface.
    """
    
    @abstractmethod
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """
        Execute the agent request.
        
        Args:
            context: Request context containing user input and task info
            event_queue: Event queue for publishing task updates
        """
        pass
    
    @abstractmethod
    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """
        Cancel the current task execution.
        
        Args:
            context: Request context for the task to cancel
            event_queue: Event queue for publishing cancellation events
        """
        pass
    
    def get_agent_name(self) -> str:
        """Get the name of this agent"""
        return self.__class__.__name__
    
    def get_supported_domains(self) -> list:
        """Get list of domains this agent supports"""
        return ["general"]
    
    def get_confidence_for_task(self, user_input: str) -> float:
        """
        Get confidence score for handling this task.
        
        Args:
            user_input: The user's input message
            
        Returns:
            Confidence score between 0.0 and 1.0
        """
        return 0.5  # Default medium confidence


class ExecutionResult:
    """
    Result of agent execution.
    
    Contains information about the execution result including success status,
    response content, and any artifacts generated.
    """
    
    def __init__(
        self,
        success: bool,
        content: str,
        requires_input: bool = False,
        is_complete: bool = True,
        artifacts: Optional[list] = None,
        error: Optional[str] = None,
        handoff_request: Optional[Dict[str, Any]] = None
    ):
        self.success = success
        self.content = content
        self.requires_input = requires_input
        self.is_complete = is_complete
        self.artifacts = artifacts or []
        self.error = error
        self.handoff_request = handoff_request
        self.timestamp = datetime.utcnow()


class BaseAgentExecutor(AgentExecutor):
    """
    Base implementation of AgentExecutor with common functionality.
    
    Provides helper methods and default implementations that can be used
    by concrete agent executors.
    """
    
    def __init__(self, agent_name: str = None, supported_domains: list = None):
        self.agent_name = agent_name or self.__class__.__name__
        self.supported_domains = supported_domains or ["general"]
        self._execution_count = 0
        self._error_count = 0
        self._start_time = datetime.utcnow()
    
    def get_agent_name(self) -> str:
        """Get the name of this agent"""
        return self.agent_name
    
    def get_supported_domains(self) -> list:
        """Get list of domains this agent supports"""
        return self.supported_domains
    
    async def execute(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """
        Execute the agent request with error handling and metrics.
        
        Args:
            context: Request context containing user input and task info
            event_queue: Event queue for publishing task updates
        """
        self._execution_count += 1
        
        try:
            logger.info(f"Starting execution for agent {self.agent_name}")
            await self._execute_impl(context, event_queue)
            logger.info(f"Completed execution for agent {self.agent_name}")
            
        except Exception as e:
            self._error_count += 1
            logger.error(f"Error in agent {self.agent_name}: {e}", exc_info=True)
            await self._handle_execution_error(context, event_queue, e)
    
    @abstractmethod
    async def _execute_impl(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """
        Concrete implementation of execution logic.
        
        Subclasses must implement this method with their specific logic.
        """
        pass
    
    async def _handle_execution_error(
        self,
        context: RequestContext,
        event_queue: EventQueue,
        error: Exception
    ) -> None:
        """
        Handle execution errors by publishing error events.
        
        Args:
            context: Request context
            event_queue: Event queue for publishing error events
            error: The exception that occurred
        """
        from ..types import TaskStatusUpdateEvent, TaskStatus, TaskState
        from ..utils import new_agent_text_message
        
        error_message = f"I apologize, but I encountered an error while processing your request: {str(error)}"
        
        if context.current_task:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.failed,
                        message=new_agent_text_message(
                            error_message,
                            context.task_context.id,
                            context.current_task.id,
                            self.agent_name
                        ),
                        error_details=str(error)
                    ),
                    final=True,
                    contextId=context.task_context.id,
                    taskId=context.current_task.id
                )
            )
    
    async def cancel(
        self,
        context: RequestContext,
        event_queue: EventQueue
    ) -> None:
        """
        Default cancellation implementation.
        
        Can be overridden by subclasses for custom cancellation logic.
        """
        logger.warning(f"Cancellation requested for agent {self.agent_name}")
        
        from ..types import TaskStatusUpdateEvent, TaskStatus, TaskState
        from ..utils import new_agent_text_message
        
        if context.current_task:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    status=TaskStatus(
                        state=TaskState.cancelled,
                        message=new_agent_text_message(
                            "Task was cancelled",
                            context.task_context.id,
                            context.current_task.id,
                            self.agent_name
                        )
                    ),
                    final=True,
                    contextId=context.task_context.id,
                    taskId=context.current_task.id
                )
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """Get execution statistics for this agent"""
        uptime = datetime.utcnow() - self._start_time
        
        return {
            "agent_name": self.agent_name,
            "supported_domains": self.supported_domains,
            "execution_count": self._execution_count,
            "error_count": self._error_count,
            "error_rate": self._error_count / max(1, self._execution_count),
            "uptime_seconds": uptime.total_seconds(),
            "start_time": self._start_time.isoformat()
        }