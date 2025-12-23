"""
Task management and push notification services for A2A Protocol
"""
import asyncio
import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set
import httpx
from ..types import Task, TaskContext, TaskState


logger = logging.getLogger(__name__)


class TaskStore(ABC):
    """Abstract base class for task storage"""
    
    @abstractmethod
    async def create_task(self, task: Task) -> Task:
        """Create a new task"""
        pass
    
    @abstractmethod
    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        pass
    
    @abstractmethod
    async def update_task(self, task: Task) -> Task:
        """Update an existing task"""
        pass
    
    @abstractmethod
    async def list_tasks(
        self, 
        context_id: Optional[str] = None,
        state: Optional[TaskState] = None,
        limit: int = 100
    ) -> List[Task]:
        """List tasks with optional filtering"""
        pass
    
    @abstractmethod
    async def delete_task(self, task_id: str) -> bool:
        """Delete a task"""
        pass


class InMemoryTaskStore(TaskStore):
    """In-memory implementation of task store"""
    
    def __init__(self):
        self._tasks: Dict[str, Task] = {}
        self._context_tasks: Dict[str, Set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
    
    async def create_task(self, task: Task) -> Task:
        """Create a new task"""
        async with self._lock:
            self._tasks[task.id] = task
            self._context_tasks[task.contextId].add(task.id)
            logger.debug(f"Created task {task.id} in context {task.contextId}")
            return task
    
    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get task by ID"""
        async with self._lock:
            return self._tasks.get(task_id)
    
    async def update_task(self, task: Task) -> Task:
        """Update an existing task"""
        async with self._lock:
            if task.id in self._tasks:
                task.updated_at = datetime.utcnow()
                self._tasks[task.id] = task
                logger.debug(f"Updated task {task.id}")
            return task
    
    async def list_tasks(
        self, 
        context_id: Optional[str] = None,
        state: Optional[TaskState] = None,
        limit: int = 100
    ) -> List[Task]:
        """List tasks with optional filtering"""
        async with self._lock:
            tasks = list(self._tasks.values())
            
            # Filter by context
            if context_id:
                tasks = [t for t in tasks if t.contextId == context_id]
            
            # Filter by state
            if state:
                tasks = [t for t in tasks if t.state == state]
            
            # Sort by creation time (newest first)
            tasks.sort(key=lambda t: t.created_at, reverse=True)
            
            # Apply limit
            return tasks[:limit]
    
    async def delete_task(self, task_id: str) -> bool:
        """Delete a task"""
        async with self._lock:
            if task_id in self._tasks:
                task = self._tasks[task_id]
                del self._tasks[task_id]
                self._context_tasks[task.contextId].discard(task_id)
                logger.debug(f"Deleted task {task_id}")
                return True
            return False
    
    async def cleanup_old_tasks(self, max_age_hours: int = 24) -> int:
        """Clean up tasks older than specified hours"""
        cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
        deleted_count = 0
        
        async with self._lock:
            old_task_ids = [
                task_id for task_id, task in self._tasks.items()
                if task.created_at < cutoff_time
            ]
            
            for task_id in old_task_ids:
                task = self._tasks[task_id]
                del self._tasks[task_id]
                self._context_tasks[task.contextId].discard(task_id)
                deleted_count += 1
        
        logger.info(f"Cleaned up {deleted_count} old tasks")
        return deleted_count


class PushNotificationConfig(ABC):
    """Base class for push notification configuration"""
    pass


class InMemoryPushNotificationConfigStore:
    """In-memory store for push notification configurations"""
    
    def __init__(self):
        self._configs: Dict[str, PushNotificationConfig] = {}
        self._lock = asyncio.Lock()
    
    async def get_config(self, context_id: str) -> Optional[PushNotificationConfig]:
        """Get push notification config for a context"""
        async with self._lock:
            return self._configs.get(context_id)
    
    async def set_config(self, context_id: str, config: PushNotificationConfig) -> None:
        """Set push notification config for a context"""
        async with self._lock:
            self._configs[context_id] = config
    
    async def delete_config(self, context_id: str) -> bool:
        """Delete push notification config"""
        async with self._lock:
            if context_id in self._configs:
                del self._configs[context_id]
                return True
            return False


class BasePushNotificationSender:
    """
    Basic push notification sender using HTTP requests.
    
    This is a simple implementation that can be extended for specific
    push notification services like Firebase, AWS SNS, etc.
    """
    
    def __init__(
        self, 
        httpx_client: httpx.AsyncClient,
        config_store: InMemoryPushNotificationConfigStore
    ):
        self.httpx_client = httpx_client
        self.config_store = config_store
    
    async def send_notification(
        self, 
        context_id: str, 
        title: str, 
        message: str,
        data: Optional[Dict[str, Any]] = None
    ) -> bool:
        """
        Send a push notification.
        
        Args:
            context_id: Context ID to send notification to
            title: Notification title
            message: Notification message
            data: Additional data to include
            
        Returns:
            True if notification was sent successfully
        """
        try:
            config = await self.config_store.get_config(context_id)
            if not config:
                logger.debug(f"No push config found for context {context_id}")
                return False
            
            # For now, just log the notification
            # In a real implementation, this would send via the configured service
            logger.info(
                f"Push notification for {context_id}: {title} - {message}"
            )
            
            return True
            
        except Exception as e:
            logger.error(f"Failed to send push notification: {e}")
            return False


class ContextStore(ABC):
    """Abstract base class for context storage"""
    
    @abstractmethod
    async def create_context(self, context: TaskContext) -> TaskContext:
        """Create a new context"""
        pass
    
    @abstractmethod
    async def get_context(self, context_id: str) -> Optional[TaskContext]:
        """Get context by ID"""
        pass
    
    @abstractmethod
    async def update_context(self, context: TaskContext) -> TaskContext:
        """Update an existing context"""
        pass
    
    @abstractmethod
    async def list_contexts(
        self,
        user_id: Optional[str] = None,
        limit: int = 100
    ) -> List[TaskContext]:
        """List contexts with optional filtering"""
        pass
    
    @abstractmethod
    async def delete_context(self, context_id: str) -> bool:
        """Delete a context"""
        pass


class InMemoryContextStore(ContextStore):
    """In-memory implementation of context store"""
    
    def __init__(self):
        self._contexts: Dict[str, TaskContext] = {}
        self._user_contexts: Dict[str, Set[str]] = defaultdict(set)
        self._lock = asyncio.Lock()
    
    async def create_context(self, context: TaskContext) -> TaskContext:
        """Create a new context"""
        async with self._lock:
            self._contexts[context.id] = context
            if context.user_id:
                self._user_contexts[context.user_id].add(context.id)
            logger.debug(f"Created context {context.id}")
            return context
    
    async def get_context(self, context_id: str) -> Optional[TaskContext]:
        """Get context by ID"""
        async with self._lock:
            return self._contexts.get(context_id)
    
    async def update_context(self, context: TaskContext) -> TaskContext:
        """Update an existing context"""
        async with self._lock:
            if context.id in self._contexts:
                context.updated_at = datetime.utcnow()
                self._contexts[context.id] = context
                logger.debug(f"Updated context {context.id}")
            return context
    
    async def list_contexts(
        self,
        user_id: Optional[str] = None,
        limit: int = 100
    ) -> List[TaskContext]:
        """List contexts with optional filtering"""
        async with self._lock:
            contexts = list(self._contexts.values())
            
            # Filter by user
            if user_id:
                contexts = [c for c in contexts if c.user_id == user_id]
            
            # Sort by creation time (newest first)
            contexts.sort(key=lambda c: c.created_at, reverse=True)
            
            # Apply limit
            return contexts[:limit]
    
    async def delete_context(self, context_id: str) -> bool:
        """Delete a context"""
        async with self._lock:
            if context_id in self._contexts:
                context = self._contexts[context_id]
                del self._contexts[context_id]
                if context.user_id:
                    self._user_contexts[context.user_id].discard(context_id)
                logger.debug(f"Deleted context {context_id}")
                return True
            return False
    
    async def cleanup_old_contexts(self, max_age_hours: int = 48) -> int:
        """Clean up contexts older than specified hours"""
        cutoff_time = datetime.utcnow() - timedelta(hours=max_age_hours)
        deleted_count = 0
        
        async with self._lock:
            old_context_ids = [
                context_id for context_id, context in self._contexts.items()
                if context.created_at < cutoff_time
            ]
            
            for context_id in old_context_ids:
                context = self._contexts[context_id]
                del self._contexts[context_id]
                if context.user_id:
                    self._user_contexts[context.user_id].discard(context_id)
                deleted_count += 1
        
        logger.info(f"Cleaned up {deleted_count} old contexts")
        return deleted_count