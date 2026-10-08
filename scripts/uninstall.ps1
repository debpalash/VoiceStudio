<#
.SYNOPSIS
  VoiceStudio — clean uninstaller (Windows).

.DESCRIPTION
  Finds every folder VoiceStudio wrote (app data, the managed Python env, config,
  logs) and — separately, because it's a SHARED cache — the Hugging Face model
  cache, prints each with its size, and removes them. Dry-run by default: it
  prints what it WOULD delete and stops, so you always see the plan first.

  Covers the Electron desktop app (its VoiceStudio app folder: managed Python
  runtime, window state, logs, updater cache) and the folders a final Tauri
  install left behind (com.debpalash.omnivoice-studio).

  Without -RemoveApp it never deletes the app itself (uninstall that via
  Settings > Apps), and never touches anything outside the paths it lists.
  Applying -RemoveApp requires a non-administrator PowerShell window; an
  elevated request stops before deleting data or invoking any uninstaller.
  Mirrors backend/core/config.py and electron/src/main/backend.ts.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -Yes
  powershell -ExecutionPolicy Bypass -File scripts\uninstall.ps1 -Yes -Models
#>
[CmdletBinding()]
param(
  [switch]$Yes,
  [switch]$Models,
  [switch]$RemoveApp
)

$ErrorActionPreference = 'Stop'
# Final Tauri installs used this identifier for their config + Python env.
$legacyIdentifier = 'com.debpalash.omnivoice-studio'
# Electron app folder (app.getPath('userData'); electron/src/main/app-identity.ts)
# and electron-updater's download cache (package name + "-updater").
$electronAppName = 'VoiceStudio'
$electronUpdaterCache = 'voicestudio-electron-updater'

# The installed app: the Electron NSIS installer (default `irm ... | iex`
# install) registers "Uninstall VoiceStudio.exe"; a final Tauri install is an
# MSI product.
$nsisUninstaller = $null
$nsisScope = $null
$msiProduct = $null
if ($RemoveApp) {
  # Refuse before registry lookup, analytics, or data cleanup. Checking a file's
  # ACL cannot make an arbitrary registered executable safe to run elevated:
  # another user may replace a writable ancestor between inspection and launch.
  $isElevated = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
  if ($Yes -and $isElevated) {
    throw 'App removal must run without administrator rights. No files were removed. Open a normal PowerShell window and rerun with -Yes -RemoveApp, or uninstall VoiceStudio through Settings > Apps first, then run -Yes without -RemoveApp to clean up its data.'
  }
  $uninstallKeys = @(
    'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
    'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*'
  )
  $entries = @(Get-ItemProperty $uninstallKeys -ErrorAction SilentlyContinue |
    Where-Object { $_.DisplayName -match 'VoiceStudio|OmniVoice' })
  # The actual removal path is unelevated. A registered uninstaller that needs
  # administrator privileges requests its own consent; this script never lends
  # an elevated token to a registry-selected executable.
  foreach ($entry in $entries) {
    if ([string]$entry.UninstallString -match '^"([^"]+\.exe)"(?:\s+(/currentuser|/allusers))?\s*$' -and
        (Split-Path $Matches[1] -Leaf) -eq 'Uninstall VoiceStudio.exe' -and
        (Test-Path -LiteralPath $Matches[1] -PathType Leaf)) {
      $candidate = [System.IO.Path]::GetFullPath($Matches[1])
      $scope = $Matches[2]
      $nsisUninstaller = $candidate
      $nsisScope = $scope
      break
    }
  }
  $msiProduct = $entries |
    Where-Object { [string]$_.UninstallString -match '(?i)msiexec' -and $_.PSChildName -match '^\{[0-9A-Fa-f-]+\}$' } |
    Select-Object -First 1
}

# ── Resolve platform default paths (mirrors the app) ────────────────────────
$appData   = [Environment]::GetEnvironmentVariable('APPDATA')
$localApp   = [Environment]::GetEnvironmentVariable('LOCALAPPDATA')

$dataDefault   = Join-Path $appData 'OmniVoice'
# Electron keeps its runtime, window state and logs in its app folder.
$electronUserData = Join-Path $appData $electronAppName
$electronCache = Join-Path $localApp $electronUpdaterCache
$legacyConfig = @((Join-Path $localApp $legacyIdentifier), (Join-Path $appData $legacyIdentifier))
# Windows model-cache default: OmniVoice redirects HF cache to a short path to
# dodge MAX_PATH, unless HF_HOME is set (see backend/core/config.py).
$modelsDefault = Join-Path (Join-Path $localApp 'OmniVoice') 'hf_cache'

