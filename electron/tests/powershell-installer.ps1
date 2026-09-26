# Cross-platform flow tests: network/process calls are mocked. Windows NSIS
# execution itself requires a Windows release smoke test.
$ErrorActionPreference = 'Stop'
$source = Get-Content "$PSScriptRoot/../../scripts/install.ps1" -Raw
$requiredVersionLines = @(
    '$vsVersion = $Version',
    '$vsVersion = (Get-Content',
    '$asset = "VoiceStudio-Electron-$vsVersion-win-x64.exe"'
)
foreach ($line in $requiredVersionLines) {
    if (-not $source.Contains($line)) { throw "Installer version must be isolated from PowerShell's Version parameter: $line" }
}
$tokens = $null; $parseErrors = $null
$null = [Management.Automation.Language.Parser]::ParseInput($source, [ref]$tokens, [ref]$parseErrors)
if ($parseErrors.Count) { throw ($parseErrors | Out-String) }
# Remove only the platform check to exercise the Windows flow on macOS/Linux.
if ([Environment]::OSVersion.Platform -ne 'Win32NT') { $source = $source.Replace("if ([Environment]::OSVersion.Platform -ne 'Win32NT') { throw 'Use install.sh on macOS or Linux.' }", '') }
$installer = [scriptblock]::Create($source)
$env:PROCESSOR_ARCHITECTURE = 'AMD64'
$env:VOICESTUDIO_VERSION = ''
$env:VOICESTUDIO_INSTALL_MODE = ''
$script:urls = [Collections.Generic.List[string]]::new()
$script:launched = $false
$script:corrupt = $false
$script:cancel = $false
$script:rateLimited = $false
$script:legacyResponse = $false
function Invoke-RestMethod($Uri) {
    $script:urls.Add($Uri)
    if ($script:rateLimited) { throw 'GitHub quota exceeded' }
    return @{ tag_name = 'v1.2.3' }
}
function Invoke-WebRequest($Uri, $OutFile, [switch]$UseBasicParsing) {
    $script:urls.Add($Uri)
    if ($Uri.EndsWith('/latest')) {
        $target = [uri]'https://github.com/debpalash/VoiceStudio/releases/tag/v1.2.3'
        if ($script:legacyResponse) { return @{ BaseResponse = @{ ResponseUri = $target } } }
        return @{ BaseResponse = @{ RequestMessage = @{ RequestUri = $target } } }
    }
    if ($Uri.EndsWith('.exe')) {
        [IO.File]::WriteAllText($OutFile, 'fixture installer')
        $script:downloaded = $OutFile
    } else {
        $hash = (Get-FileHash $script:downloaded -Algorithm SHA256).Hash
        if ($script:corrupt) { $hash = '0' * 64 }
        [IO.File]::WriteAllText($OutFile, "$hash  $([IO.Path]::GetFileName($script:downloaded))")
    }
}
function Start-Process($FilePath, [switch]$Wait, [switch]$PassThru, $ArgumentList) {
    if (-not (Test-Path $FilePath)) { throw 'Installer missing' }
    $script:launched = $true
    $script:launchCount++
    $script:lastArguments = $ArgumentList
    return @{ ExitCode = $(if ($script:cancel) { 1 } else { 0 }) }
}
foreach ($version in @('', 'v1.0.0')) {
    $script:urls.Clear(); $script:launched = $false
    & $installer -Version $version
    if (-not $script:launched) { throw 'Setup was not launched' }
    $selected = if ($version) { '1.0.0' } else { '1.2.3' }
    if (-not ($script:urls -match "download/v$selected/VoiceStudio-Electron-$selected-win-x64.exe")) { throw 'Wrong package selected' }
    if ($version -and ($script:urls -match '/latest')) { throw 'Pinned version consulted latest' }
    if (Test-Path $script:downloaded) { throw 'Temporary installer not cleaned up' }
}
$script:rateLimited = $true
foreach ($legacy in @($true, $false)) {
    $script:legacyResponse = $legacy
    $script:urls.Clear(); $script:launched = $false; $script:downloaded = $null; $script:launchCount = 0
    & $installer -Silent
    if (-not $script:launched -or $script:launchCount -ne 1) { throw 'Fallback must launch setup exactly once' }
    if (-not ($script:urls -match 'download/v1\.2\.3/VoiceStudio-Electron-1\.2\.3-win-x64\.exe')) { throw 'Rate-limit fallback selected wrong package' }
    if ($script:lastArguments -notcontains '/S') { throw 'Silent fallback lost its setup flag' }
    if (Test-Path $script:downloaded) { throw 'Fallback left its temporary installer behind' }
}
$script:rateLimited = $false
$script:corrupt = $true; $script:launched = $false
try { & $installer -Version '1.2.3'; throw 'Checksum accepted' } catch {
    if ($_.Exception.Message -notmatch 'Checksum mismatch') { throw }
}
if ($script:launched) { throw 'Corrupt installer launched' }
$script:corrupt = $false; $script:cancel = $true
try { & $installer -Version '1.2.3'; throw 'Cancellation accepted' } catch {
    if ($_.Exception.Message -notmatch 'cancelled') { throw }
}
Write-Output 'PASS: PowerShell latest, pinned version, checksum refusal, cancellation, and cleanup'
$uninstallRoot = Join-Path ([IO.Path]::GetTempPath()) ('vs-uninstall-test-' + [Guid]::NewGuid())
New-Item -ItemType Directory -Path $uninstallRoot | Out-Null
$script:uninstaller = Join-Path $uninstallRoot 'Uninstall VoiceStudio.exe'
[IO.File]::WriteAllText($script:uninstaller, 'fixture')
function Get-ItemProperty($Path, $ErrorAction) {
    return @(
        @{ DisplayName = 'VoiceStudio'; UninstallString = 'MsiExec.exe /X{legacy-tauri}' },
        @{ DisplayName = $script:displayName; UninstallString = '"' + $script:uninstaller + '" /currentuser' },
        @{ DisplayName = $script:displayName; UninstallString = '"' + $script:uninstaller + '" /allusers' },
        @{ DisplayName = 'VoiceStudio'; UninstallString = 'MsiExec.exe /X{other-legacy}' }
    )
}
try {
    $script:displayName = 'VoiceStudio 0.5.4'
    $script:cancel = $false; $script:launched = $false; $script:urls.Clear(); $script:launchCount = 0
    & $installer -Uninstall -Silent
    if ($script:launchCount -ne 1 -or $script:lastArguments -notcontains '/S' -or $script:lastArguments -notcontains '/currentuser') { throw 'Expected exactly one silent Electron uninstall' }
    if (-not $script:launched -or $script:urls.Count) { throw 'Uninstall must launch registered setup without downloading' }
    $script:displayName = 'VoiceStudio'
    $script:cancel = $true
    try { & $installer -Uninstall; throw 'Uninstall cancellation accepted' } catch {
        if ($_.Exception.Message -notmatch 'cancelled') { throw }
    }
    try { & $installer -Uninstall -Main; throw 'Conflicting modes accepted' } catch {
        if ($_.Exception.Message -notmatch 'cannot be combined') { throw }
    }
    Write-Output 'PASS: registered uninstall, no downloads, cancellation, conflicting modes'
} finally {
    Remove-Item -LiteralPath $uninstallRoot -Recurse -Force
}
