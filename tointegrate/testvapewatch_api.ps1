# test_vapewatch_api.ps1
param(
    [string]$ImagePath = "D:\NUS MCOMP\cloud computing\cloud_computing\Project\VapeDataSet\test\1b1263a746f35da7075114f7300ccc35_jpg.rf.1a1ea5dd14e6b840c9480f8b596c87dc.jpg"
)

# 1️⃣ Read image bytes
$bytes = [System.IO.File]::ReadAllBytes($ImagePath)

# 2️⃣ Convert to Base64
$base64 = [System.Convert]::ToBase64String($bytes)

# 3️⃣ Build JSON payload
$payload = @{
    image = $base64
    confidence_threshold = 0.5
} | ConvertTo-Json -Compress

# 4️⃣ Send to API Gateway
$endpoint = "https://1im6kl2mzj.execute-api.ap-southeast-1.amazonaws.com/default/vapewatch-inference-lambda"

try {
    Write-Host "🚀 Sending image to VapeWatch endpoint..." -ForegroundColor Cyan
    $response = Invoke-RestMethod -Uri $endpoint `
        -Method POST `
        -Headers @{ "Content-Type" = "application/json" } `
        -Body $payload

    Write-Host "`n✅ API Response:" -ForegroundColor Green
    $response | ConvertTo-Json -Depth 5
}
catch {
    Write-Host "`n❌ Error:" $_.Exception.Message -ForegroundColor Red
}
