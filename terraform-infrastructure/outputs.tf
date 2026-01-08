output "storageAccountName" {
  value       = azapi_resource.storage.name
  description = "Storage account name"
}

output "container_registry_name" {
  value       = azurerm_container_registry.acr.name
  description = "Azure Container Registry name"
}

output "application_name" {
  value       = azurerm_linux_web_app.app.name
  description = "App Service name"
}

output "application_url" {
  value       = azurerm_linux_web_app.app.default_hostname
  description = "Primary host name for the App Service"
}

output "ai_foundry_primary_region" {
  value       = local.primary_foundry_region
  description = "Region used for the primary (chat) Foundry account"
}

output "ai_foundry_primary_name" {
  value       = local.foundry_names[local.primary_foundry_region]
  description = "Primary MSFT Foundry account name (chat region)"
}

output "ai_project_primary_name" {
  value       = local.ai_project_names[local.primary_foundry_region]
  description = "Primary MSFT Foundry project name (chat region)"
}

output "ai_foundry_names_by_region" {
  value       = local.foundry_names
  description = "Map of region => Foundry account name"
}

output "ai_foundry_endpoints_by_model" {
  value       = local.model_endpoints
  description = "Map of model => Foundry endpoint (per-model routing)"
}

output "resource_group_name" {
  value       = azurerm_resource_group.rg.name
  description = "Resource group name"
}

output "agent_region_assignments" {
  description = "Agent-to-region mapping configuration"
  value       = var.agent_region_assignments
}

output "agent_model_assignments" {
  description = "Agent-to-model mapping configuration"
  value       = var.agent_model_assignments
}

output "model_regions" {
  description = "Model-to-region deployment mapping"
  value       = local.model_regions_final
}

output "model_specs" {
  description = "Model specifications and versions"
  value       = var.model_specs
  sensitive   = false
}

output "subscription_id" {
  value       = data.azurerm_client_config.current.subscription_id
  description = "Azure subscription ID"
}

output "application_insights_connection_string" {
  value       = azurerm_application_insights.appinsights.connection_string
  description = "Application Insights connection string"
  sensitive   = true
}

output "ai_foundry_primary_endpoint" {
  value       = local.foundry_endpoints[local.primary_foundry_region]
  description = "Primary MSFT Foundry endpoint URL (chat region)"
}

output "ai_project_primary_endpoint" {
  value       = local.ai_project_endpoints[local.primary_foundry_region]
  description = "Primary AI Project endpoint URL for agents API"
}

# Agent secret names (backup approach for zero-touch deployment)
output "agent_ids" {
  value = var.enable_ai_automation ? {
    for k, v in {
      agent_orchestrator_id        = azurerm_key_vault_secret.agent_orchestrator_id.name
      agent_cropping_agent_id      = azurerm_key_vault_secret.agent_cropping_agent_id.name
      agent_background_agent_id    = azurerm_key_vault_secret.agent_background_agent_id.name
      agent_thumbnail_generator_id = azurerm_key_vault_secret.agent_thumbnail_generator_id.name
      agent_video_agent_id         = azurerm_key_vault_secret.agent_video_agent_id.name
    } : k => v
  } : {}
  description = "Map of agent secret names for backup agent configuration"
}

output "agent_statuses" {
  value = var.enable_ai_automation ? {
    for agent_name in [
      "agent_orchestrator_id",
      "agent_cropping_agent_id",
      "agent_background_agent_id",
      "agent_thumbnail_generator_id",
      "agent_video_agent_id"
    ] : agent_name => "backup"
  } : {}
  description = "Status of agent secrets (backup mode for zero-touch deployment)"
}

output "key_vault_name" {
  value       = azurerm_key_vault.kv.name
  description = "Name of the Key Vault used for secret storage"
}

output "key_vault_uri" {
  value       = azurerm_key_vault.kv.vault_uri
  description = "Base URI of the Key Vault"
}

# === Real Agent Outputs (ochartarotr) ===
# NOTE: Commented out - Azure Agents API not yet available via ARM/Terraform
# output "cora_agent_id" {
#   value       = azapi_resource.cora_agent.id
#   description = "Cora agent resource ID"
# }
# output "interior_design_agent_id" {
#   value       = azapi_resource.interior_design_agent.id
#   description = "Interior Designer agent resource ID"
# }
# output "inventory_agent_id" {
#   value       = azapi_resource.inventory_agent.id
#   description = "Inventory Manager agent resource ID"
# }
# output "customer_loyalty_agent_id" {
#   value       = azapi_resource.customer_loyalty_agent.id
#   description = "Customer Loyalty agent resource ID"
# }
# output "cart_manager_agent_id" {
#   value       = azapi_resource.cart_manager_agent.id
#   description = "Cart Manager agent resource ID"
# }

