"""
Foundry Agents API Handler - Uses Conversations and Responses
This replaces thread/run-based execution with the versioned Agents API.

Key differences:
- Conversations (not threads) - stateful context with conversation store
- Responses (not runs) - synchronous execution, direct output
- No polling required - responses return immediately
- Supports advanced features: versioning, MCP tools, web search
"""
import os
import logging
from typing import List, Dict, Any, Optional
from datetime import datetime
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

logger = logging.getLogger(__name__)


class FoundryAgentHandler:
    """
    Handler for Azure AI Foundry Agents API.
    Uses conversations and responses instead of threads and runs.
    """
    
    def __init__(self, project_endpoint: str = None, agent_id: str = None):
        """
        Initialize the NEW Agents API handler.
        
        Args:
            project_endpoint: Azure AI Foundry project endpoint (optional, will use env var if not provided)
            agent_id: Agent ID to use for this handler (optional)
        """
        self.project_endpoint = project_endpoint or os.getenv("PROJECT_CONNECTION_STRING")
        self.agent_id = agent_id
        self.client = None
        self.conversation_id = None
        
        if not self.project_endpoint:
            raise ValueError("PROJECT_CONNECTION_STRING environment variable not set")
        
        logger.info(f"Initializing NEW Agents API handler with endpoint: {self.project_endpoint[:50]}...")
        
        try:
            # Initialize AI Project Client
            credential = DefaultAzureCredential()
            self.client = AIProjectClient.from_connection_string(
                conn_str=self.project_endpoint,
                credential=credential
            )
            logger.info("Successfully initialized NEW Agents API client")
        except Exception as e:
            logger.error(f"Failed to initialize NEW Agents API client: {e}")
            raise
    
    def create_conversation(
        self, 
        agent_id: str = None, 
        store: bool = True,
        metadata: Dict[str, Any] = None
    ) -> str:
        """
        Create a new conversation using the NEW API.
        Conversations replace threads and support stateful context.
        
        Args:
            agent_id: Agent ID to use (uses self.agent_id if not provided)
            store: Whether to enable stateful context (default: True)
            metadata: Optional metadata for the conversation
        
        Returns:
            Conversation ID
        """
        agent_id = agent_id or self.agent_id
        if not agent_id:
            raise ValueError("agent_id must be provided")
        
        try:
            logger.info(f"Creating NEW conversation for agent: {agent_id}")
            
            # Create conversation using NEW API
            conversation = self.client.conversations.create(
                agent_id=agent_id,
                store=store,
                metadata=metadata or {},
                headers={"x-ms-enable-preview": "true"}
            )
            
            self.conversation_id = conversation.id
            logger.info(f"Created conversation: {self.conversation_id}")
            return self.conversation_id
            
        except Exception as e:
            logger.error(f"Failed to create conversation: {e}")
            raise
    
    def send_message(
        self,
        user_message: str,
        conversation_id: str = None,
        agent_id: str = None,
        additional_context: Dict[str, Any] = None
    ) -> Dict[str, Any]:
        """
        Send a message and get a response using the NEW Responses API.
        This is synchronous - no polling required!
        
        Args:
            user_message: The user's message
            conversation_id: Conversation ID (uses self.conversation_id if not provided)
            agent_id: Agent ID (uses self.agent_id if not provided)
            additional_context: Optional additional context
        
        Returns:
            Dictionary with:
            - content: The agent's response text
            - output_items: List of output items from the response
            - conversation_id: The conversation ID
            - metadata: Response metadata
        """
        conversation_id = conversation_id or self.conversation_id
        agent_id = agent_id or self.agent_id
        
        if not conversation_id:
            # Auto-create conversation if not exists
            logger.info("No conversation exists, creating new one")
            conversation_id = self.create_conversation(agent_id=agent_id)
        
        if not agent_id:
            raise ValueError("agent_id must be provided")
        
        try:
            logger.info(f"Sending message to conversation: {conversation_id}")
            logger.debug(f"Message: {user_message[:100]}...")
            
            # Build input for NEW API
            input_data = {
                "role": "user",
                "content": user_message
            }
            
            # Add additional context if provided
            if additional_context:
                input_data["metadata"] = additional_context
            
            # Create response using NEW API (synchronous!)
            response = self.client.responses.create(
                agent_id=agent_id,
                conversation_id=conversation_id,
                input=input_data,
                headers={"x-ms-enable-preview": "true"}
            )
            
            # Extract response content from output items
            content = self._extract_content_from_response(response)
            
            logger.info(f"Received response: {len(content)} characters")
            
            return {
                "content": content,
                "output_items": response.output if hasattr(response, 'output') else [],
                "conversation_id": conversation_id,
                "metadata": {
                    "response_id": response.id if hasattr(response, 'id') else None,
                    "timestamp": datetime.utcnow().isoformat()
                }
            }
            
        except Exception as e:
            logger.error(f"Failed to send message: {e}")
            raise
    
    def send_message_streaming(
        self,
        user_message: str,
        conversation_id: str = None,
        agent_id: str = None,
        additional_context: Dict[str, Any] = None
    ):
        """
        Send a message and stream the response using the NEW Responses API.
        
        Args:
            user_message: The user's message
            conversation_id: Conversation ID (uses self.conversation_id if not provided)
            agent_id: Agent ID (uses self.agent_id if not provided)
            additional_context: Optional additional context
        
        Yields:
            Response chunks as they arrive
        """
        conversation_id = conversation_id or self.conversation_id
        agent_id = agent_id or self.agent_id
        
        if not conversation_id:
            # Auto-create conversation if not exists
            logger.info("No conversation exists, creating new one")
            conversation_id = self.create_conversation(agent_id=agent_id)
        
        if not agent_id:
            raise ValueError("agent_id must be provided")
        
        try:
            logger.info(f"Streaming message to conversation: {conversation_id}")
            
            # Build input for NEW API
            input_data = {
                "role": "user",
                "content": user_message
            }
            
            if additional_context:
                input_data["metadata"] = additional_context
            
            # Create streaming response using NEW API
            stream = self.client.responses.create_stream(
                agent_id=agent_id,
                conversation_id=conversation_id,
                input=input_data,
                headers={"x-ms-enable-preview": "true"}
            )
            
            # Yield chunks as they arrive
            for chunk in stream:
                if hasattr(chunk, 'content'):
                    yield chunk.content
                elif hasattr(chunk, 'delta'):
                    yield chunk.delta
                else:
                    # Try to extract any text from the chunk
                    yield str(chunk)
            
        except Exception as e:
            logger.error(f"Failed to stream message: {e}")
            yield f"Error: {str(e)}"
    
    def get_conversation_history(
        self,
        conversation_id: str = None,
        limit: int = 20
    ) -> List[Dict[str, Any]]:
        """
        Get conversation history from a conversation.
        
        Args:
            conversation_id: Conversation ID (uses self.conversation_id if not provided)
            limit: Maximum number of messages to retrieve
        
        Returns:
            List of messages in the conversation
        """
        conversation_id = conversation_id or self.conversation_id
        
        if not conversation_id:
            return []
        
        try:
            logger.info(f"Retrieving history for conversation: {conversation_id}")
            
            # Get conversation details
            conversation = self.client.conversations.get(
                conversation_id=conversation_id,
                headers={"x-ms-enable-preview": "true"}
            )
            
            # Extract messages/items from conversation
            items = []
            if hasattr(conversation, 'items'):
                items = conversation.items[:limit] if limit > 0 else conversation.items
            
            # Convert to standard message format
            history = []
            for item in items:
                if hasattr(item, 'role') and hasattr(item, 'content'):
                    history.append({
                        "role": item.role,
                        "content": item.content,
                        "timestamp": item.timestamp if hasattr(item, 'timestamp') else None
                    })
            
            logger.info(f"Retrieved {len(history)} messages from conversation")
            return history
            
        except Exception as e:
            logger.error(f"Failed to get conversation history: {e}")
            return []
    
    def _extract_content_from_response(self, response) -> str:
        """
        Extract text content from a response object.
        Handles different response formats.
        
        Args:
            response: Response object from NEW API
        
        Returns:
            Extracted text content
        """
        # Try different attributes where content might be
        if hasattr(response, 'content'):
            return str(response.content)
        
        if hasattr(response, 'output'):
            # Output is a list of items
            outputs = response.output if isinstance(response.output, list) else [response.output]
            content_parts = []
            
            for output in outputs:
                if hasattr(output, 'content'):
                    content_parts.append(str(output.content))
                elif hasattr(output, 'text'):
                    content_parts.append(str(output.text))
                elif isinstance(output, str):
                    content_parts.append(output)
            
            return "\n".join(content_parts)
        
        if hasattr(response, 'text'):
            return str(response.text)
        
        if hasattr(response, 'message'):
            if hasattr(response.message, 'content'):
                return str(response.message.content)
            return str(response.message)
        
        # Fallback: return string representation
        return str(response)
    
    def close(self):
        """Clean up resources."""
        self.client = None
        self.conversation_id = None
        logger.info("Closed NEW Agents API handler")


def create_new_api_handler(agent_id: str, project_endpoint: str = None) -> NewAgentsAPIHandler:
    """
    Factory function to create a NEW Agents API handler.
    
    Args:
        agent_id: Agent ID to use
        project_endpoint: Optional project endpoint (uses env var if not provided)
    
    Returns:
        NewAgentsAPIHandler instance
    """
    return NewAgentsAPIHandler(
        project_endpoint=project_endpoint,
        agent_id=agent_id
    )
