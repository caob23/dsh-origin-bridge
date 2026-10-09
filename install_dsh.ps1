# Install Origin Bridge into DeepSeek Harness.
#
#   powershell -ExecutionPolicy Bypass -File .\install_dsh.ps1
#   powershell -ExecutionPolicy Bypass -File .\install_dsh.ps1 -Profile web
#
# ASCII only on purpose: Windows PowerShell 5.1 reads .ps1 as ANSI unless a BOM
# is present, so non-ASCII text can decode into stray quote characters and break
# parsing.
#
# Why an `insert:` block and not a bare `- id:` entry:
#   a top-level `- id: X` in the profile layer OVERRIDES an entry X that a lower
#   (bundle) layer already created. For a brand-new id it silently does nothing,
#   which looks exactly like "I edited the config, restarted, and nothing
#   happened". `- insert:` creates the entry.
#
# Why we do not copy into node_modules:
#   a bundle is only applied when it is BOTH a dependency and listed in
#   dsh.profile.bundles in the profile package.json. Registering there means
#   running pnpm against a live profile (it prunes/rewrites node_modules). The
#   profile patch layer is applied unconditionally, so an insert here needs no
#   package-manager surgery.

param(
    [string]$Profile = 'desktop',
    [string]$PythonExe = '',
    [string]$DshHome = '',
    [switch]$RemoveOld
)

$ErrorActionPreference = 'Stop'
$src = Split-Path -Parent $MyInvocation.MyCommand.Path
$entryId = 'mcp-origin-bridge'
$oldEntryId = 'mcp-originlab-bridge'
$serverPy = Join-Path $src 'server.py'

if (-not (Test-Path $serverPy)) { throw "server.py not next to this script: $serverPy" }

if (-not $DshHome) { $DshHome = Join-Path $env:USERPROFILE '.dsh' }
$profileDir = Join-Path $DshHome "profiles\$Profile"
if (-not (Test-Path $profileDir)) {
    throw "dsh profile dir not found: $profileDir (pass -Profile web or desktop)"
}

if (-not $PythonExe) {
    $candidate = Join-Path $src '.venv\Scripts\python.exe'
    if (Test-Path $candidate) { $PythonExe = $candidate }
    else { $PythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source }
}
if (-not $PythonExe -or -not (Test-Path $PythonExe)) {
    throw "python interpreter not found. Create it first: python -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt"
}

$prevEap = $ErrorActionPreference
$ErrorActionPreference = 'Continue'
& $PythonExe -c "import originpro" 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Warning "$PythonExe has no originpro; the server will start but cannot connect to Origin"
}

$patch = Join-Path $profileDir 'cordis.patch.yml'
$q = "'"
$block = ""
$block += "`r`n# --- origin-bridge (installed $(Get-Date -Format 'yyyy-MM-dd HH:mm')) ---`r`n"
$block += "# insert: creates the loader entry in the profile layer. Do NOT add a second`r`n"
$block += "# full insert for ${entryId}: duplicate loader entry id makes dsh refuse to boot.`r`n"
$block += "# failOnStartupError keeps dsh usable when Origin is missing.`r`n"
$block += "- insert:`r`n"
$block += "    - id: $entryId`r`n"
$block += "      name: '@deepseek-ai/dsh-mcp-client'`r`n"
$block += "      config:`r`n"
$block += "        serverName: origin_bridge`r`n"
$block += "        transport: stdio`r`n"
$block += "        command: $q$PythonExe$q`r`n"
$block += "        args:`r`n"
$block += "          - '-u'`r`n"
$block += "          - '-X'`r`n"
$block += "          - utf8`r`n"
$block += "          - $q$serverPy$q`r`n"
$block += "        env:`r`n"
$block += "          PYTHONIOENCODING: utf-8`r`n"
$block += "          PYTHONUNBUFFERED: '1'`r`n"
$block += "        failOnStartupError: false`r`n"
$block += "        toolCallTimeoutMs: 180000`r`n"

# Self-mount: without it the plugin only shows up as a tag under the "mcp-client"
# card. dsh's plugin UI groups by loader module name, and the market decides
# "live" by looking for this bundle's package name among loaded entry names --
# so the name must resolve to a real package in the profile's node_modules.
# We stage a registration-only package (no python code) so there is never a
# second copy of the server to drift out of sync with C:\...\origin-bridge.
$pkgName = 'dsh-origin-bridge'
$pkgDir = Join-Path $profileDir "node_modules\$pkgName"
if (-not (Test-Path (Join-Path $pkgDir 'index.js'))) {
    New-Item -ItemType Directory -Force -Path $pkgDir | Out-Null
    @'
{
  "name": "dsh-origin-bridge",
  "version": "0.4.0",
  "description": "Origin Bridge: natural language" -> local Origin/OriginPro -> editable .opju + publication images",
  "main": "./index.js",
  "type": "module",
  "license": "MIT"
}
'@ | Set-Content -Path (Join-Path $pkgDir 'package.json') -Encoding ASCII
    @'
// Registration-only bundle. The Origin tools come from server.py in this
// package's own folder, registered by the mcp-origin-bridge insert in the
// profile's cordis.patch.yml. This module exists so dsh's plugin UI can show
// origin-bridge as its own card; keep apply() side-effect free because it
// runs on every dsh startup.
export const name = 'dsh-origin-bridge';
export function activate() {}
export function apply() {}
export default { name, activate, apply };
'@ | Set-Content -Path (Join-Path $pkgDir 'index.js') -Encoding ASCII
    Write-Host "staged registration-only package -> $pkgDir"
}

