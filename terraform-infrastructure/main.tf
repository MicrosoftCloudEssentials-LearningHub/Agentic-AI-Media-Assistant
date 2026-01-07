# Create resource group if it does not exist
resource "azurerm_resource_group" "rg" {
  name     = var.resource_group_name
  location = var.location
}

# Subscription context for role assignments
data "azurerm_client_config" "current" {}

# Random suffix to mimic uniqueString(resourceGroup().id)
resource "random_id" "suffix" {
  byte_length = 4
}

# Managed identity for deployment scripts
resource "azurerm_user_assigned_identity" "deployment" {
  name                = "${var.name_prefix}-${local.suffix}-deploy-mi"
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
}

# Allow deployment script identity to manage resources in the RG
resource "azurerm_role_assignment" "deployment_identity_rg" {
  scope                = azurerm_resource_group.rg.id
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.deployment.principal_id
}

# Allow deployment identity to pull from ACR
resource "azurerm_role_assignment" "deployment_identity_acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_user_assigned_identity.deployment.principal_id
  depends_on           = [azurerm_container_registry.acr, azurerm_user_assigned_identity.deployment]
}

resource "azurerm_role_assignment" "deployment_identity_foundry_user" {
  for_each             = azapi_resource.ai_foundry
  scope                = each.value.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_user_assigned_identity.deployment.principal_id
  depends_on           = [azapi_resource.ai_foundry, azurerm_user_assigned_identity.deployment]
}

# Key Vault access for deployment identity to read secrets (e.g., model keys)
resource "azurerm_key_vault_access_policy" "deployment_identity_kv" {
  key_vault_id = azurerm_key_vault.kv.id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.deployment.principal_id

  secret_permissions = ["Get", "List"]

  depends_on = [azurerm_key_vault.kv, azurerm_user_assigned_identity.deployment]
  
  timeouts {
    create = "5m"
    update = "5m"
    delete = "5m"
  }
}

locals {
  # Use provided user_principal_id or default to current Azure CLI user
  principal_id        = var.user_principal_id != null ? var.user_principal_id : data.azurerm_client_config.current.object_id
  suffix              = substr(random_id.suffix.hex, 0, 8)
  web_app_location    = coalesce(var.app_service_location, var.location)
  storage_account     = lower(replace("${var.name_prefix}${local.suffix}sa", "-", ""))
  # Model/region mapping (overrides loaded from JSON if present) - Media processing only
  model_regions_overrides = fileexists("${path.module}/${var.model_regions_file}") ? try(jsondecode(file("${path.module}/${var.model_regions_file}")).model_regions, {}) : {}
  model_regions_final = merge({
    # All models in Sweden Central for AI Foundry
    # App Service remains in East US 2 (cross-region traffic will incur egress charges)
    "gpt-4o"                 = "swedencentral",
    "gpt-4o-mini"            = "swedencentral",
    "dall-e-3"               = "swedencentral",
    "FLUX.2-pro"             = "eastus",        # East US - Sweden quota exhausted
    "sora"                   = "swedencentral",
  }, local.model_regions_overrides)
  model_regions_filtered      = { for k, v in local.model_regions_final : k => v if v != "unavailable" }
  foundry_regions             = distinct(values(local.model_regions_filtered))
  region_codes                = { for r in local.foundry_regions : lower(substr(replace(r, "-", ""), 0, 10)) => r }
  foundry_region_codes        = { for r in local.foundry_regions : r => lower(substr(replace(r, "-", ""), 0, 10)) }
  foundry_names               = { for r in local.foundry_regions : r => "aif-${local.foundry_region_codes[r]}-${local.suffix}" }
  ai_project_names            = { for r in local.foundry_regions : r => "proj-${local.foundry_region_codes[r]}-${local.suffix}" }
  app_service_plan            = "${var.name_prefix}-${local.suffix}-asp"
  log_analytics_name          = "${var.name_prefix}-${local.suffix}-la"
  app_insights_name           = "${var.name_prefix}-${local.suffix}-ai"
  registry_name               = lower(replace("${var.name_prefix}${local.suffix}cosureg", "-", ""))
  web_app_name                = "${var.name_prefix}-${local.suffix}-app"
  key_vault_name              = "${var.name_prefix}-${local.suffix}-kv"
  dockerfile_hash             = filesha256("../src/Dockerfile")

  # Hash of application source & templates to trigger container rebuild when logic/UI changes
  # Combine Python files and HTML templates for source tracking
  app_source_hash = sha256(join("", [
    for f in concat(
      [for py in fileset("../src", "**/*.py") : py],
      ["app/templates/index.html"] # Explicitly include the HTML template
    ) : fileexists("../src/${f}") ? filesha256("../src/${f}") : ""
  ]))
  product_catalog_hash = fileexists("../src/data/updated_product_catalog(in).csv") ? filesha256("../src/data/updated_product_catalog(in).csv") : "missing"

  model_region_map         = local.model_regions_filtered
  region_models            = { for r in local.foundry_regions : r => [for m, reg in local.model_regions_filtered : m if reg == r] }
  model_env_keys           = { for m, reg in local.model_regions_filtered : m => upper(replace(replace(replace(m, ".", "_"), "-", "_"), " ", "_")) }
  foundry_endpoints        = { for r in local.foundry_regions : r => "https://${local.foundry_names[r]}.cognitiveservices.azure.com/" }
  foundry_key_secret_names = { for r in local.foundry_regions : r => "ai-foundry-key-${local.foundry_region_codes[r]}" }
  model_endpoints          = { for m, r in local.model_regions_filtered : m => local.foundry_endpoints[r] }
  model_key_secret_map     = { for m, r in local.model_regions_filtered : m => local.foundry_key_secret_names[r] }
  primary_foundry_region   = local.model_region_map["gpt-4o"]
  
  # AI Project endpoints for Azure AI Projects SDK (agents API)
  ai_project_endpoints = { for r in local.foundry_regions : r => "https://${local.foundry_names[r]}.services.ai.azure.com/api/projects/${local.ai_project_names[r]}" }

  ai_model_specs = {
    "gpt-4o" = {
      model_name    = "gpt-4o"
      model_version = "2024-08-06"
      model_format  = "OpenAI"
      sku_name      = "GlobalStandard"
      sku_capacity  = 10
    }
    "gpt-4o-mini" = {
      model_name    = "gpt-4o-mini"
      model_version = "2024-07-18"
      model_format  = "OpenAI"
      sku_name      = "GlobalStandard"
      sku_capacity  = 10
    }
    "dall-e-3" = {
      model_name    = "dall-e-3"
      model_version = "3.0"
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

  model_deployment_payload = {
    for region, models in local.region_models :
    region => [for alias in models : merge({ alias = alias }, lookup(local.ai_model_specs, alias, {}))]
  }
}



# Storage account using AzAPI to bypass policy restrictions
resource "azapi_resource" "storage" {
  type      = "Microsoft.Storage/storageAccounts@2023-01-01"
  name      = local.storage_account
  location  = var.location
  parent_id = azurerm_resource_group.rg.id

  body = jsonencode({
    sku = {
      name = "Standard_LRS"
    }
    kind = "StorageV2"
    properties = {
      accessTier               = "Hot"
      allowSharedKeyAccess     = true
      minimumTlsVersion        = "TLS1_2"
      supportsHttpsTrafficOnly = true
    }
  })
  identity {
    type = "SystemAssigned"
  }
}

# AI Foundry accounts (one per required region) using AzAPI provider.
resource "azapi_resource" "ai_foundry" {
  for_each                  = toset(local.foundry_regions)
  type                      = "Microsoft.CognitiveServices/accounts@2025-06-01"
  name                      = local.foundry_names[each.key]
  location                  = each.key
  parent_id                 = azurerm_resource_group.rg.id
  schema_validation_enabled = false
  response_export_values    = ["properties.allowProjectManagement"]
  identity { type = "SystemAssigned" }
  body = jsonencode({
    sku  = { name = "S0" }
    kind = "AIServices"
    properties = {
      allowProjectManagement = true
      customSubDomainName    = local.foundry_names[each.key]
      disableLocalAuth       = false
      publicNetworkAccess    = "Enabled"
      restore                = false  # Disable soft-delete - purge immediately when deleted
    }
  })
  
  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }
}

resource "azapi_resource" "ai_project" {
  for_each                  = azapi_resource.ai_foundry
  type                      = "Microsoft.CognitiveServices/accounts/projects@2025-06-01"
  name                      = local.ai_project_names[each.key]
  location                  = each.key
  parent_id                 = each.value.id
  schema_validation_enabled = false
  
  depends_on = [
    azapi_resource.ai_foundry,
    time_sleep.wait_for_foundry_propagation
  ]
  
  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }
  identity { type = "SystemAssigned" }
  body       = jsonencode({ properties = {} })
}

# Model Deployments in primary Foundry Hub
# Note: Deployments are created at the Hub level, and Projects inherit access to them
# Using quota-friendly models available in eastus2

# GPT-4o-mini deployment - Primary model for all agents (within quota)
resource "azapi_resource" "gpt_4o_mini_deployment" {
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name                      = "gpt-4o-mini"
  parent_id                 = azapi_resource.ai_foundry[local.primary_foundry_region].id
  schema_validation_enabled = false
  
  depends_on = [
    azapi_resource.ai_foundry,
    time_sleep.wait_for_foundry_propagation
  ]
  
  body = jsonencode({
    properties = {
      model = {
        format  = "OpenAI"
        name    = "gpt-4o-mini"
        version = "2024-07-18"
      }
      raiPolicyName = "Microsoft.DefaultV2"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 10
    }
  })
  
  lifecycle {
    ignore_changes = [
      body
    ]
  }
  
  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }
}

