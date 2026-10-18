# Resell Toolkit - Windows installer (first step).
# Run it from PowerShell with:
#   irm https://raw.githubusercontent.com/dom1911k/flipping_scout/claude/gracious-dirac-39brnb/windows/install.ps1 | iex
# or double-click Install-ResellToolkit.bat. It installs Git, downloads the toolkit to
# %USERPROFILE%\ResellToolkit and then runs windows\setup.ps1 for the rest. Safe to run again.

$ErrorActionPreference = "Continue"   # git writes progress to stderr; we check $LASTEXITCODE instead
$Repo   = "https://github.com/dom1911k/flipping_scout"
$Branch = "claude/gracious-dirac-39brnb"
$Dest   = Join-Path $env:USERPROFILE "ResellToolkit"

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

Write-Host ""
Write-Host "Resell Toolkit installer" -ForegroundColor Cyan
Write-Host "Installs into $Dest"
Write-Host ""

if (-not (Get-Command winget -ErrorAction SilentlyContinue)) {
    Write-Host "winget is missing. Install 'App Installer' from the Microsoft Store, then run this again." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "Installing Git..."
    winget install -e --id Git.Git --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) { $env:Path += ";$env:ProgramFiles\Git\cmd" }
}

if (Test-Path (Join-Path $Dest ".git")) {
    Write-Host "Toolkit already downloaded - getting the latest version..."
    git -C $Dest pull --rebase --autostash origin $Branch
} else {
    Write-Host "Downloading the toolkit..."
    git clone -b $Branch $Repo $Dest
}
if ($LASTEXITCODE -ne 0) {
    Write-Host "Download failed. Check your internet connection and run this again." -ForegroundColor Red
    Read-Host "Press Enter to close"
    exit 1
}

powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $Dest "windows\setup.ps1")
