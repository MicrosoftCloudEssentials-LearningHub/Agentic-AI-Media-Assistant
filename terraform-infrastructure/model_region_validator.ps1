param(
    [string[]]$Regions = @(
        "eastus2",     # often has image/video previews
        "eastus",      # fallback US east
        "westus3",     # common for new previews
        "westus2",     # legacy west
        "westus",      # additional west option
        "centralus",   # broad GA coverage
        "southcentralus", # sometimes used for previews
        "swedencentral" # current primary
    ),
    [string[]]$Models = @(
        "sora-2",
        "gpt-image-1",
        "FLUX.1-Kontext-pro",
        "dall-e-3",
        "gpt-4o-mini",
        "text-embedding-3-small",
        "model-router"
    ),
    [string]$SubscriptionId,
    [string]$OutputPath = "./model_regions.json"
)

# Simple availability probe: picks the first region in priority order where the model is listed
$assignments = @{}
foreach ($m in $Models) {
    $found = $null
    foreach ($r in $Regions) {
        try {
            $res = az cognitiveservices model list --subscription $SubscriptionId --location $r --query "[?name=='$m']" -o tsv 2>$null
            if ($LASTEXITCODE -eq 0 -and $res) {
                $found = $r
                break
            }
        } catch {
            # ignore and continue to next region
        }
    }
    if ($found) {
        $assignments[$m] = $found
    } else {
        $assignments[$m] = "unavailable"
    }
}

$result = @{ model_regions = $assignments; probed_regions = $Regions }
$result | ConvertTo-Json | Set-Content -Encoding UTF8 $OutputPath
Write-Host "Model-region map written to $OutputPath"
