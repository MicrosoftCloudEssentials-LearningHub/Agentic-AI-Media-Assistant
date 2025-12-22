variable "resource_group_name" {
  type        = string
  description = "Existing resource group name where resources will be deployed"
}

variable "location" {
  type        = string
  description = "Azure region for resources"
  default     = "eastus"
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

variable "enable_cosmos_local_auth" {
  type        = bool
  description = "Whether to enable local auth on Cosmos DB account"
  default     = true
}

variable "enable_ai_automation" {
  type        = bool
  description = "Whether to run Azure AI Foundry automation steps (model deployments, connections, .env creation)"
  default     = true
}

variable "enable_data_pipeline" {
  type        = bool
  description = "Whether to run data pipeline automation (requires Python and data files)"
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

