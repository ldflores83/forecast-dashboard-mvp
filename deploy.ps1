# deploy.ps1 - Revenue Intelligence deploy script
# Usage:
#   .\deploy.ps1              -> deploy frontend only (default)
#   .\deploy.ps1 -all         -> frontend + Cloud Function
#   .\deploy.ps1 -views       -> re-create BQ views only
#   .\deploy.ps1 -all -views  -> everything

param(
    [switch]$all,
    [switch]$views
)

$BUCKET     = "gs://forecast-dashboard-mvp-frontend"
$FRONTEND   = "frontend"
$DEPLOY_URL = "https://storage.googleapis.com/forecast-dashboard-mvp-frontend/revenue-intelligence.html"
$API_URL    = "https://us-central1-forecast-dashboard-mvp.cloudfunctions.net/dashboard-api"

Write-Host ""
Write-Host "  Revenue Intelligence - Deploy" -ForegroundColor Cyan
Write-Host "  ==============================" -ForegroundColor Cyan
Write-Host ""

# 1. BQ VIEWS
if ($views) {
    Write-Host "  [1/3] Re-creating BigQuery views..." -ForegroundColor Yellow
    python scripts/setup_views.py
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAILED - BigQuery views not updated." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK - Views updated." -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "  [1/3] BQ views - skipped (use -views to update)" -ForegroundColor DarkGray
}

# 2. CLOUD FUNCTION
if ($all) {
    Write-Host "  [2/3] Deploying Cloud Function..." -ForegroundColor Yellow
    $functionArgs = @(
        "functions", "deploy", "dashboard-api",
        "--gen2",
        "--runtime=python311",
        "--region=us-central1",
        "--source=api",
        "--entry-point=dashboard_api",
        "--trigger-http",
        "--allow-unauthenticated",
        "--memory=512MB",
        "--timeout=60s"
    )
    & gcloud @functionArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAILED - Cloud Function not deployed." -ForegroundColor Red
        exit 1
    }
    Write-Host "  OK - Cloud Function deployed." -ForegroundColor Green
    Write-Host ""
} else {
    Write-Host "  [2/3] Cloud Function - skipped (use -all to deploy)" -ForegroundColor DarkGray
}

# 3. FRONTEND
Write-Host "  [3/3] Uploading frontend..." -ForegroundColor Yellow

if (-not (Test-Path $FRONTEND)) {
    Write-Host "  FAILED - $FRONTEND not found." -ForegroundColor Red
    Write-Host "  Make sure HTML files are in the frontend\ folder." -ForegroundColor Red
    exit 1
}

# Upload all HTML files in frontend/ folder
Get-ChildItem "$FRONTEND\*.html" | ForEach-Object {
    $fileName = $_.Name
    $storageArgs = @(
        "storage", "cp", $_.FullName,
        "$BUCKET/$fileName",
        "--cache-control=no-cache, no-store, max-age=0"
    )
    Write-Host "  Copying $fileName..." -ForegroundColor DarkGray
    & gcloud @storageArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  FAILED - $fileName not uploaded." -ForegroundColor Red
        exit 1
    }
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "  FAILED - Frontend not uploaded." -ForegroundColor Red
    exit 1
}

Write-Host "  OK - Frontend uploaded." -ForegroundColor Green
Write-Host ""
Write-Host "  ==============================" -ForegroundColor Cyan
Write-Host "  Deploy complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Dashboard : $DEPLOY_URL" -ForegroundColor Cyan
Write-Host "  API       : $API_URL" -ForegroundColor DarkGray
Write-Host ""