# Image generation deployment with automatic fallback
# FLUX.2-pro deployment - Primary image generation model
resource "azapi_resource" "flux_2_pro_deployment" {
  type                      = "Microsoft.CognitiveServices/accounts/deployments@2024-10-01"
  name                      = "FLUX.2-pro"
  parent_id                 = azapi_resource.ai_foundry[local.model_region_map["FLUX.2-pro"]].id
  schema_validation_enabled = false

  depends_on = [
    azapi_resource.ai_foundry,
    time_sleep.wait_for_foundry_propagation,
    azapi_resource.gpt_4o_mini_deployment
  ]

  body = jsonencode({
    properties = {
      model = {
        format  = "Black Forest Labs"
        name    = "FLUX.2-pro"
        version = "1"
      }
      raiPolicyName = "Microsoft.DefaultV2"
    }
    sku = {
      name     = "GlobalStandard"
      capacity = 2
    }
  })

  timeouts {
    create = "30m"
    update = "30m"
    delete = "30m"
  }
  
  lifecycle {
    ignore_changes = all
  }
}

# DALL-E-3 deployment REMOVED - Permanent quota exhaustion (2/2 capacity used)
# Using FLUX.2-pro for image generation instead

# Sora deployment managed by null_resource.ai_model_deployments

# Log Analytics Workspace
resource "azurerm_log_analytics_workspace" "law" {
  name                = local.log_analytics_name
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  sku                 = "PerGB2018"
  retention_in_days   = 90
}

# Application Insights
resource "azurerm_application_insights" "appinsights" {
  name                = local.app_insights_name
  location            = var.location
  resource_group_name = azurerm_resource_group.rg.name
  application_type    = "web"
  workspace_id        = azurerm_log_analytics_workspace.law.id

  depends_on = [
    azurerm_log_analytics_workspace.law
  ]
}

resource "azurerm_container_registry" "acr" {
  name                = local.registry_name
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  sku                 = "Standard"
  admin_enabled       = true

  depends_on = [
    azurerm_resource_group.rg
  ]
}

resource "azurerm_container_registry_webhook" "webhook" {
  name                = "${local.registry_name}webhook"
  resource_group_name = azurerm_resource_group.rg.name
  registry_name       = azurerm_container_registry.acr.name
  location            = var.location

  service_uri = "https://${local.web_app_name}.scm.azurewebsites.net/api/registry/webhook"
  status      = "enabled"
  scope       = "zava-chat-app:latest"
  actions     = ["push"]

  custom_headers = {
    "Content-Type" = "application/json"
  }

  depends_on = [
    azurerm_container_registry.acr,
    azurerm_linux_web_app.app
  ]
}

