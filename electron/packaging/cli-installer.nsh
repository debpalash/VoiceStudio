!macro customInstall
  StrCpy $0 "User"
  ${If} $installMode == "all"
    StrCpy $0 "Machine"
  ${EndIf}
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\cli\path.ps1" -Action Install -Scope $0'
  Pop $0
  ${If} $0 != 0
    ; /SD IDOK: a silent (/S) install must never wait on a dialog.
    MessageBox MB_OK|MB_ICONEXCLAMATION "VoiceStudio was installed, but its terminal command could not be added to PATH. Add $INSTDIR\resources\cli to PATH manually." /SD IDOK
  ${EndIf}
!macroend

!macro customUnInstall
  ; An update runs the old uninstaller first; if the update then rolls back, the
  ; restored files must still be on PATH. The new install re-adds the entry.
  ${IfNot} ${isUpdated}
    StrCpy $0 "User"
    ${If} $installMode == "all"
      StrCpy $0 "Machine"
    ${EndIf}
    nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\cli\path.ps1" -Action Uninstall -Scope $0'
    Pop $0
  ${EndIf}
!macroend
