resource_group_name = "RG-AI-Mediaxi30"
location            = "eastus2"
name_prefix         = "zava"
app_service_location = "westus3"
app_service_sku      = "P0v3"

# Enable multi-region AI Foundry with 2 projects
enable_multi_region_foundry = true
foundry_regions            = ["swedencentral", "westus3"]

# Enable multi-agent architecture
enable_multi_agent = true

# Enable AI automation and model deployments
enable_ai_automation = true

# Agent-to-Region Assignments (2 agents in 1 region)
# Sweden Central: 2 chat agents (orchestrator, cropping_agent)
# West US 3: NO agents - only FLUX.2-pro model accessed via Direct API
# NOTE: Agents only support chat models. Image models (FLUX) accessed via DirectModelService.
agent_region_assignments = {
  swedencentral = ["orchestrator", "cropping_agent"]  # Only chat-capable agents
  # westus3 = []  # No agents - FLUX.2-pro accessed via Direct API only
}

# Agent-to-Model Assignments (ONLY chat models)
# NOTE: Only agents that use chat models are deployed.
# Image/video models (FLUX, Sora) are accessed via Direct API, not agents.
agent_model_assignments = {
  orchestrator   = "model-router"  # Sweden - Orchestration with 18-model routing (chat)
  cropping_agent = "gpt-4o"        # Sweden - Vision capabilities (chat with vision)
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
    sku_capacity  = 10
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
