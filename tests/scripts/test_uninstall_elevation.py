"""Run the PowerShell uninstaller with mocked Windows platform boundaries.

The script itself and its filesystem cleanup run for real. Identity, registry,
ACL and process-launch APIs are simulated; this is not a Windows ACL test.
"""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "uninstall.ps1"
PWSH = shutil.which("pwsh")
pytestmark = pytest.mark.skipif(PWSH is None, reason="PowerShell is unavailable")

HARNESS = r'''
param([string]$ScriptPath, [string]$Root, [string]$Elevated, [string]$Apply, [string]$Remove)
$ErrorActionPreference = 'Stop'
$global:caseRoot = $Root
$global:candidate = Join-Path $Root 'shared/VoiceStudio/Uninstall VoiceStudio.exe'
function Get-ItemProperty {
  [CmdletBinding()] param([string[]]$Path)
  [pscustomobject]@{
    DisplayName = 'VoiceStudio'
    UninstallString = '"' + $global:candidate + '" /allusers'
    PSPath = 'Microsoft.PowerShell.Core\Registry::HKEY_LOCAL_MACHINE\Software\Uninstall\VoiceStudio'
    PSChildName = 'VoiceStudio'
  }
}
function Get-Acl {
  param([string]$LiteralPath)
  # The leaf and immediate parent look administrator-only. The shared ancestor
  # is writable by another user: checking only those two ACLs cannot prove safety.
  Add-Content -LiteralPath (Join-Path $global:caseRoot 'acl-reads.txt') -Value $LiteralPath
  $acl = [pscustomobject]@{Shared=($LiteralPath -eq (Join-Path $global:caseRoot 'shared'))}
  $acl | Add-Member ScriptMethod GetOwner { param($type)
    [pscustomobject]@{Value='S-1-5-32-544'}
  }
  $acl | Add-Member ScriptMethod GetAccessRules { param($explicit,$inherited,$type)
    if ($this.Shared) {
      [pscustomobject]@{AccessControlType='Allow'; PropagationFlags=0;
        FileSystemRights=[Security.AccessControl.FileSystemRights]::FullControl;
        IdentityReference=[pscustomobject]@{Value='S-1-1-0'}}
    }
  }
  return $acl
}
function Start-Process {
  param($FilePath,$ArgumentList,[switch]$Wait,[switch]$PassThru)
  Set-Content -LiteralPath (Join-Path $global:caseRoot 'launched.txt') -Value $FilePath
  return [pscustomobject]@{ExitCode=0}
}
# Substitute only Windows identity detection assignments. All safety branches,
# registry selection, dry-run behavior and filesystem operations remain intact.
$source = Get-Content -LiteralPath $ScriptPath -Raw
$tokens = $null; $errors = $null
$ast = [System.Management.Automation.Language.Parser]::ParseInput($source,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'Uninstaller syntax errors' }
$assignments = $ast.FindAll({param($node)
  $node -is [System.Management.Automation.Language.AssignmentStatementAst] -and
  $node.Left.Extent.Text -in @('$isElevated','$currentIdentity')
}, $true) | Sort-Object { $_.Extent.StartOffset } -Descending
if (-not ($assignments | Where-Object {$_.Left.Extent.Text -eq '$isElevated'})) {
  throw 'No elevation detection assignment found'
}
foreach ($assignment in $assignments) {
  $replacement = if ($assignment.Left.Extent.Text -eq '$isElevated') {
    '$isElevated = $' + $Elevated
  } else {
    '$currentIdentity = [pscustomobject]@{User=[pscustomobject]@{Value="S-1-5-32-544"}}'
  }
  $source = $source.Remove($assignment.Extent.StartOffset,
    $assignment.Extent.EndOffset - $assignment.Extent.StartOffset).Insert(
    $assignment.Extent.StartOffset,$replacement)
}
$parameters = @{}
if ($Remove -eq 'true') { $parameters.RemoveApp=$true }
if ($Apply -eq 'true') { $parameters.Yes=$true }
& ([scriptblock]::Create($source)) @parameters
'''


def run_uninstaller(tmp_path, *, elevated, apply, remove_app=True):
    data = tmp_path / "appdata" / "OmniVoice"
    userdata = tmp_path / "appdata" / "VoiceStudio"
    installation = tmp_path / "shared" / "VoiceStudio"
    for folder in (data, userdata, installation, tmp_path / "localapp", tmp_path / "profile"):
        folder.mkdir(parents=True, exist_ok=True)
    (data / "project.txt").write_text("user project")
    (userdata / "settings.json").write_text("{}")
    executable = installation / "Uninstall VoiceStudio.exe"
    executable.write_text("synthetic executable placeholder; never executed")
    harness = tmp_path / "harness.ps1"
    harness.write_text(HARNESS)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("OMNIVOICE_", "HF_"))}
    env.update(APPDATA=str(tmp_path / "appdata"), LOCALAPPDATA=str(tmp_path / "localapp"),
               USERPROFILE=str(tmp_path / "profile"))
    result = subprocess.run(
        [PWSH, "-NoProfile", "-File", str(harness), "-ScriptPath", str(SCRIPT),
         "-Root", str(tmp_path), "-Elevated", str(elevated).lower(), "-Apply", str(apply).lower(),
         "-Remove", str(remove_app).lower()],
        env=env, capture_output=True, text=True, timeout=30,
    )
    return result, data, userdata, executable


def test_elevated_app_removal_preserves_resources_even_with_protected_leaf(tmp_path):
    result, data, userdata, executable = run_uninstaller(tmp_path, elevated=True, apply=True)
    assert result.returncode != 0
    assert "without administrator rights" in result.stdout + result.stderr
    assert (data / "project.txt").read_text() == "user project"
    assert (userdata / "settings.json").read_text() == "{}"
    assert executable.exists()
    assert not (tmp_path / "launched.txt").exists()
    assert not (tmp_path / "acl-reads.txt").exists(), "refuse rather than trusting a path ACL scan"


def test_unelevated_app_removal_keeps_existing_cleanup_and_launcher_flow(tmp_path):
    result, data, userdata, executable = run_uninstaller(tmp_path, elevated=False, apply=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not data.exists()
    assert not userdata.exists()
    assert (tmp_path / "launched.txt").read_text().strip() == str(executable)


def test_elevated_dry_run_lists_app_without_deleting_or_launching(tmp_path):
    result, data, userdata, executable = run_uninstaller(tmp_path, elevated=True, apply=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "DRY RUN" in result.stdout
    assert str(executable) in result.stdout
    assert data.exists() and userdata.exists() and executable.exists()
    assert not (tmp_path / "launched.txt").exists()


def test_elevated_data_only_cleanup_keeps_the_installed_app(tmp_path):
    result, data, userdata, executable = run_uninstaller(
        tmp_path, elevated=True, apply=True, remove_app=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not data.exists() and not userdata.exists()
    assert executable.exists()
    assert not (tmp_path / "launched.txt").exists()
