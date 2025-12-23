Param()

# Read JSON input from Terraform external data source via stdin
$input = [Console]::In.ReadToEnd() | ConvertFrom-Json
$kvName = $input.kv_name
$rg = $input.rg_name

[Console]::Error.WriteLine("[ensure_kv_network_rule] Ensuring network rule exists for Key Vault: $kvName in RG: $rg")

try {
  $ip = (Invoke-RestMethod -Uri https://api.ipify.org).ToString()
  [Console]::Error.WriteLine("[ensure_kv_network_rule] Current public IP: $ip")

  # Add IP rule to Key Vault (suppress command output)
  az keyvault network-rule add --name $kvName --resource-group $rg --ip-address "$ip/32" | Out-Null

  # Ensure default action allows data plane so Terraform provider can access secrets
  az keyvault update --name $kvName --resource-group $rg --default-action Allow --bypass AzureServices | Out-Null

  $output = @{ result = "ok"; ip = $ip }
  # Write only JSON to stdout for Terraform external data source
  $output | ConvertTo-Json -Compress
  exit 0
}
catch {
  $err = $_.Exception.Message
  $output = @{ result = "error"; message = $err }
  $output | ConvertTo-Json -Compress
  exit 1
}