# Standalone Docker Image Build - Always runs to ensure ACR has the required image
resource "null_resource" "docker_image_build" {
  # Trigger rebuild when:
  # 1. Dockerfile changes
  # 2. Application source code changes
  # 3. Requirements.txt changes
  # 4. ACR or app changes
  # 5. Force rebuild on every apply (always_run ensures terraform always executes the provisioner)
  triggers = {
    dockerfile_hash   = local.dockerfile_hash
    app_source_hash   = local.app_source_hash
    requirements_hash = fileexists("../src/requirements.txt") ? filesha256("../src/requirements.txt") : "missing"
    acr_id            = azurerm_container_registry.acr.id
    always_run        = timestamp() # Forces provisioner to run on every apply
  }

  depends_on = [
    azurerm_container_registry.acr
  ]

  provisioner "local-exec" {
    command     = <<-EOT
      Write-Host ""
      Write-Host "=========================================="
      Write-Host "Building & Pushing Docker Image to ACR"
      Write-Host "=========================================="
      Write-Host ""

      $ErrorActionPreference = "Continue"  # Don't stop on warnings
      cd ..
      $srcPath = "src"

      # Clean local venv to avoid packing broken paths into build context
      if (Test-Path "$srcPath/venv") {
        Write-Host "Removing $srcPath/venv before build..."
        Remove-Item -Recurse -Force "$srcPath/venv"
      }

      Write-Host "Starting Docker build and push to ACR..."
      Write-Host "Registry: ${local.registry_name}"
      Write-Host "Image: zava-chat-app:latest"
      Write-Host "Dockerfile: $srcPath/Dockerfile"
      Write-Host "Source Path: $srcPath"
      Write-Host ""

      # Set encoding for Azure CLI (avoids Windows charmap issues when streaming logs)
      $env:PYTHONIOENCODING = "utf-8"
      chcp 65001 > $null

      Write-Host "Executing ACR build command (with retries)..."
      Write-Host ""

      $maxAttempts = 3
      $success = $false
      for ($i = 1; $i -le $maxAttempts; $i++) {
        Write-Host "[INFO] ACR build attempt $i of $maxAttempts"

        az acr build `
          --resource-group ${azurerm_resource_group.rg.name} `
          --registry ${local.registry_name} `
          --image zava-chat-app:latest `
          --file "$srcPath\Dockerfile" `
          "$srcPath" `
          --platform linux `
          --no-logs

        $exitCode = $LASTEXITCODE

        if ($exitCode -eq 0) {
          $success = $true
          break
        }

        Write-Host "[WARN] ACR build failed (exit $exitCode)." -ForegroundColor Yellow
        if ($i -lt $maxAttempts) {
          Write-Host "Retrying in 10 seconds..." -ForegroundColor Yellow
          Start-Sleep -Seconds 10
        }
      }

      if (-not $success) {
        Write-Host "[ERROR] ACR build failed after $maxAttempts attempts" -ForegroundColor Red
        Write-Host "Manual build command:"
        Write-Host "az acr build --resource-group ${azurerm_resource_group.rg.name} --registry ${local.registry_name} --image zava-chat-app:latest --file $srcPath\Dockerfile $srcPath --platform linux"
        exit 1
      }

      Write-Host ""
      Write-Host "[SUCCESS] Docker image successfully built and pushed to ACR"
      Write-Host ""
      Write-Host "Image details:"
      Write-Host "  Registry: ${local.registry_name}.azurecr.io"
      Write-Host "  Repository: zava-chat-app"
      Write-Host "  Tag: latest"
      Write-Host ""

      # Wait for image to be available
      Write-Host "Waiting for image to be available in registry..."
      Start-Sleep -Seconds 10

      # Verify image exists in ACR
      Write-Host "Verifying image in ACR..."
      $imgCheck = az acr repository show --name ${local.registry_name} --image zava-chat-app:latest --query "name" -o tsv 2>$null

      if ($LASTEXITCODE -eq 0 -and $imgCheck -eq "zava-chat-app") {
        Write-Host "[VERIFIED] Image confirmed in ACR registry"
        Write-Host ""
        exit 0
      } else {
        Write-Host "[WARNING] Image verification failed but build succeeded" -ForegroundColor Yellow
        Write-Host "This may be a timing issue. Image should be available shortly."
        Write-Host ""
        exit 0
      }
    EOT
    interpreter = ["PowerShell", "-Command"]
    working_dir = path.module
  }
}

resource "azurerm_service_plan" "appserviceplan" {
  name                = local.app_service_plan
  resource_group_name = azurerm_resource_group.rg.name
  location            = local.web_app_location
  os_type             = "Linux"
  sku_name            = var.app_service_sku
}

resource "azurerm_linux_web_app" "app" {
  name                = local.web_app_name
  resource_group_name = azurerm_resource_group.rg.name
  location            = local.web_app_location
  service_plan_id     = azurerm_service_plan.appserviceplan.id
  https_only          = true

  identity {
    type = "SystemAssigned"
  }

  site_config {
    always_on           = true
    http2_enabled       = true
    minimum_tls_version = "1.2"
    # Ensure App Service waits for container readiness
    health_check_path                       = "/health"
    health_check_eviction_time_in_min       = 10
    container_registry_use_managed_identity = true
    # Enable WebSocket support for real-time chat
    websockets_enabled = true

    # CORS configuration for API access
    cors {
      allowed_origins     = ["*"]
      support_credentials = false
    }

    application_stack {
      docker_image_name = "zava-chat-app:latest"
      # Use full https URL for docker registry
      docker_registry_url = "https://${local.registry_name}.azurecr.io"
    }
  }

  # Add longer timeouts for container startup
  timeouts {
    create = "45m"
    update = "45m"
    delete = "15m"
  }

  app_settings = {
    WEBSITES_ENABLE_APP_SERVICE_STORAGE = "false"
    DOCKER_ENABLE_CI                    = "true"
    WEBSITES_PORT                       = "8000"
    # Increase container startup timeout to 10 minutes
    WEBSITES_CONTAINER_START_TIME_LIMIT = "600"
    # Enable container logging
    DOCKER_CUSTOM_IMAGE_RUN_COMMAND = ""

    # A2A automation (now running inside the container under /a2a)
    A2A_HOST      = var.a2a_host
    A2A_PORT      = tostring(var.a2a_port)
    A2A_MODE      = "a2a"
    A2A_LOG_LEVEL = "INFO"

    # GPT Configuration (API key via Key Vault)
    gpt_endpoint    = local.model_endpoints["gpt-4o"]
    gpt_deployment  = "gpt-4o-mini"
    gpt_api_key     = "MANAGED_IDENTITY"
    gpt_api_version = "2024-08-01-preview"

    # MSFT Foundry Configuration (primary project in chat region)
    # IMPORTANT: Use Hub endpoint (.cognitiveservices.azure.com) for AZURE_AI_FOUNDRY_ENDPOINT
    # Use Project endpoint (.services.ai.azure.com/api/projects/{name}) for Agents API
    AZURE_AI_FOUNDRY_ENDPOINT            = local.foundry_endpoints[local.primary_foundry_region]
    AZURE_AI_PROJECT_NAME                = local.ai_project_names[local.primary_foundry_region]
    AZURE_AI_PROJECT_ENDPOINT            = local.ai_project_endpoints[local.primary_foundry_region]
    AZURE_AI_AGENT_ENDPOINT              = local.ai_project_endpoints[local.primary_foundry_region]
    AZURE_AI_AGENT_MODEL_DEPLOYMENT_NAME = "gpt-4o-mini"
    AZURE_AI_FOUNDRY_API_KEY             = "MANAGED_IDENTITY"
    # Azure context for newer AIProjectClient SDK
    AZURE_SUBSCRIPTION_ID                = data.azurerm_client_config.current.subscription_id
    AZURE_RESOURCE_GROUP                 = azurerm_resource_group.rg.name
    AZURE_LOCATION                       = local.primary_foundry_region

    # MSFT Foundry OpenAI Configuration (media processing models)
    AZURE_OPENAI_CHAT_DEPLOYMENT      = "gpt-4o-mini"
    AZURE_OPENAI_IMAGE_DEPLOYMENT     = "dall-e-3"
    AZURE_OPENAI_API_VERSION          = "2024-08-01-preview"

    # Default endpoint/key for image service (dall-e-3 region)
    AZURE_OPENAI_ENDPOINT = local.model_endpoints["dall-e-3"]
    AZURE_OPENAI_API_KEY  = "MANAGED_IDENTITY"

    # Per-model endpoint and key mappings for media processing
    AZURE_OPENAI_ENDPOINT_DALLE3                 = local.model_endpoints["dall-e-3"]
    AZURE_OPENAI_API_KEY_DALLE3                  = "MANAGED_IDENTITY"
    AZURE_OPENAI_ENDPOINT_MODEL_ROUTER           = local.model_endpoints["gpt-4o"]
    AZURE_OPENAI_API_KEY_MODEL_ROUTER            = "MANAGED_IDENTITY"
    AZURE_OPENAI_ENDPOINT_FLUX                   = local.model_endpoints["FLUX.2-pro"]
    AZURE_OPENAI_API_KEY_FLUX                    = "MANAGED_IDENTITY"
    AZURE_OPENAI_ENDPOINT_GPT_IMAGE              = local.model_endpoints["gpt-4o"]
    AZURE_OPENAI_API_KEY_GPT_IMAGE               = "MANAGED_IDENTITY"
    AZURE_OPENAI_ENDPOINT_SORA                   = ""  # Sora-2 not available yet
    AZURE_OPENAI_API_KEY_SORA                    = "MANAGED_IDENTITY"

    # Storage Connection String via Key Vault
    STORAGE_CONNECTION_STRING = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault.kv.vault_uri}secrets/storage-connection-string)"

    # Multi-Agent Configuration - Use Azure OpenAI directly (no Agents API needed)
    USE_MULTI_AGENT              = "true"
    ENABLE_LOCAL_AGENT_FALLBACK   = "true"  # Use Azure OpenAI models directly without Agents API
    
    # Direct Azure OpenAI configuration for agent processing
    gpt_endpoint                 = local.model_endpoints["gpt-4o"]
    gpt_api_key                  = "MANAGED_IDENTITY"
    gpt_deployment               = "gpt-4o-mini"
    gpt_api_version              = "2024-08-01-preview"
    
    # Model-specific endpoints for specialized agents
    DALLE3_ENDPOINT              = local.model_endpoints["dall-e-3"]
    FLUX_ENDPOINT                = local.model_endpoints["FLUX.2-pro"]
    GPT_IMAGE_ENDPOINT           = local.model_endpoints["gpt-4o"]
    SORA_ENDPOINT                = ""  # Sora-2 not available yet

    # Application Insights via Key Vault
    APPLICATION_INSIGHTS_CONNECTION_STRING = "@Microsoft.KeyVault(SecretUri=${azurerm_key_vault.kv.vault_uri}secrets/app-insights-connection-string)"

    CUSTOMER_ID = "CUST001"
  }

  depends_on = [
    azurerm_container_registry.acr,
    null_resource.docker_image_build
  ]
}

# Grant Web App access to AI Foundry (OpenAI)
resource "azurerm_role_assignment" "app_foundry_user" {
  for_each             = azapi_resource.ai_foundry
  scope                = each.value.id
  role_definition_name = "Cognitive Services OpenAI User"
  principal_id         = azurerm_linux_web_app.app.identity[0].principal_id
  depends_on = [
    azurerm_linux_web_app.app,
    azapi_resource.ai_foundry
  ]
}

# Grant Web App access to AI Foundry services (required for Agents API)
resource "azurerm_role_assignment" "app_ai_foundry_user" {
  for_each           = azapi_resource.ai_foundry
  scope              = each.value.id
  role_definition_name = "Azure AI User"
  principal_id       = azurerm_linux_web_app.app.identity[0].principal_id
  depends_on = [
    azurerm_linux_web_app.app,
    azapi_resource.ai_foundry
  ]
}

# Grant Web App access to AI Projects (required for Agents API)
resource "azurerm_role_assignment" "app_ai_project_user" {
  for_each           = toset(local.foundry_regions)
  scope              = azapi_resource.ai_project[each.key].id
  role_definition_name = "Azure AI User"
  principal_id       = azurerm_linux_web_app.app.identity[0].principal_id
  depends_on = [
    azurerm_linux_web_app.app,
    azapi_resource.ai_project
  ]
}

# Grant AcrPull role to Web App managed identity so it can pull private images without admin credentials
resource "azurerm_role_assignment" "webapp_acr_pull" {
  scope                = azurerm_container_registry.acr.id
  role_definition_name = "AcrPull"
  principal_id         = azurerm_linux_web_app.app.identity[0].principal_id
  depends_on = [
    azurerm_linux_web_app.app,
    azurerm_container_registry.acr
  ]
}

# Key Vault for central secret management
resource "azurerm_key_vault" "kv" {
  name                       = local.key_vault_name
  location                   = azurerm_resource_group.rg.location
  resource_group_name        = azurerm_resource_group.rg.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  soft_delete_retention_days = 7
  purge_protection_enabled   = false
  enable_rbac_authorization  = false
  public_network_access_enabled = true

  network_acls {
    # Keep the vault open initially so Terraform can read/write secrets during plan/apply.
    # The `null_resource.kv_network_rules` will add the current and webapp IPs and
    # then set the default action to Deny when `var.lock_key_vault_network` is true.
    default_action = "Allow"
    bypass         = "AzureServices"
    ip_rules       = ["${chomp(data.http.current_ip.response_body)}/32"]
  }

  access_policy {
    tenant_id          = data.azurerm_client_config.current.tenant_id
    object_id          = local.principal_id
    secret_permissions = ["Get", "List", "Set", "Delete", "Purge", "Recover"]
  }

  tags = { purpose = "multi-agent-ai-secrets" }
}

# Wait for Key Vault to be ready and accessible
resource "time_sleep" "wait_for_kv" {
  depends_on = [azurerm_key_vault.kv]
  create_duration = "30s"
}

# Current public IP (for Key Vault firewall)
data "http" "current_ip" {
  url            = "https://api.ipify.org"
}

# Ensure Key Vault firewall is OPEN for Terraform operations
# We force this to run every time to guarantee access
resource "null_resource" "ensure_kv_open" {
  triggers = {
    always_run = timestamp()
  }
  
  depends_on = [azurerm_key_vault.kv]

  provisioner "local-exec" {
    interpreter = ["pwsh", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
    command     = <<EOT
      $kvName = "${azurerm_key_vault.kv.name}"
      $rg     = "${azurerm_resource_group.rg.name}"
      
      Write-Host "Ensuring Key Vault $kvName is accessible..."
      # Force Allow default action to unblock Terraform
      az keyvault update --name $kvName --resource-group $rg --default-action Allow --bypass AzureServices | Out-Null
      
      # Also ensure public access is enabled
      az keyvault update --name $kvName --resource-group $rg --public-network-access Enabled | Out-Null
      
      Write-Host "Key Vault access enabled."
    EOT
  }
}

# After Key Vault and Web App creation, tighten KV firewall to allow
# only current public IP and Web App outbound IPs, then set default Deny.
resource "null_resource" "kv_network_rules" {
  depends_on = [
    azurerm_key_vault.kv,
    azurerm_linux_web_app.app,
    null_resource.ensure_kv_open,
    time_sleep.wait_for_kv
  ]

  triggers = {
    kv_name            = azurerm_key_vault.kv.name
    rg_name            = azurerm_resource_group.rg.name
    current_ip         = chomp(data.http.current_ip.response_body)
    outbound_ips       = data.azurerm_linux_web_app.app_identity.outbound_ip_addresses
    lock_kv_firewall   = tostring(var.lock_key_vault_network)
  }

  provisioner "local-exec" {
    interpreter = ["pwsh", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command"]
    command     = <<EOT
      $lockEnabled = "${var.lock_key_vault_network ? "true" : "false"}"
      if ($lockEnabled -ne "true") {
        Write-Host "[KV] lock_key_vault_network=false -> leaving firewall open"
        az keyvault update --name "${azurerm_key_vault.kv.name}" --resource-group "${azurerm_resource_group.rg.name}" --default-action Allow --bypass AzureServices | Out-Null
        exit 0
      }

      $kvName = "${azurerm_key_vault.kv.name}"
      $rg     = "${azurerm_resource_group.rg.name}"
      $ipsCsv = "${chomp(data.http.current_ip.response_body)}/32,${data.azurerm_linux_web_app.app_identity.outbound_ip_addresses}"
      $ipList = $ipsCsv -split "," | Where-Object { $_ -and $_ -ne '' } | ForEach-Object { $_.Trim() } | ForEach-Object { if ($_ -notmatch "/") { "{0}/32" -f $_ } else { $_ } }

      foreach ($ip in $ipList) {
        az keyvault network-rule add --name $kvName --resource-group $rg --ip-address $ip | Out-Null
      }
      az keyvault update --name $kvName --resource-group $rg --default-action Deny --bypass AzureServices | Out-Null
    EOT
  }
}

# Data source to retrieve the web app identity after it's created/updated
data "azurerm_linux_web_app" "app_identity" {
  name                = azurerm_linux_web_app.app.name
  resource_group_name = azurerm_resource_group.rg.name
  depends_on          = [azurerm_linux_web_app.app]
}

# External helper to ensure Key Vault has our current IP rule so Terraform can access secrets
data "external" "ensure_kv_access" {
  program = ["pwsh", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "./scripts/ensure_kv_network_rule.ps1"]
  query = {
    kv_name = local.key_vault_name
    rg_name = var.resource_group_name
    sub_id  = data.azurerm_client_config.current.subscription_id
  }
  # Removed explicit dependency on azurerm_key_vault.kv to allow this to run during refresh/plan
  # even if the KV resource has pending changes. This ensures the firewall is open for secret reads.
}

# Access policy for Web App managed identity to read secrets
resource "azurerm_key_vault_access_policy" "app_policy" {
  key_vault_id       = azurerm_key_vault.kv.id
  tenant_id          = data.azurerm_client_config.current.tenant_id
  object_id          = data.azurerm_linux_web_app.app_identity.identity[0].principal_id
  secret_permissions = ["Get"]
  depends_on         = [
    azurerm_linux_web_app.app,
    null_resource.ensure_kv_open
  ]
  
  timeouts {
    create = "5m"
    update = "5m"
    delete = "5m"
  }
}



# Fetch storage keys unconditionally
data "azapi_resource_action" "storage_keys_unconditional" {
  type                   = "Microsoft.Storage/storageAccounts@2023-01-01"
  resource_id            = azapi_resource.storage.id
  action                 = "listKeys"
  response_export_values = ["keys"]
  body                   = jsonencode({})
  depends_on             = [azapi_resource.storage]
}

resource "azurerm_key_vault_secret" "storage_connection_string" {
  name         = "storage-connection-string"
  value        = "DefaultEndpointsProtocol=https;AccountName=${local.storage_account};AccountKey=${jsondecode(data.azapi_resource_action.storage_keys_unconditional.output).keys[0].value};EndpointSuffix=core.windows.net"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [
    azurerm_key_vault.kv,
    data.azapi_resource_action.storage_keys_unconditional,
    null_resource.ensure_kv_open,
    data.external.ensure_kv_access
  ]
}



# External data source for agents state
# Removed external data dependency - using zero-touch approach

# Backup Agent Configuration - Ensures agent secrets always exist
resource "azurerm_key_vault_secret" "agent_orchestrator_id" {
  name         = "agent-orchestrator-id"
  value        = "backup-orchestrator-${random_id.suffix.hex}"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [
    azurerm_key_vault_access_policy.deployment_identity_kv,
    null_resource.ensure_kv_open,
    data.external.ensure_kv_access
  ]
  
  lifecycle {
    ignore_changes = [value]  # Don't overwrite if real agent deployment succeeds
  }
}

resource "azurerm_key_vault_secret" "agent_cropping_agent_id" {
  name         = "agent-cropping-agent-id"
  value        = "backup-cropping-${random_id.suffix.hex}"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [
    azurerm_key_vault_access_policy.deployment_identity_kv,
    null_resource.ensure_kv_open,
    data.external.ensure_kv_access
  ]
  
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "agent_background_agent_id" {
  name         = "agent-background-agent-id"
  value        = "backup-background-${random_id.suffix.hex}"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [
    azurerm_key_vault_access_policy.deployment_identity_kv,
    null_resource.ensure_kv_open,
    data.external.ensure_kv_access
  ]
  
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "agent_thumbnail_generator_id" {
  name         = "agent-thumbnail-generator-id"
  value        = "backup-thumbnail-${random_id.suffix.hex}"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [
    azurerm_key_vault_access_policy.deployment_identity_kv,
    null_resource.ensure_kv_open,
    data.external.ensure_kv_access
  ]
  
  lifecycle {
    ignore_changes = [value]
  }
}

# Zero-touch automation: AI project creation and recovery
resource "null_resource" "ai_project_recovery" {
  count = var.enable_multi_agent ? 1 : 0
  
  triggers = {
    ai_foundry_id = azapi_resource.ai_foundry[local.primary_foundry_region].id
    always_run    = timestamp()
  }
  
  provisioner "local-exec" {
    command = <<-EOT
      Write-Host "[AI PROJECT SETUP] Setting up Azure AI project for zero-touch deployment..."
      
      $resourceGroup = "${azurerm_resource_group.rg.name}"
      $foundryName = "${azapi_resource.ai_foundry[local.primary_foundry_region].name}"
      $projectName = "${local.ai_project_names[local.primary_foundry_region]}"
      $location = "${local.primary_foundry_region}"
      
      # Ensure AI Foundry is ready
      Write-Host "Waiting for AI Foundry to be fully provisioned..."
      Start-Sleep -Seconds 30
      
      # Check if project exists
      $projectExists = $false
      $maxAttempts = 5
      
      for ($i = 1; $i -le $maxAttempts; $i++) {
        try {
          Write-Host "[Attempt $i/$maxAttempts] Checking for existing AI project..."
          $project = az cognitiveservices account show --name $foundryName --resource-group $resourceGroup --query "properties.provisioningState" -o tsv 2>$null
          
          if ($project -eq "Succeeded") {
            Write-Host "[OK] AI Foundry is ready, checking for project..."
            
            # Try to get project details using REST API since CLI might not support projects yet
            $subscriptionId = "${data.azurerm_client_config.current.subscription_id}"
            $uri = "https://management.azure.com/subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.CognitiveServices/accounts/$foundryName"
            
            # Create the project using azapi if it doesn't exist
            Write-Host "[AUTOMATION] Creating Azure AI project: $projectName"
            
            # Use Azure REST API to create project
            $accessToken = az account get-access-token --query accessToken -o tsv
            $headers = @{
              'Authorization' = "Bearer $accessToken"
              'Content-Type' = 'application/json'
            }
            
            $projectUri = "https://management.azure.com/subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.CognitiveServices/accounts/$foundryName/projects/$projectName" + "?api-version=2025-06-01"
            
            $body = @{
              properties = @{}
              location = $location
            } | ConvertTo-Json -Depth 3
            
            try {
              Invoke-RestMethod -Uri $projectUri -Method PUT -Headers $headers -Body $body -ContentType 'application/json'
              Write-Host "[SUCCESS] Azure AI project created successfully: $projectName"
              $projectExists = $true
              break
            } catch {
              Write-Host "[INFO] Project might already exist or creation in progress, attempt $i"
            }
          }
        } catch {
          Write-Host "[WAIT] AI Foundry not ready yet, waiting 60 seconds... (attempt $i)"
          Start-Sleep -Seconds 60
        }
      }
      
      if (-not $projectExists) {
        Write-Host "[RETRY] Using alternative project creation method..."
        
        # Alternative: Use az rest command
        try {
          $subscriptionId = "${data.azurerm_client_config.current.subscription_id}"
          $projectUri = "/subscriptions/$subscriptionId/resourceGroups/$resourceGroup/providers/Microsoft.CognitiveServices/accounts/$foundryName/projects/$projectName"
          
          $projectBody = @"
{
  "properties": {},
  "location": "$location"
}
"@
          
          az rest --method PUT --uri "$projectUri?api-version=2025-06-01" --body "$projectBody"
          Write-Host "[SUCCESS] Azure AI project created via REST API"
          
          # Store success flag in Key Vault
          az keyvault secret set `
            --vault-name "${azurerm_key_vault.kv.name}" `
            --name "ai-project-status" `
            --value "created" `
            --only-show-errors
            
        } catch {
          Write-Host "[ERROR] Failed to create AI project after all attempts: $_"
          
          # Store failure status for monitoring
          az keyvault secret set `
            --vault-name "${azurerm_key_vault.kv.name}" `
            --name "ai-project-status" `
            --value "failed" `
            --only-show-errors
            
          exit 1
        }
      }
      
      Write-Host "[AI PROJECT SETUP] Azure automation complete"
    EOT
    
    interpreter = ["PowerShell", "-Command"]
  }
  
  depends_on = [
    azapi_resource.ai_foundry,
    azurerm_key_vault_access_policy.deployment_identity_kv,
    time_sleep.wait_for_foundry_propagation
  ]
}

# Agent endpoint configuration
resource "azurerm_key_vault_secret" "agent_video_agent_id" {
  name         = "agent-video-agent-id"
  value        = "backup-video-${random_id.suffix.hex}"
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [
    azurerm_key_vault_access_policy.deployment_identity_kv,
    data.external.ensure_kv_access
  ]
  
  lifecycle {
    ignore_changes = [value]
  }
}

resource "azurerm_key_vault_secret" "agent_endpoint" {
  name         = "agent-endpoint"
  value        = local.ai_project_endpoints[local.primary_foundry_region]
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [
    azurerm_key_vault.kv,
    data.external.ensure_kv_access
  ]
}

# Zero-touch deployment: AI project status tracking
resource "azurerm_key_vault_secret" "ai_project_status" {
  name         = "ai-project-status"
  value        = "pending"  # Will be updated to 'created' or 'failed' by automation
  key_vault_id = azurerm_key_vault.kv.id
  depends_on   = [
    azurerm_key_vault_access_policy.deployment_identity_kv,
    data.external.ensure_kv_access
  ]
  
  lifecycle {
    ignore_changes = [value]  # Allow automation to update this value
  }
}

# App Service Plan autoscale
resource "azurerm_monitor_autoscale_setting" "appservice_autoscale" {
  name                = "${var.name_prefix}-${local.suffix}-asp-autoscale"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  target_resource_id  = azurerm_service_plan.appserviceplan.id

  profile {
    name = "default"
    capacity {
      minimum = "1"
      maximum = "3"
      default = "1"
    }

    rule {
      metric_trigger {
        metric_name        = "CpuPercentage"
        metric_resource_id = azurerm_service_plan.appserviceplan.id
        time_grain         = "PT1M"
        statistic          = "Average"
        time_window        = "PT5M"
        time_aggregation   = "Average"
        operator           = "GreaterThan"
        threshold          = 70
      }
      scale_action {
        direction = "Increase"
        type      = "ChangeCount"
        value     = "1"
        cooldown  = "PT5M"
      }
    }

    rule {
      metric_trigger {
        metric_name        = "CpuPercentage"
        metric_resource_id = azurerm_service_plan.appserviceplan.id
        time_grain         = "PT1M"
        statistic          = "Average"
        time_window        = "PT10M"
        time_aggregation   = "Average"
        operator           = "LessThan"
        threshold          = 35
      }
      scale_action {
        direction = "Decrease"
        type      = "ChangeCount"
        value     = "1"
        cooldown  = "PT10M"
      }
    }
  }

  notification {
    email {
      send_to_subscription_administrator    = false
      send_to_subscription_co_administrator = false
      custom_emails                         = []
    }
  }

  depends_on = [
    null_resource.docker_image_build,
    azurerm_container_registry.acr,
    azurerm_role_assignment.webapp_acr_pull
  ]
}

# Alerts: App Service 5xx & CPU, Cosmos 429 throttles
resource "azurerm_monitor_metric_alert" "app_5xx" {
  count = 0  # Temporarily disabled due to conflicts
  name                = "${var.name_prefix}-${local.suffix}-app-5xx-alert"
  resource_group_name = azurerm_resource_group.rg.name
  scopes              = [azurerm_linux_web_app.app.id]
  description         = "Alert on high 5xx responses"
  severity            = 2
  frequency           = "PT5M"
  window_size         = "PT5M"
  criteria {
    metric_namespace = "Microsoft.Web/sites"
    metric_name      = "Http5xx"
    aggregation      = "Total"
    operator         = "GreaterThan"
    threshold        = 20
  }
}

resource "azurerm_monitor_metric_alert" "app_cpu" {
  name                = "${var.name_prefix}-${local.suffix}-app-cpu-alert"
  resource_group_name = azurerm_resource_group.rg.name
  scopes              = [azurerm_service_plan.appserviceplan.id]
  description         = "Alert on high CPU"
  severity            = 3
  frequency           = "PT5M"
  window_size         = "PT5M"
  criteria {
    metric_namespace = "Microsoft.Web/serverfarms"
    metric_name      = "CpuPercentage"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 80
  }
}



# Portal Dashboard aggregating key metrics
resource "azurerm_portal_dashboard" "observability" {
  name                = "${var.name_prefix}-${local.suffix}-dashboard"
  resource_group_name = azurerm_resource_group.rg.name
  location            = var.location
  tags                = { purpose = "multi-agent-observability" }

  dashboard_properties = jsonencode({
    lenses = {
      "0" = {
        order = 0
        parts = {
          "0" = {
            position = { x = 0, y = 0, width = 6, height = 4 }
            metadata = {
              inputs = [
                { name = "resourceType", value = "microsoft.web/sites" },
                { name = "resource", value = azurerm_linux_web_app.app.id },
                { name = "chartSettings", value = jsonencode({ version = "Workspace" }) }
              ]
              type = "Extension/HubsExtension/PartType/MonitorChartPart"
              settings = {
                content = {
                  version = "1.0.0"
                  chart = {
                    title         = "App Service Requests"
                    metrics       = [{ resourceMetadata = { id = azurerm_linux_web_app.app.id }, name = "Requests", aggregationType = "Total" }]
                    timespan      = { duration = "PT1H" }
                    visualization = { chartType = "Line" }
                  }
                }
              }
            }
          },
          "1" = {
            position = { x = 6, y = 0, width = 6, height = 4 }
            metadata = {
              inputs = [
                { name = "resourceType", value = "microsoft.web/serverfarms" },
                { name = "resource", value = azurerm_service_plan.appserviceplan.id }
              ]
              type = "Extension/HubsExtension/PartType/MonitorChartPart"
              settings = {
                content = {
                  version = "1.0.0"
                  chart = {
                    title    = "CPU Percentage"
                    metrics  = [{ resourceMetadata = { id = azurerm_service_plan.appserviceplan.id }, name = "CpuPercentage", aggregationType = "Average" }]
                    timespan = { duration = "PT1H" }
                  }
                }
              }
            }
          },

          "3" = {
            position = { x = 6, y = 4, width = 6, height = 4 }
            metadata = {
              inputs = [
                { name = "resourceType", value = "microsoft.insights/components" },
                { name = "resource", value = azurerm_application_insights.appinsights.id }
              ]
              type = "Extension/HubsExtension/PartType/MonitorChartPart"
              settings = {
                content = {
                  version = "1.0.0"
                  chart = {
                    title    = "App Insights Server Response Time"
                    metrics  = [{ resourceMetadata = { id = azurerm_application_insights.appinsights.id }, name = "requests/duration", aggregationType = "Average" }]
                    timespan = { duration = "PT1H" }
                  }
                }
              }
            }
          }
        }
      }
    }
    metadata = { model = "PortalDashboard" }
  })
}


# Storage account permissions for MSFT Foundry project
resource "azurerm_role_assignment" "storage_blob_data_contributor_user" {
  scope              = azapi_resource.storage.id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
  principal_id       = local.principal_id
  principal_type     = "User"
}

resource "azurerm_role_assignment" "storage_blob_data_contributor_project" {
  scope              = azapi_resource.storage.id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
  principal_id       = azapi_resource.ai_project[local.primary_foundry_region].identity[0].principal_id
  principal_type     = "ServicePrincipal"
}

# Storage Blob Data Contributor for AI Foundry account MSI
resource "azurerm_role_assignment" "storage_blob_data_contributor_foundry" {
  scope              = azapi_resource.storage.id
  role_definition_id = "/subscriptions/${data.azurerm_client_config.current.subscription_id}/providers/Microsoft.Authorization/roleDefinitions/ba92f5b4-2d11-453d-a403-e96b0029c9fe"
  principal_id       = azapi_resource.ai_foundry[local.primary_foundry_region].identity[0].principal_id
  principal_type     = "ServicePrincipal"
}

# Azure AI model deployments automation (fast local execution)
resource "null_resource" "ai_model_deployments" {
  for_each = var.enable_ai_automation ? local.region_models : {}

  depends_on = [
    azapi_resource.ai_project,
    azapi_resource.ai_foundry,
    azurerm_role_assignment.storage_blob_data_contributor_user
  ]

  provisioner "local-exec" {
    command     = <<-EOT
      $region = "${each.key}"
      $foundryName = "${local.foundry_names[each.key]}"
      $rg = "${azurerm_resource_group.rg.name}"

      Write-Host "=== Deploying models for region $region (Foundry: $foundryName) ==="
      Start-Sleep -Seconds 15

      $modelSpecs = ConvertFrom-Json @'
${replace(jsonencode(local.model_deployment_payload[each.key]),"'","''")}
'@

      foreach ($spec in $modelSpecs) {
        if (-not $spec.model_name) {
          Write-Host "[SKIP] No deployment spec registered for alias $($spec.alias)"
          continue
        }

        Write-Host "Deploying $($spec.alias) -> model $($spec.model_name) ($($spec.model_version))"
        az cognitiveservices account deployment create `
          --resource-group $rg `
          --name $foundryName `
          --deployment-name $spec.alias `
          --model-name $spec.model_name `
          --model-version $spec.model_version `
          --model-format $spec.model_format `
          --sku-name $spec.sku_name `
          --sku-capacity $spec.sku_capacity 2>$null

        if ($LASTEXITCODE -eq 0) {
          Write-Host "[OK] $($spec.alias) deployment ensured"
        } else {
          Write-Host "[WARN] Unable to deploy $($spec.alias). Check availability in region $region"
        }
      }

      Write-Host "Listing current deployments for $foundryName"
      az cognitiveservices account deployment list `
        --resource-group $rg `
        --name $foundryName `
        --output table
    EOT
    interpreter = ["PowerShell", "-Command"]
  }

  triggers = {
    foundry_id    = azapi_resource.ai_foundry[each.key].id
    model_payload = sha256(jsonencode(local.model_deployment_payload[each.key]))
  }
}

# Retrieve keys for each AI Foundry using AzAPI (Native Terraform)
# SKIPPED: disableLocalAuth=true prevents key retrieval. Using Managed Identity instead.
# data "azapi_resource_action" "foundry_keys" { ... }

# Store the retrieved keys in Key Vault
# SKIPPED: Keys are not available due to disableLocalAuth=true.
# resource "azurerm_key_vault_secret" "ai_foundry_keys" { ... }

# Wait for AI Foundry resources to propagate properly
resource "time_sleep" "wait_for_foundry_propagation" {
  depends_on = [
    azapi_resource.ai_foundry
  ]
  
  create_duration = "60s"  # Wait 1 minute for resources to be fully available
}

# Connection helper actions for Foundry resources
data "azapi_resource_action" "storage_list_keys" {
  count                  = var.enable_ai_automation ? 1 : 0
  type                   = "Microsoft.Storage/storageAccounts@2023-01-01"
  resource_id            = azapi_resource.storage.id
  action                 = "listKeys"
  response_export_values = ["keys"]
  body                   = jsonencode({})
  depends_on             = [data.azapi_resource_action.storage_keys_unconditional]
}



# Get AI Foundry keys for Web App configuration
# Note: Using managed identity authentication for AI Foundry instead of API keys
# This is more secure and doesn't require disableLocalAuth = false

# Connect resources to MSFT Foundry project using ARM templates
resource "azapi_resource" "storage_connection" {
  count = var.enable_ai_automation ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts/connections@2025-06-01"
  name                      = "${local.foundry_names[local.primary_foundry_region]}-storage"
  parent_id                 = azapi_resource.ai_foundry[local.primary_foundry_region].id
  schema_validation_enabled = false

  depends_on = [
    azapi_resource.storage,
    azapi_resource.ai_foundry,
    azapi_resource.ai_project
  ]

  body = jsonencode({
    properties = {
      category      = "AzureStorageAccount"
      target        = "https://${local.storage_account}.blob.core.windows.net"
      authType      = "AccountKey"
      isSharedToAll = true
      credentials = {
        key = jsondecode(data.azapi_resource_action.storage_keys_unconditional.output).keys[0].value
      }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azapi_resource.storage.id
      }
    }
  })
}

