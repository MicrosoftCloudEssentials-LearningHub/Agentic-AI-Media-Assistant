"""
Event Queue Implementation for A2A Protocol

This module provides an event queue system for managing events in the A2A protocol.
Events are used to communicate between different components and track task progress.
"""
import asyncio
import logging
from collections import deque
from typing import Any, Callable, Deque, Dict, List, Optional
from datetime import datetime, timedelta

from ...types import BaseEvent, EventType


logger = logging.getLogger(__name__)


class EventQueue:
    """
    Asynchronous event queue for A2A protocol events.
    
    Handles event publishing, subscription, and delivery with support
    for event filtering and batch processing.
    """
    
    def __init__(self, max_size: int = 1000, event_ttl_seconds: int = 3600):
        """
        Initialize the event queue.
        
        Args:
            max_size: Maximum number of events to keep in queue
            event_ttl_seconds: Time to live for events in seconds
        """
        self.max_size = max_size
        self.event_ttl = timedelta(seconds=event_ttl_seconds)
        self._events: Deque[BaseEvent] = deque(maxlen=max_size)
        self._subscribers: Dict[EventType, List[Callable]] = {}
        self._context_subscribers: Dict[str, List[Callable]] = {}
        self._lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        
        # Start cleanup task
        self._start_cleanup_task()
    
    def _start_cleanup_task(self):
        """Start the background cleanup task"""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._cleanup_expired_events())
    
    async def _cleanup_expired_events(self):
        """Remove expired events from the queue"""
        while True:
            try:
                await asyncio.sleep(300)  # Clean up every 5 minutes
                async with self._lock:
                    current_time = datetime.utcnow()
                    # Convert deque to list for iteration, then rebuild deque
                    valid_events = [
                        event for event in self._events
                        if current_time - event.timestamp < self.event_ttl
                    ]
                    self._events.clear()
                    self._events.extend(valid_events)
                    
                logger.debug(f"Cleaned up expired events. Current queue size: {len(self._events)}")
                    
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error during event cleanup: {e}")
    
    async def enqueue_event(self, event: BaseEvent) -> None:
        """
        Add an event to the queue and notify subscribers.
        
        Args:
            event: The event to add to the queue
        """
        async with self._lock:
            self._events.append(event)
            logger.debug(f"Enqueued event: {event.type} for context {event.contextId}")
        
        # Notify subscribers asynchronously
        await self._notify_subscribers(event)
    
    async def _notify_subscribers(self, event: BaseEvent) -> None:
        """Notify all relevant subscribers about the event"""
        notification_tasks = []
        
        # Notify type-based subscribers
        if event.type in self._subscribers:
            for callback in self._subscribers[event.type]:
                task = asyncio.create_task(self._safe_callback(callback, event))
                notification_tasks.append(task)
        
        # Notify context-based subscribers
        if event.contextId in self._context_subscribers:
            for callback in self._context_subscribers[event.contextId]:
                task = asyncio.create_task(self._safe_callback(callback, event))
                notification_tasks.append(task)
        
        # Wait for all notifications to complete
        if notification_tasks:
            await asyncio.gather(*notification_tasks, return_exceptions=True)
    
    async def _safe_callback(self, callback: Callable, event: BaseEvent) -> None:
        """Safely execute a callback, catching any exceptions"""
        try:
            if asyncio.iscoroutinefunction(callback):
                await callback(event)
            else:
                callback(event)
        except Exception as e:
            logger.error(f"Error in event callback: {e}")
    
    def subscribe_to_event_type(
        self,
        event_type: EventType,
        callback: Callable[[BaseEvent], Any]
    ) -> str:
        """
        Subscribe to events of a specific type.
        
        Args:
            event_type: Type of events to subscribe to
            callback: Function to call when event occurs
            
        Returns:
            Subscription ID for unsubscribing
        """
        if event_type not in self._subscribers:
            self._subscribers[event_type] = []
        
        self._subscribers[event_type].append(callback)
        subscription_id = f"{event_type}_{len(self._subscribers[event_type])}"
        
        logger.debug(f"Added subscription for event type: {event_type}")
        return subscription_id
    
    def subscribe_to_context(
        self,
        context_id: str,
        callback: Callable[[BaseEvent], Any]
    ) -> str:
        """
        Subscribe to events for a specific context.
        
        Args:
            context_id: Context ID to subscribe to
            callback: Function to call when event occurs
            
        Returns:
            Subscription ID for unsubscribing
        """
        if context_id not in self._context_subscribers:
            self._context_subscribers[context_id] = []
        
        self._context_subscribers[context_id].append(callback)
        subscription_id = f"context_{context_id}_{len(self._context_subscribers[context_id])}"
        
        logger.debug(f"Added subscription for context: {context_id}")
        return subscription_id
    
    async def get_events_for_context(
        self,
        context_id: str,
        event_types: Optional[List[EventType]] = None,
        limit: Optional[int] = None
    ) -> List[BaseEvent]:
        """
        Get events for a specific context.
        
        Args:
            context_id: Context ID to filter by
            event_types: Optional list of event types to filter by
            limit: Maximum number of events to return
            
        Returns:
            List of matching events
        """
        async with self._lock:
            events = [
                event for event in self._events
                if event.contextId == context_id
            ]
            
            if event_types:
                events = [
                    event for event in events
                    if event.type in event_types
                ]
            
            # Sort by timestamp (most recent first)
            events.sort(key=lambda e: e.timestamp, reverse=True)
            
            if limit:
                events = events[:limit]
            
            return events
    
    async def get_recent_events(
        self,
        minutes: int = 10,
        event_types: Optional[List[EventType]] = None
    ) -> List[BaseEvent]:
        """
        Get events from the last N minutes.
        
        Args:
            minutes: Number of minutes to look back
            event_types: Optional list of event types to filter by
            
        Returns:
            List of matching events
        """
        cutoff_time = datetime.utcnow() - timedelta(minutes=minutes)
        
        async with self._lock:
            events = [
                event for event in self._events
                if event.timestamp >= cutoff_time
            ]
            
            if event_types:
                events = [
                    event for event in events
                    if event.type in event_types
                ]
            
            # Sort by timestamp (most recent first)
            events.sort(key=lambda e: e.timestamp, reverse=True)
            
            return events
    
    async def clear_context_events(self, context_id: str) -> int:
        """
        Clear all events for a specific context.
        
        Args:
            context_id: Context ID to clear events for
            
        Returns:
            Number of events cleared
        """
        async with self._lock:
            initial_count = len(self._events)
            # Rebuild deque without events for this context
            filtered_events = [
                event for event in self._events
                if event.contextId != context_id
            ]
            self._events.clear()
            self._events.extend(filtered_events)
            
            cleared_count = initial_count - len(self._events)
            logger.info(f"Cleared {cleared_count} events for context {context_id}")
            
            return cleared_count
    
    async def get_queue_stats(self) -> Dict[str, Any]:
        """
        Get statistics about the event queue.
        
        Returns:
            Dictionary with queue statistics
        """
        async with self._lock:
            event_type_counts = {}
            context_counts = {}
            
            for event in self._events:
                # Count by event type
                event_type_counts[event.type] = event_type_counts.get(event.type, 0) + 1
                
                # Count by context
                context_counts[event.contextId] = context_counts.get(event.contextId, 0) + 1
            
            return {
                "total_events": len(self._events),
                "max_size": self.max_size,
                "event_type_counts": event_type_counts,
                "context_counts": context_counts,
                "subscriber_counts": {
                    "type_subscribers": len(self._subscribers),
                    "context_subscribers": len(self._context_subscribers)
                },
                "oldest_event": min(event.timestamp for event in self._events) if self._events else None,
                "newest_event": max(event.timestamp for event in self._events) if self._events else None
            }
    
    def stop(self):
        """Stop the event queue and cleanup background tasks"""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
        
        logger.info("Event queue stopped")


# Global event queue instance
_global_event_queue: Optional[EventQueue] = None


def get_global_event_queue() -> EventQueue:
    """Get or create the global event queue instance"""
    global _global_event_queue
    if _global_event_queue is None:
        _global_event_queue = EventQueue()
    return _global_event_queue


def set_global_event_queue(event_queue: EventQueue) -> None:
    """Set the global event queue instance"""
    global _global_event_queue
    _global_event_queue = event_queue