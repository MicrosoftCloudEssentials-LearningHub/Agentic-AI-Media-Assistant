resource_group_name  = "RG-AI-Media-DemoX2"
location             = "eastus2"
name_prefix          = "zava"
app_service_location = "westus3"
app_service_sku      = "P0v3"

# Enable multi-region AI Foundry with 2 projects
enable_multi_region_foundry = true
foundry_regions             = ["swedencentral", "westus3"]

# Enable multi-agent architecture
enable_multi_agent = true

# Enable AI automation and model deployments
enable_ai_automation = true

# Enable A2A (Agent-to-Agent) automation framework
enable_a2a_automation = true

# Agent-to-Region Assignments (2 agents in 1 region)
# Sweden Central: 2 chat agents (orchestrator, cropping_agent)
# West US 3: NO agents - only FLUX.2-pro model accessed via Direct API
# NOTE: Agents only support chat models. Image models (FLUX) accessed via DirectModelService.
agent_region_assignments = {
  swedencentral = ["orchestrator", "cropping_agent"] # Only chat-capable agents
  # westus3 = []  # No agents - FLUX.2-pro accessed via Direct API only
}

# Agent-to-Model Assignments (ONLY chat models)
# NOTE: Only agents that use chat models are deployed.
# Image/video models (FLUX, Sora) are accessed via Direct API, not agents.
agent_model_assignments = {
  orchestrator   = "model-router" # Sweden - Orchestration with 18-model routing (chat)
  cropping_agent = "gpt-4o"       # Sweden - Vision capabilities (chat with vision)
  # Removed: video_agent, document_agent, visual_content_agent
  # Reason: Sora and FLUX models accessed via DirectModelService REST API
}

# Model Deployment Regions (5 models total)
# Sweden Central: 4 models (model-router, gpt-4o, FLUX.1-Kontext-pro, sora)
# West US 3: 1 model (FLUX.2-pro)
model_regions = {
  "model-router"       = "swedencentral"
  "gpt-4o"             = "swedencentral"
  "FLUX.1-Kontext-pro" = "swedencentral"
  "FLUX.2-pro"         = "westus3"
  "sora"               = "swedencentral"
}

# Model Specifications (versions, formats, SKUs, capacity)
model_specs = {
  "model-router" = {
    model_name    = "model-router"
    model_version = "2025-11-18"
    model_format  = "OpenAI"
    sku_name      = "GlobalStandard"
    sku_capacity  = 10
  }
  "gpt-4o" = {
    model_name    = "gpt-4o"
    model_version = "2024-08-06"
    model_format  = "OpenAI"
    sku_name      = "GlobalStandard"
    sku_capacity  = 10
  }
  "FLUX.1-Kontext-pro" = {
    model_name    = "FLUX.1-Kontext-pro"
    model_version = "1"
    model_format  = "Black Forest Labs"
    sku_name      = "GlobalStandard"
    sku_capacity  = 10
  }
  "FLUX.2-pro" = {
    model_name    = "FLUX.2-pro"
    model_version = "1"
    model_format  = "Black Forest Labs"
    sku_name      = "GlobalStandard"
    sku_capacity  = 4
  }
  "sora" = {
    model_name    = "sora"
    model_version = "2025-05-02"
    model_format  = "OpenAI"
    sku_name      = "GlobalStandard"
    sku_capacity  = 10
  }
}

cosmos_tenant_id = "zava-media-demo"

# user_principal_id is optional - defaults to current Azure CLI user (az login)

# Key vault network locking disabled to allow Terraform access
lock_key_vault_network = false

# Key Vault Private Endpoint (PE) migration
# Keep public access enabled initially so Terraform can still manage secrets from a laptop.
# After you move Terraform execution into the VNet (or stop managing KV secrets in Terraform),
# set `key_vault_public_network_access_enabled = false`.
enable_key_vault_private_endpoint       = true
key_vault_public_network_access_enabled = true

# --- Realistic OSS image generation (AKS GPU worker) ---
# NOTE: In this subscription/region, AKS creation currently fails due to VM SKU restrictions.
# Keep this disabled unless/until you pick allowed SKUs and have quota in `app_service_location`.
enable_oss_aks_worker = false

# Prefer auto: uses AKS worker when configured, otherwise falls back to in-process OSS baseline.
oss_baseline_mode = "auto"

# Thumbnail generation mode. Use `azure-worker` to offload to an external worker at oss_azure_worker_url_override.
oss_thumbnail_mode = "local"

# Optional: external worker URL (Container App/VM) that runs src/oss_worker.
# Example:
# oss_azure_worker_url_override = "https://my-oss-worker.example.com"
oss_azure_worker_url_override = ""

# Example worker config (enable when ready):
# oss_diffusers_model_id = "runwayml/stable-diffusion-v1-5"
# oss_aks_worker_cache_enabled = true
# oss_aks_worker_cache_size    = "50Gi"
# oss_aks_worker_cache_storage_class = "azurefile-csi"

# Realistic in-app OSS image generation via Diffusers (CPU). Keep sizes small for speed.
oss_diffusers_model_id            = "runwayml/stable-diffusion-v1-5"
oss_diffusers_device              = "cpu"
oss_diffusers_num_inference_steps = 12
oss_diffusers_guidance_scale      = 5.0

# If you enable the worker in westus3, you may need to override node SKUs, e.g.:
# aks_system_node_vm_size = "Standard_D2_v3"
# aks_gpu_node_vm_size    = "Standard_NC24ads_A100_v4"  # if that's what your subscription allows
