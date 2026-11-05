!macro customInstall
  StrCpy $0 "User"
  ${If} $installMode == "all"
    StrCpy $0 "Machine"
  ${EndIf}
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\cli\path.ps1" -Action Install -Scope $0'
  Pop $0
  ${If} $0 != 0
    MessageBox MB_OK|MB_ICONEXCLAMATION "VoiceStudio was installed, but its terminal command could not be added to PATH. Add $INSTDIR\resources\cli to PATH manually."
  ${EndIf}
!macroend

!macro customUnInstall
  StrCpy $0 "User"
  ${If} $installMode == "all"
    StrCpy $0 "Machine"
  ${EndIf}
  nsExec::ExecToLog '"$SYSDIR\WindowsPowerShell\v1.0\powershell.exe" -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "$INSTDIR\resources\cli\path.ps1" -Action Uninstall -Scope $0'
  Pop $0
!macroend