# ── Apply the env overrides the app honors ──────────────────────────────────
$dataDir = if ($env:OMNIVOICE_DATA_DIR) { $env:OMNIVOICE_DATA_DIR } else { $dataDefault }
$modelsDir =
  if     ($env:OMNIVOICE_CACHE_DIR) { $env:OMNIVOICE_CACHE_DIR }
  elseif ($env:HF_HOME)             { $env:HF_HOME }
  elseif ($env:HF_HUB_CACHE)        { $env:HF_HUB_CACHE }
  else                              { $modelsDefault }

function Get-FolderSize($path) {
  if (-not (Test-Path -LiteralPath $path)) { return $null }
  try {
    $bytes = (Get-ChildItem -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue |
      Measure-Object -Property Length -Sum).Sum
    if (-not $bytes) { return '0 B' }
    $units = 'B','KB','MB','GB','TB'; $i = 0
    while ($bytes -ge 1024 -and $i -lt 4) { $bytes /= 1024; $i++ }
    return ('{0:N1} {1}' -f $bytes, $units[$i])
  } catch { return '?' }
}

# Final Tauri installs wrote backend logs here — a sibling of hf_cache under
# %LOCALAPPDATA%\OmniVoice, so it is covered by neither the app-data nor the
# config dir.
$logsDefault = Join-Path (Join-Path $localApp 'OmniVoice') 'Logs'

