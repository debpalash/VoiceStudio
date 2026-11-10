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
    ; Downloaded runtime and models are not user data, but they are gigabytes
    ; (#3911). Ask, default No (also for a silent /S uninstall). Voices and
    ; projects live in %APPDATA%\OmniVoice and are never touched here.
    MessageBox MB_YESNO|MB_ICONQUESTION|MB_DEFBUTTON2 "Also delete VoiceStudio's downloaded runtime and models to free disk space?$\r$\n$\r$\nThis removes $APPDATA\VoiceStudio\runtime and $LOCALAPPDATA\OmniVoice\hf_cache. Your voices and projects are kept.$\r$\n$\r$\nChoose No to keep them (for example, to reinstall without downloading again)." /SD IDNO IDNO voicestudio_keep_downloads
      ClearErrors
      RMDir /r "$APPDATA\VoiceStudio\runtime"
      ${If} ${Errors}
        MessageBox MB_OK|MB_ICONEXCLAMATION "Some files in $APPDATA\VoiceStudio\runtime could not be removed. Delete that folder manually to free the space." /SD IDOK
      ${EndIf}
      ClearErrors
      RMDir /r "$LOCALAPPDATA\OmniVoice\hf_cache"
      ${If} ${Errors}
        MessageBox MB_OK|MB_ICONEXCLAMATION "Some files in $LOCALAPPDATA\OmniVoice\hf_cache could not be removed. Delete that folder manually to free the space." /SD IDOK
      ${EndIf}
    voicestudio_keep_downloads:
  ${EndIf}
!macroend
