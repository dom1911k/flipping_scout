# Resell Toolkit - Windows setup (second step, called by install.ps1). Safe to run again at any time:
# it only adds what's missing. Installs Python and ffmpeg, the toolkit's packages, your .env file,
# desktop shortcuts and the Sunday eBay scan.

$ErrorActionPreference = "Continue"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
}

function Find-Python {
    # The py launcher avoids the Microsoft Store "python" placeholder.
    foreach ($v in "3.13", "3.12", "3.11") {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            & py "-$v" -c "import sys" 2>$null
            if ($LASTEXITCODE -eq 0) { return @("py", "-$v") }
        }
    }
    return $null
}

function Step($text) { Write-Host ""; Write-Host "== $text" -ForegroundColor Cyan }

# 1. Python ------------------------------------------------------------------------------------------
Step "Python"
$py = Find-Python
if (-not $py) {
    Write-Host "Installing Python 3.12..."
    winget install -e --id Python.Python.3.12 --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
    $py = Find-Python
}
if (-not $py) {
    Write-Host "Python couldn't be installed automatically. Install Python 3.12 from python.org, then run this again." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
Write-Host "Using $($py -join ' ')"

# 2. ffmpeg (only needed for screen recordings) ------------------------------------------------------
Step "ffmpeg (for screen recordings)"
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "Already installed."
} else {
    winget install -e --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements
    Refresh-Path
    if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
        Write-Host "ffmpeg didn't install. Everything else works; recordings need it later (winget install Gyan.FFmpeg)." -ForegroundColor Yellow
    }
}

# 3. Toolkit packages --------------------------------------------------------------------------------
Step "Toolkit packages (takes a few minutes the first time)"
$venvPy = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPy)) {
    & $py[0] $py[1] -m venv .venv
}
& $venvPy -m pip install --quiet --upgrade pip
& $venvPy -m pip install --quiet -e ".[llm]"
if ($LASTEXITCODE -ne 0) {
    Write-Host "Installing the packages failed. Copy the error above and send it to Claude." -ForegroundColor Red
    Read-Host "Press Enter to close"; exit 1
}
Write-Host "Done."

# 4. Your files: data folder and .env with your keys -------------------------------------------------
Step "Your keys and sales sheet"
$data = Join-Path $Root "data"
New-Item -ItemType Directory -Force -Path $data | Out-Null
$envFile = Join-Path $Root ".env"
if (-not (Test-Path $envFile)) {
    @(
        "# Your keys. Paste each one after the = sign, save, and close Notepad.",
        "EBAY_CLIENT_ID=",
        "EBAY_CLIENT_SECRET=",
        "ANTHROPIC_API_KEY="
    ) | Set-Content -Path $envFile -Encoding ASCII
    Write-Host "Notepad opens now: paste your eBay App ID, eBay Cert ID and Anthropic key, save, close Notepad."
    Start-Process notepad.exe $envFile -Wait
} else {
    Write-Host ".env already exists (edit it any time: $envFile)."
}
$sheet = Join-Path $data "Orders_and_cost_breakdown.xlsx"
if (-not (Test-Path $sheet)) {
    Write-Host "Copy Orders_and_cost_breakdown.xlsx into the folder that opens now, then come back here."
    Start-Process explorer.exe $data
    Read-Host "Press Enter once the file is in the data folder"
}
if (Test-Path $sheet) {
    & $venvPy -m resell import
}

# 5. Desktop shortcuts --------------------------------------------------------------------------------
Step "Desktop shortcuts"
$shell = New-Object -ComObject WScript.Shell
$desktop = [Environment]::GetFolderPath("Desktop")
$links = @(
    @{ Name = "Resell Toolkit";        Target = "windows\start.bat";  Icon = "%SystemRoot%\System32\shell32.dll,13" },
    @{ Name = "Resell Toolkit Update"; Target = "windows\update.bat"; Icon = "%SystemRoot%\System32\shell32.dll,238" }
)
foreach ($l in $links) {
    $s = $shell.CreateShortcut((Join-Path $desktop ($l.Name + ".lnk")))
    $s.TargetPath = Join-Path $Root $l.Target
    $s.WorkingDirectory = $Root
    $s.IconLocation = $l.Icon
    $s.Save()
}
Write-Host "Added 'Resell Toolkit' and 'Resell Toolkit Update' to your desktop."

# 6. Sunday eBay scan ---------------------------------------------------------------------------------
Step "Weekly eBay scan (Sundays 09:00)"
try {
    $venvPyw  = Join-Path $Root ".venv\Scripts\pythonw.exe"   # pythonw: no console window pops up
    $action   = New-ScheduledTaskAction -Execute $venvPyw -Argument "-m resell ebay-scan" -WorkingDirectory $Root
    $trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Sunday -At 9am
    # StartWhenAvailable: if the PC was off on Sunday morning, it runs as soon as it's back on.
    $settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -RunOnlyIfNetworkAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
    Register-ScheduledTask -TaskName "Resell Toolkit eBay scan" -Action $action -Trigger $trigger -Settings $settings `
        -Description "Weekly eBay.de snapshot for the Resell Toolkit" -Force -ErrorAction Stop | Out-Null
    Write-Host "Scheduled. It runs in the background; results show on the Research page."
} catch {
    Write-Host "Couldn't schedule it ($($_.Exception.Message)). Run 'Resell Toolkit' and the scan by hand on Sundays." -ForegroundColor Yellow
}

# Done ------------------------------------------------------------------------------------------------
Step "All set"
Write-Host "Double-click 'Resell Toolkit' on your desktop to open it. Starting it now..."
Start-Process (Join-Path $Root "windows\start.bat") -WorkingDirectory $Root
