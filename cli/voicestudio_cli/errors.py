"""One error contract for parsers, transports and command handlers."""
import re


class CLIError(Exception):
    def __init__(self, code: str, message: str, *, status: int | None = None,
                 exit_code: int = 1, retry_after: str | None = None, details=None):
        super().__init__(message)
        self.code, self.status, self.exit_code = code, status, exit_code
        self.retry_after = retry_after
        self.details = details


def scrub(value: str, secrets=()) -> str:
    for secret in secrets:
        if secret:
            value = value.replace(secret, '[redacted]')
    value = re.sub(r'(?i)(bearer\s+)[^\s"<>]+', r'\1[redacted]', value)
    value = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', value)
    value = re.sub(r'(?i)((?:api_key|token|password|pin)=)[^&\s]+', r'\1[redacted]', value)
    value = re.sub(r'(?i)("(?:api_key|token|password|access_token|refresh_token)"\s*:\s*)"(?:[^"\\]|\\.)*"',
                   r'\1"[redacted]"', value)
    # Strip remote control characters to prevent terminal escape injection.
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    return ''.join(c for c in value if c in '\n\t' or ord(c) >= 32).replace('\x7f', '')
