Param(
  [Parameter(Mandatory = $false)]
  [string]$kv_name,

  [Parameter(Mandatory = $false)]
  [string]$rg_name,

  [Parameter(Mandatory = $false)]
  [string]$sub_id
)

# Terraform external data source passes JSON via stdin.
# For manual runs (or when stdin is empty), allow explicit params.
$stdin = [Console]::In.ReadToEnd()
$input = $null
if ($stdin -and $stdin.Trim().Length -gt 0) {
  try {
    $input = $stdin | ConvertFrom-Json
  } catch {
    $input = $null
  }
}

$kvName = $input.kv_name
$rg = $input.rg_name
$subId = $input.sub_id

if (-not $kvName) { $kvName = $kv_name }
if (-not $rg) { $rg = $rg_name }
if (-not $subId) { $subId = $sub_id }

if (-not $kvName -or -not $rg) {
  $output = @{ result = "error"; message = "Missing required inputs. Provide stdin JSON (Terraform) or -kv_name/-rg_name parameters." }
  $output | ConvertTo-Json -Compress
  exit 1
}

[Console]::Error.WriteLine("[ensure_kv_network_rule] Ensuring network rule exists for Key Vault: $kvName in RG: $rg (Sub: $subId)")

try {
  if ($subId) {
    az account set --subscription $subId
  }

  $ip = (Invoke-RestMethod -Uri https://api.ipify.org).ToString()
  [Console]::Error.WriteLine("[ensure_kv_network_rule] Current public IP: $ip")

  # Add IP rule to Key Vault (suppress command output)
  az keyvault network-rule add --name $kvName --resource-group $rg --ip-address "$ip/32" | Out-Null

  # Ensure default action allows data plane so Terraform provider can access secrets
  # AND ensure public network access is enabled
  az keyvault update --name $kvName --resource-group $rg --default-action Allow --bypass AzureServices --public-network-access Enabled | Out-Null

  [Console]::Error.WriteLine("[ensure_kv_network_rule] Verifying public network access...")
  
  for ($i = 1; $i -le 12; $i++) {
    $kv = az keyvault show --name $kvName --resource-group $rg | ConvertFrom-Json
    $access = $kv.properties.publicNetworkAccess
    [Console]::Error.WriteLine("[ensure_kv_network_rule] Attempt ${i}: PublicNetworkAccess is '$access'")
    
    if ($access -eq "Enabled") {
      [Console]::Error.WriteLine("[ensure_kv_network_rule] Access enabled. Waiting 10s for propagation...")
      Start-Sleep -Seconds 10
      break
    }
    
    Start-Sleep -Seconds 5
    # Retry the update if it's still disabled
    az keyvault update --name $kvName --resource-group $rg --public-network-access Enabled | Out-Null
  }

  $output = @{ result = "ok"; ip = $ip }
  # Write only JSON to stdout for Terraform external data source
  $output | ConvertTo-Json -Compress
  exit 0
}
catch {
  $err = $_.Exception.Message
  # If the KV doesn't exist yet, that's fine, we can't open it. 
  # But Terraform shouldn't be calling this if it doesn't exist? 
  # Actually, if it doesn't exist, the secrets won't exist either, so refresh won't fail on secrets.
  # So we can ignore "ResourceNotFound" errors.
  if ($err -match "ResourceNotFound") {
      [Console]::Error.WriteLine("[ensure_kv_network_rule] Key Vault not found. Skipping.")
      $output = @{ result = "skipped"; message = "Key Vault not found" }
      $output | ConvertTo-Json -Compress
      exit 0
  }

  $output = @{ result = "error"; message = $err }
  $output | ConvertTo-Json -Compress
  exit 1
}
