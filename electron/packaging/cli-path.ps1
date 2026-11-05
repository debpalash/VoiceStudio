# Called by NSIS; add/remove only the command directory owned by this installation.
param([ValidateSet('Install', 'Uninstall')][string]$Action,
      [ValidateSet('User', 'Machine')][string]$Scope = 'User')
$ErrorActionPreference = 'Stop'
$commandDir = [IO.Path]::GetFullPath($PSScriptRoot)
$current = [Environment]::GetEnvironmentVariable('Path', $Scope)
$entries = @($current -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ine $commandDir.TrimEnd('\') })
if ($Action -eq 'Install') { $entries = @($commandDir) + $entries }
[Environment]::SetEnvironmentVariable('Path', ($entries -join ';'), $Scope)
# Notify Explorer so newly opened terminals inherit the updated PATH.
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class VoiceStudioEnvironment {
  [DllImport("user32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
  public static extern IntPtr SendMessageTimeout(IntPtr window, uint message,
    UIntPtr wParam, string lParam, uint flags, uint timeout, out UIntPtr result);
}
'@
$result = [UIntPtr]::Zero
[void][VoiceStudioEnvironment]::SendMessageTimeout([IntPtr]0xffff, 0x1a,
  [UIntPtr]::Zero, 'Environment', 2, 5000, [ref]$result)
