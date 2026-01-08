variable "resource_group_name" {
  type        = string
  description = "Existing resource group name where resources will be deployed"
}

variable "location" {
  type        = string
  description = "Azure region for resources"
  default     = "eastus"
}

variable "enable_multi_region_foundry" {
  type        = bool
  description = "Enable multi-region AI Foundry deployment across multiple Azure regions"
  default     = true
}

variable "foundry_regions" {
  type        = list(string)
  description = "List of Azure regions where AI Foundry projects will be deployed"
  default     = ["swedencentral", "eastus"]
}

variable "model_regions_file" {
  type        = string
  description = "Path to JSON file containing model_regions map produced by model_region_validator.ps1"
  default     = "model_regions.json"
}

variable "name_prefix" {
  type        = string
  description = "Prefix for all resource names (will append random suffix)"
  default     = "zava"
}

variable "user_principal_id" {
  type        = string
  description = "Object ID of the user/principal to grant Cosmos DB data contributor access. Defaults to current Azure CLI user."
  default     = null
}

variable "agent_model_assignments" {
  type = map(string)
  description = "Map of agent environment variable names to their assigned model deployments"
  default = {
    orchestrator         = "model-router"
    cropping_agent       = "gpt-4o"
    background_agent     = "FLUX.2-pro"
    thumbnail_generator  = "FLUX.2-pro"  # DALL-E 3 quota exhausted, using FLUX.2-pro
    video_agent          = "sora"
    document_agent       = "FLUX.1-Kontext-pro"
  }
}

variable "agent_region_assignments" {
  type = map(list(string))
  description = "Map of regions to list of agents to deploy in that region"
  default = {
    swedencentral = ["orchestrator", "cropping_agent", "video_agent", "document_agent"]
    eastus        = ["background_agent", "thumbnail_generator"]
  }
}

variable "model_regions" {
  type = map(string)
  description = "Map of model names to their deployment regions"
  default = {
    "model-router"       = "swedencentral"
    "gpt-4o"             = "swedencentral"
    "FLUX.1-Kontext-pro" = "swedencentral"
    "FLUX.2-pro"         = "eastus"
    "sora"               = "swedencentral"
  }
}

variable "model_specs" {
  type = map(object({
    model_name    = string
    model_version = string
    model_format  = string
    sku_name      = string
    sku_capacity  = number
  }))
  description = "Specifications for each AI model deployment"
  default = {
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
      model_version = "1.0"
      model_format  = "OpenAI"
      sku_name      = "Standard"
      sku_capacity  = 2
    }
    "FLUX.2-pro" = {
      model_name    = "FLUX.2-pro"
      model_version = "1.0"
      model_format  = "OpenAI"
      sku_name      = "Standard"
      sku_capacity  = 2
    }
    "sora" = {
      model_name    = "sora"
      model_version = "2025-05-02"
      model_format  = "OpenAI"
      sku_name      = "GlobalStandard"
      sku_capacity  = 10
    }
  }
}

variable "cosmos_tenant_id" {
  type        = string
  description = "Logical tenant identifier stamped onto each Cosmos item"
  default     = "zava-demo"
}

variable "enable_ai_automation" {
  type        = bool
  description = "Whether to run Azure AI Foundry automation steps (model deployments, connections, .env creation)"
  default     = true
}

variable "enable_multi_agent" {
  type        = bool
  description = "Whether to deploy multi-agent architecture in Microsoft Foundry"
  default     = true
}

variable "enable_a2a_automation" {
  type        = bool
  description = "Whether to deploy the A2A automation framework with process management, testing, monitoring, and deployment automation"
  default     = true
}

variable "a2a_host" {
  type        = string
  description = "Host for the A2A automation system"
  default     = "0.0.0.0"
}

variable "a2a_port" {
  type        = number
  description = "Port for the A2A automation system"
  default     = 8001
}

variable "enable_monitoring_dashboards" {
  type        = bool
  description = "Whether to create monitoring dashboards and alerts for A2A system"
  default     = true
}

variable "enable_continuous_testing" {
  type        = bool
  description = "Whether to enable continuous testing automation for A2A system"
  default     = true
}

variable "automation_storage_path" {
  type        = string
  description = "Path for automation data storage"
  default     = "./automation_data"
}

variable "app_service_location" {
  type        = string
  description = "Optional region override for App Service / Web App hosting"
  default     = null
}

variable "app_service_sku" {
  type        = string
  description = "SKU name for the App Service plan (e.g., B1, S1, P0v3)."
  default     = "P0v3"
}

variable "lock_key_vault_network" {
  type        = bool
  description = "Whether to lock Key Vault firewall to specific IPs (set true only when deployment IPs are stable)."
  default     = false
}