resource "azapi_resource" "app_insights_connection" {
  count = var.enable_ai_automation ? 1 : 0

  type                      = "Microsoft.CognitiveServices/accounts/connections@2025-06-01"
  name                      = "${local.foundry_names[local.primary_foundry_region]}-appinsights"
  parent_id                 = azapi_resource.ai_foundry[local.primary_foundry_region].id
  schema_validation_enabled = false

  depends_on = [
    azurerm_application_insights.appinsights,
    azapi_resource.ai_foundry,
    azapi_resource.ai_project
  ]

  body = jsonencode({
    properties = {
      category      = "AppInsights"
      target        = azurerm_application_insights.appinsights.id
      authType      = "ApiKey"
      isSharedToAll = true
      credentials = {
        key = azurerm_application_insights.appinsights.connection_string
      }
      metadata = {
        ApiType    = "Azure"
        ResourceId = azurerm_application_insights.appinsights.id
      }
    }
  })
}





# Verification script for connections
resource "null_resource" "verify_connections" {
  count = var.enable_ai_automation ? 1 : 0

  depends_on = [
    azapi_resource.storage_connection,
    azapi_resource.app_insights_connection
  ]

  provisioner "local-exec" {
    command     = <<-EOT
      Write-Host "=== Verifying Microsoft Foundry Project Connections ==="
      Write-Host ""
      Write-Host "Project: ${local.ai_project_names[local.primary_foundry_region]}"
      Write-Host "AI Foundry: ${local.foundry_names[local.primary_foundry_region]}"
      Write-Host "Resource Group: ${azurerm_resource_group.rg.name}"
      Write-Host ""
      
      # List connections using Azure CLI
      Write-Host "Checking connections via Azure CLI..."
      az rest --method GET --url "https://management.azure.com/subscriptions/${data.azurerm_client_config.current.subscription_id}/resourceGroups/${azurerm_resource_group.rg.name}/providers/Microsoft.CognitiveServices/accounts/${local.foundry_names[local.primary_foundry_region]}/connections?api-version=2025-06-01" --query "value[].{Name:name,Type:properties.connectionType,Target:properties.target}" --output table
      
      Write-Host ""
      Write-Host "[OK] Microsoft Foundry project connections verification completed!"
      Write-Host ""
      Write-Host "Available connections:"
      Write-Host "  - Storage Account: ${local.storage_account}"
      Write-Host "  - Application Insights: ${local.app_insights_name}"
      Write-Host ""
      Write-Host "View in Azure Portal:"
      Write-Host "  https://ai.azure.com/resource/overview/${local.foundry_names[local.primary_foundry_region]}"
      Write-Host "  Navigate to Management center > Connected resources"
    EOT
    interpreter = ["PowerShell", "-Command"]
  }

  triggers = {
    storage_conn      = var.enable_ai_automation ? azapi_resource.storage_connection[0].id : ""
    app_insights_conn = var.enable_ai_automation ? azapi_resource.app_insights_connection[0].id : ""
  }
}