$block += "`r`n# self-mount: makes $pkgName appear as its own card in dsh's plugin UI.`r`n"
$block += "# apply() is a no-op; the tools come from the $entryId insert above.`r`n"
$block += "# NOTE: a future 'pnpm install' in this profile may prune this folder, which`r`n"
$block += "# only removes the card -- the origin_* tools keep working.`r`n"
$block += "- insert:`r`n"
$block += "    - id: $pkgName`r`n"
$block += "      name: $pkgName`r`n"

$existing = if (Test-Path $patch) { Get-Content $patch -Raw -Encoding UTF8 } else { '' }

# -RemoveOld: drop the previous plugin's insert block. Both entries pointed at
# C:\...\originlab-bridge, and a dead stdio server still shows up as a tool
# provider, so leaving it in place would mean two overlapping tool sets.
$removed = $false
if ($RemoveOld -and $existing -match [regex]::Escape($oldEntryId)) {
    $lines = @($existing -split "`r?`n")
    $from = -1; $to = $lines.Count
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($from -lt 0 -and $lines[$i] -match '^# --- originlab-bridge') { $from = $i; continue }
        if ($from -ge 0 -and $lines[$i] -match '^# --- ') { $to = $i; break }
    }
    if ($from -ge 0) {
        $kept = @($lines[0..($from - 1)]) + @($lines[$to..($lines.Count - 1)])
        $existing = ($kept -join "`r`n")
        $removed = $true
        Write-Host "removed the old $oldEntryId block (lines $from..$to)"
    } else {
        Write-Warning "$oldEntryId is referenced but its '# --- originlab-bridge' marker was not found; leaving it alone"
    }
}

# Back up once, before any write on either path below.
$backupDir = Join-Path $DshHome "backups\origin-bridge-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
New-Item -ItemType Directory -Force -Path $backupDir | Out-Null
if (Test-Path $patch) { Copy-Item $patch (Join-Path $backupDir 'cordis.patch.yml.bak') }

if ($existing -match [regex]::Escape($entryId)) {
    $tail = ''
    if ($existing -notmatch [regex]::Escape($pkgName)) {
        $tail = $block -replace '(?s)^.*?(# self-mount)', '$1'
    }
    if ($removed -or $tail) {
        Set-Content -Path $patch -Value ($existing + $tail) -Encoding UTF8 -NoNewline
    }
    if ($tail) {
        Write-Host "added the self-mount card entry only (tools insert was already present)"
    } elseif (-not $removed) {
        Write-Host "profile patch already has both entries; nothing appended"
    }
    $ErrorActionPreference = $prevEap
    exit 0
}

Set-Content -Path $patch -Value ($existing + $block) -Encoding UTF8 -NoNewline
Write-Host "appended insert block; backup -> $backupDir"

$checkers = @($PythonExe, (Get-Command python -ErrorAction SilentlyContinue).Source) |
    Where-Object { $_ -and (Test-Path $_ -ErrorAction SilentlyContinue) }
$checked = $false
$code = "import yaml,io,sys;d=yaml.safe_load(io.open(sys.argv[1],encoding='utf-8'));assert isinstance(d,list);" + `
        "ins=[e for e in d if isinstance(e,dict) and 'insert' in e];" + `
        "print('patch parses OK:',len(d),'entries; inserted ids:',[x.get('id') for e in ins for x in e['insert']])"
foreach ($c in $checkers) {
    & $c -c "import yaml" 2>$null | Out-Null
    if ($LASTEXITCODE -eq 0) {
        & $c -c $code "$patch"
        if ($LASTEXITCODE -ne 0) {
            $ErrorActionPreference = $prevEap
            throw "cordis.patch.yml does not parse; restore it from $backupDir"
        }
        $checked = $true
        break
    }
}
$ErrorActionPreference = $prevEap
if (-not $checked) { Write-Warning "no interpreter with PyYAML found; skipped YAML validation" }

Write-Host ""
Write-Host "DONE. Fully quit dsh (tray -> exit, not just close the window), reopen, then ask" -ForegroundColor Green
Write-Host "a new chat to list tools whose names contain origin_." -ForegroundColor Green
