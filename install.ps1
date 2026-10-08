# Whisperflow pro Windows: připraví konfiguraci a přidá spouštění po přihlášení.
#   powershell -ExecutionPolicy Bypass -File install.ps1             # instalace + spuštění
#   powershell -ExecutionPolicy Bypass -File install.ps1 -Uninstall  # ukončit a odebrat z Po spuštění
param([switch]$Uninstall)
$ErrorActionPreference = "Stop"

$here     = Split-Path -Parent $MyInvocation.MyCommand.Path
$script   = Join-Path $here "whisperflow_win.py"
$cfgDir   = Join-Path $env:APPDATA "whisperflow"
$cfg      = Join-Path $cfgDir "config.toml"
$shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "Whisperflow.lnk"

# pythonw.exe = Python bez okna konzole. Hledáme přes launcher py, pak v PATH.
$pythonw = $null
try { $pythonw = (& py -3 -c "import sys, pathlib; print(pathlib.Path(sys.executable).with_name('pythonw.exe'))" 2>$null) } catch {}
if (-not $pythonw -or -not (Test-Path $pythonw)) {
    $cmd = Get-Command pythonw.exe -ErrorAction SilentlyContinue
    if ($cmd) { $pythonw = $cmd.Source }
}
if (-not $pythonw -or -not (Test-Path $pythonw)) {
    throw "Nenašel jsem pythonw.exe. Nainstaluj Python 3.11+ z python.org (s volbou 'py launcher')."
}

if ($Uninstall) {
    Start-Process $pythonw "`"$script`" quit" -Wait
    Remove-Item $shortcut -ErrorAction SilentlyContinue
    Write-Host "Whisperflow ukončen a odebrán z Po spuštění. Konfigurace zůstala v $cfgDir."
    exit
}

$ver = & ($pythonw -replace 'pythonw\.exe$', 'python.exe') -c "import sys; print(sys.version_info >= (3, 11))"
if ($ver -ne "True") { throw "Whisperflow potřebuje Python 3.11 nebo novější ($pythonw)." }

New-Item -ItemType Directory -Force $cfgDir | Out-Null
if (-not (Test-Path $cfg)) {
    Copy-Item (Join-Path $here "config.example.toml") $cfg
    Write-Host "Vytvořil jsem $cfg - doplň do něj api_key a ulož."
    Start-Process notepad $cfg
    Read-Host "Po uložení konfigurace stiskni Enter"
}

$ws = New-Object -ComObject WScript.Shell
$lnk = $ws.CreateShortcut($shortcut)
$lnk.TargetPath = $pythonw
$lnk.Arguments = "`"$script`""
$lnk.WorkingDirectory = $here
$lnk.Description = "Whisperflow - diktování do schránky"
$lnk.Save()

Start-Process $pythonw "`"$script`" quit" -Wait   # případnou starou instanci ukončit
Start-Sleep -Milliseconds 500
Start-Process $pythonw "`"$script`""
Write-Host "Hotovo. Whisperflow běží a spustí se po každém přihlášení. Zkratka: Ctrl+Alt+H (změna v config.toml)."
