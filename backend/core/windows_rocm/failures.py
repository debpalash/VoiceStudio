"""Bounded probe categories; only positive package evidence permits repair."""

REPAIRABLE = frozenset({"package_missing", "package_version", "native_import", "binary_integrity"})
CATEGORIES = REPAIRABLE | {
    "native_runtime",
    "unsupported_target",
    "unsupported_gpu",
    "device_runtime",
    "timeout",
    "crash",
    "invalid_report",
    "unknown",
}


class VerificationFailure(RuntimeError):
    """A native-probe failure with a closed, machine-readable category."""

    def __init__(self, category, message):
        self.category = category if isinstance(category, str) and category in CATEGORIES else "unknown"
        super().__init__(str(message)[-2000:])

    @property
    def repairable(self):
        return self.category in REPAIRABLE


def failure_report(error):
    return {
        "ready": False,
        "scope": "model-free",
        "category": error.category if isinstance(error, VerificationFailure) else "unknown",
        "error": str(error)[-2000:],
    }
