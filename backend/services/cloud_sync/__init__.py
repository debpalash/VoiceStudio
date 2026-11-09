"""VoiceStudio Cloud settings sync (S1): vssaas desktop-app-guide §3-8.

Every Cloud call goes through ``services.cloud_account.access_token()``; ``None``
means the user switched sync or account features off, so nothing is sent.
"""
