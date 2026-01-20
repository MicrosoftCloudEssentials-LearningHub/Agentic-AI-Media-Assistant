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
from typing import Dict, Any, Optional
from azure.identity import DefaultAzureCredential
from azure.core.credentials import AccessToken

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
        self.images_api_version = os.getenv("AZURE_OPENAI_IMAGES_API_VERSION", "2025-04-01-preview")
        # Next generation v1 Azure OpenAI APIs use versionless routing: api-version=preview.
        # This is especially important for /openai/v1/* endpoints.
        self.v1_api_version = os.getenv("AZURE_OPENAI_V1_API_VERSION", "preview")
        self.sora_api_version = os.getenv("AZURE_OPENAI_SORA_API_VERSION", "preview")

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
                "response_format": kwargs.get("response_format", "url"),  # "url" or "b64_json"
            }

            last_error: Optional[Dict[str, Any]] = None
            for endpoint in self._candidate_openai_endpoints(self.flux1_inference):
                # Match the known-working pattern: v1 images route with model=<deployment>.
                v1_payload = dict(payload)
                v1_payload["model"] = self.flux1_deployment

                v1_url = f"{endpoint}/openai/v1/images/generations?api-version={self.v1_api_version}"
                v1_resp = self._post_with_retries(v1_url, headers=headers, payload=v1_payload, timeout=120)

                if (
                    v1_resp.status_code == 400
                    and self._is_api_version_not_supported(v1_resp.text)
                    and str(self.v1_api_version).lower() != "preview"
                ):
                    v1_url = f"{endpoint}/openai/v1/images/generations?api-version=preview"
                    v1_resp = self._post_with_retries(v1_url, headers=headers, payload=v1_payload, timeout=120)

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

                # Fallback: deployment-scoped images route.
                url = f"{endpoint}/openai/deployments/{self.flux1_deployment}/images/generations"
                url_with_version = f"{url}?api-version={self.images_api_version}"
                response = self._post_with_retries(url_with_version, headers=headers, payload=payload, timeout=120)

                if response.status_code == 200:
                    result = response.json()
                    logger.info("SUCCESS - FLUX.1-Kontext-pro image generated")
                    return {
                        "model": self.flux1_deployment,
                        "data": result.get("data", []),
                        "request": {"url": url, "api_version": self.images_api_version, "size": size},
                        "status": "success",
                    }

                # Prefer v1 error details when v1 was close, otherwise deployment route errors.
                last_error = {
                    "model": self.flux1_deployment,
                    "error": v1_resp.text if v1_resp.status_code != 404 else response.text,
                    "status_code": v1_resp.status_code if v1_resp.status_code != 404 else response.status_code,
                    "request": {"url": v1_url if v1_resp.status_code != 404 else url, "api_version": self.v1_api_version if v1_resp.status_code != 404 else self.images_api_version, "size": size},
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
                "response_format": kwargs.get("response_format", "url"),
            }

            last_error: Optional[Dict[str, Any]] = None
            for endpoint in self._candidate_openai_endpoints(self.flux2_inference):
                # Match the known-working pattern: v1 images route with model=<deployment>.
                v1_payload = dict(payload)
                v1_payload["model"] = self.flux2_deployment

                v1_url = f"{endpoint}/openai/v1/images/generations?api-version={self.v1_api_version}"
                v1_resp = self._post_with_retries(v1_url, headers=headers, payload=v1_payload, timeout=120)

                if (
                    v1_resp.status_code == 400
                    and self._is_api_version_not_supported(v1_resp.text)
                    and str(self.v1_api_version).lower() != "preview"
                ):
                    v1_url = f"{endpoint}/openai/v1/images/generations?api-version=preview"
                    v1_resp = self._post_with_retries(v1_url, headers=headers, payload=v1_payload, timeout=120)

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

                # Fallback: deployment-scoped images route.
                url = f"{endpoint}/openai/deployments/{self.flux2_deployment}/images/generations"
                url_with_version = f"{url}?api-version={self.images_api_version}"
                response = self._post_with_retries(url_with_version, headers=headers, payload=payload, timeout=120)

                if response.status_code == 200:
                    result = response.json()
                    logger.info("FLUX.2-pro image generated successfully")
                    return {
                        "model": self.flux2_deployment,
                        "data": result.get("data", []),
                        "request": {"url": url, "api_version": self.images_api_version, "size": size},
                        "status": "success",
                    }

                last_error = {
                    "model": self.flux2_deployment,
                    "error": v1_resp.text if v1_resp.status_code != 404 else response.text,
                    "status_code": v1_resp.status_code if v1_resp.status_code != 404 else response.status_code,
                    "request": {"url": v1_url if v1_resp.status_code != 404 else url, "api_version": self.v1_api_version if v1_resp.status_code != 404 else self.images_api_version, "size": size},
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
