resource_group_name = "RG-AI-Mediax43"
location            = "westus3"
name_prefix         = "zava"

# Enable multi-agent architecture
enable_multi_agent = true

# Disable data pipeline temporarily due to RBAC propagation delays
enable_data_pipeline = false

# user_principal_id is optional - defaults to current Azure CLI user (az login)
