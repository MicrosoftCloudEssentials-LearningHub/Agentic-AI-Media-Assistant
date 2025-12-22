"""
A2A Server Management API

This module provides FastAPI endpoints for managing the A2A server,
including agent registration, discovery, and system administration.
"""
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from datetime import datetime

from ..agent.coordinator import EnhancedProductManagementAgent
from ..server import get_global_event_queue
from ..types import AgentCard, AgentCapabilities, AgentSkill


logger = logging.getLogger(__name__)


# Request/Response Models
class AgentRegistrationRequest(BaseModel):
    """Request model for agent registration"""
    agent_card: AgentCard
    endpoint_url: str
    authentication: Optional[Dict[str, Any]] = None


class AgentDiscoveryResponse(BaseModel):
    """Response model for agent discovery"""
    agents: List[AgentCard]
    total_count: int
    available_domains: List[str]


class SystemStatsResponse(BaseModel):
    """Response model for system statistics"""
    uptime_seconds: float
    total_requests: int
    active_sessions: int
    event_queue_size: int
    agent_stats: Dict[str, Any]


class HealthCheckResponse(BaseModel):
    """Response model for health checks"""
    status: str
    timestamp: str
    version: str
    components: Dict[str, str]


class A2AServerRouter:
    """
    FastAPI router for A2A server management functionality.
    
    Provides endpoints for agent discovery, registration, system monitoring,
    and administrative functions.
    """
    
    def __init__(self, enhanced_agent: Optional[EnhancedProductManagementAgent] = None):
        self.router = APIRouter(prefix="/a2a/server", tags=["a2a-server"])
        self.enhanced_agent = enhanced_agent or EnhancedProductManagementAgent()
        self.event_queue = get_global_event_queue()
        
        # Registry for external agents (in production, use database)
        self.registered_agents: Dict[str, AgentCard] = {}
        self.start_time = datetime.utcnow()
        
        # Register routes
        self._register_routes()
        
        logger.info("A2A Server Router initialized")
    
    def _register_routes(self):
        """Register all server management routes"""
        
        @self.router.get("/", response_model=AgentCard)
        async def get_server_agent_card():
            """Get the agent card for the main server agent"""
            return await self._get_main_agent_card()
        
        @self.router.get("/health", response_model=HealthCheckResponse)
        async def health_check():
            """Health check endpoint"""
            return await self._health_check()
        
        @self.router.get("/agents", response_model=AgentDiscoveryResponse)
        async def discover_agents(domain: Optional[str] = None):
            """Discover available agents"""
            return await self._discover_agents(domain)
        
        @self.router.post("/agents/register")
        async def register_agent(request: AgentRegistrationRequest):
            """Register an external agent with the server"""
            return await self._register_agent(request)
        
        @self.router.delete("/agents/{agent_id}")
        async def unregister_agent(agent_id: str):
            """Unregister an external agent"""
            return await self._unregister_agent(agent_id)
        
        @self.router.get("/agents/{agent_id}", response_model=AgentCard)
        async def get_agent_info(agent_id: str):
            """Get information about a specific agent"""
            return await self._get_agent_info(agent_id)
        
        @self.router.get("/stats", response_model=SystemStatsResponse)
        async def get_system_stats():
            """Get system statistics"""
            return await self._get_system_stats()
        
        @self.router.get("/capabilities")
        async def get_capabilities():
            """Get capabilities of all managed agents"""
            return await self._get_capabilities()
        
        @self.router.get("/events/stats")
        async def get_event_stats():
            """Get event queue statistics"""
            return await self.event_queue.get_queue_stats()
        
        @self.router.post("/events/clear/{context_id}")
        async def clear_context_events(context_id: str):
            """Clear events for a specific context"""
            count = await self.event_queue.clear_context_events(context_id)
            return {"cleared_events": count, "context_id": context_id}
        
        @self.router.get("/debug/sessions")
        async def get_debug_sessions():
            """Get debug information about active sessions (admin only)"""
            return await self._get_debug_sessions()
    
    async def _get_main_agent_card(self) -> AgentCard:
        """Get the agent card for the main enhanced agent"""
        capabilities = AgentCapabilities(
            streaming=True,
            multimodal=True,
            function_calling=True,
            memory_persistent=True,
            handoff_supported=True,
            context_sharing=True
        )
        
        # Get all available agent capabilities
        agent_capabilities = await self.enhanced_agent.get_agent_capabilities()
        
        skills = []
        for domain, agent_info in agent_capabilities.items():
            skill = AgentSkill(
                id=f"skill_{domain}",
                name=agent_info["agent_name"],
                description=f"Specialized agent for {domain} tasks",
                tags=agent_info["supported_domains"],
                examples=self._get_examples_for_domain(domain),
                confidence_level=0.9
            )
            skills.append(skill)
        
        return AgentCard(
            name="Zava Enhanced Shopping Assistant",
            description=(
                "Enhanced multi-agent shopping assistant using A2A protocol for "
                "intelligent task routing and coordination across specialized agents."
            ),
            url="http://localhost:8001/",  # Should be configurable
            version="1.0.0",
            agent_id="zava_enhanced_assistant",
            capabilities=capabilities,
            skills=skills,
            metadata={
                "framework": "A2A Protocol",
                "coordination": "Multi-agent",
                "domains": list(agent_capabilities.keys())
            }
        )
    
    def _get_examples_for_domain(self, domain: str) -> List[str]:
        """Get example queries for each domain"""
        examples = {
            "interior_design": [
                "What colors would work well for my living room?",
                "Help me design a modern bedroom",
                "Show me paint options for a small kitchen"
            ],
            "inventory": [
                "Do you have blue paint in stock?",
                "Check availability of premium brushes",
                "Is the deluxe roller set available?"
            ],
            "customer_loyalty": [
                "What's my current discount?",
                "How many loyalty points do I have?",
                "What deals are available for members?"
            ],
            "cart_management": [
                "Add paint brushes to my cart",
                "Remove the primer from my order",
                "What's in my shopping cart?"
            ],
            "cora": [
                "Tell me about your paint products",
                "What tools do you recommend for beginners?",
                "Help me plan my painting project"
            ]
        }
        return examples.get(domain, ["General assistance"])
    
    async def _health_check(self) -> HealthCheckResponse:
        """Perform health check on all system components"""
        components = {}
        
        # Check enhanced agent
        try:
            stats = self.enhanced_agent.get_stats()
            components["enhanced_agent"] = "healthy"
        except Exception as e:
            components["enhanced_agent"] = f"error: {str(e)}"
        
        # Check event queue
        try:
            queue_stats = await self.event_queue.get_queue_stats()
            components["event_queue"] = "healthy"
        except Exception as e:
            components["event_queue"] = f"error: {str(e)}"
        
        # Check agent capabilities
        try:
            capabilities = await self.enhanced_agent.get_agent_capabilities()
            components["agent_capabilities"] = f"healthy ({len(capabilities)} agents)"
        except Exception as e:
            components["agent_capabilities"] = f"error: {str(e)}"
        
        overall_status = "healthy" if all(
            status == "healthy" or status.startswith("healthy (")
            for status in components.values()
        ) else "degraded"
        
        return HealthCheckResponse(
            status=overall_status,
            timestamp=datetime.utcnow().isoformat(),
            version="1.0.0",
            components=components
        )
    
    async def _discover_agents(self, domain: Optional[str] = None) -> AgentDiscoveryResponse:
        """Discover available agents, optionally filtered by domain"""
        # Get internal agents
        capabilities = await self.enhanced_agent.get_agent_capabilities()
        
        internal_cards = []
        for domain_name, agent_info in capabilities.items():
            if domain and domain not in agent_info["supported_domains"]:
                continue
            
            card = AgentCard(
                name=agent_info["agent_name"],
                description=f"Internal agent for {domain_name}",
                url="internal://zava-agent",
                agent_id=f"internal_{domain_name}",
                capabilities=AgentCapabilities(streaming=True, function_calling=True),
                skills=[AgentSkill(
                    id=f"skill_{domain_name}",
                    name=domain_name.replace("_", " ").title(),
                    description=f"Handles {domain_name} related tasks",
                    tags=agent_info["supported_domains"]
                )]
            )
            internal_cards.append(card)
        
        # Get registered external agents
        external_cards = []
        for agent_card in self.registered_agents.values():
            if domain:
                # Check if any skill supports this domain
                if not any(domain in skill.tags for skill in agent_card.skills):
                    continue
            external_cards.append(agent_card)
        
        all_cards = internal_cards + external_cards
        all_domains = set()
        
        for card in all_cards:
            for skill in card.skills:
                all_domains.update(skill.tags)
        
        return AgentDiscoveryResponse(
            agents=all_cards,
            total_count=len(all_cards),
            available_domains=list(all_domains)
        )
    
    async def _register_agent(self, request: AgentRegistrationRequest) -> Dict[str, Any]:
        """Register an external agent"""
        agent_card = request.agent_card
        
        # Validate agent card
        if not agent_card.agent_id:
            raise HTTPException(status_code=400, detail="Agent ID is required")
        
        if agent_card.agent_id in self.registered_agents:
            raise HTTPException(status_code=409, detail="Agent already registered")
        
        # Store agent card
        self.registered_agents[agent_card.agent_id] = agent_card
        
        logger.info(f"Registered external agent: {agent_card.agent_id}")
        
        return {
            "message": f"Agent {agent_card.agent_id} registered successfully",
            "agent_id": agent_card.agent_id,
            "registered_at": datetime.utcnow().isoformat()
        }
    
    async def _unregister_agent(self, agent_id: str) -> Dict[str, Any]:
        """Unregister an external agent"""
        if agent_id not in self.registered_agents:
            raise HTTPException(status_code=404, detail="Agent not found")
        
        del self.registered_agents[agent_id]
        
        logger.info(f"Unregistered external agent: {agent_id}")
        
        return {
            "message": f"Agent {agent_id} unregistered successfully",
            "unregistered_at": datetime.utcnow().isoformat()
        }
    
    async def _get_agent_info(self, agent_id: str) -> AgentCard:
        """Get information about a specific agent"""
        # Check registered external agents first
        if agent_id in self.registered_agents:
            return self.registered_agents[agent_id]
        
        # Check internal agents
        capabilities = await self.enhanced_agent.get_agent_capabilities()
        for domain, agent_info in capabilities.items():
            internal_id = f"internal_{domain}"
            if agent_id == internal_id:
                return AgentCard(
                    name=agent_info["agent_name"],
                    description=f"Internal agent for {domain}",
                    url="internal://zava-agent",
                    agent_id=internal_id,
                    capabilities=AgentCapabilities(streaming=True, function_calling=True),
                    skills=[AgentSkill(
                        id=f"skill_{domain}",
                        name=domain.replace("_", " ").title(),
                        description=f"Handles {domain} related tasks",
                        tags=agent_info["supported_domains"]
                    )]
                )
        
        raise HTTPException(status_code=404, detail="Agent not found")
    
    async def _get_system_stats(self) -> SystemStatsResponse:
        """Get comprehensive system statistics"""
        uptime = (datetime.utcnow() - self.start_time).total_seconds()
        
        # Get agent stats
        agent_stats = self.enhanced_agent.get_stats()
        
        # Get event queue stats
        queue_stats = await self.event_queue.get_queue_stats()
        
        return SystemStatsResponse(
            uptime_seconds=uptime,
            total_requests=agent_stats.get("execution_count", 0),
            active_sessions=0,  # Would need session tracking
            event_queue_size=queue_stats.get("total_events", 0),
            agent_stats=agent_stats
        )
    
    async def _get_capabilities(self) -> Dict[str, Any]:
        """Get comprehensive capabilities information"""
        internal_capabilities = await self.enhanced_agent.get_agent_capabilities()
        
        external_capabilities = {}
        for agent_id, agent_card in self.registered_agents.items():
            external_capabilities[agent_id] = {
                "agent_name": agent_card.name,
                "supported_domains": [tag for skill in agent_card.skills for tag in skill.tags],
                "available": True,  # Would check actual availability
                "endpoint": agent_card.url
            }
        
        return {
            "internal_agents": internal_capabilities,
            "external_agents": external_capabilities,
            "total_agents": len(internal_capabilities) + len(external_capabilities),
            "server_capabilities": {
                "streaming": True,
                "multi_agent": True,
                "handoff_support": True,
                "context_sharing": True
            }
        }
    
    async def _get_debug_sessions(self) -> Dict[str, Any]:
        """Get debug information about active sessions"""
        # This would return session information if available
        return {
            "note": "Debug session information not implemented",
            "active_contexts": 0,
            "registered_agents": len(self.registered_agents)
        }
    
    def get_router(self) -> APIRouter:
        """Get the configured FastAPI router"""
        return self.router