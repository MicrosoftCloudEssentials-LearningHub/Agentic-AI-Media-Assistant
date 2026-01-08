resource_group_name = "RG-AI-Mediaxi27"
location            = "eastus2"
name_prefix         = "zava"
app_service_location = "westus3"
app_service_sku      = "P0v3"

# Enable multi-region AI Foundry with 2 projects
enable_multi_region_foundry = true
foundry_regions            = ["swedencentral", "eastus"]

# Enable multi-agent architecture
enable_multi_agent = true

# Enable AI automation and model deployments
enable_ai_automation = true

# Agent-to-Region Assignments (5 agents across 2 regions)
# Sweden Central: 4 agents, East US: 1 agent
# East US agent co-located with FLUX.2-pro for low-latency image generation
agent_region_assignments = {
  swedencentral = ["orchestrator", "cropping_agent", "video_agent", "document_agent"]
  eastus        = ["visual_content_agent"]  # Handles backgrounds + thumbnails with FLUX.2-pro
}

# Agent-to-Model Assignments (dynamically configurable)
agent_model_assignments = {
  orchestrator         = "model-router"  # Sweden - Orchestration with 18-model routing
  cropping_agent       = "gpt-4o"        # Sweden - Vision capabilities
  video_agent          = "sora"          # Sweden - Video generation
  document_agent       = "FLUX.1-Kontext-pro"  # Sweden - Document processing
  visual_content_agent = "FLUX.2-pro"    # East US - Image generation and visual content
}

# Model Deployment Regions (5 models total)
# Sweden Central: 4 models (model-router, gpt-4o, FLUX.1-Kontext-pro, sora)
# East US: 1 model (FLUX.2-pro)
model_regions = {
  "model-router"       = "swedencentral"
  "gpt-4o"             = "swedencentral"
  "FLUX.1-Kontext-pro" = "swedencentral"
  "FLUX.2-pro"         = "eastus"
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
