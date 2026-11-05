# Called by NSIS; add/remove only the command directory owned by this installation.
# PATH is edited in the registry as stored: %SystemRoot%-style entries stay
# unexpanded and the value keeps its type ([Environment]::SetEnvironmentVariable
# would expand every entry and rewrite the value as a plain string).
param([ValidateSet('Install', 'Uninstall')][string]$Action,
      [ValidateSet('User', 'Machine')][string]$Scope = 'User',
      # Tests point this at a scratch key under HKCU; installers never pass it.
      [string]$TestKey = '')
$ErrorActionPreference = 'Stop'
$commandDir = [IO.Path]::GetFullPath($PSScriptRoot)
if ($TestKey) {
  $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey($TestKey)
} elseif ($Scope -eq 'Machine') {
  $key = [Microsoft.Win32.Registry]::LocalMachine.OpenSubKey(
    'SYSTEM\CurrentControlSet\Control\Session Manager\Environment', $true)
} else {
  $key = [Microsoft.Win32.Registry]::CurrentUser.CreateSubKey('Environment')
}
try {
  $current = [string]$key.GetValue('Path', '',
    [Microsoft.Win32.RegistryValueOptions]::DoNotExpandEnvironmentNames)
  $kind = [Microsoft.Win32.RegistryValueKind]::ExpandString
  if ($key.GetValueNames() -contains 'Path') { $kind = $key.GetValueKind('Path') }
  $entries = @($current -split ';' | Where-Object { $_ -and $_.TrimEnd('\') -ine $commandDir.TrimEnd('\') })
  if ($Action -eq 'Install') { $entries = @($commandDir) + $entries }
  $key.SetValue('Path', ($entries -join ';'), $kind)
} finally {
  $key.Close()
}
if ($TestKey) { exit 0 }
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
