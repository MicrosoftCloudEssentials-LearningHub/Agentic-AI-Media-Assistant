resource_group_name = "RG-AI-Mediaxi22"
location            = "eastus2"
name_prefix         = "zava"
app_service_location = "westus3"
app_service_sku      = "P0v3"

# Enable multi-agent architecture
enable_multi_agent = true

# Enable AI automation and model deployments
enable_ai_automation = true

cosmos_tenant_id = "zava-media-demo"

# user_principal_id is optional - defaults to current Azure CLI user (az login)

# Key vault network locking disabled to allow Terraform access
lock_key_vault_network = false
