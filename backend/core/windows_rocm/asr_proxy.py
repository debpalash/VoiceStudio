"""Reuse the owned ASR transport while preserving the normal backend's model."""
import atexit

from services.subprocess_asr import IsolatedFasterWhisperBackend


class RocmASRProxy(IsolatedFasterWhisperBackend):
    """Keep the normal backend's identity and options across owned ASR frames."""
    id = "faster-whisper"

    def __init__(self, model_source, device):
        """Bind the exact model source and the parent's effective device choice."""
        super().__init__()
        self.model_source = str(model_source)
        self.parent_device = device
        self.execution_device = self.execution_precision = None
        self.fallback_reason = self.fallback_stage = None
        self._retired = False

    def _spawn(self):
        """Never restart a delegate after its owner has unloaded it."""
        if self._retired:
            raise RuntimeError("ASR proxy was unloaded; retry with a fresh worker")
        return super()._spawn()

    def _device(self):
        """Describe execution failures without probing another native runtime."""
        return self.execution_device or self.parent_device

    def _send(self, message):
        """Attach the model and VAD contract to normal transcription frames."""
        if message.get("op") == "transcribe":
            message = dict(message, configuration={"model": self.model_source,
                                                  "vad_filter": True, "device": self.parent_device})
        return super()._send(message)

    def _recv_with_timeout(self, timeout_s):
        """Accept only worker-reported native device and precision evidence."""
        reply = super()._recv_with_timeout(timeout_s)
        if reply and reply.get("op") == "segments":
            evidence = reply.pop("execution", {})
            device, precision = evidence.get("device"), evidence.get("compute_type")
            if device not in {"cpu", "cuda"} or precision not in {
                "float32", "float16", "bfloat16", "int8", "int8_float32", "int8_float16",
                "int8_bfloat16", "int16",
            }:
                raise RuntimeError("ASR sidecar did not report its actual execution device/precision")
            self.execution_device, self.execution_precision = device, precision
            self.fallback_reason = evidence.get("fallback_reason")
            self.fallback_stage = evidence.get("fallback_stage")
        return reply

    def execution_evidence_loaded(self):
        """Require a live worker and an actual native model report."""
        return (self._proc is not None and self._proc.poll() is None
                and self.execution_device is not None)

    def close(self, wait=False):
        """Release an idle worker and its exit hook; optionally wait for ownership."""
        if not self._lock.acquire(blocking=wait):
            return False
        try:
            self._retired = True
            self.shutdown()
            # Do not retain every short-lived reference pass via its exit hook.
            atexit.unregister(self.shutdown)
            return True
        finally:
            self._lock.release()