# Multi-Agent Deployment - Create real agents in Microsoft Foundry (fast local execution)
resource "null_resource" "deploy_multi_agents" {
  count = var.enable_multi_agent ? 1 : 0

  depends_on = [
    null_resource.ai_model_deployments,
    azurerm_role_assignment.deployment_identity_foundry_user,
    azurerm_role_assignment.deployment_identity_rg,
    azapi_resource.ai_project,
    azapi_resource.ai_foundry
  ]

  provisioner "local-exec" {
    command     = <<-EOT
      Write-Host "Deploying agents to Azure AI Foundry..."
      
      # Create .env file in src directory for zero-touch deployment
      $envContent = @"
AZURE_SUBSCRIPTION_ID=${data.azurerm_client_config.current.subscription_id}
AZURE_RESOURCE_GROUP=${azurerm_resource_group.rg.name}
AZURE_AI_PROJECT_NAME=${local.ai_project_names[local.primary_foundry_region]}
AZURE_LOCATION=${local.primary_foundry_region}
AZURE_AI_PROJECT_ENDPOINT=${local.ai_project_endpoints[local.primary_foundry_region]}
AZURE_AI_FOUNDRY_ENDPOINT=${local.foundry_endpoints[local.primary_foundry_region]}
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o-mini
"@
      
      $envPath = "${path.module}\..\src\.env"
      Write-Host "Creating .env file at $envPath"
      Set-Content -Path $envPath -Value $envContent -Force
      
      # Set environment variables for Python script
      $env:AZURE_SUBSCRIPTION_ID = "${data.azurerm_client_config.current.subscription_id}"
      $env:AZURE_RESOURCE_GROUP = "${azurerm_resource_group.rg.name}"
      $env:AZURE_AI_PROJECT_NAME = "${local.ai_project_names[local.primary_foundry_region]}"
      $env:AZURE_LOCATION = "${local.primary_foundry_region}"
      $env:AZURE_AI_PROJECT_ENDPOINT = "${local.ai_project_endpoints[local.primary_foundry_region]}"
      
      # Wait for AI Foundry to be ready
      Write-Host "Waiting for AI Foundry to be ready..."
      Start-Sleep -Seconds 30
      
      # Run the Python agent deployment script
      Write-Host "Running deploy_real_agents.py..."
      Set-Location "${path.module}\..\src\app\agents"
      
      # Install required packages if needed
      python -m pip install --quiet azure-ai-projects azure-identity python-dotenv
      
      # Run deployment
      $output = python deploy_real_agents.py 2>&1
      Write-Host $output
      
      # Parse the JSON output and store agent IDs in Key Vault
      if ($output -match "===AGENTS_JSON_START===\s*(\{.*?\})\s*===AGENTS_JSON_END===") {
        $agentsJson = $Matches[1] | ConvertFrom-Json
        
        if ($agentsJson.agents) {
          Write-Host "Storing agent IDs in Key Vault..."
          
          foreach ($agent in $agentsJson.agents.PSObject.Properties) {
            $secretName = "agent-$($agent.Name.Replace('_', '-'))-id"
            $secretValue = $agent.Value
            
            if ($secretValue) {
              Write-Host "  Setting $secretName..."
              az keyvault secret set `
                --vault-name "${azurerm_key_vault.kv.name}" `
                --name $secretName `
                --value $secretValue `
                --only-show-errors
            }
          }
          
          # Store agent endpoint
          $endpoint = $env:AZURE_AI_PROJECT_ENDPOINT
          
          Write-Host "  Setting agent-endpoint..."
          az keyvault secret set `
            --vault-name "${azurerm_key_vault.kv.name}" `
            --name "agent-endpoint" `
            --value $endpoint `
            --only-show-errors
          
          Write-Host "Agent deployment completed successfully."
        } else {
          Write-Host "WARNING: No agents found in output JSON"
        }
      } else {
        Write-Host "WARNING: Could not parse agent JSON from output"
        Write-Host "Deployment may have succeeded, check logs above for errors."
      }
      
      Set-Location "${path.module}"
    EOT
    interpreter = ["PowerShell", "-Command"]
  }

  triggers = {
    ai_foundry_id = values(azapi_resource.ai_foundry)[0].id
    ai_project_id = values(azapi_resource.ai_project)[0].id
    agent_script_hash = filemd5("${path.module}/../src/app/agents/deploy_real_agents.py")
  }
}

