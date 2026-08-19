[CmdletBinding()]
param(
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

$projectRoot = Split-Path -Parent $PSScriptRoot
$requestPath = Join-Path $projectRoot "examples\hangzhou_request.json"

# Windows PowerShell 5.1 does not default to UTF-8 for BOM-less files.
$body = Get-Content -LiteralPath $requestPath -Raw -Encoding UTF8

# Fail locally with a clear error before sending malformed JSON to the API.
$null = $body | ConvertFrom-Json

Invoke-RestMethod `
    -Method Post `
    -Uri "$BaseUrl/api/v1/plans" `
    -ContentType "application/json; charset=utf-8" `
    -Body $body

