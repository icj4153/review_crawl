$ErrorActionPreference = "Stop"

python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --collect-all playwright `
  --name "NaverSmartStoreReviewCollector" `
  app.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host ""
Write-Host "Build complete: dist\NaverSmartStoreReviewCollector.exe"