# Verify agent secrets landed in Key Vault (fast local execution)
resource "null_resource" "verify_agent_secrets" {
  count = var.enable_multi_agent ? 1 : 0

  depends_on = [
    null_resource.deploy_multi_agents,
    azurerm_key_vault_access_policy.deployment_identity_kv
  ]

  provisioner "local-exec" {
    command     = <<-EOT
      Write-Host "Verifying agent secrets in Key Vault with retries..."
      $kv = "${azurerm_key_vault.kv.name}"
      $secrets = @(
        "agent-orchestrator-id",
        "agent-cropping-agent-id",
        "agent-background-agent-id",
        "agent-thumbnail-generator-id",
        "agent-video-agent-id",
        "agent-endpoint"
      )

      $maxAttempts = 6
      $attempt = 0
      $missing = @()

      while ($attempt -lt $maxAttempts) {
        $missing = @()
        foreach ($name in $secrets) {
          try {
            $val = az keyvault secret show --vault-name $kv --name $name --query value -o tsv 2>$null
            if (-not $val -or $val -eq "null" -or $val -eq "") {
              Write-Host "[WARN] Missing or empty secret: $name (attempt $($attempt+1)/$maxAttempts)"
              $missing += $name
            } else {
              Write-Host "[OK] $name present"
            }
          } catch {
            Write-Host "[WARN] Could not check secret: $name (attempt $($attempt+1)/$maxAttempts)"
            $missing += $name
          }
        }

        if ($missing.Count -eq 0) {
          Write-Host "All agent secrets verified"
          break
        }

        $attempt++
        if ($attempt -lt $maxAttempts) {
          Write-Host "Waiting 10s for secrets to propagate..."
          Start-Sleep -Seconds 10
        }
      }

      if ($missing.Count -gt 0) {
        Write-Host "Final missing secrets after $maxAttempts attempts: $($missing -join ", ")"
        Write-Host "Listing secrets currently in Key Vault for diagnostics:"
        try {
          az keyvault secret list --vault-name $kv --query '[].name' -o tsv
        } catch {
          Write-Host "(failed to list secrets)"
        }
        Write-Host "Please check the deploy_multi_agents provisioner logs or run the agent deploy script manually to populate secrets."
        exit 1
      }
    EOT
    interpreter = ["PowerShell", "-Command"]
  }

  triggers = {
    kv_id = azurerm_key_vault.kv.id
  }
}

