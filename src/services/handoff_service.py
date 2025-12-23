"""
Handoff Service for routing user queries to appropriate agents.
Uses GPT with structured output to classify user intent.
"""
import os
import json
from typing import Dict, Any, Optional
from pydantic import BaseModel
from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential
from dotenv import load_dotenv

load_dotenv()


class IntentClassification(BaseModel):
    """Structured output for intent classification"""
    domain: str
    reasoning: str
    confidence: float


class HandoffService:
    """
    Service to classify user intent and route to appropriate agent.
    
    Domains:
    - orchestrator: General routing, complex requests, or requests involving multiple steps
    - cropping_agent: Cropping images, identifying objects/coordinates
    - background_agent: Removing or replacing backgrounds
    - thumbnail_generator: Creating video thumbnails, adding text/overlays
    - video_agent: Generating videos from text or images
    """
    
    def __init__(self):
        """Initialize the handoff service with GPT client"""
        endpoint = os.getenv("gpt_endpoint")
        api_key = os.getenv("gpt_api_key")
        deployment = os.getenv("gpt_deployment")
        
        if not all([endpoint, api_key, deployment]):
            raise ValueError("Missing GPT configuration in environment")
        
        # Convert endpoint to Azure AI Foundry format
        foundry_endpoint = endpoint.replace('.cognitiveservices.', '.services.ai.')
        if '.services.azure.com' in foundry_endpoint and '.services.ai.azure.com' not in foundry_endpoint:
            foundry_endpoint = foundry_endpoint.replace('.services.azure.com', '.services.ai.azure.com')
        if not foundry_endpoint.endswith('/models'):
            foundry_endpoint = f"{foundry_endpoint.rstrip('/')}/models"
        
        self.client = ChatCompletionsClient(
            endpoint=foundry_endpoint,
            credential=AzureKeyCredential(api_key)
        )
        self.deployment = deployment
    
    def classify_intent(
        self,
        user_message: str,
        conversation_history: Optional[list] = None
    ) -> Dict[str, Any]:
        """
        Classify user intent to determine which agent should handle the request.
        
        Args:
            user_message: The user's message
            conversation_history: Optional conversation context
        
        Returns:
            Dictionary with domain, reasoning, and confidence
        """
        # Build context from conversation history
        context = ""
        if conversation_history:
            recent_messages = conversation_history[-5:]  # Last 5 messages
            context = "\n".join([
                f"{'User' if msg.get('role') == 'user' else 'Assistant'}: {msg.get('content', '')}"
                for msg in recent_messages
            ])
        
        # Classification prompt
        system_prompt = """You are a routing assistant for Zava's multi-agent media system.
        
Classify user messages into one of these domains:

1. **cropping_agent**: 
   - Crop image, cut out object
   - "Crop this to 16:9", "Focus on the dog"
   - Object detection for cropping
   
2. **background_agent**:
   - Remove background, transparent background
   - Replace background with [scene]
   - "Put this on a beach", "Change background to white"
   
3. **thumbnail_generator**:
   - Create thumbnail for YouTube/video
   - Add text overlay, make it click-baity
   - "Make a thumbnail for my vlog"
   
4. **video_agent**:
   - Generate video, create movie
   - Animate this image
   - "Create a video of a cat running"
   
5. **orchestrator** (general/complex):
   - General questions, greetings
   - Complex workflows involving multiple steps
   - Requests that don't fit clearly into one specialist
   - "Help me edit this photo" (vague)

Return your classification with reasoning and confidence (0.0-1.0).
"""
        
        user_prompt = f"""Conversation context:
{context if context else 'No previous context'}

Current user message: "{user_message}"

Classify this message into the appropriate domain."""
        
        try:
            # Call GPT for classification
            response = self.client.complete(
                model=self.deployment,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=200,
                temperature=0.3
            )
            
            # Parse response
            response_text = response.choices[0].message.content.strip()
            
            # Simple parsing - look for domain keywords
            # Simple parsing - look for domain keywords
            response_lower = response_text.lower()
            
            if "crop" in response_lower:
                domain = "cropping_agent"
            elif "background" in response_lower:
                domain = "background_agent"
            elif "thumbnail" in response_lower:
                domain = "thumbnail_generator"
            elif "video" in response_lower:
                domain = "video_agent"
            else:
                domain = "orchestrator"
            
            return {
                "domain": domain,
                "reasoning": response_text,
                "confidence": 0.85
            }
            
        except Exception as e:
            # Default to orchestrator on error
            return {
                "domain": "orchestrator",
                "reasoning": f"Error during classification: {str(e)}",
                "confidence": 0.5
            }