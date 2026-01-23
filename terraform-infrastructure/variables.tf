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
  type        = map(string)
  description = "Map of agent environment variable names to their assigned model deployments"
  default = {
    orchestrator         = "model-router"
    cropping_agent       = "gpt-4o"
    visual_content_agent = "FLUX.2-pro"
    video_agent          = "sora"
    document_agent       = "FLUX.1-Kontext-pro"
  }
}

variable "agent_region_assignments" {
  type        = map(list(string))
  description = "Map of regions to list of agents to deploy in that region"
  default = {
    swedencentral = ["orchestrator", "cropping_agent", "video_agent", "document_agent"]
    eastus        = ["visual_content_agent"]
  }
}

variable "model_regions" {
  type        = map(string)
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

variable "enable_key_vault_private_endpoint" {
  type        = bool
  description = "Enable a Private Endpoint + Private DNS for Key Vault. When enabled and public access is disabled, Terraform must run from inside the VNet to manage secrets."
  default     = false
}

variable "key_vault_public_network_access_enabled" {
  type        = bool
  description = "Controls Key Vault public network access. For Private Endpoint-only access, set false."
  default     = true
}

variable "key_vault_vnet_address_space" {
  type        = string
  description = "CIDR for the VNet used for Key Vault Private Endpoint and App Service VNet integration."
  default     = "10.50.0.0/16"
}

variable "key_vault_private_endpoint_subnet_cidr" {
  type        = string
  description = "CIDR for the subnet that hosts the Key Vault Private Endpoint."
  default     = "10.50.1.0/24"
}

variable "app_service_integration_subnet_cidr" {
  type        = string
  description = "CIDR for the delegated subnet used by App Service VNet integration (Swift)."
  default     = "10.50.2.0/24"
}

variable "flux_2_pro_sku_name" {
  type        = string
  description = "SKU name for the FLUX.2-pro deployment (maps to throughput/limits behavior)."
  default     = "GlobalStandard"
}

variable "flux_2_pro_sku_capacity" {
  type        = number
  description = "SKU capacity for the FLUX.2-pro deployment. This is NOT subscription quota; it controls deployment capacity and can still be constrained by quota."
  default     = 2
}

variable "oss_image_backend_url" {
  type        = string
  description = "Optional base URL for a remote open-source image backend (e.g., https://my-a1111.example.com). Only used when oss_baseline_mode=remote."
  default     = ""
}

variable "oss_image_backend_kind" {
  type        = string
  description = "Open-source image backend kind. Currently supported by the app: a1111."
  default     = "a1111"
}

variable "oss_image_backend_timeout_seconds" {
  type        = number
  description = "Timeout (seconds) for open-source image backend requests."
  default     = 120
}

variable "oss_image_backend_auth_bearer" {
  type        = string
  description = "Optional bearer token for the OSS image backend. Prefer passing a Key Vault reference string rather than a raw token."
  default     = ""
  sensitive   = true
}

variable "oss_video_backend_url" {
  type        = string
  description = "Optional base URL for a remote open-source video backend (e.g., https://my-oss-video.example.com). Only used when oss_baseline_mode=remote."
  default     = ""
}

variable "oss_video_backend_kind" {
  type        = string
  description = "Open-source video backend kind. Currently supported by the app: generic (POST /generate)."
  default     = "generic"
}

variable "oss_video_backend_timeout_seconds" {
  type        = number
  description = "Timeout (seconds) for open-source video backend requests."
  default     = 300
}

variable "oss_video_backend_auth_bearer" {
  type        = string
  description = "Optional bearer token for the OSS video backend. Prefer passing a Key Vault reference string rather than a raw token."
  default     = ""
  sensitive   = true
}

variable "oss_baseline_mode" {
  type        = string
  description = "Controls OSS baseline behavior. local (default) uses in-app open-source libraries (CPU-safe). remote uses OSS_IMAGE_BACKEND_URL / OSS_VIDEO_BACKEND_URL endpoints."
  default     = "local"
}

variable "oss_thumbnail_mode" {
  type        = string
  description = "Controls OSS thumbnail generation. local (default) uses in-app libraries. azure-worker/aks offloads to OSS_AZURE_WORKER_URL /generate-thumbnail when configured."
  default     = "local"
}

variable "oss_azure_worker_url_override" {
  type        = string
  description = "Optional override for OSS_AZURE_WORKER_URL (e.g., a Container App/VM URL hosting src/oss_worker). Used even when enable_oss_aks_worker=false."
  default     = ""
}

variable "enable_oss_aks_worker" {
  type        = bool
  description = "Enable an AKS-hosted OSS Diffusers worker (GPU) for realistic OSS images. When enabled, the app can call the worker via OSS_AZURE_WORKER_URL."
  default     = false
}

variable "aks_nodes_subnet_cidr" {
  type        = string
  description = "CIDR for the subnet that hosts AKS nodes (used when enable_oss_aks_worker=true). Must fit inside key_vault_vnet_address_space."
  default     = "10.50.3.0/24"
}

variable "oss_aks_worker_lb_ip" {
  type        = string
  description = "Static private IP (within aks_nodes_subnet_cidr) for the internal LoadBalancer service that exposes the OSS worker to the App Service via VNet integration."
  default     = "10.50.3.10"
}

variable "oss_aks_worker_auth_bearer" {
  type        = string
  description = "Optional shared bearer token required by the OSS worker. Set the same value in the worker and the web app."
  default     = ""
  sensitive   = true
}

variable "oss_diffusers_model_id" {
  type        = string
  description = "Model id or local path for Diffusers pipeline used by the OSS worker (e.g., SDXL model id). Required when enable_oss_aks_worker=true."
  default     = ""
}

variable "oss_diffusers_device" {
  type        = string
  description = "Diffusers device hint for in-app OSS generation (cpu|cuda). Default cpu." 
  default     = "cpu"
}

variable "oss_diffusers_num_inference_steps" {
  type        = number
  description = "Default Diffusers inference steps for OSS generation (lower is faster)."
  default     = 12
}

variable "oss_diffusers_guidance_scale" {
  type        = number
  description = "Default Diffusers guidance scale for OSS generation."
  default     = 5.0
}

variable "oss_aks_worker_replicas" {
  type        = number
  description = "Number of replicas for the AKS OSS worker deployment. Increase for higher throughput (requires GPU capacity)."
  default     = 1
}

variable "oss_aks_worker_preload" {
  type        = bool
  description = "Whether the OSS worker should preload the Diffusers pipeline at container startup for lower first-request latency."
  default     = true
}

variable "oss_aks_worker_cache_enabled" {
  type        = bool
  description = "Enable a persistent RWX cache volume (Azure Files) for HuggingFace/Diffusers model weights to reduce cold starts on reschedules."
  default     = false
}

variable "oss_aks_worker_cache_size" {
  type        = string
  description = "PVC size for the OSS worker cache (e.g., 50Gi). Only used when oss_aks_worker_cache_enabled=true."
  default     = "50Gi"
}

variable "oss_aks_worker_cache_storage_class" {
  type        = string
  description = "StorageClass name for the OSS worker cache PVC. For RWX on AKS, azurefile-csi is commonly available. Only used when oss_aks_worker_cache_enabled=true."
  default     = "azurefile-csi"
}

variable "aks_kubernetes_version" {
  type        = string
  description = "AKS Kubernetes version (optional). Leave empty to let Azure pick a default supported version."
  default     = ""
}

variable "aks_system_node_vm_size" {
  type        = string
  description = "VM size for the AKS system node pool."
  default     = "Standard_D2_v3"
}

variable "aks_gpu_node_vm_size" {
  type        = string
  description = "VM size for the AKS GPU node pool (must be a GPU SKU like Standard_NC*)."
  default     = "Standard_NC6s_v3"
}

variable "aks_gpu_node_count" {
  type        = number
  description = "Number of nodes in the AKS GPU node pool."
  default     = 1
}

