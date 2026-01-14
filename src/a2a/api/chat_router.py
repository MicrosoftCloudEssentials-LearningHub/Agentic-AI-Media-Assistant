"""
A2A (Agent-to-Agent) Framework - Chat Router
Routes chat requests through the agent system.
"""
import os
import logging
from typing import Dict, Any, Optional
from a2a.agent.coordinator import AgentCoordinator

logger = logging.getLogger(__name__)

class ChatRouter:
    """Routes chat requests through the multi-agent system."""
    
    def __init__(self):
        """Initialize chat router."""
        try:
            self.coordinator = AgentCoordinator()
        except Exception as e:
            logger.error(f"Failed to initialize chat router: {e}")
            self.coordinator = None
    
    def process_chat(self, message: str, session_id: str = "default", context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Process a chat message through the agent system.
        
        Args:
            message: User's chat message
            session_id: Session identifier
            context: Optional context
            
        Returns:
            Chat response
        """
        if not self.coordinator:
            return {
                "response": "Chat system temporarily unavailable",
                "error": True
            }
        
        try:
            # Route through coordinator
            result = self.coordinator.route_request(message, context)
            
            if result.get("error"):
                return {
                    "response": "I'm having trouble processing your request right now. Please try again.",
                    "error": True
                }
            
            return {
                "response": result.get("response", "No response generated"),
                "session_id": session_id,
                "classification": result.get("classification"),
                "error": False
            }
            
        except Exception as e:
            logger.error(f"Chat processing failed: {e}")
            return {
                "response": "An error occurred processing your message",
                "error": True
            }
    
    def health_check(self) -> Dict[str, Any]:
        """Check health of chat routing system."""
        try:
            if not self.coordinator:
                return {"status": "unhealthy", "reason": "coordinator_unavailable"}
            
            agent_status = self.coordinator.get_agent_status()
            return {
                "status": "healthy" if agent_status.get("coordinator") == "active" else "degraded",
                "agents": agent_status
            }
        except:
            return {"status": "unhealthy", "reason": "health_check_failed"}

if __name__ == "__main__":
    router = ChatRouter()
    health = router.health_check()
    print(f"Chat Router Health: {health}")