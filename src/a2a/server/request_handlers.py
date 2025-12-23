"""
Request handlers for the A2A Protocol Server

This module provides request handling infrastructure that manages the flow
between incoming requests, agent execution, and response generation.
"""
import asyncio
import logging
from typing import Any, Dict, Optional
from datetime import datetime

from .agent_execution import AgentExecutor, RequestContext
from .events.event_queue import EventQueue
from .tasks import TaskStore, InMemoryPushNotificationConfigStore, BasePushNotificationSender, ContextStore, InMemoryContextStore
from ..types import (
    AgentMessage, Task, TaskContext, TaskState, TaskStatus, 
    TaskStatusUpdateEvent, EventType
)
from ..utils import new_task, new_context, new_agent_text_message


logger = logging.getLogger(__name__)


class RequestHandler:
    """
    Base request handler for A2A protocol.
    
    Manages the flow from incoming requests through agent execution
    and response generation.
    """
    
    def __init__(
        self,
        agent_executor: AgentExecutor,
        event_queue: EventQueue,
        task_store: TaskStore,
        context_store: ContextStore,
        push_config_store: InMemoryPushNotificationConfigStore,
        push_sender: BasePushNotificationSender
    ):
        self.agent_executor = agent_executor
        self.event_queue = event_queue
        self.task_store = task_store
        self.context_store = context_store
        self.push_config_store = push_config_store
        self.push_sender = push_sender
        self._active_executions: Dict[str, asyncio.Task] = {}
    
    async def handle_request(
        self,
        user_message: str,
        session_id: str,
        user_id: Optional[str] = None,
        context_id: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> RequestContext:
        """
        Handle an incoming request and start agent execution.
        
        Args:
            user_message: The user's message
            session_id: Session identifier
            user_id: Optional user identifier
            context_id: Optional context identifier
            additional_data: Additional data to pass to agent
            
        Returns:
            RequestContext for the created request
        """
        # Get or create context
        if context_id:
            task_context = await self.context_store.get_context(context_id)
            if not task_context:
                raise ValueError(f"Context {context_id} not found")
        else:
            task_context = new_context(session_id, user_id, additional_data)
            task_context = await self.context_store.create_context(task_context)
        
        # Create agent message
        agent_message = new_agent_text_message(
            content=user_message,
            context_id=task_context.id,
            task_id="",  # Will be set when task is created
            agent_id="user"
        )
        
        # Create request context
        request_context = RequestContext(
            message=agent_message,
            task_context=task_context,
            additional_data=additional_data or {}
        )
        
        # Start agent execution
        execution_task = asyncio.create_task(
            self._execute_agent(request_context)
        )
        
        # Store the execution task for potential cancellation
        self._active_executions[request_context.message.id] = execution_task
        
        return request_context
    
    async def _execute_agent(self, context: RequestContext) -> None:
        """Execute the agent for the given context"""
        try:
            await self.agent_executor.execute(context, self.event_queue)
        except Exception as e:
            logger.error(f"Error during agent execution: {e}", exc_info=True)
        finally:
            # Clean up execution tracking
            if context.message.id in self._active_executions:
                del self._active_executions[context.message.id]
    
    async def cancel_request(self, message_id: str) -> bool:
        """
        Cancel an active request.
        
        Args:
            message_id: ID of the message/request to cancel
            
        Returns:
            True if request was cancelled, False if not found
        """
        if message_id in self._active_executions:
            execution_task = self._active_executions[message_id]
            execution_task.cancel()
            
            try:
                await execution_task
            except asyncio.CancelledError:
                logger.info(f"Successfully cancelled request {message_id}")
                return True
            except Exception as e:
                logger.error(f"Error during request cancellation: {e}")
        
        return False
    
    async def get_active_requests(self) -> list:
        """Get list of active request IDs"""
        return list(self._active_executions.keys())


class DefaultRequestHandler(RequestHandler):
    """
    Default implementation of request handler with enhanced features.
    
    Provides additional functionality like conversation history management,
    context sharing, and automatic task state tracking.
    """
    
    def __init__(
        self,
        agent_executor: AgentExecutor,
        task_store: TaskStore,
        push_config_store: InMemoryPushNotificationConfigStore,
        push_sender: BasePushNotificationSender,
        context_store: Optional[ContextStore] = None,
        event_queue: Optional[EventQueue] = None
    ):
        from .events.event_queue import get_global_event_queue
        
        # Use provided or create default instances
        context_store = context_store or InMemoryContextStore()
        event_queue = event_queue or get_global_event_queue()
        
        super().__init__(
            agent_executor=agent_executor,
            event_queue=event_queue,
            task_store=task_store,
            context_store=context_store,
            push_config_store=push_config_store,
            push_sender=push_sender
        )
        
        # Subscribe to task events for automatic state management
        self.event_queue.subscribe_to_event_type(
            EventType.task_status_update,
            self._handle_task_status_update
        )
    
    async def handle_request(
        self,
        user_message: str,
        session_id: str,
        user_id: Optional[str] = None,
        context_id: Optional[str] = None,
        additional_data: Optional[Dict[str, Any]] = None
    ) -> RequestContext:
        """
        Enhanced request handling with conversation history.
        """
        # Get or create context
        if context_id:
            task_context = await self.context_store.get_context(context_id)
            if not task_context:
                raise ValueError(f"Context {context_id} not found")
        else:
            task_context = new_context(session_id, user_id, additional_data)
            task_context = await self.context_store.create_context(task_context)
        
        # Add user message to conversation history
        from ..utils import add_to_conversation_history
        task_context = add_to_conversation_history(
            task_context, "user", user_message
        )
        await self.context_store.update_context(task_context)
        
        # Create agent message
        agent_message = new_agent_text_message(
            content=user_message,
            context_id=task_context.id,
            task_id="",  # Will be set when task is created
            agent_id="user"
        )
        
        # Create request context with enhanced data
        enhanced_data = additional_data or {}
        enhanced_data.update({
            "conversation_history": task_context.conversation_history,
            "shared_data": task_context.shared_data
        })
        
        request_context = RequestContext(
            message=agent_message,
            task_context=task_context,
            additional_data=enhanced_data
        )
        
        # Start agent execution
        execution_task = asyncio.create_task(
            self._execute_agent_with_tracking(request_context)
        )
        
        # Store the execution task for potential cancellation
        self._active_executions[request_context.message.id] = execution_task
        
        return request_context
    
    async def _execute_agent_with_tracking(self, context: RequestContext) -> None:
        """Execute agent with enhanced tracking and context management"""
        try:
            # Create initial task if not exists
            if not context.current_task:
                task = new_task(context.message)
                context.current_task = await self.task_store.create_task(task)
                
                # Update message with task ID
                context.message.task_id = task.id
            
            # Execute the agent
            await self.agent_executor.execute(context, self.event_queue)
            
        except Exception as e:
            logger.error(f"Error during enhanced agent execution: {e}", exc_info=True)
            
            # Send error status update
            if context.current_task:
                await self.event_queue.enqueue_event(
                    TaskStatusUpdateEvent(
                        status=TaskStatus(
                            state=TaskState.failed,
                            message=new_agent_text_message(
                                f"Execution failed: {str(e)}",
                                context.task_context.id,
                                context.current_task.id,
                                self.agent_executor.get_agent_name()
                            ),
                            error_details=str(e)
                        ),
                        final=True,
                        contextId=context.task_context.id,
                        taskId=context.current_task.id
                    )
                )
        finally:
            # Clean up execution tracking
            if context.message.id in self._active_executions:
                del self._active_executions[context.message.id]
    
    async def _handle_task_status_update(self, event: TaskStatusUpdateEvent) -> None:
        """Handle task status update events"""
        # Update task in store
        task = await self.task_store.get_task(event.taskId)
        if task:
            task.state = event.status.state
            task.updated_at = datetime.utcnow()
            if event.status.message:
                task.metadata["last_message"] = event.status.message.content
            await self.task_store.update_task(task)
        
        # Send push notification if configured
        if event.final and event.status.message:
            await self.push_sender.send_notification(
                context_id=event.contextId,
                title="Task Update",
                message=event.status.message.content,
                data={"task_id": event.taskId, "state": event.status.state}
            )
    
    async def get_context_history(
        self, 
        context_id: str, 
        limit: int = 50
    ) -> Dict[str, Any]:
        """
        Get conversation history and context data.
        
        Args:
            context_id: Context identifier
            limit: Maximum number of history entries
            
        Returns:
            Dictionary with conversation history and context data
        """
        context = await self.context_store.get_context(context_id)
        if not context:
            return {"error": "Context not found"}
        
        history = context.conversation_history[-limit:] if limit > 0 else context.conversation_history
        
        return {
            "context_id": context_id,
            "conversation_history": history,
            "shared_data": context.shared_data,
            "created_at": context.created_at.isoformat(),
            "updated_at": context.updated_at.isoformat()
        }
    
    async def clear_context(self, context_id: str) -> bool:
        """
        Clear a context and all associated data.
        
        Args:
            context_id: Context identifier to clear
            
        Returns:
            True if context was cleared successfully
        """
        # Clear context from store
        success = await self.context_store.delete_context(context_id)
        
        if success:
            # Clear associated events
            await self.event_queue.clear_context_events(context_id)
            
            # Clear associated tasks
            tasks = await self.task_store.list_tasks(context_id=context_id)
            for task in tasks:
                await self.task_store.delete_task(task.id)
            
            logger.info(f"Cleared context {context_id} and associated data")
        
        return success