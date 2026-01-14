"""
A2A (Agent-to-Agent) Framework - Agent Coordinator
Coordinates interactions between the multiple agents in Zava Media AI system.
"""
import os
import logging
from typing import Dict, Any, Optional
from services.hybrid_agent_service import HybridAgentService
from services.handoff_service import HandoffService

logger = logging.getLogger(__name__)

class AgentCoordinator:
    """Coordinates agent interactions and routing."""
    
    def __init__(self):
        """Initialize agent coordinator."""
        try:
            self.hybrid_service = HybridAgentService()
            self.handoff_service = HandoffService()
            self.available = True
        except Exception as e:
            logger.error(f"Failed to initialize agent coordinator: {e}")
            self.available = False
    
    def route_request(self, user_message: str, context: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Route user request to appropriate agent.
        
        Args:
            user_message: User's message
            context: Optional context information
            
        Returns:
            Response from appropriate agent
        """
        if not self.available:
            return {
                "error": "Agent coordinator unavailable",
                "fallback": True
            }
        
        try:
            # Classify intent to determine routing
            classification = self.handoff_service.classify_intent(
                user_message,
                context.get("conversation_history") if context else None
            )
            
            # Route to hybrid service
            response = self.hybrid_service.process_message(
                user_message,
                context or {}
            )
            
            return {
                "response": response,
                "classification": classification,
                "routed_successfully": True
            }
            
        except Exception as e:
            logger.error(f"Agent routing failed: {e}")
            return {
                "error": str(e),
                "fallback": True
            }
    
    def get_agent_status(self) -> Dict[str, str]:
        """Get status of all agents."""
        if not self.available:
            return {"status": "unavailable"}
        
        try:
            return {
                "coordinator": "active",
                "hybrid_service": "active" if self.hybrid_service else "inactive",
                "handoff_service": "active" if self.handoff_service else "inactive"
            }
        except:
            return {"status": "error"}

if __name__ == "__main__":
    coordinator = AgentCoordinator()
    status = coordinator.get_agent_status()
    print(f"Agent Status: {status}")