"""
Direct Model API Service - FLUX and Sora
Uses direct Azure inference API calls (not through agents)
"""
import os
import logging
import json
import requests
import time
import random
import base64
import threading
import tempfile
import hashlib
import re
from io import BytesIO
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AccessToken

from PIL import Image, ImageDraw, ImageFont
 

logger = logging.getLogger(__name__)


class DirectModelService:
    """
    Direct API access to FLUX and Sora models.
    These models are deployed in Azure AI Foundry but accessed via inference API,
    not through agents (which require chat_completion capability).
    """
    
    def __init__(self):
        """Initialize direct model API access."""
        self.credential = DefaultAzureCredential()
        
        # Azure Cognitive Services endpoints (dynamically set by terraform)
        # Format: https://<foundry-name>.cognitiveservices.azure.com
        self.sweden_inference = os.getenv("AZURE_AI_INFERENCE_ENDPOINT_SWEDEN")
        self.westus3_inference = os.getenv("AZURE_AI_INFERENCE_ENDPOINT_WESTUS3")

        # Model-specific inference endpoints (preferred). These should point to the
        # Foundry hub where the corresponding model deployment exists.
        # Terraform already sets AZURE_OPENAI_ENDPOINT_FLUX / AZURE_OPENAI_ENDPOINT_SORA,
        # so we can safely fall back to those as well.
        self.flux1_inference = (
            os.getenv("AZURE_AI_INFERENCE_ENDPOINT_FLUX1")
            or os.getenv("AZURE_OPENAI_ENDPOINT_FLUX_KON")
            or self.sweden_inference
        )
        self.flux2_inference = (
            os.getenv("AZURE_AI_INFERENCE_ENDPOINT_FLUX2")
            or os.getenv("AZURE_OPENAI_ENDPOINT_FLUX")
            or self.westus3_inference
        )
        self.sora_inference = (
            os.getenv("AZURE_AI_INFERENCE_ENDPOINT_SORA")
            or os.getenv("AZURE_OPENAI_ENDPOINT_SORA")
            or self.sweden_inference
        )
        
        if not self.sweden_inference or not self.westus3_inference:
            logger.warning(
                "Inference endpoints not set in environment. "
                "Set AZURE_AI_INFERENCE_ENDPOINT_SWEDEN and AZURE_AI_INFERENCE_ENDPOINT_WESTUS3. "
                "These are automatically configured by terraform."
            )

        if not self.flux2_inference:
            logger.warning(
                "FLUX.2-pro inference endpoint not set. "
                "Set AZURE_AI_INFERENCE_ENDPOINT_FLUX2 or AZURE_OPENAI_ENDPOINT_FLUX."
            )
        
        # Deployment names (overridable via env)
        self.flux1_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_FLUX1", "FLUX.1-Kontext-pro")  # Sweden Central
        self.flux2_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_FLUX2", "FLUX.2-pro")          # West US 3
        self.sora_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_SORA", "sora")                 # Sweden Central
        
        # API versions
        # - Images typically require newer preview versions in Foundry/Azure OpenAI compatible endpoints.
        # - Sora uses a v1 job-based API with api-version=preview.
        self.api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-02-01")
        # For Foundry hub endpoints, FLUX image generation is supported on 2024-06-01.
        # Using an unsupported api-version can manifest as 404/400 depending on the route.
        self.images_api_version = os.getenv("AZURE_OPENAI_IMAGES_API_VERSION", "2024-06-01")
        self.images_api_version_fallback = os.getenv(
            "AZURE_OPENAI_IMAGES_API_VERSION_FALLBACK",
            "2024-06-01",
        )
        self.images_api_version_cache_seconds = int(
            os.getenv("AZURE_OPENAI_IMAGES_API_VERSION_CACHE_SECONDS", "3600")
        )
        self._images_api_version_cache: Dict[Tuple[str, str], Dict[str, Any]] = {}
        # Next generation v1 Azure OpenAI APIs use versionless routing: api-version=preview.
        # This is especially important for /openai/v1/* endpoints.
        self.v1_api_version = os.getenv("AZURE_OPENAI_V1_API_VERSION", "preview")
        self.sora_api_version = os.getenv("AZURE_OPENAI_SORA_API_VERSION", "preview")

        # Rate-limit protection for image endpoints.
        # These help reduce user-visible failures when the pricing tier call-rate is exceeded.
        self.images_max_retries = int(os.getenv("AZURE_OPENAI_IMAGES_MAX_RETRIES", "3"))
        self.images_retry_base_seconds = float(os.getenv("AZURE_OPENAI_IMAGES_RETRY_BASE_SECONDS", "1.5"))
        self.images_retry_max_seconds = float(os.getenv("AZURE_OPENAI_IMAGES_RETRY_MAX_SECONDS", "20"))
        self.images_concurrency_limit = max(1, int(os.getenv("AZURE_OPENAI_IMAGES_CONCURRENCY_LIMIT", "2")))
        self._images_semaphore = threading.BoundedSemaphore(value=self.images_concurrency_limit)

        # Optional: local Diffusers (Stable Diffusion) pipeline for more realistic OSS images.
        # This is intentionally lazy-loaded so default deployments do not require heavy ML deps.
        self._diffusers_lock = threading.Lock()
        self._diffusers_pipe = None
        self._diffusers_pipe_id: Optional[str] = None
        self._diffusers_pipe_device: Optional[str] = None

    def _retry_after_seconds(self, resp: requests.Response) -> Optional[float]:
        """Best-effort parse for server-provided retry delay."""
        try:
            ra = resp.headers.get("Retry-After")
            if ra is not None:
                return float(ra)
        except Exception:
            pass
        try:
            ra_ms = resp.headers.get("retry-after-ms") or resp.headers.get("x-ms-retry-after-ms")
            if ra_ms is not None:
                return float(ra_ms) / 1000.0
        except Exception:
            pass
        return None

    def _post_with_rate_limit_retries(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        timeout: float,
    ) -> requests.Response:
        """POST with simple 429 backoff, respecting Retry-After when available."""
        max_attempts = max(1, self.images_max_retries + 1)
        last_resp: Optional[requests.Response] = None

        for attempt_index in range(max_attempts):
            with self._images_semaphore:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            last_resp = resp

            if resp.status_code != 429:
                return resp

            if attempt_index >= max_attempts - 1:
                return resp

            retry_after = self._retry_after_seconds(resp)
            if retry_after is None:
                backoff = self.images_retry_base_seconds * (2 ** attempt_index)
                # Small jitter so concurrent clients don't retry in lockstep.
                backoff = backoff * (0.9 + random.random() * 0.2)
                retry_after = min(backoff, self.images_retry_max_seconds)
            else:
                retry_after = min(float(retry_after), self.images_retry_max_seconds)

            time.sleep(max(0.0, retry_after))

        return last_resp  # type: ignore[return-value]

    def _allowed_image_sizes(self) -> set[str]:
        """Return the allowed image sizes for image generation.

        Defaults align with common v1 images APIs. Override with:
        AZURE_OPENAI_ALLOWED_IMAGE_SIZES="1024x1024,1024x1792,1792x1024"
        """
        raw = (os.getenv("AZURE_OPENAI_ALLOWED_IMAGE_SIZES") or "").strip()
        if raw:
            sizes = {s.strip() for s in raw.split(",") if s.strip()}
            if sizes:
                return sizes
        return {"1024x1024", "1024x1792", "1792x1024"}

    def _normalize_image_size(self, size: Optional[str]) -> str:
        """Normalize image size to an allowed value.

        If the requested size isn't allowed, fall back to 1024x1024.
        """
        requested = (size or "").strip().lower()
        allowed = {s.lower() for s in self._allowed_image_sizes()}
        if requested and requested in allowed:
            # Preserve canonical casing from allowlist when possible.
            for s in self._allowed_image_sizes():
                if s.lower() == requested:
                    return s
            return requested
        fallback = "1024x1024"
        if requested and requested != fallback:
            logger.warning(
                "Requested image size '%s' not allowed; falling back to %s. Allowed=%s",
                requested,
                fallback,
                sorted(self._allowed_image_sizes()),
            )
        return fallback

    def _is_api_version_not_supported(self, response_text: str) -> bool:
        try:
            obj = json.loads(response_text)
            msg = (((obj or {}).get("error") or {}).get("message") or "")
            return "api version not supported" in str(msg).lower()
        except Exception:
            return "api version not supported" in str(response_text).lower()

    def _looks_like_not_found(self, response_text: str) -> bool:
        text = (response_text or "").strip()
        if not text:
            return False
        if text.upper() == "NOT FOUND":
            return True
        try:
            obj = json.loads(text)
            err = (obj or {}).get("error") or {}
            msg = str(err.get("message") or "")
            code = str(err.get("code") or "")
            combined = f"{code} {msg}".lower()
            return "resource not found" in combined or "not found" in combined
        except Exception:
            lowered = text.lower()
            return "resource not found" in lowered or lowered == "not found" or "not found" in lowered

    def _get_cached_images_api_version(self, endpoint: str, deployment: str) -> Optional[str]:
        key = (endpoint.rstrip("/"), deployment)
        entry = self._images_api_version_cache.get(key)
        if not entry:
            return None
        if float(entry.get("expires_at", 0)) < time.time():
            self._images_api_version_cache.pop(key, None)
            return None
        return entry.get("api_version")

    def _cache_images_api_version(self, endpoint: str, deployment: str, api_version: str) -> None:
        ttl = max(60, self.images_api_version_cache_seconds)
        key = (endpoint.rstrip("/"), deployment)
        self._images_api_version_cache[key] = {
            "api_version": api_version,
            "expires_at": time.time() + ttl,
        }

    def _post_deployment_images_with_version_fallback(
        self,
        endpoint: str,
        deployment: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        size: str,
    ) -> Dict[str, Any]:
        endpoint = endpoint.rstrip("/")
        cached = self._get_cached_images_api_version(endpoint, deployment)
        primary_version = cached or self.images_api_version
        fallback_version = self.images_api_version_fallback

        def attempt(api_version: str) -> Dict[str, Any]:
            url = f"{endpoint}/openai/deployments/{deployment}/images/generations"
            url_with_version = f"{url}?api-version={api_version}"
            resp = self._post_with_rate_limit_retries(url_with_version, headers=headers, payload=payload, timeout=120)
            return {
                "status_code": resp.status_code,
                "text": resp.text,
                "json": (resp.json() if resp.headers.get("content-type", "").lower().startswith("application/json") else None),
                "url": url,
                "api_version": api_version,
            }

        first = attempt(primary_version)
        if first["status_code"] == 200:
            self._cache_images_api_version(endpoint, deployment, primary_version)
            return {
                "ok": True,
                "result": first["json"] or {},
                "request": {"url": first["url"], "api_version": primary_version, "size": size},
            }

        should_retry_fallback = (
            self._is_api_version_not_supported(first["text"])
            or (first["status_code"] == 404 and self._looks_like_not_found(first["text"]))
        )
        if should_retry_fallback and fallback_version and fallback_version != primary_version:
            second = attempt(fallback_version)
            if second["status_code"] == 200:
                self._cache_images_api_version(endpoint, deployment, fallback_version)
                return {
                    "ok": True,
                    "result": second["json"] or {},
                    "request": {"url": second["url"], "api_version": fallback_version, "size": size},
                }
            return {
                "ok": False,
                "error": second["text"],
                "status_code": second["status_code"],
                "request": {"url": second["url"], "api_version": fallback_version, "size": size},
            }

        return {
            "ok": False,
            "error": first["text"],
            "status_code": first["status_code"],
            "request": {"url": first["url"], "api_version": primary_version, "size": size},
        }

    def _normalize_endpoint(self, endpoint: Optional[str]) -> Optional[str]:
        if not endpoint:
            return endpoint
        return endpoint.rstrip("/")

    def _strip_openai_v1_suffix(self, endpoint: Optional[str]) -> Optional[str]:
        if not endpoint:
            return endpoint
        e = self._normalize_endpoint(endpoint)
        if not e:
            return e
        # Support user-provided endpoints that already include /openai/v1
        for suffix in ("/openai/v1", "/openai/v1/"):
            if e.lower().endswith(suffix):
                return e[: -len(suffix)]
        return e

    def _to_openai_azure_domain(self, endpoint: Optional[str]) -> Optional[str]:
        if not endpoint:
            return endpoint
        e = self._normalize_endpoint(endpoint)
        if not e:
            return e
        return e.replace(".cognitiveservices.azure.com", ".openai.azure.com")

    def _candidate_openai_endpoints(self, endpoint: Optional[str]) -> list[str]:
        """Return endpoint candidates, preferring the provided endpoint then a domain-swapped fallback."""
        base = self._strip_openai_v1_suffix(endpoint)
        if not base:
            return []
        swapped = self._to_openai_azure_domain(base)

        # Prefer the Azure OpenAI-compatible domain first when the input is a
        # Cognitive Services domain. This avoids an extra 404 on /openai/v1/*
        # for some resources and reduces overall request volume (helps with 429s).
        candidates: list[str] = []
        if base and ".cognitiveservices.azure.com" in base.lower() and swapped and swapped != base:
            candidates = [swapped, base]
        else:
            candidates = [base]
            if swapped and swapped != base:
                candidates.append(swapped)
        # De-dup while preserving order
        out: list[str] = []
        for c in candidates:
            if c and c not in out:
                out.append(c)
        return out

    def _get_optional_api_key(self, env_key_name: str) -> Optional[str]:
        """Return an API key if configured for key-based auth; otherwise None."""
        api_key = os.getenv(env_key_name) or os.getenv("AZURE_OPENAI_API_KEY")
        if not api_key or api_key.strip().upper() == "MANAGED_IDENTITY":
            return None
        return api_key

    def _build_headers(self, api_key: Optional[str] = None) -> Dict[str, str]:
        token = self._get_access_token()
        headers: Dict[str, str] = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
        # Only include api-key header when using key-based auth.
        if api_key:
            headers["api-key"] = api_key
        return headers
    
    def _get_access_token(self) -> str:
        """Get Azure AD access token for API authentication."""
        token = self.credential.get_token("https://cognitiveservices.azure.com/.default")
        return token.token

    def _request_max_retries(self) -> int:
        try:
            return max(0, int(os.getenv("AZURE_OPENAI_HTTP_MAX_RETRIES", "6")))
        except Exception:
            return 6

    def _request_base_delay_seconds(self) -> float:
        try:
            return max(0.0, float(os.getenv("AZURE_OPENAI_HTTP_RETRY_BASE_DELAY_SECONDS", "2.0")))
        except Exception:
            return 2.0

    def _parse_retry_after_seconds(self, response: requests.Response) -> Optional[float]:
        # Retry-After is typically seconds for these endpoints.
        try:
            value = response.headers.get("Retry-After")
            if value is None:
                return None
            value = str(value).strip()
            if not value:
                return None
            return float(value)
        except Exception:
            return None

    def _post_with_retries(
        self,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        timeout: int,
        max_retries: Optional[int] = None,
    ) -> requests.Response:
        """POST with basic retry/backoff for transient failures.

        Handles 429 using Retry-After when present.
        """
        retries = self._request_max_retries() if max_retries is None else max_retries
        base_delay = self._request_base_delay_seconds()

        last_exc: Optional[Exception] = None
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)

                if resp.status_code in (429, 500, 502, 503, 504) and attempt <= (retries + 1):
                    # Respect server hint when rate-limited.
                    retry_after = self._parse_retry_after_seconds(resp)
                    if retry_after is None:
                        # Exponential backoff with a small jitter.
                        retry_after = min(30.0, base_delay * (2 ** (attempt - 1)))
                        retry_after += random.uniform(0.0, 0.25)

                    # Only retry if we have remaining budget.
                    if attempt <= (retries + 1):
                        logger.warning(
                            "HTTP %s from %s (attempt %s/%s). Retrying in %.2fs",
                            resp.status_code,
                            url,
                            attempt,
                            retries + 1,
                            float(retry_after),
                        )
                        time.sleep(float(retry_after))
                        continue

                return resp

            except requests.RequestException as e:
                last_exc = e
                if attempt > (retries + 1):
                    break
                delay = min(30.0, base_delay * (2 ** (attempt - 1))) + random.uniform(0.0, 0.25)
                logger.warning(
                    "Request exception posting to %s (attempt %s/%s): %s. Retrying in %.2fs",
                    url,
                    attempt,
                    retries + 1,
                    str(e),
                    float(delay),
                )
                time.sleep(float(delay))

        # Final attempt failed via exception path; raise to preserve error signal.
        raise last_exc if last_exc else RuntimeError("Request failed")

    def _parse_resolution(self, resolution: str) -> tuple[int, int]:
        """Parse resolution strings like '1920x1080' into (width,height)."""
        raw = (resolution or "").strip().lower()
        if "x" in raw:
            parts = raw.split("x", 1)
            try:
                w = int(str(parts[0]).strip())
                h = int(str(parts[1]).strip())
                if w > 0 and h > 0:
                    return w, h
            except Exception:
                pass
        # Default to square when unknown.
        return 1080, 1080

    def _parse_image_size(self, size: str) -> tuple[int, int]:
        """Parse image size strings like '1024x1024' into (width,height)."""
        raw = (size or "").strip().lower()
        if "x" in raw:
            parts = raw.split("x", 1)
            try:
                w = int(str(parts[0]).strip())
                h = int(str(parts[1]).strip())
                if w > 0 and h > 0:
                    return w, h
            except Exception:
                pass
        return 1024, 1024

    def _post_model_images_route(
        self,
        endpoint: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        deployment: str,
        size: str,
    ) -> Dict[str, Any]:
        """POST to /openai/images/generations with model=<deployment>.

        Some Foundry/Azure endpoints expose images APIs on this route rather than
        /openai/v1/images/* or deployment-scoped routes.
        """
        endpoint = endpoint.rstrip("/")
        url = f"{endpoint}/openai/images/generations"

        model_payload = dict(payload)
        model_payload["model"] = deployment

        def attempt(api_version: str) -> Dict[str, Any]:
            url_with_version = f"{url}?api-version={api_version}"
            resp = self._post_with_rate_limit_retries(url_with_version, headers=headers, payload=model_payload, timeout=120)
            return {
                "status_code": resp.status_code,
                "text": resp.text,
                "json": (
                    resp.json()
                    if resp.headers.get("content-type", "").lower().startswith("application/json")
                    else None
                ),
                "url": url,
                "api_version": api_version,
            }

        primary_version = self.images_api_version
        fallback_version = self.images_api_version_fallback

        first = attempt(primary_version)
        if first["status_code"] == 200:
            return {
                "ok": True,
                "result": first["json"] or {},
                "request": {"url": first["url"], "api_version": primary_version, "size": size},
            }

        should_retry_fallback = (
            self._is_api_version_not_supported(first["text"])
            or (first["status_code"] == 404 and self._looks_like_not_found(first["text"]))
        )
        if should_retry_fallback and fallback_version and fallback_version != primary_version:
            second = attempt(fallback_version)
            if second["status_code"] == 200:
                return {
                    "ok": True,
                    "result": second["json"] or {},
                    "request": {"url": second["url"], "api_version": fallback_version, "size": size},
                }
            return {
                "ok": False,
                "error": second["text"],
                "status_code": second["status_code"],
                "request": {"url": second["url"], "api_version": fallback_version, "size": size},
            }

        return {
            "ok": False,
            "error": first["text"],
            "status_code": first["status_code"],
            "request": {"url": first["url"], "api_version": primary_version, "size": size},
        }
    
    def generate_image_flux1(self, prompt: str, size: str = "1024x1024", **kwargs) -> Dict[str, Any]:
        """
        Generate images using FLUX.1-Kontext-pro (for documents/contextual images).
        
        Args:
            prompt: Image description
            size: Image size (default: "1024x1024")
            **kwargs: Additional parameters (quality, style, etc.)
        
        Returns:
            dict with image data (base64 or URL) and metadata
        """
        logger.info(f"Generating image with FLUX.1-Kontext-pro: {prompt[:100]}")

        size = self._normalize_image_size(size)
        
        try:
            api_key = self._get_optional_api_key("AZURE_OPENAI_API_KEY_FLUX")
            headers = self._build_headers(api_key=api_key)

            payload = {
                "prompt": prompt,
                "size": size,
                "n": kwargs.get("n", 1),
                "quality": kwargs.get("quality", "standard"),
                "response_format": kwargs.get("response_format", "b64_json"),  # "url" or "b64_json"
            }

            last_error: Optional[Dict[str, Any]] = None
            for endpoint in self._candidate_openai_endpoints(self.flux1_inference):
                # Match the known-working pattern: v1 images route with model=<deployment>.
                v1_payload = dict(payload)
                v1_payload["model"] = self.flux1_deployment

                v1_url = f"{endpoint}/openai/v1/images/generations?api-version={self.v1_api_version}"
                v1_resp = self._post_with_rate_limit_retries(v1_url, headers=headers, payload=v1_payload, timeout=120)

                if (
                    v1_resp.status_code == 400
                    and self._is_api_version_not_supported(v1_resp.text)
                    and str(self.v1_api_version).lower() != "preview"
                ):
                    v1_url = f"{endpoint}/openai/v1/images/generations?api-version=preview"
                    v1_resp = self._post_with_rate_limit_retries(v1_url, headers=headers, payload=v1_payload, timeout=120)

                if v1_resp.status_code == 200:
                    v1_result = v1_resp.json()
                    logger.info("SUCCESS - FLUX.1-Kontext-pro image generated via v1 route")
                    return {
                        "model": self.flux1_deployment,
                        "data": v1_result.get("data", []),
                        "request": {"url": v1_url, "api_version": self.v1_api_version, "size": size},
                        "status": "success",
                    }

                # If we're rate-limited or unauthorized, do not attempt an additional
                # fallback request (it increases load and can surface confusing 404s).
                if v1_resp.status_code in (401, 403, 429):
                    last_error = {
                        "model": self.flux1_deployment,
                        "error": v1_resp.text,
                        "status_code": v1_resp.status_code,
                        "request": {"url": v1_url, "api_version": self.v1_api_version, "size": size},
                        "status": "error",
                    }
                    continue

                # Fallback: model-based images route (non-v1) with model=<deployment>.
                model_route = self._post_model_images_route(
                    endpoint=endpoint,
                    headers=headers,
                    payload=payload,
                    deployment=self.flux1_deployment,
                    size=size,
                )

                if model_route.get("ok"):
                    result = model_route.get("result") or {}
                    logger.info("SUCCESS - FLUX.1-Kontext-pro image generated via model route")
                    return {
                        "model": self.flux1_deployment,
                        "data": result.get("data", []),
                        "request": model_route.get("request"),
                        "status": "success",
                    }

                # Fallback: deployment-scoped images route (auto-retry api-version + cache).
                dep = self._post_deployment_images_with_version_fallback(
                    endpoint=endpoint,
                    deployment=self.flux1_deployment,
                    headers=headers,
                    payload=payload,
                    size=size,
                )

                if dep.get("ok"):
                    result = dep.get("result") or {}
                    logger.info("SUCCESS - FLUX.1-Kontext-pro image generated")
                    return {
                        "model": self.flux1_deployment,
                        "data": result.get("data", []),
                        "request": dep.get("request"),
                        "status": "success",
                    }

                # Prefer v1 error details when v1 was close, otherwise deployment route errors.
                last_error = {
                    "model": self.flux1_deployment,
                    "error": (
                        model_route.get("error")
                        if model_route.get("status_code")
                        else (dep.get("error") if v1_resp.status_code == 404 else v1_resp.text)
                    ),
                    "status_code": (
                        model_route.get("status_code")
                        if model_route.get("status_code")
                        else (dep.get("status_code") if v1_resp.status_code == 404 else v1_resp.status_code)
                    ),
                    "request": {
                        "url": (
                            (model_route.get("request") or {}).get("url")
                            if model_route.get("status_code")
                            else (v1_url if v1_resp.status_code != 404 else (dep.get("request") or {}).get("url"))
                        ),
                        "api_version": (
                            (model_route.get("request") or {}).get("api_version")
                            if model_route.get("status_code")
                            else (self.v1_api_version if v1_resp.status_code != 404 else (dep.get("request") or {}).get("api_version"))
                        ),
                        "size": size,
                    },
                    "status": "error",
                }

            logger.error(f"FLUX.1 API error: {last_error}")
            return last_error or {
                "model": self.flux1_deployment,
                "error": "No usable inference endpoint configured.",
                "status_code": 500,
                "request": {"url": None, "api_version": self.images_api_version, "size": size},
                "status": "error",
            }
                
        except Exception as e:
            logger.error(f"Exception in FLUX.1 generation: {str(e)}")
            return {
                "model": self.flux1_deployment,
                "error": str(e),
                "status": "exception"
            }
    
    def generate_image_flux2(self, prompt: str, size: str = "1024x1024", **kwargs) -> Dict[str, Any]:
        """
        Generate images using FLUX.2-pro (for high-quality visual content).
        
        Args:
            prompt: Image description
            size: Image size (default: "1024x1024")
            **kwargs: Additional parameters (quality, style, etc.)
        
        Returns:
            dict with image data (base64 or URL) and metadata
        """
        logger.info(f"Generating image with FLUX.2-pro: {prompt[:100]}")

        size = self._normalize_image_size(size)
        
        try:
            api_key = self._get_optional_api_key("AZURE_OPENAI_API_KEY_FLUX")
            headers = self._build_headers(api_key=api_key)
            payload = {
                "prompt": prompt,
                "size": size,
                "n": kwargs.get("n", 1),
                "quality": kwargs.get("quality", "hd"),
                "response_format": kwargs.get("response_format", "b64_json"),
            }

            last_error: Optional[Dict[str, Any]] = None
            for endpoint in self._candidate_openai_endpoints(self.flux2_inference):
                # Match the known-working pattern: v1 images route with model=<deployment>.
                v1_payload = dict(payload)
                v1_payload["model"] = self.flux2_deployment

                v1_url = f"{endpoint}/openai/v1/images/generations?api-version={self.v1_api_version}"
                v1_resp = self._post_with_rate_limit_retries(v1_url, headers=headers, payload=v1_payload, timeout=120)

                if (
                    v1_resp.status_code == 400
                    and self._is_api_version_not_supported(v1_resp.text)
                    and str(self.v1_api_version).lower() != "preview"
                ):
                    v1_url = f"{endpoint}/openai/v1/images/generations?api-version=preview"
                    v1_resp = self._post_with_rate_limit_retries(v1_url, headers=headers, payload=v1_payload, timeout=120)

                if v1_resp.status_code == 200:
                    v1_result = v1_resp.json()
                    logger.info("FLUX.2-pro image generated successfully via v1 route")
                    return {
                        "model": self.flux2_deployment,
                        "data": v1_result.get("data", []),
                        "request": {"url": v1_url, "api_version": self.v1_api_version, "size": size},
                        "status": "success",
                    }

                # If we're rate-limited or unauthorized, do not attempt an additional
                # fallback request (it increases load and can surface confusing 404s).
                if v1_resp.status_code in (401, 403, 429):
                    last_error = {
                        "model": self.flux2_deployment,
                        "error": v1_resp.text,
                        "status_code": v1_resp.status_code,
                        "request": {"url": v1_url, "api_version": self.v1_api_version, "size": size},
                        "status": "error",
                    }
                    continue

                # Fallback: model-based images route (non-v1) with model=<deployment>.
                model_route = self._post_model_images_route(
                    endpoint=endpoint,
                    headers=headers,
                    payload=payload,
                    deployment=self.flux2_deployment,
                    size=size,
                )

                if model_route.get("ok"):
                    result = model_route.get("result") or {}
                    logger.info("✓ FLUX.2-pro image generated successfully via model route")
                    return {
                        "model": self.flux2_deployment,
                        "data": result.get("data", []),
                        "request": model_route.get("request"),
                        "status": "success",
                    }

                # Fallback: deployment-scoped images route (auto-retry api-version + cache).
                dep = self._post_deployment_images_with_version_fallback(
                    endpoint=endpoint,
                    deployment=self.flux2_deployment,
                    headers=headers,
                    payload=payload,
                    size=size,
                )

                if dep.get("ok"):
                    result = dep.get("result") or {}
                    logger.info("✓ FLUX.2-pro image generated successfully")
                    return {
                        "model": self.flux2_deployment,
                        "data": result.get("data", []),
                        "request": dep.get("request"),
                        "status": "success",
                    }

                last_error = {
                    "model": self.flux2_deployment,
                    "error": (
                        model_route.get("error")
                        if model_route.get("status_code")
                        else (dep.get("error") if v1_resp.status_code == 404 else v1_resp.text)
                    ),
                    "status_code": (
                        model_route.get("status_code")
                        if model_route.get("status_code")
                        else (dep.get("status_code") if v1_resp.status_code == 404 else v1_resp.status_code)
                    ),
                    "request": {
                        "url": (
                            (model_route.get("request") or {}).get("url")
                            if model_route.get("status_code")
                            else (v1_url if v1_resp.status_code != 404 else (dep.get("request") or {}).get("url"))
                        ),
                        "api_version": (
                            (model_route.get("request") or {}).get("api_version")
                            if model_route.get("status_code")
                            else (self.v1_api_version if v1_resp.status_code != 404 else (dep.get("request") or {}).get("api_version"))
                        ),
                        "size": size,
                    },
                    "status": "error",
                }
                continue

            logger.error(f"FLUX.2 API error: {last_error}")
            return last_error or {
                "model": self.flux2_deployment,
                "error": "No usable inference endpoint configured.",
                "status_code": 500,
                "request": {"url": None, "api_version": self.images_api_version, "size": size},
                "status": "error",
            }
                
        except Exception as e:
            logger.error(f"Exception in FLUX.2 generation: {str(e)}")
            return {
                "model": self.flux2_deployment,
                "error": str(e),
                "status": "exception"
            }

    def generate_image_open_source(self, prompt: str, size: str = "1024x1024", **kwargs) -> Dict[str, Any]:
        """Generate an image using an open-source backend (cloud-hosted).

        Default behavior: local-library baseline (CPU-safe) when OSS_BASELINE_MODE is 'local' (default).
        Optional local realistic behavior: set OSS_BASELINE_MODE=diffusers and configure OSS_DIFFUSERS_MODEL_ID.
        Optional automatic behavior: set OSS_BASELINE_MODE=auto to prefer Diffusers when configured/available,
        otherwise fall back to the lightweight local baseline.
        Optional Azure/AKS worker behavior: set OSS_BASELINE_MODE=aks or OSS_BASELINE_MODE=azure-worker and configure
        OSS_AZURE_WORKER_URL (recommended for running Diffusers on GPU in your Azure subscription).
        Optional remote behavior: set OSS_BASELINE_MODE=remote and configure OSS_IMAGE_BACKEND_URL.

        Remote implementation supports an AUTOMATIC1111-compatible API:
        POST {OSS_IMAGE_BACKEND_URL}/sdapi/v1/txt2img
        """
        mode = (os.getenv("OSS_BASELINE_MODE") or "local").strip().lower()
        backend_url = (os.getenv("OSS_IMAGE_BACKEND_URL") or "").strip().rstrip("/")

        # Prefer an internal Azure worker (e.g., AKS GPU service) when configured.
        # This keeps "OSS" as open-source libraries, but uses Azure compute for heavy inference.
        worker_url = (os.getenv("OSS_AZURE_WORKER_URL") or os.getenv("OSS_AKS_WORKER_URL") or "").strip().rstrip("/")
        if mode in {"aks", "azure-worker"}:
            return self._generate_image_azure_worker(prompt, size=size, worker_url=worker_url, **kwargs)

        if mode == "auto":
            if worker_url:
                return self._generate_image_azure_worker(prompt, size=size, worker_url=worker_url, **kwargs)
            # Only attempt local Diffusers when explicitly configured.
            # Otherwise, fall back to the lightweight in-process baseline.
            model_id = (os.getenv("OSS_DIFFUSERS_MODEL_ID") or "").strip()
            if model_id:
                return self._generate_image_diffusers_local(prompt, size=size, **kwargs)
            return self._generate_image_baseline_local(prompt, size=size)

        if mode == "diffusers":
            return self._generate_image_diffusers_local(prompt, size=size, **kwargs)

        # Default behavior: local-library baseline (works on App Service CPU, no external services).
        if mode != "remote" or not backend_url:
            return self._generate_image_baseline_local(prompt, size=size)

        # mode == remote falls through to the external OSS backend.

        kind = (os.getenv("OSS_IMAGE_BACKEND_KIND") or "a1111").strip().lower()
        timeout = float(os.getenv("OSS_IMAGE_BACKEND_TIMEOUT_SECONDS", "120"))
        bearer = (os.getenv("OSS_IMAGE_BACKEND_AUTH_BEARER") or "").strip()

        size = self._normalize_image_size(size)
        width, height = self._parse_image_size(size)

        if kind != "a1111":
            return {
                "model": f"oss:{kind}",
                "status": "error",
                "status_code": 400,
                "error": f"Unsupported OSS_IMAGE_BACKEND_KIND '{kind}' (supported: a1111)",
                "request": {"url": backend_url, "size": size},
            }

        url = f"{backend_url}/sdapi/v1/txt2img"
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        payload: Dict[str, Any] = {
            "prompt": prompt,
            "width": width,
            "height": height,
            "steps": int(kwargs.get("steps", os.getenv("OSS_IMAGE_STEPS", "20"))),
            "sampler_name": kwargs.get("sampler_name", os.getenv("OSS_IMAGE_SAMPLER", "Euler a")),
        }

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as e:
            return {
                "model": "oss:a1111",
                "status": "error",
                "status_code": 0,
                "error": str(e),
                "request": {"url": url, "size": size},
            }

        if resp.status_code != 200:
            return {
                "model": "oss:a1111",
                "status": "error",
                "status_code": resp.status_code,
                "error": resp.text,
                "request": {"url": url, "size": size},
            }

        try:
            obj = resp.json() or {}
        except Exception:
            return {
                "model": "oss:a1111",
                "status": "error",
                "status_code": 502,
                "error": "OSS backend returned non-JSON response",
                "request": {"url": url, "size": size},
            }

        images = obj.get("images") or []
        if not isinstance(images, list) or not images:
            return {
                "model": "oss:a1111",
                "status": "error",
                "status_code": 502,
                "error": "OSS backend returned no images",
                "request": {"url": url, "size": size},
            }

        b64 = str(images[0])
        return {
            "model": "oss:a1111",
            "status": "success",
            "data": [{"b64_json": b64}],
            "request": {"url": url, "size": size},
        }


    def remove_background_open_source(self, image_input: Any, **_kwargs) -> Dict[str, Any]:
        """Remove background from an input image using local open-source libraries.

        Returns a transparent PNG as base64 in the standard {data:[{b64_json:...}]} shape.
        """
        try:
            image_bytes = self._coerce_image_input_to_bytes(image_input)
        except Exception as e:
            return {
                "model": "oss:bgremove",
                "status": "error",
                "status_code": 400,
                "error": f"Invalid image input: {e}",
            }

        try:
            b64_png = self._remove_background_baseline_local(image_bytes)
            return {
                "model": "oss:bgremove:local",
                "status": "success",
                "data": [{"b64_json": b64_png}],
            }
        except Exception as e:
            logger.error(f"Exception in background removal: {e}", exc_info=True)
            return {
                "model": "oss:bgremove:local",
                "status": "error",
                "status_code": 500,
                "error": str(e),
            }


    def blur_background_open_source(self, image_input: Any, **kwargs) -> Dict[str, Any]:
        """Blur background while keeping foreground sharp.

        Uses an OpenCV GrabCut mask when available.
        """
        try:
            image_bytes = self._coerce_image_input_to_bytes(image_input)
        except Exception as e:
            return {
                "model": "oss:blur_bg",
                "status": "error",
                "status_code": 400,
                "error": f"Invalid image input: {e}",
            }

        try:
            b64_png = self._blur_background_local(image_bytes, **kwargs)
            return {
                "model": "oss:blur_bg:local",
                "status": "success",
                "data": [{"b64_json": b64_png}],
            }
        except Exception as e:
            logger.error(f"Exception in background blur: {e}", exc_info=True)
            return {
                "model": "oss:blur_bg:local",
                "status": "error",
                "status_code": 500,
                "error": str(e),
            }


    def blur_faces_open_source(self, image_input: Any, **kwargs) -> Dict[str, Any]:
        """Blur faces using OpenCV Haar cascades (no external model downloads)."""
        try:
            image_bytes = self._coerce_image_input_to_bytes(image_input)
        except Exception as e:
            return {
                "model": "oss:blur_faces",
                "status": "error",
                "status_code": 400,
                "error": f"Invalid image input: {e}",
            }

        try:
            b64_png, faces = self._blur_faces_local(image_bytes, **kwargs)
            return {
                "model": "oss:blur_faces:local",
                "status": "success",
                "data": [{"b64_json": b64_png}],
                "faces": faces,
            }
        except Exception as e:
            logger.error(f"Exception in face blur: {e}", exc_info=True)
            return {
                "model": "oss:blur_faces:local",
                "status": "error",
                "status_code": 500,
                "error": str(e),
            }


    def apply_filter_open_source(self, image_input: Any, filter_name: str, **kwargs) -> Dict[str, Any]:
        """Apply a simple OpenCV-based filter.

        Supported filters: edge, sharpen, denoise, cartoon, posterize
        """
        try:
            image_bytes = self._coerce_image_input_to_bytes(image_input)
        except Exception as e:
            return {
                "model": "oss:filter",
                "status": "error",
                "status_code": 400,
                "error": f"Invalid image input: {e}",
            }

        try:
            b64_png = self._apply_filter_local(image_bytes, filter_name=filter_name, **kwargs)
            return {
                "model": f"oss:filter:{str(filter_name).strip().lower()}:local",
                "status": "success",
                "data": [{"b64_json": b64_png}],
            }
        except Exception as e:
            logger.error(f"Exception applying filter '{filter_name}': {e}", exc_info=True)
            return {
                "model": "oss:filter:local",
                "status": "error",
                "status_code": 500,
                "error": str(e),
            }


    def transform_image_open_source(self, image_input: Any, **kwargs) -> Dict[str, Any]:
        """Deterministic crop/resize/pad transforms.

        kwargs:
          - width, height (optional)
          - aspect (e.g., '16:9', '1:1', '4:5', '9:16') (optional)
          - mode: 'crop' (default) or 'pad'
        """
        try:
            image_bytes = self._coerce_image_input_to_bytes(image_input)
        except Exception as e:
            return {
                "model": "oss:transform",
                "status": "error",
                "status_code": 400,
                "error": f"Invalid image input: {e}",
            }

        try:
            b64_png, info = self._transform_image_local(image_bytes, **kwargs)
            return {
                "model": "oss:transform:local",
                "status": "success",
                "data": [{"b64_json": b64_png}],
                "transform": info,
            }
        except Exception as e:
            logger.error(f"Exception transforming image: {e}", exc_info=True)
            return {
                "model": "oss:transform:local",
                "status": "error",
                "status_code": 500,
                "error": str(e),
            }


    def generate_thumbnail_open_source(
        self,
        image_input: Any,
        size: str = "1024x1024",
        *,
        mode: str = "crop",
        enhance: bool = True,
        **_kwargs,
    ) -> Dict[str, Any]:
        """Create a thumbnail-style image derived from an uploaded image.

        This is intentionally deterministic and uses in-process open-source libraries
        (Pillow/OpenCV/NumPy) rather than hosted endpoints.
        """
        # Optional: offload thumbnail pipeline to an internal Azure/AKS worker (GPU-capable).
        # This enables heavier pipelines (e.g., YOLO/rembg/ffmpeg) without bloating the web app.
        thumbnail_mode = (os.getenv("OSS_THUMBNAIL_MODE") or "local").strip().lower()
        worker_url = (os.getenv("OSS_AZURE_WORKER_URL") or os.getenv("OSS_AKS_WORKER_URL") or "").strip().rstrip("/")

        if thumbnail_mode in {"aks", "azure-worker", "worker"}:
            try:
                image_bytes = self._coerce_image_input_to_bytes(image_input)
            except Exception as e:
                return {
                    "model": "oss:thumbnail:worker",
                    "status": "error",
                    "status_code": 400,
                    "error": f"Invalid image input: {e}",
                }

            timeout = float(os.getenv("OSS_WORKER_TIMEOUT_SECONDS", "300"))
            bearer = (os.getenv("OSS_WORKER_AUTH_BEARER") or "").strip()

            try:
                target_size = self._normalize_image_size(size)
                width, height = self._parse_image_size(target_size)
            except Exception:
                width, height = self._parse_image_size("1280x720")

            url = f"{worker_url}/generate-thumbnail" if worker_url else ""
            if not url:
                # Keep behavior non-breaking: fall back to local thumbnail.
                # (Worker URL is intentionally optional.)
                pass
            else:
                headers: Dict[str, str] = {"Content-Type": "application/json"}
                if bearer:
                    headers["Authorization"] = f"Bearer {bearer}"

                payload: Dict[str, Any] = {
                    "image_b64": base64.b64encode(image_bytes).decode("utf-8"),
                    "prompt": None,
                    "width": int(width),
                    "height": int(height),
                    "output": "png",
                }

                try:
                    resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
                except Exception as e:
                    # Fall back to local behavior.
                    resp = None
                    worker_error = f"Worker request failed: {e}"

                if resp is not None and resp.status_code == 200:
                    try:
                        obj = resp.json() or {}
                    except Exception as e:
                        obj = {"status": "error", "error": f"Worker returned non-JSON response: {e}"}

                    # If the worker returns the standard UI payload shape, pass it through.
                    try:
                        _ = (obj.get("data") or [])[0].get("b64_json")
                        obj.setdefault("model", "oss:thumbnail:worker")
                        obj.setdefault("status", "success")
                        obj.setdefault("request", {})
                        obj["request"].update({"url": url, "size": size, "mode": mode, "enhance": bool(enhance)})
                        obj.setdefault("diagnostics", {})
                        obj["diagnostics"].setdefault("azure_worker", {"enabled": True, "url": url})
                        return obj
                    except Exception:
                        # If it's not in the expected shape, fall back.
                        worker_error = "Worker response missing data[0].b64_json"
                elif resp is not None:
                    worker_error = f"Worker returned HTTP {resp.status_code}: {resp.text[:500]}"

                # Fall back to local thumbnail, but include diagnostics so it's obvious what happened.
                # (We don't early-return here; the local path below remains the source of truth.)

        try:
            image_bytes = self._coerce_image_input_to_bytes(image_input)
        except Exception as e:
            return {
                "model": "oss:thumbnail",
                "status": "error",
                "status_code": 400,
                "error": f"Invalid image input: {e}",
            }

        try:
            width, height = self._parse_image_size(size)
            b64_png, info = self._transform_image_local(
                image_bytes,
                width=width,
                height=height,
                mode=mode,
            )

            if enhance:
                # Light-touch enhancement for punchier thumbnails.
                from PIL import ImageEnhance, ImageFilter  # type: ignore

                img = Image.open(BytesIO(base64.b64decode(b64_png))).convert("RGB")
                img = ImageEnhance.Contrast(img).enhance(1.08)
                img = ImageEnhance.Color(img).enhance(1.06)
                img = img.filter(ImageFilter.UnsharpMask(radius=1.2, percent=120, threshold=3))
                b64_png = self._encode_png_b64(img)

            result = {
                "model": "oss:thumbnail:local",
                "status": "success",
                "data": [{"b64_json": b64_png}],
                "transform": info,
                "request": {"size": size, "mode": mode, "enhance": bool(enhance)},
            }
            if thumbnail_mode in {"aks", "azure-worker", "worker"}:
                result.setdefault("diagnostics", {})
                result["diagnostics"]["azure_worker"] = {
                    "enabled": True,
                    "url": (f"{worker_url}/generate-thumbnail" if worker_url else None),
                    "status": "fallback",
                    "reason": locals().get("worker_error") or "Worker unavailable; used local thumbnail",
                }
            return result
        except Exception as e:
            logger.error(f"Exception generating OSS thumbnail: {e}", exc_info=True)
            return {
                "model": "oss:thumbnail:local",
                "status": "error",
                "status_code": 500,
                "error": str(e),
            }


    def enhance_image_open_source(
        self,
        image_input: Any,
        *,
        strength: str = "auto",
        **_kwargs,
    ) -> Dict[str, Any]:
        """Lightweight visual enhancement for an uploaded image.

        Keeps the original dimensions and uses only in-process open-source libraries.
        """
        try:
            image_bytes = self._coerce_image_input_to_bytes(image_input)
        except Exception as e:
            return {
                "model": "oss:enhance",
                "status": "error",
                "status_code": 400,
                "error": f"Invalid image input: {e}",
            }

        try:
            from PIL import ImageEnhance, ImageFilter  # type: ignore

            img = Image.open(BytesIO(image_bytes)).convert("RGB")

            s = (strength or "auto").strip().lower()
            if s in {"low", "light"}:
                contrast, color, radius, percent, threshold = 1.05, 1.03, 1.0, 110, 3
            elif s in {"high", "strong"}:
                contrast, color, radius, percent, threshold = 1.12, 1.10, 1.4, 140, 2
            else:
                contrast, color, radius, percent, threshold = 1.08, 1.06, 1.2, 120, 3

            img = ImageEnhance.Contrast(img).enhance(float(contrast))
            img = ImageEnhance.Color(img).enhance(float(color))
            img = img.filter(ImageFilter.UnsharpMask(radius=float(radius), percent=int(percent), threshold=int(threshold)))

            return {
                "model": "oss:enhance:local",
                "status": "success",
                "data": [{"b64_json": self._encode_png_b64(img)}],
                "request": {"strength": s},
            }
        except Exception as e:
            logger.error(f"Exception enhancing image: {e}", exc_info=True)
            return {
                "model": "oss:enhance:local",
                "status": "error",
                "status_code": 500,
                "error": str(e),
            }


    def _coerce_image_input_to_bytes(self, image_input: Any) -> bytes:
        if image_input is None:
            raise ValueError("no image provided")
        if isinstance(image_input, (bytes, bytearray)):
            return bytes(image_input)
        if not isinstance(image_input, str):
            raise ValueError("unsupported image type")

        s = image_input.strip()
        if not s:
            raise ValueError("empty image string")

        if s.startswith("data:"):
            comma = s.find(",")
            if comma < 0:
                raise ValueError("invalid data URI")
            b64_part = s[comma + 1 :].strip()
            return base64.b64decode(b64_part)

        if s.startswith("http://") or s.startswith("https://"):
            resp = requests.get(s, timeout=30)
            resp.raise_for_status()
            return resp.content

        # Support the app's upload URLs (e.g. /static/uploads/<file>). These are
        # served from the local static folder mounted at /static.
        if s.startswith("/static/") or s.startswith("static/"):
            rel = s[1:] if s.startswith("/") else s
            # /static/... maps to app/static/...
            candidates = [Path("app") / rel, Path(rel)]
            attempted: list[str] = []
            for p in candidates:
                try:
                    attempted.append(str(p))
                    if p.exists() and p.is_file():
                        return p.read_bytes()
                except Exception:
                    continue
            attempted_s = ", ".join(attempted) if attempted else "(none)"
            raise ValueError(f"static file not found for URL '{s}'. Tried: {attempted_s}")

        # Best-effort: treat as raw base64
        try:
            return base64.b64decode(s)
        except Exception as e:
            raise ValueError(f"unrecognized image string (not url/data-uri/base64): {e}")


    def _remove_background_baseline_local(self, image_bytes: bytes) -> str:
        """Return base64 PNG bytes with transparent background."""
        # Prefer OpenCV GrabCut when available (better results for many photos).
        try:
            import numpy as np  # type: ignore
            import cv2  # type: ignore

            arr = np.frombuffer(image_bytes, dtype=np.uint8)
            bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if bgr is None:
                raise ValueError("OpenCV failed to decode image")

            h, w = bgr.shape[:2]
            # GrabCut rectangle: leave a small margin around edges.
            margin_x = max(5, int(w * 0.05))
            margin_y = max(5, int(h * 0.05))
            rect = (margin_x, margin_y, max(1, w - 2 * margin_x), max(1, h - 2 * margin_y))

            mask = np.zeros((h, w), np.uint8)
            bgd_model = np.zeros((1, 65), np.float64)
            fgd_model = np.zeros((1, 65), np.float64)

            cv2.grabCut(bgr, mask, rect, bgd_model, fgd_model, 5, cv2.GC_INIT_WITH_RECT)
            fg_mask = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")

            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            rgba = np.dstack([rgb, fg_mask])
            out = Image.fromarray(rgba, mode="RGBA")

            buf = BytesIO()
            out.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")

        except Exception:
            # Fallback: if OpenCV isn't available (or can't decode), return an RGBA PNG.
            # This keeps the pipeline working even when the environment lacks cv2.
            img = Image.open(BytesIO(image_bytes)).convert("RGBA")
            buf = BytesIO()
            img.save(buf, format="PNG")
            return base64.b64encode(buf.getvalue()).decode("utf-8")


    def _decode_bgr(self, image_bytes: bytes):
        import numpy as np  # type: ignore
        import cv2  # type: ignore

        arr = np.frombuffer(image_bytes, dtype=np.uint8)
        bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if bgr is None:
            raise ValueError("OpenCV failed to decode image")
        return bgr


    def _encode_png_b64(self, pil_img: Image.Image) -> str:
        buf = BytesIO()
        pil_img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")


    def _compute_grabcut_foreground_mask(self, bgr, *, iters: int = 5):
        import numpy as np  # type: ignore
        import cv2  # type: ignore

        h, w = bgr.shape[:2]
        margin_x = max(5, int(w * 0.05))
        margin_y = max(5, int(h * 0.05))
        rect = (margin_x, margin_y, max(1, w - 2 * margin_x), max(1, h - 2 * margin_y))

        mask = np.zeros((h, w), np.uint8)
        bgd_model = np.zeros((1, 65), np.float64)
        fgd_model = np.zeros((1, 65), np.float64)
        cv2.grabCut(bgr, mask, rect, bgd_model, fgd_model, int(iters), cv2.GC_INIT_WITH_RECT)

        fg = np.where((mask == cv2.GC_FGD) | (mask == cv2.GC_PR_FGD), 255, 0).astype("uint8")
        # Feather edges a bit
        fg = cv2.GaussianBlur(fg, (0, 0), sigmaX=2.0)
        fg = np.clip(fg, 0, 255).astype("uint8")
        return fg


    def _blur_background_local(self, image_bytes: bytes, **kwargs) -> str:
        try:
            import numpy as np  # type: ignore
            import cv2  # type: ignore

            bgr = self._decode_bgr(image_bytes)
            h, w = bgr.shape[:2]

            iters = int(kwargs.get("grabcut_iters", 5))
            iters = max(1, min(10, iters))
            fg_mask = self._compute_grabcut_foreground_mask(bgr, iters=iters)

            # Background blur kernel size based on image size.
            blur_sigma = float(kwargs.get("blur_sigma", 12.0))
            blur_sigma = max(1.0, min(40.0, blur_sigma))
            blurred = cv2.GaussianBlur(bgr, (0, 0), sigmaX=blur_sigma)

            alpha = (fg_mask.astype(np.float32) / 255.0)[:, :, None]
            comp = (bgr.astype(np.float32) * alpha) + (blurred.astype(np.float32) * (1.0 - alpha))
            comp = np.clip(comp, 0, 255).astype("uint8")
            rgb = cv2.cvtColor(comp, cv2.COLOR_BGR2RGB)
            out = Image.fromarray(rgb, mode="RGB")
            return self._encode_png_b64(out)
        except Exception:
            # Fallback: pillow-only blur (whole image). Still returns something useful.
            img = Image.open(BytesIO(image_bytes)).convert("RGB")
            radius = int(kwargs.get("blur_radius", 10))
            radius = max(1, min(30, radius))
            try:
                from PIL import ImageFilter

                img = img.filter(ImageFilter.GaussianBlur(radius=radius))
            except Exception:
                pass
            return self._encode_png_b64(img)


    def _blur_faces_local(self, image_bytes: bytes, **kwargs) -> tuple[str, int]:
        import numpy as np  # type: ignore
        import cv2  # type: ignore

        bgr = self._decode_bgr(image_bytes)
        gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

        scale_factor = float(kwargs.get("scale_factor", 1.1))
        min_neighbors = int(kwargs.get("min_neighbors", 5))
        min_size = int(kwargs.get("min_size", 30))
        scale_factor = max(1.05, min(1.4, scale_factor))
        min_neighbors = max(3, min(10, min_neighbors))
        min_size = max(20, min(120, min_size))

        cascade_path = os.path.join(cv2.data.haarcascades, "haarcascade_frontalface_default.xml")
        face_cascade = cv2.CascadeClassifier(cascade_path)
        faces = face_cascade.detectMultiScale(
            gray,
            scaleFactor=scale_factor,
            minNeighbors=min_neighbors,
            minSize=(min_size, min_size),
        )

        # Blur each detected face region
        k = int(kwargs.get("blur_kernel", 35))
        if k % 2 == 0:
            k += 1
        k = max(9, min(99, k))

        for (x, y, w, h) in faces:
            roi = bgr[y : y + h, x : x + w]
            if roi.size == 0:
                continue
            roi_blur = cv2.GaussianBlur(roi, (k, k), sigmaX=0)
            bgr[y : y + h, x : x + w] = roi_blur

        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        out = Image.fromarray(rgb, mode="RGB")
        return self._encode_png_b64(out), int(len(faces))


    def _apply_filter_local(self, image_bytes: bytes, *, filter_name: str, **kwargs) -> str:
        import numpy as np  # type: ignore
        import cv2  # type: ignore

        name = (filter_name or "").strip().lower()
        if not name:
            raise ValueError("missing filter_name")

        bgr = self._decode_bgr(image_bytes)

        if name in {"edge", "edges", "edge-detect", "edge_detect"}:
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            t1 = int(kwargs.get("canny_t1", 80))
            t2 = int(kwargs.get("canny_t2", 160))
            edges = cv2.Canny(gray, t1, t2)
            edges_bgr = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
            out_bgr = cv2.addWeighted(bgr, 0.65, edges_bgr, 0.85, 0)

        elif name in {"sharpen", "sharp"}:
            kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]], dtype=np.float32)
            out_bgr = cv2.filter2D(bgr, -1, kernel)

        elif name in {"denoise", "denoising"}:
            h = int(kwargs.get("h", 10))
            h = max(3, min(25, h))
            out_bgr = cv2.fastNlMeansDenoisingColored(bgr, None, h, h, 7, 21)

        elif name in {"cartoon", "toon"}:
            # Smooth colors
            color = cv2.bilateralFilter(bgr, d=9, sigmaColor=75, sigmaSpace=75)
            gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
            gray = cv2.medianBlur(gray, 7)
            edges = cv2.adaptiveThreshold(
                gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, 2
            )
            edges_bgr = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
            out_bgr = cv2.bitwise_and(color, edges_bgr)

        elif name in {"posterize", "poster"}:
            levels = int(kwargs.get("levels", 6))
            levels = max(2, min(12, levels))
            step = max(1, 256 // levels)
            out_bgr = (bgr // step) * step

        else:
            raise ValueError("unsupported filter (supported: edge, sharpen, denoise, cartoon, posterize)")

        rgb = cv2.cvtColor(out_bgr, cv2.COLOR_BGR2RGB)
        out = Image.fromarray(rgb, mode="RGB")
        return self._encode_png_b64(out)


    def _parse_aspect(self, aspect: Optional[str]) -> Optional[tuple[int, int]]:
        raw = (aspect or "").strip().lower()
        if not raw:
            return None
        if raw in {"square", "1:1", "1x1"}:
            return (1, 1)
        if raw in {"16:9", "16x9"}:
            return (16, 9)
        if raw in {"9:16", "9x16"}:
            return (9, 16)
        if raw in {"4:5", "4x5"}:
            return (4, 5)
        if raw in {"3:2", "3x2"}:
            return (3, 2)
        m = re.search(r"(\d+)\s*[:x]\s*(\d+)", raw)
        if not m:
            return None
        a = int(m.group(1))
        b = int(m.group(2))
        if a <= 0 or b <= 0:
            return None
        return (a, b)


    def _transform_image_local(self, image_bytes: bytes, **kwargs) -> tuple[str, Dict[str, Any]]:
        img = Image.open(BytesIO(image_bytes)).convert("RGB")
        src_w, src_h = img.size

        mode = (kwargs.get("mode") or "crop").strip().lower()
        if mode not in {"crop", "pad"}:
            mode = "crop"

        width = kwargs.get("width")
        height = kwargs.get("height")
        try:
            width = int(width) if width is not None else None
        except Exception:
            width = None
        try:
            height = int(height) if height is not None else None
        except Exception:
            height = None

        aspect = self._parse_aspect(kwargs.get("aspect"))
        target_aspect = (aspect[0] / aspect[1]) if aspect else None

        # Default: preserve current size unless a target is given.
        dst_w = width if width and width > 0 else src_w
        dst_h = height if height and height > 0 else src_h

        work = img
        if target_aspect:
            cur_aspect = src_w / max(1, src_h)
            if mode == "crop":
                # Center crop to aspect
                if cur_aspect > target_aspect:
                    new_w = int(src_h * target_aspect)
                    x0 = (src_w - new_w) // 2
                    work = work.crop((x0, 0, x0 + new_w, src_h))
                else:
                    new_h = int(src_w / target_aspect)
                    y0 = (src_h - new_h) // 2
                    work = work.crop((0, y0, src_w, y0 + new_h))
            else:
                # Pad to aspect
                if cur_aspect > target_aspect:
                    new_h = int(src_w / target_aspect)
                    pad = (new_h - src_h)
                    top = pad // 2
                    bottom = pad - top
                    canvas = Image.new("RGB", (src_w, new_h), color=(20, 20, 20))
                    canvas.paste(work, (0, top))
                    work = canvas
                else:
                    new_w = int(src_h * target_aspect)
                    pad = (new_w - src_w)
                    left = pad // 2
                    right = pad - left
                    canvas = Image.new("RGB", (new_w, src_h), color=(20, 20, 20))
                    canvas.paste(work, (left, 0))
                    work = canvas

        # Resize if a target size was provided.
        if (width and width > 0) or (height and height > 0):
            work = work.resize((dst_w, dst_h), resample=Image.Resampling.LANCZOS)

        info = {
            "mode": mode,
            "src_size": f"{src_w}x{src_h}",
            "dst_size": f"{work.size[0]}x{work.size[1]}",
            "aspect": (f"{aspect[0]}:{aspect[1]}" if aspect else None),
        }
        return self._encode_png_b64(work), info
    def generate_video_open_source(
        self,
        prompt: str,
        duration: int = 10,
        resolution: str = "1920x1080",
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate a video using an open-source backend (cloud-hosted).

        Default behavior: local-library baseline (CPU-safe) when OSS_BASELINE_MODE != 'remote'.
        Optional remote behavior: set OSS_BASELINE_MODE=remote and configure OSS_VIDEO_BACKEND_URL.

        Default contract (kind=generic):
        POST {OSS_VIDEO_BACKEND_URL}/generate
        Payload: {prompt, n_seconds, width, height}
        Response supports either:
        - {"video_url": "https://.../video.mp4"}
        - {"video_b64": "<base64 mp4>"} (or b64_mp4/content_b64)
        """
        mode = (os.getenv("OSS_BASELINE_MODE") or "local").strip().lower()
        backend_url = (os.getenv("OSS_VIDEO_BACKEND_URL") or "").strip().rstrip("/")

        # Prefer an internal Azure worker (e.g., AKS GPU service) when configured.
        worker_url = (os.getenv("OSS_AZURE_WORKER_URL") or os.getenv("OSS_AKS_WORKER_URL") or "").strip().rstrip("/")
        if mode in {"aks", "azure-worker"}:
            return self._generate_video_azure_worker(
                prompt,
                duration=duration,
                resolution=resolution,
                worker_url=worker_url,
                **kwargs,
            )

        # Default behavior: local-library baseline (works on App Service CPU, no external services).
        if mode != "remote" or not backend_url:
            return self._generate_video_baseline_local(prompt, duration=duration, resolution=resolution, **kwargs)

        kind = (os.getenv("OSS_VIDEO_BACKEND_KIND") or "generic").strip().lower()
        timeout = float(os.getenv("OSS_VIDEO_BACKEND_TIMEOUT_SECONDS", "300"))
        bearer = (os.getenv("OSS_VIDEO_BACKEND_AUTH_BEARER") or "").strip()

        if kind != "generic":
            return {
                "model": f"oss:video:{kind}",
                "status": "error",
                "status_code": 400,
                "error": f"Unsupported OSS_VIDEO_BACKEND_KIND '{kind}' (supported: generic)",
                "request": {"url": backend_url, "duration": duration, "resolution": resolution},
            }

        width, height = self._parse_resolution(resolution)
        url = f"{backend_url}/generate"
        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        payload: Dict[str, Any] = {
            "prompt": prompt,
            "n_seconds": int(duration),
            "width": int(width),
            "height": int(height),
        }
        payload.update({k: v for k, v in kwargs.items() if v is not None})

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as e:
            return {
                "model": "oss:video:generic",
                "status": "error",
                "status_code": 0,
                "error": str(e),
                "request": {"url": url, "duration": duration, "resolution": resolution},
            }

        if resp.status_code != 200:
            return {
                "model": "oss:video:generic",
                "status": "error",
                "status_code": resp.status_code,
                "error": resp.text,
                "request": {"url": url, "duration": duration, "resolution": resolution},
            }

        try:
            obj = resp.json() or {}
        except Exception:
            return {
                "model": "oss:video:generic",
                "status": "error",
                "status_code": 502,
                "error": "OSS video backend returned non-JSON response",
                "request": {"url": url, "duration": duration, "resolution": resolution},
            }

        if isinstance(obj, dict) and obj.get("video_url"):
            return {
                "model": "oss:video:generic",
                "status": "success",
                "video_url": str(obj.get("video_url")),
                "request": {"url": url, "duration": duration, "resolution": resolution},
            }

        b64 = None
        for k in ("video_b64", "b64_mp4", "content_b64", "b64"):
            if isinstance(obj, dict) and obj.get(k):
                b64 = obj.get(k)
                break
        if not b64:
            return {
                "model": "oss:video:generic",
                "status": "error",
                "status_code": 502,
                "error": "OSS video backend returned no video_url or base64 payload",
                "raw": obj,
                "request": {"url": url, "duration": duration, "resolution": resolution},
            }

        try:
            content = base64.b64decode(str(b64), validate=False)
        except Exception as e:
            return {
                "model": "oss:video:generic",
                "status": "error",
                "status_code": 502,
                "error": f"Failed to decode OSS video base64: {e}",
                "request": {"url": url, "duration": duration, "resolution": resolution},
            }

        if not content:
            return {
                "model": "oss:video:generic",
                "status": "error",
                "status_code": 502,
                "error": "OSS video backend returned empty content",
                "request": {"url": url, "duration": duration, "resolution": resolution},
            }

        return {
            "model": "oss:video:generic",
            "status": "success",
            "content_bytes": content,
            "request": {"url": url, "duration": duration, "resolution": resolution},
        }

    def _generate_image_baseline_local(self, prompt: str, *, size: str = "1024x1024") -> Dict[str, Any]:
        resolved_size = self._normalize_image_size(size)
        width, height = self._parse_image_size(resolved_size)
        width = int(min(max(256, width), 1024))
        height = int(min(max(256, height), 1024))

        prompt_text = (prompt or "").strip()
        if len(prompt_text) > 280:
            prompt_text = prompt_text[:280] + "…"
        prompt_l = prompt_text.lower()

        wants_transparent = any(k in prompt_l for k in ["transparent background", "remove background", "cut out", "cutout"]) 
        is_thumbnailish = any(k in prompt_l for k in ["thumbnail", "youtube thumbnail", "poster", "banner", "cover", "logo"]) 

        # Deterministic palette per prompt.
        seed = int(hashlib.sha256(prompt_text.encode("utf-8")).hexdigest()[:8], 16) if prompt_text else 0
        def _pick(a: int, b: int) -> int:
            return a + (seed % max(1, (b - a)))

        bg1 = (_pick(20, 60), _pick(20, 60), _pick(20, 60), 255)
        bg2 = (_pick(80, 150), _pick(50, 130), _pick(80, 160), 255)

        bg1, bg2, bg_decorative = self._infer_background_palette(prompt_l, seed, bg1, bg2)

        # Use RGBA so we can do alpha and transparent backgrounds.
        base_alpha = 0 if wants_transparent else 255
        img = Image.new("RGBA", (width, height), color=(28, 28, 28, base_alpha))
        draw = ImageDraw.Draw(img, "RGBA")

        title_font = self._load_font(max(18, min(52, width // 18)), bold=True)
        small_font = self._load_font(14, bold=False)

        if not wants_transparent:
            # Simple vertical gradient background
            for y in range(height):
                t = y / max(1, height - 1)
                r = int(bg1[0] * (1 - t) + bg2[0] * t)
                g = int(bg1[1] * (1 - t) + bg2[1] * t)
                b = int(bg1[2] * (1 - t) + bg2[2] * t)
                draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

            if bg_decorative:
                # Decorative shapes
                for i in range(7):
                    rr = 30 + ((seed >> (i * 3)) % 90)
                    x = int((seed >> (i * 5)) % max(1, width))
                    y = int((seed >> (i * 7)) % max(1, height))
                    c = (255, 255, 255, 18)
                    draw.ellipse([(x - rr, y - rr), (x + rr, y + rr)], fill=c)

        # Header bar
        header_h = 42
        if not wants_transparent:
            draw.rectangle([(0, 0), (width, header_h)], fill=(0, 0, 0, 90))
        draw.text((12, 12), "OSS baseline (local libraries)", fill=(255, 255, 255, 220), font=small_font)

        # Extract a readable title from prompt
        title = self._extract_subject_title(prompt_text)
        if not title:
            title = "Local baseline"
        if len(title) > 40:
            title = title[:40] + "…"

        # Draw title (thumbnail/poster style)
        if is_thumbnailish and not wants_transparent:
            # Shadow then main title
            tx, ty = 18, header_h + 22
            draw.text((tx + 3, ty + 3), title.upper(), fill=(0, 0, 0, 120), font=title_font)
            draw.text((tx, ty), title.upper(), fill=(255, 255, 255, 235), font=title_font)

        # Subject icon rendering
        drew_subject = False
        cx, cy = width // 2, int(height * 0.58)
        r = int(min(width, height) * (0.22 if is_thumbnailish else 0.20))

        if not wants_transparent and ("table" in prompt_l or "on a table" in prompt_l):
            table_y = int(height * 0.76)
            draw.rectangle([(0, table_y), (width, height)], fill=(85, 65, 45, 190))
            # subtle table highlight
            draw.rectangle([(0, table_y), (width, table_y + 8)], fill=(110, 85, 60, 90))

        if not wants_transparent and ("plate" in prompt_l or "on a plate" in prompt_l):
            plate_w = int(r * 3.0)
            plate_h = int(r * 0.85)
            plate_y = cy + int(r * 0.92)
            draw.ellipse(
                [(cx - plate_w // 2, plate_y - plate_h // 2), (cx + plate_w // 2, plate_y + plate_h // 2)],
                fill=(255, 255, 255, 65),
                outline=(255, 255, 255, 95),
                width=2,
            )

        if "apple" in prompt_l:
            count = self._infer_count_for(prompt_l, "apple")
            variant = self._infer_apple_variant(prompt_l)
            if count <= 1:
                self._draw_apple(draw, cx, cy, r, variant=variant)
            else:
                rr = int(r * 0.85)
                spacing = int(rr * 1.55)
                start_x = cx - int(spacing * (count - 1) / 2)
                for i in range(count):
                    x = start_x + i * spacing
                    y = cy + int(((i % 2) - 0.5) * rr * 0.10)
                    self._draw_apple(draw, int(x), int(y), rr, variant=variant)
            drew_subject = True
        elif "banana" in prompt_l:
            count = self._infer_count_for(prompt_l, "banana")
            if count <= 1:
                self._draw_banana(draw, cx, cy, r)
            else:
                rr = int(r * 0.85)
                spacing = int(rr * 1.55)
                start_x = cx - int(spacing * (count - 1) / 2)
                for i in range(count):
                    x = start_x + i * spacing
                    y = cy + int(((i % 2) - 0.5) * rr * 0.10)
                    self._draw_banana(draw, int(x), int(y), rr)
            drew_subject = True
        elif "orange" in prompt_l or "citrus" in prompt_l:
            count = self._infer_count_for(prompt_l, "orange")
            if count <= 1:
                self._draw_orange(draw, cx, cy, r)
            else:
                rr = int(r * 0.85)
                spacing = int(rr * 1.55)
                start_x = cx - int(spacing * (count - 1) / 2)
                for i in range(count):
                    x = start_x + i * spacing
                    y = cy + int(((i % 2) - 0.5) * rr * 0.10)
                    self._draw_orange(draw, int(x), int(y), rr)
            drew_subject = True
        elif "cat" in prompt_l:
            count = self._infer_count_for(prompt_l, "cat")
            if count <= 1:
                self._draw_cat(draw, cx, cy, r)
            else:
                rr = int(r * 0.85)
                spacing = int(rr * 1.55)
                start_x = cx - int(spacing * (count - 1) / 2)
                for i in range(count):
                    x = start_x + i * spacing
                    y = cy + int(((i % 2) - 0.5) * rr * 0.10)
                    self._draw_cat(draw, int(x), int(y), rr)
            drew_subject = True
        elif "dog" in prompt_l:
            count = self._infer_count_for(prompt_l, "dog")
            if count <= 1:
                self._draw_dog(draw, cx, cy, r)
            else:
                rr = int(r * 0.85)
                spacing = int(rr * 1.55)
                start_x = cx - int(spacing * (count - 1) / 2)
                for i in range(count):
                    x = start_x + i * spacing
                    y = cy + int(((i % 2) - 0.5) * rr * 0.10)
                    self._draw_dog(draw, int(x), int(y), rr)
            drew_subject = True
        elif "logo" in prompt_l:
            self._draw_logo_mark(draw, cx, cy, r)
            drew_subject = True

        if not drew_subject and not wants_transparent:
            # Generic subject: a centered "card" with an icon dot.
            card_w, card_h = int(r * 2.2), int(r * 1.6)
            draw.rounded_rectangle(
                [(cx - card_w // 2, cy - card_h // 2), (cx + card_w // 2, cy + card_h // 2)],
                radius=24,
                fill=(0, 0, 0, 70),
                outline=(255, 255, 255, 55),
                width=2,
            )
            draw.ellipse([(cx - 18, cy - 18), (cx + 18, cy + 18)], fill=(255, 255, 255, 190))

        # Footer prompt label for transparency.
        footer_h = 44
        body = f"Prompt: {prompt_text}" if prompt_text else "Prompt: (empty)"
        if not wants_transparent:
            draw.rectangle([(0, height - footer_h), (width, height)], fill=(0, 0, 0, 110))
        draw.text((10, height - footer_h + 14), body, fill=(255, 255, 255, 220), font=small_font)

        buf = tempfile.SpooledTemporaryFile(max_size=2_000_000)
        img.save(buf, format="PNG")
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode("utf-8")
        return {
            "model": "oss:local-image",
            "status": "success",
            "data": [{"b64_json": b64}],
            "request": {"url": None, "size": resolved_size},
        }

    def _load_font(self, size: int, *, bold: bool = False) -> ImageFont.ImageFont:
        try:
            # Common Linux locations (App Service)
            candidates = [
                "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/dejavu/DejaVuSans.ttf",
            ]
            for p in candidates:
                if os.path.exists(p):
                    return ImageFont.truetype(p, size=size)
        except Exception:
            pass
        return ImageFont.load_default()

    def _extract_subject_title(self, prompt_text: str) -> str:
        text = (prompt_text or "").strip()
        if not text:
            return ""
        # Strip common instruction prefixes.
        text = re.sub(r"^(please\s+)?(create|make|generate|draw)\s+(an?\s+)?(image|picture|thumbnail|poster|banner|logo|cover)\s+(of\s+)?", "", text, flags=re.IGNORECASE)
        text = re.sub(r"^eye-catching\s+youtube\s+thumbnail:\s*", "", text, flags=re.IGNORECASE)
        # Take a short leading phrase.
        parts = re.split(r"[\.|\n|,|;|-]{1,}", text)


        def _generate_video_azure_worker(
            self,
            prompt: str,
            *,
            duration: int,
            resolution: str,
            worker_url: str,
            **kwargs,
        ) -> Dict[str, Any]:
            """Generate an OSS baseline video via an internal Azure worker (e.g., AKS GPU).

            Expected worker API:
              POST {worker_url}/generate-video
            Response supports:
              - {"video_b64": "<base64 mp4>"}
            """

            if not worker_url:
                base = self._generate_video_baseline_local(prompt, duration=duration, resolution=resolution, **kwargs)
                base.setdefault("diagnostics", {})
                base["diagnostics"]["azure_worker"] = {
                    "enabled": True,
                    "status": "skipped",
                    "reason": "OSS_AZURE_WORKER_URL is not set",
                }
                return base

            timeout = float(os.getenv("OSS_WORKER_TIMEOUT_SECONDS", "300"))
            bearer = (os.getenv("OSS_WORKER_AUTH_BEARER") or "").strip()

            width, height = self._parse_resolution(resolution)
            url = f"{worker_url}/generate-video"

            payload: Dict[str, Any] = {
                "prompt": prompt,
                "n_seconds": int(duration),
                "width": int(width),
                "height": int(height),
            }
            if "fps" in kwargs and kwargs.get("fps") is not None:
                payload["fps"] = int(kwargs.get("fps"))
            for key in ["seed", "num_inference_steps", "guidance_scale", "negative_prompt"]:
                if key in kwargs and kwargs.get(key) is not None:
                    payload[key] = kwargs.get(key)

            headers: Dict[str, str] = {"Content-Type": "application/json"}
            if bearer:
                headers["Authorization"] = f"Bearer {bearer}"

            try:
                resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
            except Exception as e:
                base = self._generate_video_baseline_local(prompt, duration=duration, resolution=resolution, **kwargs)
                base.setdefault("diagnostics", {})
                base["diagnostics"]["azure_worker"] = {
                    "enabled": True,
                    "status": "error",
                    "reason": f"Worker request failed: {e}",
                    "url": url,
                }
                return base

            if resp.status_code != 200:
                base = self._generate_video_baseline_local(prompt, duration=duration, resolution=resolution, **kwargs)
                base.setdefault("diagnostics", {})
                base["diagnostics"]["azure_worker"] = {
                    "enabled": True,
                    "status": "error",
                    "reason": f"Worker returned HTTP {resp.status_code}",
                    "url": url,
                    "body": resp.text[:2000],
                }
                return base

            try:
                obj = resp.json() or {}
            except Exception as e:
                base = self._generate_video_baseline_local(prompt, duration=duration, resolution=resolution, **kwargs)
                base.setdefault("diagnostics", {})
                base["diagnostics"]["azure_worker"] = {
                    "enabled": True,
                    "status": "error",
                    "reason": f"Worker returned non-JSON response: {e}",
                    "url": url,
                }
                return base

            b64 = (
                obj.get("video_b64")
                or obj.get("b64_mp4")
                or obj.get("content_b64")
                or obj.get("video")
            )
            if not b64:
                base = self._generate_video_baseline_local(prompt, duration=duration, resolution=resolution, **kwargs)
                base.setdefault("diagnostics", {})
                base["diagnostics"]["azure_worker"] = {
                    "enabled": True,
                    "status": "error",
                    "reason": "Worker response missing video base64 payload",
                    "url": url,
                }
                return base

            try:
                content_bytes = base64.b64decode(str(b64))
            except Exception as e:
                base = self._generate_video_baseline_local(prompt, duration=duration, resolution=resolution, **kwargs)
                base.setdefault("diagnostics", {})
                base["diagnostics"]["azure_worker"] = {
                    "enabled": True,
                    "status": "error",
                    "reason": f"Invalid base64 from worker: {e}",
                    "url": url,
                }
                return base

            return {
                "model": obj.get("model") or "oss:aks:video",
                "status": "success",
                "content_bytes": content_bytes,
                "request": {"url": url, "duration": int(duration), "resolution": resolution},
                "diagnostics": {
                    "azure_worker": {"enabled": True, "url": url},
                    **(obj.get("diagnostics") or {}),
                },
            }
        head = (parts[0] or "").strip()
        return head

    def _infer_apple_variant(self, prompt_l: str) -> str:
        t = (prompt_l or "").lower()

        # Prefer explicit apple color mentions and common varieties.
        if "green apple" in t or "granny smith" in t or re.search(r"\bgreen\b", t):
            return "green"
        if "yellow apple" in t or "golden delicious" in t or re.search(r"\byellow\b|\bgolden\b", t):
            return "yellow"
        if "red apple" in t or re.search(r"\bred\b|\bcrimson\b|\bscarlet\b", t):
            return "red"

        return "red"

    def _infer_count_for(self, prompt_l: str, noun: str) -> int:
        t = (prompt_l or "").lower()
        n = (noun or "").strip().lower()
        if not n:
            return 1

        m = re.search(rf"\b(\d+)\s+{re.escape(n)}s?\b", t)
        if m:
            try:
                return max(1, min(4, int(m.group(1))))
            except Exception:
                return 1

        word_map = {"one": 1, "two": 2, "three": 3, "four": 4}
        for w, v in word_map.items():
            if re.search(rf"\b{w}\s+{re.escape(n)}s?\b", t):
                return v

        return 1

    def _infer_background_palette(
        self,
        prompt_l: str,
        seed: int,
        bg1: Tuple[int, int, int, int],
        bg2: Tuple[int, int, int, int],
    ) -> Tuple[Tuple[int, int, int, int], Tuple[int, int, int, int], bool]:
        t = (prompt_l or "").lower()
        decorative = True

        if any(k in t for k in ["plain background", "solid background", "flat background", "simple background"]):
            decorative = False

        def _as_rgba(rgb: Tuple[int, int, int]) -> Tuple[int, int, int, int]:
            return (int(rgb[0]), int(rgb[1]), int(rgb[2]), 255)

        # Basic color intent for background.
        # Prefer explicit background phrases.
        if re.search(r"\bwhite\b.*\bbackground\b|\bbackground\b.*\bwhite\b", t):
            return _as_rgba((245, 245, 245)), _as_rgba((215, 215, 215)), False
        if re.search(r"\bblack\b.*\bbackground\b|\bbackground\b.*\bblack\b", t):
            return _as_rgba((20, 20, 22)), _as_rgba((55, 55, 60)), decorative
        if re.search(r"\bblue\b.*\bbackground\b|\bbackground\b.*\bblue\b", t):
            return _as_rgba((20, 40, 90)), _as_rgba((60, 110, 190)), decorative
        if re.search(r"\bgreen\b.*\bbackground\b|\bbackground\b.*\bgreen\b", t):
            return _as_rgba((18, 60, 38)), _as_rgba((70, 150, 95)), decorative
        if re.search(r"\bred\b.*\bbackground\b|\bbackground\b.*\bred\b", t):
            return _as_rgba((70, 18, 25)), _as_rgba((170, 60, 70)), decorative
        if re.search(r"\bgray\b.*\bbackground\b|\bgrey\b.*\bbackground\b|\bbackground\b.*\bgray\b|\bbackground\b.*\bgrey\b", t):
            return _as_rgba((55, 55, 60)), _as_rgba((130, 130, 140)), decorative

        # Small nudge: if prompt asks for "minimal"/"clean", reduce clutter.
        if any(k in t for k in ["minimal", "clean", "simple"]):
            decorative = False

        return bg1, bg2, decorative

    def _get_diffusers_pipe(self, model_id: str, device: str, *, torch_dtype):
        with self._diffusers_lock:
            if (
                self._diffusers_pipe is not None
                and self._diffusers_pipe_id == model_id
                and self._diffusers_pipe_device == device
            ):
                return self._diffusers_pipe

            # Import lazily so default installs don't require ML deps.
            from diffusers import DiffusionPipeline

            pipe = DiffusionPipeline.from_pretrained(
                model_id,
                torch_dtype=torch_dtype,
                safety_checker=None,
                requires_safety_checker=False,
            )
            pipe = pipe.to(device)

            # Reduce memory spikes and avoid chatty console output.
            try:
                pipe.set_progress_bar_config(disable=True)
            except Exception:
                pass

            # Helpful on both CPU and GPU for memory; safe no-op if unsupported.
            try:
                pipe.enable_attention_slicing()
            except Exception:
                pass

            self._diffusers_pipe = pipe
            self._diffusers_pipe_id = model_id
            self._diffusers_pipe_device = device
            return pipe

    def _generate_image_diffusers_local(self, prompt: str, *, size: str = "1024x1024", **kwargs) -> Dict[str, Any]:
        """Generate a more realistic local OSS image using Diffusers.

        This is intended for local dev / GPU machines. It can run on CPU, but will be slow.
        Enable via:
          - OSS_BASELINE_MODE=diffusers
          - OSS_DIFFUSERS_MODEL_ID=<huggingface model id or local path>
        Optional knobs:
          - OSS_DIFFUSERS_DEVICE=cpu|cuda
                    - OSS_DIFFUSERS_NUM_INFERENCE_STEPS (default 25)
                    - OSS_DIFFUSERS_GUIDANCE_SCALE (default 7.5)
          - OSS_DIFFUSERS_SEED (optional)
          - OSS_DIFFUSERS_NEGATIVE_PROMPT (optional)
        """

        model_id = (os.getenv("OSS_DIFFUSERS_MODEL_ID") or "").strip()
        if not model_id:
            # Keep behavior non-breaking: fall back to lightweight baseline.
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["diffusers"] = {
                "enabled": True,
                "status": "skipped",
                "reason": "OSS_DIFFUSERS_MODEL_ID is not set",
            }
            return base

        try:
            import torch
        except Exception as e:
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["diffusers"] = {
                "enabled": True,
                "status": "unavailable",
                "reason": f"torch import failed: {e}",
            }
            return base

        # Resolve size; keep within reasonable bounds for local inference.
        # CPU inference gets very slow above ~512-768px, so cap more aggressively on CPU.
        resolved_size = self._normalize_image_size(size)
        width, height = self._parse_image_size(resolved_size)
        width = int(min(max(256, width), 1024))
        height = int(min(max(256, height), 1024))

        # Many latent diffusion pipelines require sizes that are multiples of 8.
        width = max(256, width - (width % 8))
        height = max(256, height - (height % 8))

        device = (os.getenv("OSS_DIFFUSERS_DEVICE") or "").strip().lower()
        if not device:
            device = "cuda" if torch.cuda.is_available() else "cpu"

        torch_dtype = torch.float16 if device.startswith("cuda") else torch.float32

        if device == "cpu":
            max_dim = int(os.getenv("OSS_DIFFUSERS_CPU_MAX_DIM", "512"))
            width = int(min(width, max_dim))
            height = int(min(height, max_dim))

        # Inference knobs (allow per-call overrides via kwargs too).
        default_steps = "12" if device == "cpu" else "25"
        num_steps = int(kwargs.get("num_inference_steps", os.getenv("OSS_DIFFUSERS_NUM_INFERENCE_STEPS", default_steps)))
        default_guidance = "5.0" if device == "cpu" else "7.5"
        guidance = float(kwargs.get("guidance_scale", os.getenv("OSS_DIFFUSERS_GUIDANCE_SCALE", default_guidance)))
        negative = (kwargs.get("negative_prompt") or os.getenv("OSS_DIFFUSERS_NEGATIVE_PROMPT") or "").strip()

        seed_env = (os.getenv("OSS_DIFFUSERS_SEED") or "").strip()
        seed = None
        if seed_env:
            try:
                seed = int(seed_env)
            except Exception:
                seed = None
        if "seed" in kwargs and kwargs.get("seed") is not None:
            try:
                seed = int(kwargs.get("seed"))
            except Exception:
                pass

        try:
            pipe = self._get_diffusers_pipe(model_id, device, torch_dtype=torch_dtype)
        except Exception as e:
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["diffusers"] = {
                "enabled": True,
                "status": "error",
                "reason": f"Failed to load diffusers pipeline: {e}",
                "model_id": model_id,
                "device": device,
            }
            return base

        gen = None
        try:
            if seed is not None:
                gen = torch.Generator(device=device).manual_seed(seed)
        except Exception:
            gen = None

        try:
            with torch.inference_mode():
                result = pipe(
                    prompt=prompt,
                    negative_prompt=negative or None,
                    num_inference_steps=max(1, min(50, num_steps)),
                    guidance_scale=max(0.0, min(20.0, guidance)),
                    width=width,
                    height=height,
                    generator=gen,
                )
                img = result.images[0]
        except Exception as e:
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["diffusers"] = {
                "enabled": True,
                "status": "error",
                "reason": f"Diffusers inference failed: {e}",
                "model_id": model_id,
                "device": device,
            }
            return base

        # Encode to base64 PNG for the existing UI path.
        buf = BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        payload = {
            "model": f"oss:diffusers:{model_id}",
            "status": "success",
            "data": [{"b64_json": b64}],
            "request": {"url": None, "size": resolved_size},
            "diagnostics": {
                "diffusers": {
                    "enabled": True,
                    "model_id": model_id,
                    "device": device,
                    "num_inference_steps": num_steps,
                    "guidance_scale": guidance,
                    "seed": seed,
                }
            },
        }
        return payload

    def _generate_image_azure_worker(
        self,
        prompt: str,
        *,
        size: str = "1024x1024",
        worker_url: str,
        **kwargs,
    ) -> Dict[str, Any]:
        """Generate a realistic OSS image via an internal Azure worker (e.g., AKS GPU).

        This keeps the implementation "OSS" (Diffusers/torch), but runs inference on Azure compute.
        Configure via:
          - OSS_AZURE_WORKER_URL or OSS_AKS_WORKER_URL (e.g., http://10.50.3.10)
          - Optional OSS_WORKER_AUTH_BEARER (shared secret)

        Expected worker API:
          POST {worker_url}/generate-image
        """

        if not worker_url:
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["azure_worker"] = {
                "enabled": True,
                "status": "skipped",
                "reason": "OSS_AZURE_WORKER_URL is not set",
            }
            return base

        timeout = float(os.getenv("OSS_WORKER_TIMEOUT_SECONDS", "300"))
        bearer = (os.getenv("OSS_WORKER_AUTH_BEARER") or "").strip()

        resolved_size = self._normalize_image_size(size)
        width, height = self._parse_image_size(resolved_size)

        payload: Dict[str, Any] = {
            "prompt": prompt,
            "width": int(width),
            "height": int(height),
        }
        # Optional knobs (keep names aligned with diffusers helper)
        for key in ["seed", "num_inference_steps", "guidance_scale", "negative_prompt"]:
            if key in kwargs and kwargs.get(key) is not None:
                payload[key] = kwargs.get(key)

        headers: Dict[str, str] = {"Content-Type": "application/json"}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"

        url = f"{worker_url}/generate-image"

        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except Exception as e:
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["azure_worker"] = {
                "enabled": True,
                "status": "error",
                "reason": f"Worker request failed: {e}",
                "url": url,
            }
            return base

        if resp.status_code != 200:
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["azure_worker"] = {
                "enabled": True,
                "status": "error",
                "reason": f"Worker returned HTTP {resp.status_code}",
                "url": url,
                "body": resp.text[:2000],
            }
            return base

        try:
            obj = resp.json() or {}
        except Exception as e:
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["azure_worker"] = {
                "enabled": True,
                "status": "error",
                "reason": f"Worker returned non-JSON response: {e}",
                "url": url,
            }
            return base

        # Pass through the worker payload if it already matches expected UI shape.
        # Ensure it contains 'data[0].b64_json'.
        try:
            _ = (obj.get("data") or [])[0].get("b64_json")
        except Exception:
            base = self._generate_image_baseline_local(prompt, size=size)
            base.setdefault("diagnostics", {})
            base["diagnostics"]["azure_worker"] = {
                "enabled": True,
                "status": "error",
                "reason": "Worker response missing data[0].b64_json",
                "url": url,
            }
            return base

        obj.setdefault("request", {})
        obj["request"].setdefault("size", resolved_size)
        obj.setdefault("diagnostics", {})
        obj["diagnostics"]["azure_worker"] = {
            "enabled": True,
            "url": url,
        }
        return obj

    def _draw_apple(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int, *, variant: str = "red") -> None:
        v = (variant or "red").strip().lower()
        if v == "green":
            body_fill = (80, 170, 80, 255)
            shade_fill = (40, 120, 60, 90)
        elif v == "yellow":
            body_fill = (235, 205, 70, 255)
            shade_fill = (190, 150, 40, 90)
        else:
            body_fill = (210, 35, 45, 255)
            shade_fill = (150, 20, 30, 90)

        shadow_w, shadow_h = int(r * 1.8), int(r * 0.45)
        draw.ellipse(
            [(cx - shadow_w // 2, cy + r - shadow_h // 2), (cx + shadow_w // 2, cy + r + shadow_h // 2)],
            fill=(0, 0, 0, 110),
        )
        body_bbox = [(cx - r, cy - r), (cx + r, cy + r)]
        draw.ellipse(body_bbox, fill=body_fill)
        shade_bbox = [(cx - int(r * 0.95), cy - int(r * 0.7)), (cx + int(r * 0.1), cy + int(r * 0.9))]
        draw.ellipse(shade_bbox, fill=shade_fill)
        hi_r = int(r * 0.35)
        draw.ellipse(
            [(cx + int(r * 0.25), cy - int(r * 0.55)), (cx + int(r * 0.25) + hi_r, cy - int(r * 0.55) + int(hi_r * 1.4))],
            fill=(255, 255, 255, 90),
        )
        stem_w, stem_h = max(6, int(r * 0.12)), max(18, int(r * 0.35))
        stem_x0 = cx - stem_w // 2
        stem_y0 = cy - r - int(stem_h * 0.15)
        draw.rounded_rectangle(
            [(stem_x0, stem_y0), (stem_x0 + stem_w, stem_y0 + stem_h)],
            radius=max(2, stem_w // 3),
            fill=(110, 75, 35, 255),
        )
        leaf_w, leaf_h = int(r * 0.75), int(r * 0.35)
        leaf_x = cx + int(r * 0.15)
        leaf_y = cy - r - int(stem_h * 0.1)
        leaf_bbox = [(leaf_x, leaf_y), (leaf_x + leaf_w, leaf_y + leaf_h)]
        draw.ellipse(leaf_bbox, fill=(40, 160, 80, 230))
        draw.line(
            [(leaf_x + int(leaf_w * 0.1), leaf_y + int(leaf_h * 0.65)), (leaf_x + int(leaf_w * 0.9), leaf_y + int(leaf_h * 0.35))],
            fill=(20, 120, 60, 200),
            width=max(2, leaf_h // 8),
        )

    def _draw_orange(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
        draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(255, 140, 30, 255))
        draw.ellipse([(cx - int(r * 0.8), cy - int(r * 0.8)), (cx + int(r * 0.8), cy + int(r * 0.8))], outline=(255, 255, 255, 45), width=3)
        draw.ellipse([(cx + int(r * 0.2), cy - int(r * 0.55)), (cx + int(r * 0.55), cy - int(r * 0.15))], fill=(255, 255, 255, 70))

    def _draw_banana(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
        # Crescent shape via two arcs
        w, h = int(r * 2.2), int(r * 1.3)
        box1 = [(cx - w // 2, cy - h // 2), (cx + w // 2, cy + h // 2)]
        box2 = [(cx - int(w * 0.45), cy - int(h * 0.25)), (cx + int(w * 0.45), cy + int(h * 0.85))]
        draw.pieslice(box1, start=210, end=330, fill=(255, 225, 60, 255))
        draw.pieslice(box2, start=210, end=330, fill=(28, 28, 28, 0))
        draw.arc(box1, start=210, end=330, fill=(255, 255, 255, 90), width=4)

    def _draw_cat(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
        face_r = int(r * 0.85)
        draw.ellipse([(cx - face_r, cy - face_r), (cx + face_r, cy + face_r)], fill=(240, 240, 240, 230))
        # Ears
        ear = int(face_r * 0.65)
        draw.polygon([(cx - int(face_r * 0.7), cy - int(face_r * 0.4)), (cx - int(face_r * 0.1), cy - int(face_r * 1.1)), (cx + int(face_r * 0.1), cy - int(face_r * 0.35))], fill=(240, 240, 240, 230))
        draw.polygon([(cx + int(face_r * 0.7), cy - int(face_r * 0.4)), (cx + int(face_r * 0.1), cy - int(face_r * 1.1)), (cx - int(face_r * 0.1), cy - int(face_r * 0.35))], fill=(240, 240, 240, 230))
        # Eyes
        eye_y = cy - int(face_r * 0.15)
        draw.ellipse([(cx - int(face_r * 0.45), eye_y - 10), (cx - int(face_r * 0.25), eye_y + 10)], fill=(30, 30, 30, 220))
        draw.ellipse([(cx + int(face_r * 0.25), eye_y - 10), (cx + int(face_r * 0.45), eye_y + 10)], fill=(30, 30, 30, 220))
        # Nose
        draw.polygon([(cx, cy + 6), (cx - 8, cy + 18), (cx + 8, cy + 18)], fill=(220, 120, 140, 230))

    def _draw_dog(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
        face_r = int(r * 0.85)
        draw.ellipse([(cx - face_r, cy - face_r), (cx + face_r, cy + face_r)], fill=(210, 180, 140, 230))
        # Ears
        ear_w, ear_h = int(face_r * 0.55), int(face_r * 0.75)
        draw.rounded_rectangle([(cx - face_r - ear_w // 3, cy - ear_h // 2), (cx - face_r + ear_w, cy + ear_h)], radius=18, fill=(170, 130, 90, 230))
        draw.rounded_rectangle([(cx + face_r - ear_w, cy - ear_h // 2), (cx + face_r + ear_w // 3, cy + ear_h)], radius=18, fill=(170, 130, 90, 230))
        # Snout
        sn_w, sn_h = int(face_r * 0.95), int(face_r * 0.7)
        draw.rounded_rectangle([(cx - sn_w // 2, cy + int(face_r * 0.15)), (cx + sn_w // 2, cy + int(face_r * 0.15) + sn_h)], radius=28, fill=(235, 220, 200, 230))
        # Nose
        draw.ellipse([(cx - 10, cy + int(face_r * 0.25)), (cx + 10, cy + int(face_r * 0.25) + 16)], fill=(35, 35, 35, 230))

    def _draw_logo_mark(self, draw: ImageDraw.ImageDraw, cx: int, cy: int, r: int) -> None:
        # Simple geometric logomark
        draw.rounded_rectangle([(cx - r, cy - r), (cx + r, cy + r)], radius=max(16, r // 4), fill=(0, 0, 0, 90), outline=(255, 255, 255, 120), width=3)
        draw.polygon([(cx, cy - int(r * 0.65)), (cx - int(r * 0.6), cy + int(r * 0.55)), (cx + int(r * 0.6), cy + int(r * 0.55))], fill=(255, 255, 255, 200))

    def _generate_video_baseline_local(
        self,
        prompt: str,
        *,
        duration: int = 10,
        resolution: str = "1920x1080",
        **kwargs,
    ) -> Dict[str, Any]:
        try:
            import numpy as np  # type: ignore
            import cv2  # type: ignore
        except Exception as e:
            return {
                "model": "oss:local-video",
                "status": "error",
                "status_code": 500,
                "error": f"OpenCV/numpy unavailable for local video baseline: {e}",
                "request": {"url": None, "duration": int(duration or 0), "resolution": resolution},
            }

        width, height = self._parse_resolution(resolution)
        width = int(min(max(256, width), 640))
        height = int(min(max(144, height), 360))
        fps = int(kwargs.get("fps", 2))
        fps = max(1, min(6, fps))
        seconds = int(max(1, min(int(duration or 1), 4)))
        frame_count = fps * seconds

        prompt_text = (prompt or "").strip()
        if len(prompt_text) > 140:
            prompt_text = prompt_text[:140] + "…"
        prompt_l = prompt_text.lower()

        title = self._extract_subject_title(prompt_text) or "OSS baseline"
        if len(title) > 28:
            title = title[:28] + "…"
        title_font = self._load_font(max(14, min(28, width // 18)), bold=True)
        small_font = self._load_font(12, bold=False)

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as tmp:
            tmp_path = tmp.name

        content = b""
        try:
            writer = None
            last_open_error: Optional[str] = None
            for codec in ("mp4v", "avc1", "H264"):
                fourcc = cv2.VideoWriter_fourcc(*codec)
                w = cv2.VideoWriter(tmp_path, fourcc, float(fps), (width, height))
                if w.isOpened():
                    writer = w
                    break
                last_open_error = f"OpenCV VideoWriter failed to open (codec={codec})"
                try:
                    w.release()
                except Exception:
                    pass

            if writer is None:
                raise RuntimeError(last_open_error or "OpenCV VideoWriter failed to open")

            bg1 = (35, 30, 55, 255)
            bg2 = (115, 90, 175, 255)
            bg1, bg2, bg_decorative = self._infer_background_palette(prompt_l, 0, bg1, bg2)

            for i in range(frame_count):
                img = Image.new("RGBA", (width, height), color=(18, 18, 22, 255))
                draw = ImageDraw.Draw(img, "RGBA")

                # Background
                for y in range(height):
                    t = y / max(1, height - 1)
                    r = int(bg1[0] * (1 - t) + bg2[0] * t)
                    g = int(bg1[1] * (1 - t) + bg2[1] * t)
                    b = int(bg1[2] * (1 - t) + bg2[2] * t)
                    draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

                if bg_decorative:
                    # A few subtle sparkles/dots
                    for j in range(4):
                        rr = 14 + (j * 6)
                        x = int((width * (0.12 + 0.22 * j) + (i * 13)) % max(1, width))
                        y = int((height * (0.18 + 0.12 * j) + (i * 9)) % max(1, height))
                        draw.ellipse([(x - rr, y - rr), (x + rr, y + rr)], fill=(255, 255, 255, 14))

                # Header
                draw.rectangle([(0, 0), (width, 36)], fill=(0, 0, 0, 100))
                draw.text((10, 10), "OSS baseline (local video)", fill=(255, 255, 255, 230), font=small_font)

                # Title
                draw.text((10, 48), title.upper(), fill=(255, 255, 255, 235), font=title_font)

                # Animate a simple subject
                cx = int(width * 0.5 + (width * 0.18) * np.sin(i * 0.35))
                cy = int(height * 0.62 + (height * 0.04) * np.cos(i * 0.28))
                r = int(min(width, height) * 0.20)

                if "table" in prompt_l or "on a table" in prompt_l:
                    table_y = int(height * 0.76)
                    draw.rectangle([(0, table_y), (width, height)], fill=(85, 65, 45, 190))
                    draw.rectangle([(0, table_y), (width, table_y + 6)], fill=(110, 85, 60, 80))

                if "plate" in prompt_l or "on a plate" in prompt_l:
                    plate_w = int(r * 3.0)
                    plate_h = int(r * 0.85)
                    plate_y = cy + int(r * 0.92)
                    draw.ellipse(
                        [(cx - plate_w // 2, plate_y - plate_h // 2), (cx + plate_w // 2, plate_y + plate_h // 2)],
                        fill=(255, 255, 255, 60),
                        outline=(255, 255, 255, 90),
                        width=2,
                    )

                if "apple" in prompt_l:
                    count = self._infer_count_for(prompt_l, "apple")
                    variant = self._infer_apple_variant(prompt_l)
                    if count <= 1:
                        self._draw_apple(draw, cx, cy, r, variant=variant)
                    else:
                        rr = int(r * 0.85)
                        spacing = int(rr * 1.55)
                        start_x = cx - int(spacing * (count - 1) / 2)
                        wobble = int(rr * 0.10 * np.sin(i * 0.22))
                        for k in range(count):
                            x = start_x + k * spacing
                            y = cy + wobble + int(((k % 2) - 0.5) * rr * 0.10)
                            self._draw_apple(draw, int(x), int(y), rr, variant=variant)
                elif "cat" in prompt_l:
                    self._draw_cat(draw, cx, cy, r)
                elif "dog" in prompt_l:
                    self._draw_dog(draw, cx, cy, r)
                else:
                    draw.ellipse([(cx - r, cy - r), (cx + r, cy + r)], fill=(255, 255, 255, 120))
                    draw.ellipse([(cx - int(r * 0.7), cy - int(r * 0.7)), (cx + int(r * 0.7), cy + int(r * 0.7))], fill=(0, 0, 0, 70))

                # Footer prompt
                footer_h = 34
                draw.rectangle([(0, height - footer_h), (width, height)], fill=(0, 0, 0, 110))
                draw.text((10, height - footer_h + 10), f"Prompt: {prompt_text if prompt_text else '(empty)'}", fill=(255, 255, 255, 220), font=small_font)

                frame = cv2.cvtColor(np.array(img.convert("RGB")), cv2.COLOR_RGB2BGR)
                writer.write(frame)

            try:
                writer.release()
            except Exception:
                pass

            with open(tmp_path, "rb") as f:
                content = f.read()
        except Exception as e:
            return {
                "model": "oss:local-video",
                "status": "error",
                "status_code": 502,
                "error": str(e),
                "request": {"url": None, "duration": seconds, "resolution": f"{width}x{height}"},
            }
        finally:
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except Exception:
                pass

        if not content:
            return {
                "model": "oss:local-video",
                "status": "error",
                "status_code": 502,
                "error": "Local video generator produced empty output",
                "request": {"url": None, "duration": seconds, "resolution": f"{width}x{height}"},
            }

        return {
            "model": "oss:local-video",
            "status": "success",
            "content_bytes": content,
            "request": {"url": None, "duration": seconds, "resolution": f"{width}x{height}"},
        }
    
    def generate_video_sora(self, prompt: str, duration: int = 10, resolution: str = "1920x1080", **kwargs) -> Dict[str, Any]:
        """
        Generate videos using Sora.
        
        Args:
            prompt: Video description
            duration: Video length in seconds (default: 10)
            resolution: Video resolution (default: "1920x1080")
            **kwargs: Additional parameters (fps, style, etc.)
        
        Returns:
            dict with video data and metadata
        """
        logger.info(f"Generating video with Sora: {prompt[:100]}, duration={duration}s")
        
        try:
            endpoints = self._candidate_openai_endpoints(self.sora_inference)
            if not endpoints:
                return {
                    "model": self.sora_deployment,
                    "error": (
                        "Sora endpoint is not configured. Set AZURE_OPENAI_ENDPOINT_SORA or "
                        "AZURE_AI_INFERENCE_ENDPOINT_SORA (or AZURE_AI_INFERENCE_ENDPOINT_SWEDEN as fallback)."
                    ),
                    "status": "error",
                }

            api_key = self._get_optional_api_key("AZURE_OPENAI_API_KEY_SORA")
            headers = self._build_headers(api_key=api_key)

            width, height = self._parse_resolution(resolution)
            n_variants = kwargs.get("n_variants", 1)

            # Sora v1 job-based API (matches your working curl schema)
            payload = {
                "prompt": prompt,
                "height": height,
                "width": width,
                "n_seconds": int(duration),
                "n_variants": int(n_variants),
                "model": self.sora_deployment,
            }

            last_error: Optional[Dict[str, Any]] = None
            job: Optional[Dict[str, Any]] = None
            create_url: Optional[str] = None
            selected_endpoint: Optional[str] = None
            for endpoint in endpoints:
                create_url = f"{endpoint}/openai/v1/video/generations/jobs?api-version={self.sora_api_version}"
                create_resp = requests.post(create_url, headers=headers, json=payload, timeout=120)
                if create_resp.status_code in (200, 201):
                    job = create_resp.json()
                    selected_endpoint = endpoint
                    break
                last_error = {
                    "model": self.sora_deployment,
                    "error": create_resp.text,
                    "status_code": create_resp.status_code,
                    "request": {"url": create_url, "api_version": self.sora_api_version},
                    "status": "error",
                }

            if not job:
                return last_error or {
                    "model": self.sora_deployment,
                    "error": "Sora job creation failed.",
                    "status": "error",
                }
            job_id = job.get("id") or job.get("job_id")
            if not job_id:
                return {
                    "model": self.sora_deployment,
                    "error": "Sora job creation succeeded but returned no job id",
                    "raw": job,
                    "request": {"url": create_url, "api_version": self.sora_api_version},
                    "status": "error",
                }

            base_endpoint = selected_endpoint or endpoints[0]
            get_job_url = f"{base_endpoint}/openai/v1/video/generations/jobs/{job_id}?api-version={self.sora_api_version}"

            max_poll_seconds = int(kwargs.get("max_poll_seconds", 600))
            poll_interval_seconds = float(kwargs.get("poll_interval_seconds", 2.0))
            start = time.time()

            status = str(job.get("status") or "queued")
            last_job = job
            while status in ("queued", "preprocessing", "in_progress", "processing", "running"):
                if time.time() - start > max_poll_seconds:
                    return {
                        "model": self.sora_deployment,
                        "status": "error",
                        "error": f"Timed out polling Sora job after {max_poll_seconds}s",
                        "job_id": job_id,
                        "last_job": last_job,
                        "request": {"url": get_job_url, "api_version": self.sora_api_version},
                    }
                time.sleep(poll_interval_seconds)
                poll_resp = requests.get(get_job_url, headers=headers, timeout=60)
                if poll_resp.status_code != 200:
                    return {
                        "model": self.sora_deployment,
                        "status": "error",
                        "error": poll_resp.text,
                        "status_code": poll_resp.status_code,
                        "job_id": job_id,
                        "request": {"url": get_job_url, "api_version": self.sora_api_version},
                    }
                last_job = poll_resp.json()
                status = str(last_job.get("status") or status)

            if status not in ("succeeded", "completed"):
                return {
                    "model": self.sora_deployment,
                    "status": "error",
                    "error": f"Sora job ended with status '{status}'",
                    "job_id": job_id,
                    "last_job": last_job,
                    "request": {"url": get_job_url, "api_version": self.sora_api_version},
                }

            generations = last_job.get("generations") or last_job.get("output") or []
            generation_id = None
            if isinstance(generations, list) and generations:
                first = generations[0]
                if isinstance(first, dict):
                    generation_id = first.get("id") or first.get("generation_id")
                elif isinstance(first, str):
                    generation_id = first

            if not generation_id:
                return {
                    "model": self.sora_deployment,
                    "status": "error",
                    "error": "Sora job succeeded but returned no generation id",
                    "job_id": job_id,
                    "last_job": last_job,
                }

            content_url = f"{base_endpoint}/openai/v1/video/generations/{generation_id}/content/video?api-version={self.sora_api_version}"
            content_headers = dict(headers)
            content_headers.pop("Content-Type", None)
            content_headers["Accept"] = "application/octet-stream"

            content_resp = requests.get(content_url, headers=content_headers, timeout=300)
            if content_resp.status_code != 200:
                return {
                    "model": self.sora_deployment,
                    "status": "error",
                    "error": content_resp.text,
                    "status_code": content_resp.status_code,
                    "job_id": job_id,
                    "generation_id": generation_id,
                    "request": {"url": content_url, "api_version": self.sora_api_version},
                }

            logger.info("Sora video bytes downloaded successfully")
            return {
                "model": self.sora_deployment,
                "status": "success",
                "job_id": job_id,
                "generation_id": generation_id,
                "duration": duration,
                "content_bytes": content_resp.content,
                "request": {"create_url": create_url, "job_url": get_job_url, "content_url": content_url, "api_version": self.sora_api_version},
            }

        except Exception as e:
            logger.error(f"Exception in Sora generation: {str(e)}")
            return {
                "model": self.sora_deployment,
                "error": str(e),
                "status": "exception"
            }
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get information about available models and their endpoints."""
        return {
            "models": {
                "FLUX.1-Kontext-pro": {
                    "deployment": self.flux1_deployment,
                    "endpoint": self.sweden_inference,
                    "region": "Sweden Central",
                    "purpose": "Document and contextual image generation"
                },
                "FLUX.2-pro": {
                    "deployment": self.flux2_deployment,
                    "endpoint": self.westus3_inference,
                    "region": "West US 3",
                    "purpose": "High-quality visual content generation"
                },
                "Sora": {
                    "deployment": self.sora_deployment,
                    "endpoint": self.sweden_inference,
                    "region": "Sweden Central",
                    "purpose": "Video generation"
                }
            },
            "api_version": self.api_version
        }

    
    def generate_background(self, prompt: str, remove_background: bool = False, **kwargs):
        """
        Generate or replace backgrounds using FLUX.2-pro.
        
        Args:
            prompt: Background description
            remove_background: Whether to generate transparent background
            **kwargs: Additional parameters
        
        Returns:
            Generated background image
        """
        if remove_background:
            prompt = f"{prompt}, transparent background, isolated subject"
        else:
            prompt = f"Background scene: {prompt}"
        
        return self.generate_visual_content(prompt, **kwargs)
    
    def generate_thumbnail(self, prompt: str, aspect_ratio="16:9", **kwargs):
        """
        Generate video thumbnails using FLUX.2-pro.
        
        Args:
            prompt: Thumbnail description
            aspect_ratio: Thumbnail aspect ratio
            **kwargs: Additional parameters
        
        Returns:
            Generated thumbnail image
        """
        # Map aspect ratios to sizes
        size_map = {
            "16:9": "1792x1024",
            "9:16": "1024x1792",
            "1:1": "1024x1024"
        }
        
        kwargs['size'] = size_map.get(aspect_ratio, "1792x1024")
        kwargs['quality'] = 'hd'  # Thumbnails need high quality
        
        enhanced_prompt = f"Eye-catching YouTube thumbnail: {prompt}, professional, vibrant colors, high contrast"
        
        return self.generate_visual_content(enhanced_prompt, **kwargs)
