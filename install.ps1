# ═══════════════════════════════════════════════════════════════
#  zClaude — Windows PowerShell Installer
#  Installs proxy, GUI, provider manager for Windows
# ═══════════════════════════════════════════════════════════════
[CmdletBinding()]
param(
    [ValidateSet("install", "uninstall")]
    [string]$Action = "install"
)

$ErrorActionPreference = "Stop"
$Version = "1.0.0"

function Write-Info($msg)   { Write-Host "[INFO] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)     { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn($msg)   { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)    { Write-Host "[ERROR] $msg" -ForegroundColor Red }

# ─── Detect Python ──────────────────────────────────────
$PythonCmd = $null
foreach ($cmd in @("python3", "python", "py")) {
    if (Get-Command $cmd -ErrorAction SilentlyContinue) {
        $PythonCmd = $cmd
        break
    }
}

if (-not $PythonCmd) {
    Write-Err "Python 3.8+ is required but not found."
    Write-Info "Install from: https://www.python.org/downloads/"
    exit 1
}

$pyVer = & $PythonCmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
Write-Ok "Python $pyVer detected"

# ─── Paths ──────────────────────────────────────────────
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BinDir = Join-Path $env:USERPROFILE ".local\bin"
$LibDir = Join-Path $env:USERPROFILE ".local\lib\zclaude"

if ($Action -eq "uninstall") {
    Write-Info "Uninstalling zClaude..."
    Remove-Item (Join-Path $BinDir "zclaude-*") -Force -ErrorAction SilentlyContinue
    Remove-Item $LibDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Ok "Uninstalled. Config preserved in ~/.codex/"
    return
}

# ─── Banner ─────────────────────────────────────────────
Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "       zClaude Installer v$Version (Windows)" -ForegroundColor White
Write-Host "       Universal AI Proxy & Launcher" -ForegroundColor Gray
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host ""

# ─── Install ─────────────────────────────────────────────
New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
New-Item -ItemType Directory -Path $LibDir -Force | Out-Null

# Copy source files
Copy-Item -Path "$ScriptDir\*" -Destination $LibDir -Recurse -Force -Exclude "__pycache__","*.pyc",".git"

# Create launcher scripts
$Launchers = @{
    "translate-proxy.py"     = "zclaude-proxy.cmd"
    "provider_manager.py"    = "zclaude-providers.cmd"
    "session_manager.py"      = "zclaude-sessions.cmd"
    "codex-launcher-gui.py"   = "zclaude-gui.cmd"
    "codex-launcher-gui-x.py" = "zclaude-gui-x.cmd"
}

$installed = 0
foreach ($entry in $Launchers.GetEnumerator()) {
    $src = $entry.Key
    $dst = $entry.Value

    $content = "@echo off`n`"$PythonCmd\`" `"$LibDir\$src\`" %*"
    Set-Content -Path (Join-Path $BinDir $dst) -Value $content -Encoding ASCII
    $installed++
}

Write-Ok "$installed commands installed to $BinDir"

# ─── Verify ──────────────────────────────────────────────
Write-Info "Verifying installation..."
$failed = 0
foreach ($cmd in @("zclaude-proxy", "zclaude-providers")) {
    $full = Join-Path $BinDir "$cmd.cmd"
    if (Test-Path $full) {
        Write-Ok "$cmd"
    } else {
        Write-Warn "$cmd not found"
        $failed++
    }
}

# ─── PATH check ─────────────────────────────────────────
if ($env:PATH -notlike "*$BinDir*") {
    Write-Warn "$BinDir is not in your PATH."
    Write-Info "Run this to add it permanently:"
    Write-Host '  [Environment]::SetEnvironmentVariable("PATH", "$env:PATH;$BinDir", "User")' -ForegroundColor Yellow
    Write-Info "Then restart your terminal."
}

# ─── Done ───────────────────────────────────────────────
Write-Host ""
Write-Host "  ============================================ " -ForegroundColor Green
Write-Host "  OK: Installation complete!" -ForegroundColor Green
Write-Host "  ============================================ " -ForegroundColor Green
Write-Host ""
Write-Host "  Quick Start:" -ForegroundColor White
Write-Host "    zclaude-providers wizard      Setup providers" -ForegroundColor Gray
Write-Host "    zclaude-proxy                 Start proxy" -ForegroundColor Gray
Write-Host "    zclaude-gui                  Launch GUI" -ForegroundColor Gray
Write-Host ""