# A custom runtime location the Electron app created (and therefore owns) is
# recorded in runtime-location.json. Only an owned, absolute folder named
# VoiceStudio that holds the app's project is removed — the same rule the in-app uninstall applies; a reused
# Tauri environment is recorded unowned and kept.
$electronRuntime = $null
try {
  $locationFile = Join-Path $electronUserData 'runtime-location.json'
  if (Test-Path -LiteralPath $locationFile) {
    $location = Get-Content -LiteralPath $locationFile -Raw | ConvertFrom-Json
    $root = [string]$location.root
    if ($location.owned -eq $true -and [System.IO.Path]::IsPathRooted($root) -and
        (Split-Path $root -Leaf) -eq 'VoiceStudio' -and
        $root.TrimEnd('\') -ne (Join-Path $electronUserData 'runtime') -and
        (Test-Path -LiteralPath (Join-Path $root 'project') -PathType Container)) {
      $electronRuntime = $root
    }
  }
} catch {
  # A malformed location file only means there is no custom runtime to list.
}

# Durable per-user env file — backend/core/user_env.py uses expanduser('~/.config/
# omnivoice/env') on EVERY OS, so it lands under %USERPROFILE% on Windows too. It
# persists OMNIVOICE_CACHE_DIR (and can hold HF_TOKEN); leaving it behind silently
# redirected a fresh reinstall's model cache to the old location.
$userEnvDir = Join-Path ([Environment]::GetEnvironmentVariable('USERPROFILE')) '.config\omnivoice'

$appTargets = @()
foreach ($p in @($dataDir, $electronUserData, $electronRuntime, $electronCache) + $legacyConfig + @($logsDefault, $userEnvDir)) {
  if ($p -and (Test-Path -LiteralPath $p)) { $appTargets += $p }
}
if ($RemoveApp -and $nsisUninstaller) { $appTargets += "App: VoiceStudio ($nsisUninstaller)" }
if ($RemoveApp -and $msiProduct) { $appTargets += "MSI product: $($msiProduct.DisplayName)" }

Write-Host 'VoiceStudio uninstaller (Windows)'
Write-Host '---------------------------------'
if ($appTargets.Count -eq 0) {
  Write-Host 'No VoiceStudio app data / env / config folders found at the default or'
  Write-Host 'env-configured locations. Nothing to remove.'
} else {
  Write-Host 'App data, managed Python env, config, and logs:'
  foreach ($t in $appTargets) {
    if ($t -like 'MSI product:*' -or $t -like 'App: *') { Write-Host "  {-}        $t" }
    else { '  {0,-9} {1}' -f (Get-FolderSize $t), $t | Write-Host }
  }
}

$modelsPresent = Test-Path -LiteralPath $modelsDir
if ($modelsPresent) {
  Write-Host ''
  Write-Host 'Model cache (Hugging Face weights — SHARED with other HF tools):'
  '  {0,-9} {1}' -f (Get-FolderSize $modelsDir), $modelsDir | Write-Host
  Write-Host '  -> pass -Models to include this (it may hold models from OTHER apps too).'
}

Write-Host ''
if (-not $Yes) {
  Write-Host 'DRY RUN — nothing deleted. Re-run with -Yes to remove the listed folders'
  if ($modelsPresent) { Write-Host '         (add -Models to also remove the shared model cache).' }
  if (-not $RemoveApp) {
    Write-Host '         (add -RemoveApp to also uninstall the installed app).'
    Write-Host '         Or manually: Settings > Apps > VoiceStudio > Uninstall'
    Write-Host '         (listed as "OmniVoice Studio" if you have not updated since the rename).'
  }
  exit 0
}

# ── Opt-in uninstall ping (before anything is deleted) ──────────────────────
# If — and only if — the user opted in to anonymous analytics, send a single
# best-effort `app_uninstalled` event before the data (and the consent record
# it lives in) goes away. The backend writes analytics_info.json next to
# prefs.json ONLY while analytics is enabled (explicit consent + a build that
# ships a token) and deletes it on opt-out, so the file's presence is itself
# consent-gated; the prefs.json check is belt and braces. Content-free: the
# event carries the app version, the OS name, and the random per-install id —
# nothing else. Never blocks or fails the uninstall (2s timeout, silent
# failure). Not opted in => nothing is sent and nothing is printed.
try {
  $infoPath  = Join-Path $dataDir 'analytics_info.json'
  $prefsPath = Join-Path $dataDir 'prefs.json'
  if ((Test-Path -LiteralPath $infoPath) -and (Test-Path -LiteralPath $prefsPath)) {
    $prefs = Get-Content -LiteralPath $prefsPath -Raw | ConvertFrom-Json
    if ($prefs.analytics_enabled -eq $true) {
      $info = Get-Content -LiteralPath $infoPath -Raw | ConvertFrom-Json
      if ($info.token -and $info.host -and $info.distinct_id) {
        Write-Host 'Sending anonymous uninstall ping (you opted in to analytics).'
        $body = @{
          api_key     = $info.token
          event       = 'app_uninstalled'
          distinct_id = $info.distinct_id
          properties  = @{ app_version = "$($info.app_version)"; platform = "$($info.platform)" }
        } | ConvertTo-Json
        Invoke-RestMethod -Method Post -Uri "$($info.host)/capture/" `
          -ContentType 'application/json' -Body $body -TimeoutSec 2 | Out-Null
      }
    }
  }
} catch {
  # Best-effort by design: a dead network must never block the uninstall.
}

$deleted = 0
foreach ($t in $appTargets) {
  if ($t -like 'MSI product:*' -or $t -like 'App: *') { continue }
  Write-Host "Removing $t"
  Remove-Item -LiteralPath $t -Recurse -Force -ErrorAction SilentlyContinue
  $deleted++
}
if ($Models -and $modelsPresent) {
  Write-Host "Removing $modelsDir"
  Remove-Item -LiteralPath $modelsDir -Recurse -Force -ErrorAction SilentlyContinue
  $deleted++
} elseif ($modelsPresent) {
  Write-Host "Kept model cache ($modelsDir) — re-run with -Models to remove it."
}
if ($RemoveApp -and $nsisUninstaller) {
  Write-Host 'Uninstalling VoiceStudio...'
  $arguments = @('/S')
  if ($nsisScope) { $arguments = @($nsisScope) + $arguments }
  $process = Start-Process -FilePath $nsisUninstaller -ArgumentList $arguments -Wait -PassThru
  if ($process.ExitCode -in @(0, 3010)) { Write-Host 'VoiceStudio uninstalled.' }
  else { Write-Host "The uninstaller exited with $($process.ExitCode) — remove it via Settings > Apps if it is still listed." }
}
if ($RemoveApp -and $msiProduct) {
  Write-Host "Uninstalling $($msiProduct.DisplayName) via msiexec..."
  $process = Start-Process msiexec.exe -ArgumentList '/x', $msiProduct.PSChildName, '/norestart', '/qn' -Wait -PassThru
  if ($process.ExitCode -in @(0, 3010)) { Write-Host 'MSI product uninstalled.' }
  else { Write-Host "msiexec exited with $($process.ExitCode) — remove it via Settings > Apps if it is still listed." }
}

Write-Host ''
Write-Host "Done — removed $deleted folder(s)."
if (-not $RemoveApp) {
  Write-Host 'To also uninstall the app: re-run with -RemoveApp, or use Settings > Apps.'
}
