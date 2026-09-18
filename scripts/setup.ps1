# Installation complète sous Windows (PowerShell) : prérequis, dépendances, .env, vérification.
# Usage :  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

function Have($cmd) { return [bool](Get-Command $cmd -ErrorAction SilentlyContinue) }

Write-Host "== Prérequis =="
if (-not (Have "python")) { Write-Host "Python absent -> installation via winget"; winget install -e --id Python.Python.3.12 --accept-package-agreements --accept-source-agreements }
if (-not (Have "node"))   { Write-Host "Node.js absent -> installation via winget"; winget install -e --id OpenJS.NodeJS.LTS --accept-package-agreements --accept-source-agreements }
if (-not (Have "ffmpeg")) { Write-Host "FFmpeg absent -> installation via winget"; winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements }
if (-not (Have "git"))    { Write-Host "Git absent -> installation via winget";    winget install -e --id Git.Git --accept-package-agreements --accept-source-agreements }
Write-Host "Si un outil vient d'être installé, fermez et rouvrez le terminal puis relancez ce script (PATH)."

Write-Host "== Dépendances Python =="
python -m pip install --upgrade pip | Out-Null
python -m pip install -r requirements.txt

Write-Host "== Dépendances Node (HyperFrames) =="
npm install

if (-not (Test-Path ".env")) {
  Copy-Item ".env.example" ".env"
  Write-Host "Fichier .env créé : ouvrez-le et renseignez PEXELS_API_KEY (clé gratuite sur https://www.pexels.com/api/)."
}

Write-Host "== Vérification =="
python -m clipper doctor