output "deployed_models" {
  value       = var.enable_ai_automation ? keys(local.ai_model_specs) : []
  description = "List of AI models deployed across all regions"
}

output "env_file_location" {
  value       = var.enable_ai_automation ? "../src/.env" : "Not created (AI automation disabled)"
  description = "Location of the generated .env file"
}

output "chat_application_url" {
  value       = "https://${azurerm_linux_web_app.app.default_hostname}"
  description = "URL to access the Zava AI Shopping Assistant chat application"
}

output "chat_application_health" {
  value       = "https://${azurerm_linux_web_app.app.default_hostname}/health"
  description = "Health check endpoint for the chat application"
}

output "application_instructions" {
  value       = <<-EOT

  ============================================================================
  ZAVA AI SHOPPING ASSISTANT - DEPLOYMENT COMPLETE
  ============================================================================

  AZURE WEB APP:
    - App Name: ${azurerm_linux_web_app.app.name}
    - URL: https://${azurerm_linux_web_app.app.default_hostname}
    - Health Check: https://${azurerm_linux_web_app.app.default_hostname}/health

  LOCAL TESTING:
    - Primary URL: https://${azurerm_linux_web_app.app.default_hostname}
    - For Local Development: http://127.0.0.1:8000
    - To run locally:
      cd ../src
      venv\Scripts\Activate.ps1
      uvicorn chat_app:app --host 0.0.0.0 --port 8000

  A2A AUTOMATION FRAMEWORK:
    - Enabled: ${var.enable_a2a_automation}
    - Runs inside container (no local scripts required)
    - Status: https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/status
    - Metrics: https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/metrics
    - Health: https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/health
    - Testing: https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/test/run

  A2A AUTOMATION FEATURES:
    - Automated Process Management
    - Continuous Deployment Pipeline
    - Continuous Testing: ${var.enable_continuous_testing}
    - Monitoring Dashboards: ${var.enable_monitoring_dashboards}
    - Self-healing Capabilities

  A2A AUTOMATION MANAGEMENT:
    (Automation starts with the app container; no manual scripts required)

  TEST PROMPTS:
    ${var.enable_ai_automation ? "- Test the deployed AI agents with sample queries" : "- AI automation disabled - configure agents manually"}

  AZURE RESOURCES:
    - Resource Group: ${azurerm_resource_group.rg.name}
    - AI Foundry (primary): ${local.foundry_names[local.primary_foundry_region]}
    - Container Registry: ${local.registry_name}

  ============================================================================

  EOT
  description = "Deployment summary and usage instructions including A2A automation"
}

# A2A Automation Framework Outputs
output "a2a_automation_enabled" {
  description = "Whether A2A automation framework is enabled"
  value       = var.enable_a2a_automation
}

output "a2a_automation_port" {
  description = "Port for A2A automation system"
  value       = var.a2a_port
}

output "a2a_automation_endpoints" {
  description = "A2A automation endpoints"
  value = var.enable_a2a_automation ? {
    status      = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/status"
    metrics     = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/metrics"
    health      = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/health"
    testing     = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/test/run"
    deployment  = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/deploy/trigger"
    performance = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/performance"
  } : {}
}

output "monitoring_dashboards_enabled" {
  description = "Whether monitoring dashboards are enabled"
  value       = var.enable_monitoring_dashboards
}

output "continuous_testing_enabled" {
  description = "Whether continuous testing is enabled"
  value       = var.enable_continuous_testing
}

# Deployment Summary
output "deployment_summary" {
  description = "Summary of all deployed components"
  value = {
    web_application = {
      url          = "https://${azurerm_linux_web_app.app.default_hostname}"
      health_check = "https://${azurerm_linux_web_app.app.default_hostname}/health"
    }
    ai_services = {
      foundry_endpoint    = local.foundry_endpoints[local.primary_foundry_region]
      project_name        = local.ai_project_names[local.primary_foundry_region]
      multi_agent_enabled = var.enable_multi_agent
    }
    automation_framework = {
      enabled    = var.enable_a2a_automation
      port       = var.a2a_port
      monitoring = var.enable_monitoring_dashboards
      testing    = var.enable_continuous_testing
      endpoints = var.enable_a2a_automation ? {
        status  = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/status"
        metrics = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/metrics"
        health  = "https://${azurerm_linux_web_app.app.default_hostname}/a2a/automation/health"
      } : null
    }
    storage = {
      storage_account = local.storage_account
    }
  }
}
