#!/usr/bin/env python3
"""
Hybrid Agent Service - Azure AI Agents with Local Fallback
Provides robust agent orchestration with graceful degradation
"""
import os
import logging
import time
import httpx
from typing import Dict, Any, Optional
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import HttpResponseError

logger = logging.getLogger(__name__)

class HybridAgentService:
    """
    Robust agent service using Azure AI Agents with fallback handling.
    
    Architecture:
    - Primary: Azure AI Agents orchestrator for intelligent routing
    - Fallback: Local response generation when agents unavailable
    - Retry logic for transient failures
    - Comprehensive error handling and logging
    """
    
    def __init__(self):
        """Initialize the hybrid agent service with Azure AI Agents."""
        # Clear proxy environment variables to prevent httpx Client configuration conflicts
        # This fixes: "Client.__init__() got an unexpected keyword argument 'proxies'"
        for proxy_var in ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'ALL_PROXY', 'all_proxy']:
            os.environ.pop(proxy_var, None)
        
        # Create a clean HTTP client for OpenAI operations (reused across requests)
        self.http_client = httpx.Client(
            timeout=httpx.Timeout(60.0, connect=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )
        
        # Get configuration
        self.sweden_endpoint = os.getenv(
            "AZURE_AI_PROJECT_ENDPOINT_SWEDEN",
            os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        )
        
        if not self.sweden_endpoint:
            logger.error("No Azure AI Project endpoint configured")
            self.agent_client = None
            self.orchestrator_agent = None
            return
        
        # Initialize credentials and client
        try:
            credential = DefaultAzureCredential()
            self.agent_client = AIProjectClient(
                credential=credential,
                endpoint=self.sweden_endpoint
            )
            logger.info(f"Agent client initialized for endpoint: {self.sweden_endpoint}")
        except Exception as e:
            logger.error(f"Failed to initialize agent client: {e}", exc_info=True)
            self.agent_client = None
            self.orchestrator_agent = None
            return
        
        # Find orchestrator agent
        self.orchestrator_agent = self._find_orchestrator_agent()
    
    def _find_orchestrator_agent(self, max_retries: int = 3) -> Optional[Any]:
        """Find the orchestrator agent using direct lookup via agents.get()."""
        if not self.agent_client:
            return None
            
        agent_name = "zava-media-orchestrator"
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Retrieving agent '{agent_name}' (attempt {attempt + 1}/{max_retries})...")
                # Direct lookup as per SDK sample
                agent = self.agent_client.agents.get(agent_name=agent_name)
                logger.info(f"[OK] Orchestrator agent found: {agent.name} (ID: {agent.id})")
                return agent
                
            except Exception as e:
                # If 404 Not Found, it might raise HttpResponseError or similar
                logger.warning(f"Attempt {attempt + 1} failed to get agent: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                else:
                    logger.error(f"Could not find agent '{agent_name}' after {max_retries} attempts.")
                    return None
        
        return None
    
    def process_request(self, user_message: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Process user request with Azure AI Agent orchestrator.
        
        Args:
            user_message: User's input message
            context: Optional context (image data, session info, etc.)
        
        Returns:
            Dict with response text and metadata
        """
        if not user_message or not user_message.strip():
            return {
                "text": "I didn't receive a message. How can I help you?",
                "error": "Empty message"
            }
        
        logger.info(f"Processing request: {user_message[:100]}...")
        
        # Check if agent service is available
        if not self.orchestrator_agent or not self.agent_client:
            logger.warning("Orchestrator agent not available, using fallback")
            return self._fallback_response(user_message)
        
        # Try to process with agent (with retry logic)
        max_retries = 2
        for attempt in range(max_retries):
            try:
                return self._process_with_agent(user_message, attempt)
            except HttpResponseError as e:
                logger.error(f"HTTP error on attempt {attempt + 1}: {e}")
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    return self._fallback_response(user_message, str(e))
            except Exception as e:
                logger.error(f"Error processing request: {e}", exc_info=True)
                if attempt < max_retries - 1:
                    time.sleep(1)
                    continue
                else:
                    return self._fallback_response(user_message, str(e))
        
        return self._fallback_response(user_message)
    
    def _process_with_agent(self, user_message: str, attempt: int = 0) -> Dict[str, Any]:
        """Process request using Azure AI Agent via Responses API."""
        logger.info(f"Processing with agent (attempt {attempt + 1})")
        
        try:
            # Get OpenAI-compatible client with our pre-configured httpx client
            openai_client = self.agent_client.get_openai_client(http_client=self.http_client)

            logger.info(f"Invoking agent '{self.orchestrator_agent.name}' via Responses API")
            response = openai_client.responses.create(
                input=[{"role": "user", "content": user_message}],
                extra_body={
                    "agent": {
                        "name": self.orchestrator_agent.name,
                        "type": "agent_reference"
                    }
                }
            )

            response_id = response.id
            logger.info(f"Response created: {response_id} (status: {response.status})")

            # Poll until completion
            max_poll_time = 120  # 2 minutes max
            poll_start = time.time()
            while response.status in ("queued", "in_progress", "requires_action"):
                if time.time() - poll_start > max_poll_time:
                    logger.error(f"Response polling timeout after {max_poll_time}s")
                    break
                time.sleep(1)
                response = openai_client.responses.get(response_id)
                logger.debug(f"Response status: {response.status}")

            # Extract response text (prefer output_text, fallback to aggregating blocks)
            response_text = self._extract_response_text(response)

            if response.status == "completed":
                if response_text:
                    logger.info(f"Agent response: {response_text[:200]}...")
                    return {
                        "text": response_text,
                        "agent": "Zava Media Orchestrator",
                        "response_id": response_id
                    }
                else:
                    logger.warning("Response completed but no text content")
                    return {
                        "text": "The agent completed the task but returned no text.",
                        "agent": "Zava Media Orchestrator",
                        "response_id": response_id
                    }

            # Handle non-completed status
            error_msg = f"Response status: {response.status}"
            if last_error := getattr(response, "last_error", None):
                error_msg += f" - {last_error}"
            
            logger.warning(error_msg)
            return {
                "text": "I encountered an issue while processing your request with the AI agent.",
                "agent": "Zava Media Orchestrator",
                "error": error_msg,
                "response_id": response_id
            }
                
        except Exception as e:
            logger.error(f"Error calling Responses API: {e}", exc_info=True)
            raise
    
    def _extract_response_text(self, response) -> str:
        """Extract text content from response object."""
        # Prefer direct output_text attribute
        if output_text := getattr(response, "output_text", None):
            return output_text
        
        # Fallback: aggregate text from output blocks
        text_parts = []
        if hasattr(response, "output") and response.output:
            for item in response.output:
                if content := getattr(item, "content", []):
                    for block in content:
                        if text_val := (getattr(block, "text", None) or getattr(block, "output_text", None)):
                            text_parts.append(str(text_val))
        
        return "".join(text_parts)
    
    def _fallback_response(self, user_message: str, error: Optional[str] = None) -> Dict[str, Any]:
        """Generate a fallback response when agent is unavailable."""
        logger.info("Using fallback response mechanism")
        
        # Build detailed diagnostic information
        diagnostics = {
            "endpoint": self.sweden_endpoint or "NOT CONFIGURED",
            "agent_client_initialized": self.agent_client is not None,
            "orchestrator_found": self.orchestrator_agent is not None,
            "orchestrator_id": self.orchestrator_agent.id if self.orchestrator_agent else "NONE",
            "error_details": error or "No specific error - agent not initialized"
        }
        
        # Simple keyword-based responses
        message_lower = user_message.lower()
        
        if any(word in message_lower for word in ["image", "picture", "photo"]):
            response = "I can help you with image generation and manipulation. However, the AI agent service is currently unavailable."
        elif any(word in message_lower for word in ["video", "movie", "clip"]):
            response = "I can help you create videos. However, the AI agent service is currently unavailable."
        elif any(word in message_lower for word in ["crop", "cut", "trim"]):
            response = "I can help you crop images. However, the AI agent service is currently unavailable."
        elif any(word in message_lower for word in ["background", "bg"]):
            response = "I can help you with background removal and replacement. However, the AI agent service is currently unavailable."
        else:
            response = "I'm the Zava Media AI Assistant. I can help with image generation, video creation, cropping, and background editing. However, the AI agent service is currently unavailable."
        
        # Add debug info
        debug_info = f"\n\n[DEBUG INFO]\n"
        debug_info += f"Endpoint: {diagnostics['endpoint']}\n"
        debug_info += f"Client Init: {diagnostics['agent_client_initialized']}\n"
        debug_info += f"Orchestrator Found: {diagnostics['orchestrator_found']}\n"
        debug_info += f"Orchestrator ID: {diagnostics['orchestrator_id']}\n"
        debug_info += f"Error: {diagnostics['error_details']}"
        
        return {
            "text": response + debug_info,
            "agent": "Fallback Handler",
            "fallback": True,
            "error": error or "Agent service unavailable",
            "diagnostics": diagnostics
        }
