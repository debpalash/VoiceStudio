# VoiceStudio Electron: latest, a release version, or build main.
param([string]$Version = $env:VOICESTUDIO_VERSION, [switch]$Main, [switch]$Source, [switch]$Uninstall, [switch]$Help, [switch]$Silent)
$ErrorActionPreference = 'Stop'
if ($Help) {
    Write-Output @'
VoiceStudio Electron installer (Windows x64)
  irm https://voicestudio.sh/install | iex
  $env:VOICESTUDIO_VERSION='X.Y.Z'; irm https://voicestudio.sh/install | iex
  $env:VOICESTUDIO_INSTALL_MODE='main'; irm https://voicestudio.sh/install | iex
File usage: .\install.ps1 [-Version X.Y.Z] [-Main | -Source] [-Silent]
Uninstall: .\install.ps1 -Uninstall (preserves user data)
  $env:VOICESTUDIO_INSTALL_MODE='uninstall'; irm https://voicestudio.sh/install.ps1 | iex
Main requires Git, Node.js 22+, Bun, Rust/Cargo, and MSVC build tools.
Only Electron releases are supported. Quit the app first; user data is preserved.
'@
    return
}
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Use install.sh on macOS or Linux.' }
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -and $env:PROCESSOR_ARCHITEW6432 -ne 'AMD64') { throw 'Windows x64 is required.' }
$mode = if ($Main -or $Source) { 'main' } elseif ($env:VOICESTUDIO_INSTALL_MODE) { $env:VOICESTUDIO_INSTALL_MODE } else { 'binary' }
if ($mode -eq 'source') { $mode = 'main' }
if ($Uninstall) { $mode = 'uninstall' }
if ($mode -eq 'uninstall') {
    if ($Main -or $Source -or $Version) { throw 'Uninstall cannot be combined with installation options; clear VOICESTUDIO_VERSION first.' }
    $keys = @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*')
    $entries = @(Get-ItemProperty $keys -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match '^VoiceStudio(?: \d+\.\d+\.\d+(?:-[A-Za-z0-9.-]+)?)?$' })
    foreach ($entry in $entries) {
        $command = [string]$entry.UninstallString
        if ($command -notmatch '^"([^"]+\.exe)"(?:\s+(/currentuser|/allusers))?\s*$') { continue }
        $exe = $Matches[1]
        $scope = $Matches[2]
        if ((Split-Path $exe -Leaf) -ne 'Uninstall VoiceStudio.exe' -or -not (Test-Path -LiteralPath $exe -PathType Leaf)) { continue }
        $launch = @{ FilePath = $exe; Wait = $true; PassThru = $true }
        $arguments = @()
        if ($scope) { $arguments += $scope }
        if ($Silent) { $arguments += '/S' }
        if ($arguments.Count) { $launch.ArgumentList = $arguments }
        $process = Start-Process @launch
        if ($process.ExitCode -notin @(0, 3010)) { throw "Uninstall failed or cancelled (exit $($process.ExitCode))." }
        break
    }
    Write-Output 'Uninstall complete (or app was not installed). User data is preserved.'
    return
}
if ($mode -notin @('binary', 'main')) { throw 'Install mode must be binary, main, source, or uninstall.' }
if ($mode -eq 'main' -and $Version) { throw 'Main cannot be combined with Version.' }
function Assert-Version([string]$Value) {
    if ($Value -notmatch '^\d+\.\d+\.\d+(-[A-Za-z0-9]+([.-][A-Za-z0-9]+)*)?$') { throw "Invalid version: $Value" }
}
function Run-Checked([string]$Command, [string[]]$Arguments) {
    & $Command @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Command failed (exit $LASTEXITCODE)." }
}
if ($Version) { $Version = $Version -replace '^v', ''; Assert-Version $Version }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$work = Join-Path ([IO.Path]::GetTempPath()) ('voicestudio-install-' + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $work | Out-Null
try {
    if ($mode -eq 'main') {
        foreach ($tool in @('git', 'node', 'bun', 'cargo')) {
            if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) { throw "Main builds require $tool; see docs/install/script.md." }
        }
        Run-Checked node @('-e', 'if (Number(process.versions.node.split(".")[0]) < 22) process.exit(1)')
        $checkout = Join-Path $work 'source'
        Run-Checked git @('clone', '--depth', '1', '--branch', 'main', '--single-branch', 'https://github.com/debpalash/VoiceStudio.git', $checkout)
        Push-Location $checkout
        try {
            Run-Checked git @('rev-parse', 'HEAD')
            Run-Checked bun @('install', '--frozen-lockfile')
            Push-Location 'electron'
            try {
                Run-Checked bun @('run', 'build')
                Run-Checked node @('tests/packaging-contract.mjs')
                Run-Checked bun @('run', 'electron-builder', '--config', 'electron-builder.config.mjs', '--publish', 'never', '--win', '--x64')
                Run-Checked node @('tests/update-package-contract.mjs')
            } finally { Pop-Location }
        } finally { Pop-Location }
        $Version = (Get-Content (Join-Path $checkout 'package.json') -Raw | ConvertFrom-Json).version
        Assert-Version $Version
        $packageDir = Join-Path $checkout 'electron/release'
    } else {
        if (-not $Version) {
            try {
                $release = Invoke-RestMethod 'https://api.github.com/repos/debpalash/VoiceStudio/releases/latest'
                $Version = $release.tag_name -replace '^v', ''
            } catch {
                $response = Invoke-WebRequest -UseBasicParsing 'https://github.com/debpalash/VoiceStudio/releases/latest'
                # HttpWebResponse on Windows PowerShell 5.1, HttpResponseMessage on 7.
                $uri = if ($response.BaseResponse.ResponseUri) { $response.BaseResponse.ResponseUri } else { $response.BaseResponse.RequestMessage.RequestUri }
                if ([string]$uri -notmatch '^https://github\.com/debpalash/VoiceStudio/releases/tag/v([^/?#]+)$') { throw 'Could not resolve the latest release tag.' }
                $Version = $Matches[1]
            }
            Assert-Version $Version
        }
        $packageDir = $work
    }
    $asset = "VoiceStudio-Electron-$Version-win-x64.exe"
    $package = Join-Path $packageDir $asset
    if ($mode -eq 'binary') {
        $base = "https://github.com/debpalash/VoiceStudio/releases/download/v$Version"
        Write-Output "Downloading $asset"
        Invoke-WebRequest -UseBasicParsing "$base/$asset" -OutFile $package
        $sums = Join-Path $work 'SHA256SUMS.txt'
        Invoke-WebRequest -UseBasicParsing "$base/SHA256SUMS.txt" -OutFile $sums
        $pattern = '^([A-Fa-f0-9]{64})\s+' + [regex]::Escape($asset) + '$'
        $entries = @(Get-Content $sums | Where-Object { $_ -match $pattern })
        if ($entries.Count -ne 1) { throw 'Missing, duplicate, or invalid checksum entry; refusing to install.' }
        $expected = ($entries[0] -split '\s+')[0]
        if ((Get-FileHash $package -Algorithm SHA256).Hash -ne $expected) { throw 'Checksum mismatch; refusing to install.' }
        Write-Output 'Checksum verified.'
    }
    if (-not (Test-Path $package -PathType Leaf)) { throw "Expected Electron package missing: $package" }
    Write-Output 'Opening Electron setup. Existing settings and models are preserved.'
    $launch = @{ FilePath = $package; Wait = $true; PassThru = $true }
    if ($Silent) { $launch.ArgumentList = @('/S') }
    $process = Start-Process @launch
    if ($process.ExitCode -notin @(0, 3010)) { throw "Setup failed or was cancelled (exit $($process.ExitCode))." }
    Write-Output 'Electron installed. Open VoiceStudio to configure the backend and choose models.'
} finally {
    Remove-Item -LiteralPath $work -Recurse -Force -ErrorAction SilentlyContinue
}