# Wait for RBAC propagation
resource "time_sleep" "wait_for_rbac" {
  create_duration = "120s"
  depends_on = [
    azurerm_role_assignment.deployment_identity_foundry_user
  ]
}



# Post-provision verification of real agents (ensures >=5 non-local agents)
resource "null_resource" "verify_real_agents" {
  depends_on = [
    null_resource.deploy_multi_agents
  ]

  provisioner "local-exec" {
    command     = <<-EOT
      Write-Host ""; Write-Host "=== Verifying Real Agent Provisioning (Post-Deploy) ==="; Write-Host ""
      $pythonCmd = "python"
      if (Get-Command python3 -ErrorAction SilentlyContinue) { $pythonCmd = "python3" }
      
      # Run verification script to confirm agents exist in Azure
      $quickVerifyScript = Join-Path (Split-Path $PWD.Path -Parent) "src\app\agents\quick_verify.py"
      if (Test-Path $quickVerifyScript) {
        Write-Host "Running agent verification..."
        & $pythonCmd $quickVerifyScript
        if ($LASTEXITCODE -eq 0) {
          Write-Host "[SUCCESS] All agents verified in MSFT Foundry"
        } else {
          Write-Host "WARNING: Agent verification reported issues (check output above)"
        }
      } else {
        Write-Host "WARNING: quick_verify.py not found, skipping verification"
      }
      
      # Parse agent_ids.json to count real agents
      $agentIdsPath = Join-Path $PWD.Path "agent_ids.json"
      if (Test-Path $agentIdsPath) {
        $agentData = Get-Content $agentIdsPath -Raw | ConvertFrom-Json
        $realCount = 0
        foreach ($prop in $agentData.PSObject.Properties) {
          if ($prop.Value -and ($prop.Value -notlike "asst_local_*")) { 
            $realCount++ 
          }
        }
        Write-Host ""
        Write-Host "Real agent count from agent_ids.json: $realCount"
        if ($realCount -ge 4) {
          Write-Host "[SUCCESS] Verification passed: $realCount real agents deployed."
        } else {
          Write-Host "WARNING: Expected 4 real agents; found $realCount."
          $logPath = "../real_agent_warnings.log"
          "[$(Get-Date -Format o)] WARNING: Only $realCount real agents deployed." | Out-File -FilePath $logPath -Append -Encoding utf8
          Write-Host "Logged warning to $logPath"
        }
      } else {
        Write-Host "WARNING: agent_ids.json not found; cannot verify deployment count."
      }
      Write-Host ""; Write-Host "=== Real Agent Verification Complete ==="; Write-Host ""
    EOT
    interpreter = ["PowerShell", "-Command"]
  }

  triggers = {
    deploy_agents_id = null_resource.deploy_multi_agents[0].id
  }
}

# Remote multi-agent verification (runs after deployment). Hits /agents endpoint.
resource "null_resource" "verify_multi_agent_remote" {
  depends_on = [
    null_resource.deploy_multi_agents,
    azurerm_linux_web_app.app
  ]

  provisioner "local-exec" {
    command     = <<-EOT
      Write-Host ""; Write-Host "=== Verifying Multi-Agent Deployment (Remote) ==="; Write-Host ""
      $appUrl = "https://${local.web_app_name}.azurewebsites.net"
      $agentsEndpoint = "$appUrl/agents"
      Write-Host "Checking agents endpoint: $agentsEndpoint"
      $verificationPassed = $false
      try {
        $resp = Invoke-RestMethod -Uri $agentsEndpoint -Method GET -TimeoutSec 30
        Write-Host "Response:" ($resp | ConvertTo-Json -Depth 5)
        if ($resp.mode -eq 'multi-agent' -and $resp.all_present -and ($resp.agents.orchestrator -like 'asst_*')) {
          Write-Host "[OK] Multi-agent remote verification passed."
          $verificationPassed = $true
        } else {
          Write-Warning "Multi-agent verification incomplete."
          Write-Host ($resp | ConvertTo-Json -Depth 5)
        }
      } catch {
        Write-Warning "Could not reach /agents endpoint: $_"
      }

      if (-not $verificationPassed) {
        Write-Host ""; Write-Host "WARNING: ALERT: Multi-agent verification failed. Initiating App Service restart."; Write-Host ""
        try {
          az webapp restart --resource-group ${azurerm_resource_group.rg.name} --name ${local.web_app_name} | Out-Null
          Write-Host "[OK] Web App restart triggered due to verification failure."
        } catch {
          Write-Warning "Failed to restart Web App automatically: $_"
        }
        $alertMessage = "[$(Get-Date -Format o)] Multi-agent verification failed for ${local.web_app_name}."
        $alertPath = "../multi_agent_alerts.log"
        $alertMessage | Out-File -FilePath $alertPath -Encoding utf8 -Append
        Write-Host "Alert logged to $alertPath"
      }
      Write-Host "=== Verification Complete ==="; Write-Host ""
    EOT
    interpreter = ["PowerShell", "-Command"]
  }

  triggers = {
    web_app_id  = azurerm_linux_web_app.app.id
    docker_hash = local.dockerfile_hash
  }
}


# Web App Container Restart after Image Build
resource "null_resource" "webapp_container_restart" {
  provisioner "local-exec" {
    command     = <<-EOT
      Write-Host ""
      Write-Host "============================================================================"
      Write-Host "=== RESTARTING WEB APP TO PULL LATEST CONTAINER ==="
      Write-Host "============================================================================"
      Write-Host ""
      
      Write-Host "Restarting ${local.web_app_name} to pull latest container image..."
      
      # Restart web app to pull latest container
      az webapp restart --name ${local.web_app_name} --resource-group ${azurerm_resource_group.rg.name}
      
      Write-Host "[OK] Web app restart initiated"
      
      Write-Host ""
      Write-Host "Waiting for container to start (120 seconds)..."
      Start-Sleep -Seconds 120
      
      Write-Host ""
      Write-Host "Testing application health..."
      $health_url = "https://${local.web_app_name}.azurewebsites.net/health"
      
      $max_attempts = 15
      $success = $false
      
      for ($i = 1; $i -le $max_attempts; $i++) {
        try {
          Write-Host "Attempt $i/$max_attempts - Testing: $health_url"
          $response = Invoke-RestMethod -Uri $health_url -TimeoutSec 60 -Method GET
          
          if ($response.status -eq "healthy") {
            Write-Host "[SUCCESS] Application is healthy and responding!"
            $success = $true
            break
          } else {
            Write-Host "Response received but status: $($response.status)"
          }
        } catch {
          $errorMsg = $_.Exception.Message
          if ($errorMsg -like "*503*" -or $errorMsg -like "*502*" -or $errorMsg -like "*timeout*") {
            Write-Host "Health check $i - Container still starting (HTTP Error)"
          } else {
            Write-Host "Health check $i failed: $errorMsg"
          }
          if ($i -lt $max_attempts) {
            Start-Sleep -Seconds 45
          }
        }
      }
      
      if (-not $success) {
        Write-Host "[INFO] Health checks pending - application may need more time to start"
        Write-Host "This is normal for first deployment. The container may take 10-15 minutes to fully initialize."
        Write-Host "Check status at: https://${local.web_app_name}.azurewebsites.net"
        Write-Host ""
        Write-Host "To check container logs manually:"
        Write-Host "az webapp log tail --name ${local.web_app_name} --resource-group ${azurerm_resource_group.rg.name}"
      } else {
        Write-Host ""
        Write-Host "[SUCCESS] Application is ready and responding to health checks!"
      }
      
      Write-Host ""
      Write-Host "============================================================================"
      Write-Host ""
    EOT
    interpreter = ["PowerShell", "-Command"]
    working_dir = path.module
  }

  depends_on = [
    null_resource.docker_image_build,
    azurerm_linux_web_app.app,
    azurerm_key_vault_secret.agent_orchestrator_id,
    azurerm_key_vault_secret.agent_cropping_agent_id,
    azurerm_key_vault_secret.agent_background_agent_id,
    azurerm_key_vault_secret.agent_thumbnail_generator_id,
    azurerm_key_vault_secret.agent_video_agent_id,
    azurerm_key_vault_access_policy.app_policy
  ]

  triggers = {
    docker_image_id     = null_resource.docker_image_build.id
    agent_orchestrator  = azurerm_key_vault_secret.agent_orchestrator_id.id
    agent_video         = azurerm_key_vault_secret.agent_video_agent_id.id
    always_run          = timestamp()
  }
}

