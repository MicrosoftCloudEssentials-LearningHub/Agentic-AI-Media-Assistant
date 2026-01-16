#!/usr/bin/env python3
"""
Hybrid Agent Service - Azure AI Agents with Local Fallback
Provides robust agent orchestration with graceful degradation
"""
import os
import logging
import time
import asyncio
import threading
from typing import Dict, Any, Optional, AsyncIterator
from azure.identity import DefaultAzureCredential
from azure.ai.projects import AIProjectClient
from azure.core.exceptions import HttpResponseError

from services.env_utils import get_env

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
        
        # Get configuration
        self.sweden_endpoint = get_env("AZURE_AI_PROJECT_ENDPOINT_SWEDEN") or get_env("AZURE_AI_PROJECT_ENDPOINT")
        
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

        # Keep a stable conversation for the orchestrator agent (Agents API).
        self._orchestrator_conversation_id: Optional[str] = None

        # Model deployment name required by the Responses API.
        # Prefer the dedicated env var, then fall back to the app's default deployment.
        self.agent_model = (
            get_env("AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME")
            or get_env("gpt_deployment")
            or "model-router"
        )

    def _get_or_create_orchestrator_conversation_id(self) -> Optional[str]:
        if not self.agent_client or not self.orchestrator_agent:
            return None

        if self._orchestrator_conversation_id:
            return self._orchestrator_conversation_id

        try:
            conv = self.agent_client.conversations.create(
                agent_id=self.orchestrator_agent.id,
                store=True,
                metadata={"app": "zava-media-assistant"},
                headers={"x-ms-enable-preview": "true"},
            )
            self._orchestrator_conversation_id = getattr(conv, "id", None)
            return self._orchestrator_conversation_id
        except Exception as e:
            logger.warning(f"Failed to create orchestrator conversation: {e}")
            return None

    def _extract_agents_api_text(self, response: Any) -> str:
        # Best-effort extraction from Azure AI Projects Agents API responses.
        if response is None:
            return ""

        output_text = getattr(response, "output_text", None)
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        if hasattr(response, "content"):
            try:
                val = getattr(response, "content")
                if isinstance(val, str):
                    return val
            except Exception:
                pass

        parts: list[str] = []
        out = getattr(response, "output", None)
        if out is not None:
            outputs = out if isinstance(out, list) else [out]
            for item in outputs:
                for attr in ("content", "text", "output_text"):
                    try:
                        v = getattr(item, attr, None)
                        if isinstance(v, str) and v.strip():
                            parts.append(v)
                            break
                    except Exception:
                        continue
                else:
                    if isinstance(item, str) and item.strip():
                        parts.append(item)

        return "\n".join([p for p in parts if p])

    def _invoke_orchestrator_via_agents_api(self, user_message_to_send: str) -> Dict[str, Any]:
        """Invoke the orchestrator using the Azure AI Projects Agents API.

        This avoids the OpenAI-compatible agent_reference path which can reject the `model` param.
        """
        if not self.agent_client or not self.orchestrator_agent:
            raise RuntimeError("Agent client or orchestrator agent not initialized")

        conversation_id = self._get_or_create_orchestrator_conversation_id()
        if not conversation_id:
            raise RuntimeError("Failed to create or retrieve an orchestrator conversation")

        input_data = {"role": "user", "content": user_message_to_send}
        resp = self.agent_client.responses.create(
            agent_id=self.orchestrator_agent.id,
            conversation_id=conversation_id,
            input=input_data,
            headers={"x-ms-enable-preview": "true"},
        )

        text = self._extract_agents_api_text(resp)
        return {
            "text": text or "The agent completed the task but returned no text.",
            "agent": "Zava Media Orchestrator",
            "response_id": getattr(resp, "id", None),
            "diagnostics": {
                "agent_call_path": "agents_api",
                "conversation_id": conversation_id,
            },
        }
    
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
                return self._process_with_agent(user_message, context=context, attempt=attempt)
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

    def _maybe_augment_user_message(self, user_message: str, context: Optional[Dict[str, Any]] = None) -> str:
        if not context:
            return user_message

        # If a document was uploaded and extracted server-side, inject it as context.
        # Keep this as plain content (not a tool call) so the orchestrator can answer questions about it.
        doc_text = context.get("document_text")
        if isinstance(doc_text, str) and doc_text.strip():
            try:
                max_chars = int(get_env("MAX_DOCUMENT_CHARS", "40000"))
            except Exception:
                max_chars = 40000

            clipped = doc_text[:max_chars]
            truncated = len(doc_text) > max_chars

            doc_name = context.get("file_name") or "unknown"
            doc_type = context.get("file_content_type") or context.get("document_kind") or "unknown"
            used_ocr = context.get("document_used_ocr")

            doc_parts = [
                "\n\n---\n",
                "DOCUMENT CONTEXT (attached file)\n",
                f"Name: {doc_name}\n",
                f"Type: {doc_type}\n",
                f"OCR used: {used_ocr}\n",
                "Content:\n",
                f"{clipped}\n",
            ]
            if truncated:
                doc_parts.append(f"[Content truncated to {max_chars} characters]\n")
            doc_parts.append("---\n")
            doc_block = "".join(doc_parts)
            user_message = f"{user_message}{doc_block}"

        standard_format_instructions = (
            "---\n"
            "STANDARD RESPONSE FORMAT (for normal text replies in this app):\n"
            "Return the response using EXACTLY these sections and labels, in this exact order:\n\n"
            "**<One short summary paragraph>**\n\n"
            "---\n\n"
            "## What I did\n"
            "- <0-3 bullets; if none, write '- None'>\n\n"
            "## Next steps\n"
            "- <0-3 bullets; if none, write '- None'>\n\n"
            "## Assumptions\n"
            "- <0-3 bullets; if none, write '- None'>\n\n"
            "Rules:\n"
            "- Do not add other headings.\n"
            "- Do not use markdown code fences.\n"
            "- Do not use emojis.\n"
            "- Keep bullets short; one line each.\n\n"
            "CRITICAL TOOL RULE:\n"
            "- If you return a tool/action JSON payload, output ONLY the JSON object and nothing else.\n"
        )

        explain_answer_instructions = ""
        if context.get("explain_answer"):
            # IMPORTANT: This is a post-hoc explanation request. It is NOT chain-of-thought.
            explain_answer_instructions = (
                "\nAlso append a section titled 'Explain answer (3 bullets)' AFTER the sections above.\n"
                "Rules for that section:\n"
                "- This is NOT internal chain-of-thought. Do not reveal hidden reasoning steps.\n"
                "- Output exactly 3 bullets starting with '- '. Each bullet must be <= 20 words.\n"
                "- Do not output any content after those 3 bullets.\n"
            )

        return f"{user_message}\n\n{standard_format_instructions}{explain_answer_instructions}"

    def _should_retry_without_model(self, exc: Exception) -> bool:
        msg = str(exc) or ""
        lowered = msg.lower()
        return (
            "not allowed when agent is specified" in lowered
            or "invalid_payload" in lowered and "param" in lowered and "model" in lowered
            or "param': 'model'" in msg
        )

    def _should_retry_with_model(self, exc: Exception) -> bool:
        msg = str(exc) or ""
        lowered = msg.lower()
        return (
            "model must be provided" in lowered
            or "missing required" in lowered and "model" in lowered
        )

    def _responses_create_with_agent(self, openai_client, user_message_to_send: str):
        base_kwargs = {
            "input": [{"role": "user", "content": user_message_to_send}],
            "extra_body": {
                "agent": {
                    "name": self.orchestrator_agent.name,
                    "type": "agent_reference",
                }
            },
        }

        # Prefer no model when using agent_reference; some endpoints reject it.
        try:
            return openai_client.responses.create(**base_kwargs)
        except TypeError as e:
            # Signature requires model.
            return openai_client.responses.create(model=self.agent_model, **base_kwargs)
        except Exception as e:
            if self._should_retry_with_model(e):
                return openai_client.responses.create(model=self.agent_model, **base_kwargs)
            if self._should_retry_without_model(e):
                # Retry without model (may still TypeError if signature requires it).
                try:
                    return openai_client.responses.create(**base_kwargs)
                except Exception:
                    pass
            raise

    def _responses_stream_with_agent(self, openai_client, user_message_to_send: str):
        base_kwargs = {
            "input": [{"role": "user", "content": user_message_to_send}],
            "extra_body": {
                "agent": {
                    "name": self.orchestrator_agent.name,
                    "type": "agent_reference",
                }
            },
        }

        # Prefer no model when using agent_reference; some endpoints reject it.
        try:
            return openai_client.responses.stream(**base_kwargs)
        except TypeError:
            return openai_client.responses.stream(model=self.agent_model, **base_kwargs)
        except Exception as e:
            if self._should_retry_with_model(e):
                return openai_client.responses.stream(model=self.agent_model, **base_kwargs)
            if self._should_retry_without_model(e):
                try:
                    return openai_client.responses.stream(**base_kwargs)
                except Exception:
                    pass
            raise

    async def stream_request(self, user_message: str, context: Optional[Dict[str, Any]] = None) -> AsyncIterator[Dict[str, Any]]:
        """Stream response deltas when supported by the SDK.

        Yields events:
        - {"type": "delta", "delta": "..."}
        - {"type": "final", "result": {"text": "...", "agent": "...", ...}}

        If streaming isn't available, falls back to generating the full response
        and emitting it in small chunks.
        """
        if not user_message or not user_message.strip():
            yield {"type": "final", "result": {"text": "I didn't receive a message. How can I help you?", "error": "Empty message"}}
            return

        if not self.orchestrator_agent or not self.agent_client:
            result = self._fallback_response(user_message)
            for chunk in self._chunk_text(result.get("text", "")):
                yield {"type": "delta", "delta": chunk}
                await asyncio.sleep(0)
            yield {"type": "final", "result": result}
            return

        user_message_to_send = self._maybe_augment_user_message(user_message, context)

        # Prefer Agents API streaming (doesn't require model; avoids agent_reference incompatibilities).
        try:
            conversation_id = self._get_or_create_orchestrator_conversation_id()
            if not conversation_id:
                raise RuntimeError("No orchestrator conversation available")

            loop = asyncio.get_running_loop()
            queue: asyncio.Queue[Optional[Dict[str, Any]]] = asyncio.Queue()

            def _safe_put(item: Optional[Dict[str, Any]]) -> None:
                loop.call_soon_threadsafe(queue.put_nowait, item)

            def _run_stream_agents_api() -> None:
                try:
                    input_data = {"role": "user", "content": user_message_to_send}
                    stream = self.agent_client.responses.create_stream(
                        agent_id=self.orchestrator_agent.id,
                        conversation_id=conversation_id,
                        input=input_data,
                        headers={"x-ms-enable-preview": "true"},
                    )
                    full_parts: list[str] = []
                    for chunk in stream:
                        delta = getattr(chunk, "delta", None)
                        content = getattr(chunk, "content", None)
                        piece = delta if isinstance(delta, str) and delta else content if isinstance(content, str) and content else None
                        if piece:
                            full_parts.append(piece)
                            _safe_put({"type": "delta", "delta": piece})

                    final_text = "".join(full_parts).strip()
                    _safe_put(
                        {
                            "type": "final",
                            "result": {
                                "text": final_text or "The agent completed the task but returned no text.",
                                "agent": "Zava Media Orchestrator",
                                "diagnostics": {
                                    "agent_call_path": "agents_api_stream",
                                    "conversation_id": conversation_id,
                                },
                            },
                        }
                    )
                except Exception as e:
                    _safe_put({"type": "final", "result": self._fallback_response(user_message, str(e))})
                finally:
                    _safe_put(None)

            threading.Thread(target=_run_stream_agents_api, daemon=True).start()

            while True:
                item = await queue.get()
                if item is None:
                    return
                yield item

        except Exception:
            # Fallback: one-shot generation + chunked emission.
            result = await asyncio.to_thread(self.process_request, user_message, context)
            for chunk in self._chunk_text(result.get("text", "")):
                yield {"type": "delta", "delta": chunk}
                await asyncio.sleep(0)
            yield {"type": "final", "result": result}

    def _chunk_text(self, text: str, chunk_size: int = 8) -> list[str]:
        if not text:
            return []
        return [text[i : i + chunk_size] for i in range(0, len(text), chunk_size)]
    
    def _process_with_agent(self, user_message: str, context: Optional[Dict[str, Any]] = None, attempt: int = 0) -> Dict[str, Any]:
        """Process request using Azure AI Agent via Responses API."""
        logger.info(f"Processing with agent (attempt {attempt + 1})")
        
        try:
            user_message_to_send = self._maybe_augment_user_message(user_message, context)

            # Primary: Azure AI Projects Agents API (agent_id + conversation_id).
            try:
                return self._invoke_orchestrator_via_agents_api(user_message_to_send)
            except Exception as e:
                # Secondary: OpenAI-compatible Responses API with agent_reference.
                logger.warning(f"Agents API path failed, trying agent_reference fallback: {e}")
                openai_client = self.agent_client.get_openai_client()
                response = self._responses_create_with_agent(openai_client, user_message_to_send)

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

                response_text = self._extract_response_text(response)

                if response.status == "completed" and response_text:
                    return {
                        "text": response_text,
                        "agent": "Zava Media Orchestrator",
                        "response_id": response_id,
                        "diagnostics": {"agent_call_path": "agent_reference_fallback"},
                    }

                error_msg = f"Response status: {response.status}"
                if last_error := getattr(response, "last_error", None):
                    error_msg += f" - {last_error}"
                return {
                    "text": "I encountered an issue while processing your request with the AI agent.",
                    "agent": "Zava Media Orchestrator",
                    "error": error_msg,
                    "response_id": response_id,
                    "diagnostics": {"agent_call_path": "agent_reference_fallback"},
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

        proxy_vars = ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]
        proxy_env = {k: ("SET" if os.environ.get(k) else "") for k in proxy_vars}

        sdk_versions: Dict[str, str] = {}
        try:
            import importlib.metadata as _md

            for pkg in ["azure-ai-projects", "azure-identity", "openai", "httpx"]:
                try:
                    sdk_versions[pkg] = _md.version(pkg)
                except Exception:
                    sdk_versions[pkg] = "NOT INSTALLED"
        except Exception:
            sdk_versions = {"error": "Could not read package versions"}

        httpx_client_signature: Dict[str, Any] = {}
        try:
            import inspect
            import httpx as _httpx

            params = list(inspect.signature(_httpx.Client).parameters.keys())
            httpx_client_signature = {
                "supports_proxies_kw": "proxies" in params,
                "supports_proxy_kw": "proxy" in params,
            }
        except Exception:
            httpx_client_signature = {"error": "Could not inspect httpx.Client signature"}
        
        # Build detailed diagnostic information
        diagnostics = {
            "endpoint": self.sweden_endpoint or "NOT CONFIGURED",
            "agent_client_initialized": self.agent_client is not None,
            "orchestrator_found": self.orchestrator_agent is not None,
            "orchestrator_id": self.orchestrator_agent.id if self.orchestrator_agent else "NONE",
            "error_details": error or "No specific error - agent not initialized",
            "sdk_versions": sdk_versions,
            "httpx_client_signature": httpx_client_signature,
            "proxy_env": proxy_env,
        }
        
        # Simple keyword-based responses
        message_lower = user_message.lower()

        error_lower = (error or "").lower()
        deployment_hint = ""
        if "not allowed when agent is specified" in error_lower and "model" in error_lower:
            deployment_hint = (
                "\n\nNOTE: The service rejected a request where an agent was specified AND a 'model' parameter was sent. "
                "This typically means the running container is still using the older OpenAI-compatible 'agent_reference' path. "
                "Redeploy/restart the web app so it picks up the newer Agents API (agent_id + conversation_id) call path, "
                "which does not send 'model'."
            )
        
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

        if deployment_hint:
            response += deployment_hint
        
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
