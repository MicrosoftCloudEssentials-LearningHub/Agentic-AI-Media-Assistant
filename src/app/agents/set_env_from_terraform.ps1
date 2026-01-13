# Set environment variables from Terraform outputs
# Run this before executing agent scripts: . .\set_env_from_terraform.ps1

Write-Host "Loading Terraform outputs as environment variables..." -ForegroundColor Cyan

$tfDir = "..\..\..\terraform-infrastructure"
$outputs = terraform -chdir=$tfDir output -json | ConvertFrom-Json

# Extract project endpoints dynamically
$primaryEndpoint = $outputs.ai_project_primary_endpoint.value
$primaryRegion = $outputs.ai_foundry_primary_region.value
$foundryNames = $outputs.ai_foundry_names_by_region.value

# Set primary project endpoint
$env:AZURE_AI_PROJECT_ENDPOINT = $primaryEndpoint

# Set region-specific endpoints
if ($foundryNames.swedencentral) {
    $env:AZURE_AI_PROJECT_ENDPOINT_SWEDENCENTRAL = "https://$($foundryNames.swedencentral).services.ai.azure.com/api/projects/proj-swedencent-$($outputs.application_name.value.Split('-')[-1])"
}

if ($foundryNames.westus3) {
    $env:AZURE_AI_PROJECT_ENDPOINT_WESTUS3 = "https://$($foundryNames.westus3).services.ai.azure.com/api/projects/proj-westus3-$($outputs.application_name.value.Split('-')[-1])"
}

# Set Azure credentials
$env:AZURE_SUBSCRIPTION_ID = $outputs.subscription_id.value
$env:AZURE_RESOURCE_GROUP = $outputs.resource_group_name.value

# Set model and agent configurations
$env:AGENT_MODEL_MAP = $outputs.agent_model_assignments.value | ConvertTo-Json -Compress
$env:AGENT_REGION_MAP = $outputs.agent_region_assignments.value | ConvertTo-Json -Compress
$env:MODEL_REGION_MAP = $outputs.model_regions.value | ConvertTo-Json -Compress

# Display configuration
Write-Host "`n Environment configured:" -ForegroundColor Green
Write-Host "  Subscription:        $env:AZURE_SUBSCRIPTION_ID"
Write-Host "  Resource Group:      $env:AZURE_RESOURCE_GROUP"
Write-Host "  Primary Endpoint:    $env:AZURE_AI_PROJECT_ENDPOINT"
Write-Host "  Sweden Central:      $env:AZURE_AI_PROJECT_ENDPOINT_SWEDENCENTRAL"
Write-Host "  West US 3:           $env:AZURE_AI_PROJECT_ENDPOINT_WESTUS3"
Write-Host "`n Ready to run agent scripts!`n" -ForegroundColor Green