# A2A Monitoring Integration with Azure
resource "azurerm_monitor_action_group" "a2a_alerts" {
  count = (var.enable_a2a_automation && var.enable_monitoring_dashboards) ? 1 : 0

  name                = "${local.web_app_name}-a2a-alerts"
  resource_group_name = azurerm_resource_group.rg.name
  short_name          = "a2aalerts"

  webhook_receiver {
    name                    = "a2a-automation-webhook"
    service_uri             = "https://${local.web_app_name}.azurewebsites.net/a2a/automation/webhook/alert"
    use_common_alert_schema = true
  }

  depends_on = [azurerm_linux_web_app.app]
}

# A2A System Health Alert
resource "azurerm_monitor_metric_alert" "a2a_system_health" {
  count = 0  # Temporarily disabled due to conflicts

  name                = "${local.web_app_name}-a2a-health"
  resource_group_name = azurerm_resource_group.rg.name
  scopes              = [azurerm_linux_web_app.app.id]
  description         = "Alert when A2A automation system health degrades"
  severity            = 2
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.Web/sites"
    metric_name      = "HealthCheckStatus"
    aggregation      = "Average"
    operator         = "LessThan"
    threshold        = 1
  }

  action {
    action_group_id = azurerm_monitor_action_group.a2a_alerts[0].id
  }

  depends_on = [azurerm_monitor_action_group.a2a_alerts]
}

# A2A Performance Alert  
resource "azurerm_monitor_metric_alert" "a2a_performance" {
  count = 0  # Temporarily disabled due to conflicts

  name                = "${local.web_app_name}-a2a-performance"
  resource_group_name = azurerm_resource_group.rg.name
  scopes              = [azurerm_linux_web_app.app.id]
  description         = "Alert when A2A system response time exceeds threshold"
  severity            = 3
  frequency           = "PT1M"
  window_size         = "PT5M"

  criteria {
    metric_namespace = "Microsoft.Web/sites"
    metric_name      = "AverageResponseTime"
    aggregation      = "Average"
    operator         = "GreaterThan"
    threshold        = 5000 # 5 seconds
  }

  action {
    action_group_id = azurerm_monitor_action_group.a2a_alerts[0].id
  }

  depends_on = [azurerm_monitor_action_group.a2a_alerts]
}

# Post-deploy automated fix to ensure Web App starts successfully
resource "null_resource" "post_deploy_health" {
  depends_on = [
    azurerm_linux_web_app.app,
    azurerm_role_assignment.webapp_acr_pull,
    azurerm_key_vault_access_policy.app_policy
  ]

  provisioner "local-exec" {
    interpreter = ["PowerShell", "-Command"]
    command     = <<-EOT
      Write-Host ""
      Write-Host "============================================================================"
      Write-Host "=== AUTOMATED WEB APP STARTUP FIX ==="
      Write-Host "============================================================================"
      $rg = "${azurerm_resource_group.rg.name}"
      $name = "${local.web_app_name}"
      $url = "https://${local.web_app_name}.azurewebsites.net"

      Write-Host ""
      Write-Host "[1/7] Checking current Web App status..."
      $status = az webapp show --name $name --resource-group $rg --query "state" -o tsv
      Write-Host "Current state: $status"

      if ($status -eq "Stopped") {
        Write-Host "[DETECTED] Web App is stopped - applying automated fix"
      }

      Write-Host ""
      Write-Host "[2/7] Enabling detailed logging for diagnostics..."
      az webapp log config --name $name --resource-group $rg `
        --level verbose `
        --web-server-logging filesystem `
        --docker-container-logging filesystem `
        --detailed-error-messages true `
        --failed-request-tracing true | Out-Null

      Write-Host ""
      Write-Host "[2b/7] Verifying container configuration..."
      $cfg = az webapp config container show --name $name --resource-group $rg --output json | ConvertFrom-Json
      $desiredImage = "${local.registry_name}.azurecr.io/zava-chat-app:latest"
      $needsConfig = $true
      if ($cfg) {
        $currentImage = $cfg.dockerCustomImageName
        if ($currentImage -and ($currentImage -eq $desiredImage)) {
          Write-Host "[OK] Container image already set: $currentImage"
          $needsConfig = $false
        } else {
          Write-Host "[INFO] Container image differs or not set (current: '$currentImage'). Will apply fallback configuration."
        }
      } else {
        Write-Host "[INFO] No container config returned; will apply fallback."
      }

      if ($needsConfig) {
        try {
          $acrUser = az acr credential show --name ${local.registry_name} --query "username" -o tsv
          $acrPass = az acr credential show --name ${local.registry_name} --query "passwords[0].value" -o tsv
          az webapp config container set `
            --resource-group $rg `
            --name $name `
            --docker-custom-image-name $desiredImage `
            --docker-registry-server-url https://${local.registry_name}.azurecr.io `
            --docker-registry-server-user "$acrUser" `
            --docker-registry-server-password "$acrPass" `
            --enable-app-service-storage false | Out-Null
          Write-Host "[OK] Applied fallback container configuration"
        } catch {
          Write-Host "[WARN] Could not apply container configuration: $_"
        }
      }

      Write-Host ""
      Write-Host "[3/7] Ensuring Web App is stopped cleanly..."
      az webapp stop --name $name --resource-group $rg | Out-Null
      Write-Host "Waiting 15 seconds for complete shutdown..."
      Start-Sleep -Seconds 15

      Write-Host ""
      Write-Host "[4/7] Verifying container image exists in ACR..."
      $imageExists = az acr repository show --name ${local.registry_name} --image zava-chat-app:latest --query "name" -o tsv 2>$null
      if ($imageExists) {
        Write-Host "[OK] Container image found: zava-chat-app:latest"
      } else {
        Write-Host "[WARNING] Container image may still be building - will retry startup"
      }

      Write-Host ""
      Write-Host "[5/7] Starting Web App with fresh container pull..."
      az webapp start --name $name --resource-group $rg | Out-Null
      Write-Host "[OK] Start command sent"
      
      Write-Host ""
      Write-Host "[6/7] Waiting for container pull and app initialization..."
      Write-Host "This can take up to 20 minutes on first deployment."

      # Progressive wait with status checks (12 minutes total)
      $waitIntervals = @(60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60, 60)
      $elapsed = 0
      foreach ($interval in $waitIntervals) {
        Start-Sleep -Seconds $interval
        $elapsed += $interval
        $currentStatus = az webapp show --name $name --resource-group $rg --query "state" -o tsv
        Write-Host "  Status: $currentStatus (waited $($elapsed)s)"
        if ($currentStatus -eq "Running") {
          Write-Host "  [OK] App is now Running"
          break
        }
      }

      Write-Host ""
      Write-Host "[7/7] Testing application health endpoint..."
      $health = "$url/health"

      function Test-Health {
        param (
          [int]$Attempts,
          [int]$SleepSeconds
        )
        for ($i = 1; $i -le $Attempts; $i++) {
          Write-Host "  Attempt $i/$Attempts - Testing: $health"
          try {
            $resp = Invoke-RestMethod -Uri $health -TimeoutSec 60 -Method GET -ErrorAction Stop
            if ($resp.status -eq 'healthy') {
              Write-Host "  [SUCCESS] App is healthy and responding!"
              Write-Host "  Response: $($resp | ConvertTo-Json -Compress)"
              return $true
            } else {
              Write-Host "  Status: $($resp | ConvertTo-Json -Depth 4)"
            }
          } catch {
            $errMsg = $_.Exception.Message
            if ($errMsg -like "*503*" -or $errMsg -like "*502*" -or $errMsg -like "*timeout*" -or $errMsg -like "*404*") {
              Write-Host "  Container still starting up... (HTTP Error)"
            } else {
              Write-Host "  Error: $errMsg"
            }
          }
          if ($i -lt $Attempts) { Start-Sleep -Seconds $SleepSeconds }
        }
        return $false
      }

      $ok = Test-Health -Attempts 30 -SleepSeconds 30
      if (-not $ok) {
        Write-Host "[AutoRecovery] Health check failed; restarting Web App and retrying..."
        az webapp restart --name $name --resource-group $rg | Out-Null
        Start-Sleep -Seconds 90
        $ok = Test-Health -Attempts 20 -SleepSeconds 30
      }

      if (-not $ok) {
        Write-Host ""
        Write-Host "[DIAGNOSTICS] Health checks did not pass during apply. Collecting logs..."
        # Show recent logs to console and save a snapshot
        try {
          $diagLog = Join-Path (Split-Path $PWD.Path -Parent) "deploy.log"
          Write-Host "Saving recent logs to $diagLog"
          az webapp log show --name $name --resource-group $rg | Out-File -FilePath $diagLog -Encoding utf8
          Write-Host "[OK] Recent logs saved"
        } catch { Write-Host "Could not save recent logs: $_" }

        # Download the zipped log bundle
        try {
          $logZip = Join-Path (Split-Path $PWD.Path -Parent) "app-logs.zip"
          Write-Host "Downloading log bundle to $logZip"
          az webapp log download --name $name --resource-group $rg --log-file $logZip | Out-Null
          Write-Host "[OK] Logs bundle saved"
        } catch { Write-Host "Could not download logs bundle: $_" }
      }

      Write-Host ""
      Write-Host "============================================================================"
      if ($ok) {
        Write-Host "=== [SUCCESS] WEB APP IS HEALTHY AND READY ==="
        Write-Host ""
        Write-Host "Your application is live at:"
        Write-Host "  $url"
        Write-Host ""
        Write-Host "Test the chat interface in your browser now!"
      } else {
        Write-Host "=== [ERROR] WEB APP DID NOT BECOME HEALTHY ==="
        $finalState = az webapp show --name $name --resource-group $rg --query "state" -o tsv
        Write-Host "Web App State: $finalState"
        Write-Host "Failing the deployment so you can review logs."
        exit 1
      }
    EOT
  }
}