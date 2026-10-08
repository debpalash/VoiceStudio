from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from collections.abc import Iterable as _Iterable, Mapping as _Mapping
from typing import ClassVar as _ClassVar, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class ErrorClass(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    ERROR_CLASS_UNSPECIFIED: _ClassVar[ErrorClass]
    ERROR_CLASS_TRANSIENT: _ClassVar[ErrorClass]
    ERROR_CLASS_CAPABILITY: _ClassVar[ErrorClass]
    ERROR_CLASS_TERMINAL: _ClassVar[ErrorClass]
    ERROR_CLASS_CAPACITY: _ClassVar[ErrorClass]
    ERROR_CLASS_TIMEOUT: _ClassVar[ErrorClass]
    ERROR_CLASS_PROTOCOL: _ClassVar[ErrorClass]
ERROR_CLASS_UNSPECIFIED: ErrorClass
ERROR_CLASS_TRANSIENT: ErrorClass
ERROR_CLASS_CAPABILITY: ErrorClass
ERROR_CLASS_TERMINAL: ErrorClass
ERROR_CLASS_CAPACITY: ErrorClass
ERROR_CLASS_TIMEOUT: ErrorClass
ERROR_CLASS_PROTOCOL: ErrorClass

class TaskRef(_message.Message):
    __slots__ = ("task_id", "attempt_id", "session_epoch")
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_EPOCH_FIELD_NUMBER: _ClassVar[int]
    task_id: str
    attempt_id: str
    session_epoch: int
    def __init__(self, task_id: _Optional[str] = ..., attempt_id: _Optional[str] = ..., session_epoch: _Optional[int] = ...) -> None: ...

class Envelope(_message.Message):
    __slots__ = ("sequence", "trace_id", "tenant_id")
    SEQUENCE_FIELD_NUMBER: _ClassVar[int]
    TRACE_ID_FIELD_NUMBER: _ClassVar[int]
    TENANT_ID_FIELD_NUMBER: _ClassVar[int]
    sequence: int
    trace_id: str
    tenant_id: str
    def __init__(self, sequence: _Optional[int] = ..., trace_id: _Optional[str] = ..., tenant_id: _Optional[str] = ...) -> None: ...

class Error(_message.Message):
    __slots__ = ("error_class", "code", "message", "hint")
    ERROR_CLASS_FIELD_NUMBER: _ClassVar[int]
    CODE_FIELD_NUMBER: _ClassVar[int]
    MESSAGE_FIELD_NUMBER: _ClassVar[int]
    HINT_FIELD_NUMBER: _ClassVar[int]
    error_class: ErrorClass
    code: str
    message: str
    hint: str
    def __init__(self, error_class: _Optional[_Union[ErrorClass, str]] = ..., code: _Optional[str] = ..., message: _Optional[str] = ..., hint: _Optional[str] = ...) -> None: ...

class GpuInfo(_message.Message):
    __slots__ = ("vendor", "model", "backend", "memory_bytes", "free_memory_bytes", "driver_version", "compute_capability")
    VENDOR_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    MEMORY_BYTES_FIELD_NUMBER: _ClassVar[int]
    FREE_MEMORY_BYTES_FIELD_NUMBER: _ClassVar[int]
    DRIVER_VERSION_FIELD_NUMBER: _ClassVar[int]
    COMPUTE_CAPABILITY_FIELD_NUMBER: _ClassVar[int]
    vendor: str
    model: str
    backend: str
    memory_bytes: int
    free_memory_bytes: int
    driver_version: str
    compute_capability: str
    def __init__(self, vendor: _Optional[str] = ..., model: _Optional[str] = ..., backend: _Optional[str] = ..., memory_bytes: _Optional[int] = ..., free_memory_bytes: _Optional[int] = ..., driver_version: _Optional[str] = ..., compute_capability: _Optional[str] = ...) -> None: ...

class HostInfo(_message.Message):
    __slots__ = ("hostname", "os", "arch", "worker_version", "cpu_count", "system_memory_bytes", "gpus")
    HOSTNAME_FIELD_NUMBER: _ClassVar[int]
    OS_FIELD_NUMBER: _ClassVar[int]
    ARCH_FIELD_NUMBER: _ClassVar[int]
    WORKER_VERSION_FIELD_NUMBER: _ClassVar[int]
    CPU_COUNT_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_MEMORY_BYTES_FIELD_NUMBER: _ClassVar[int]
    GPUS_FIELD_NUMBER: _ClassVar[int]
    hostname: str
    os: str
    arch: str
    worker_version: str
    cpu_count: int
    system_memory_bytes: int
    gpus: _containers.RepeatedCompositeFieldContainer[GpuInfo]
    def __init__(self, hostname: _Optional[str] = ..., os: _Optional[str] = ..., arch: _Optional[str] = ..., worker_version: _Optional[str] = ..., cpu_count: _Optional[int] = ..., system_memory_bytes: _Optional[int] = ..., gpus: _Optional[_Iterable[_Union[GpuInfo, _Mapping]]] = ...) -> None: ...

class ModelCapability(_message.Message):
    __slots__ = ("engine", "model_id", "operations", "supported", "installed", "downloaded", "resident", "min_memory_bytes", "precision", "derived_concurrency", "cpu_fallback", "repo_ids", "display_name", "backend", "free_memory_bytes")
    ENGINE_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    OPERATIONS_FIELD_NUMBER: _ClassVar[int]
    SUPPORTED_FIELD_NUMBER: _ClassVar[int]
    INSTALLED_FIELD_NUMBER: _ClassVar[int]
    DOWNLOADED_FIELD_NUMBER: _ClassVar[int]
    RESIDENT_FIELD_NUMBER: _ClassVar[int]
    MIN_MEMORY_BYTES_FIELD_NUMBER: _ClassVar[int]
    PRECISION_FIELD_NUMBER: _ClassVar[int]
    DERIVED_CONCURRENCY_FIELD_NUMBER: _ClassVar[int]
    CPU_FALLBACK_FIELD_NUMBER: _ClassVar[int]
    REPO_IDS_FIELD_NUMBER: _ClassVar[int]
    DISPLAY_NAME_FIELD_NUMBER: _ClassVar[int]
    BACKEND_FIELD_NUMBER: _ClassVar[int]
    FREE_MEMORY_BYTES_FIELD_NUMBER: _ClassVar[int]
    engine: str
    model_id: str
    operations: _containers.RepeatedScalarFieldContainer[str]
    supported: bool
    installed: bool
    downloaded: bool
    resident: bool
    min_memory_bytes: int
    precision: str
    derived_concurrency: int
    cpu_fallback: bool
    repo_ids: _containers.RepeatedScalarFieldContainer[str]
    display_name: str
    backend: str
    free_memory_bytes: int
    def __init__(self, engine: _Optional[str] = ..., model_id: _Optional[str] = ..., operations: _Optional[_Iterable[str]] = ..., supported: _Optional[bool] = ..., installed: _Optional[bool] = ..., downloaded: _Optional[bool] = ..., resident: _Optional[bool] = ..., min_memory_bytes: _Optional[int] = ..., precision: _Optional[str] = ..., derived_concurrency: _Optional[int] = ..., cpu_fallback: _Optional[bool] = ..., repo_ids: _Optional[_Iterable[str]] = ..., display_name: _Optional[str] = ..., backend: _Optional[str] = ..., free_memory_bytes: _Optional[int] = ...) -> None: ...

class RegisterRequest(_message.Message):
    __slots__ = ("envelope", "protocol_version_min", "protocol_version_max", "enrollment_token", "worker_id", "public_key", "challenge_signature", "challenge", "host", "capabilities", "max_concurrent_tasks", "in_flight", "completed_unacked", "key_id", "nonce", "labels", "features")
    class LabelsEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_MIN_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_MAX_FIELD_NUMBER: _ClassVar[int]
    ENROLLMENT_TOKEN_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    PUBLIC_KEY_FIELD_NUMBER: _ClassVar[int]
    CHALLENGE_SIGNATURE_FIELD_NUMBER: _ClassVar[int]
    CHALLENGE_FIELD_NUMBER: _ClassVar[int]
    HOST_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_TASKS_FIELD_NUMBER: _ClassVar[int]
    IN_FLIGHT_FIELD_NUMBER: _ClassVar[int]
    COMPLETED_UNACKED_FIELD_NUMBER: _ClassVar[int]
    KEY_ID_FIELD_NUMBER: _ClassVar[int]
    NONCE_FIELD_NUMBER: _ClassVar[int]
    LABELS_FIELD_NUMBER: _ClassVar[int]
    FEATURES_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    protocol_version_min: int
    protocol_version_max: int
    enrollment_token: str
    worker_id: str
    public_key: bytes
    challenge_signature: bytes
    challenge: bytes
    host: HostInfo
    capabilities: _containers.RepeatedCompositeFieldContainer[ModelCapability]
    max_concurrent_tasks: int
    in_flight: _containers.RepeatedCompositeFieldContainer[TaskRef]
    completed_unacked: _containers.RepeatedCompositeFieldContainer[TaskRef]
    key_id: str
    nonce: bytes
    labels: _containers.ScalarMap[str, str]
    features: _containers.RepeatedScalarFieldContainer[str]
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., protocol_version_min: _Optional[int] = ..., protocol_version_max: _Optional[int] = ..., enrollment_token: _Optional[str] = ..., worker_id: _Optional[str] = ..., public_key: _Optional[bytes] = ..., challenge_signature: _Optional[bytes] = ..., challenge: _Optional[bytes] = ..., host: _Optional[_Union[HostInfo, _Mapping]] = ..., capabilities: _Optional[_Iterable[_Union[ModelCapability, _Mapping]]] = ..., max_concurrent_tasks: _Optional[int] = ..., in_flight: _Optional[_Iterable[_Union[TaskRef, _Mapping]]] = ..., completed_unacked: _Optional[_Iterable[_Union[TaskRef, _Mapping]]] = ..., key_id: _Optional[str] = ..., nonce: _Optional[bytes] = ..., labels: _Optional[_Mapping[str, str]] = ..., features: _Optional[_Iterable[str]] = ...) -> None: ...

class ChallengeRequest(_message.Message):
    __slots__ = ("envelope",)
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ...) -> None: ...

class ChallengeResponse(_message.Message):
    __slots__ = ("envelope", "challenge", "expires_in_seconds")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    CHALLENGE_FIELD_NUMBER: _ClassVar[int]
    EXPIRES_IN_SECONDS_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    challenge: bytes
    expires_in_seconds: int
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., challenge: _Optional[bytes] = ..., expires_in_seconds: _Optional[int] = ...) -> None: ...

class RegisterResponse(_message.Message):
    __slots__ = ("envelope", "worker_id", "session_token", "session_epoch", "protocol_version", "session_expires_at_unix", "heartbeat_interval_seconds", "authoritative_in_flight", "error")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    WORKER_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_TOKEN_FIELD_NUMBER: _ClassVar[int]
    SESSION_EPOCH_FIELD_NUMBER: _ClassVar[int]
    PROTOCOL_VERSION_FIELD_NUMBER: _ClassVar[int]
    SESSION_EXPIRES_AT_UNIX_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_INTERVAL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    AUTHORITATIVE_IN_FLIGHT_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    worker_id: str
    session_token: str
    session_epoch: int
    protocol_version: int
    session_expires_at_unix: int
    heartbeat_interval_seconds: int
    authoritative_in_flight: _containers.RepeatedCompositeFieldContainer[TaskRef]
    error: Error
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., worker_id: _Optional[str] = ..., session_token: _Optional[str] = ..., session_epoch: _Optional[int] = ..., protocol_version: _Optional[int] = ..., session_expires_at_unix: _Optional[int] = ..., heartbeat_interval_seconds: _Optional[int] = ..., authoritative_in_flight: _Optional[_Iterable[_Union[TaskRef, _Mapping]]] = ..., error: _Optional[_Union[Error, _Mapping]] = ...) -> None: ...

class Heartbeat(_message.Message):
    __slots__ = ("envelope", "active_tasks", "available_slots", "resident_models", "free_memory_bytes", "cpu_percent", "gpu_utilization_percent")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ACTIVE_TASKS_FIELD_NUMBER: _ClassVar[int]
    AVAILABLE_SLOTS_FIELD_NUMBER: _ClassVar[int]
    RESIDENT_MODELS_FIELD_NUMBER: _ClassVar[int]
    FREE_MEMORY_BYTES_FIELD_NUMBER: _ClassVar[int]
    CPU_PERCENT_FIELD_NUMBER: _ClassVar[int]
    GPU_UTILIZATION_PERCENT_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    active_tasks: int
    available_slots: int
    resident_models: _containers.RepeatedScalarFieldContainer[str]
    free_memory_bytes: int
    cpu_percent: float
    gpu_utilization_percent: float
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., active_tasks: _Optional[int] = ..., available_slots: _Optional[int] = ..., resident_models: _Optional[_Iterable[str]] = ..., free_memory_bytes: _Optional[int] = ..., cpu_percent: _Optional[float] = ..., gpu_utilization_percent: _Optional[float] = ...) -> None: ...

class TaskAccepted(_message.Message):
    __slots__ = ("ref", "envelope")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ...) -> None: ...

class TaskRejected(_message.Message):
    __slots__ = ("ref", "envelope", "error")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    error: Error
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ..., error: _Optional[_Union[Error, _Mapping]] = ...) -> None: ...

class TaskModelLoading(_message.Message):
    __slots__ = ("ref", "envelope", "engine", "progress", "detail", "eta_seconds")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ENGINE_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    ETA_SECONDS_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    engine: str
    progress: float
    detail: str
    eta_seconds: int
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ..., engine: _Optional[str] = ..., progress: _Optional[float] = ..., detail: _Optional[str] = ..., eta_seconds: _Optional[int] = ...) -> None: ...

class TaskStarted(_message.Message):
    __slots__ = ("ref", "envelope")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ...) -> None: ...

class TaskProgress(_message.Message):
    __slots__ = ("ref", "envelope", "progress", "stage", "detail", "keepalive")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    STAGE_FIELD_NUMBER: _ClassVar[int]
    DETAIL_FIELD_NUMBER: _ClassVar[int]
    KEEPALIVE_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    progress: float
    stage: str
    detail: str
    keepalive: bool
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ..., progress: _Optional[float] = ..., stage: _Optional[str] = ..., detail: _Optional[str] = ..., keepalive: _Optional[bool] = ...) -> None: ...

class UsageReport(_message.Message):
    __slots__ = ("audio_seconds_in", "audio_seconds_out", "characters_in", "wall_seconds", "gpu_seconds", "model_load_seconds", "engine", "model_id")
    AUDIO_SECONDS_IN_FIELD_NUMBER: _ClassVar[int]
    AUDIO_SECONDS_OUT_FIELD_NUMBER: _ClassVar[int]
    CHARACTERS_IN_FIELD_NUMBER: _ClassVar[int]
    WALL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    GPU_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MODEL_LOAD_SECONDS_FIELD_NUMBER: _ClassVar[int]
    ENGINE_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    audio_seconds_in: float
    audio_seconds_out: float
    characters_in: int
    wall_seconds: float
    gpu_seconds: float
    model_load_seconds: float
    engine: str
    model_id: str
    def __init__(self, audio_seconds_in: _Optional[float] = ..., audio_seconds_out: _Optional[float] = ..., characters_in: _Optional[int] = ..., wall_seconds: _Optional[float] = ..., gpu_seconds: _Optional[float] = ..., model_load_seconds: _Optional[float] = ..., engine: _Optional[str] = ..., model_id: _Optional[str] = ...) -> None: ...

class TaskResult(_message.Message):
    __slots__ = ("ref", "envelope", "inline_payload", "artifacts", "result_json", "usage")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    INLINE_PAYLOAD_FIELD_NUMBER: _ClassVar[int]
    ARTIFACTS_FIELD_NUMBER: _ClassVar[int]
    RESULT_JSON_FIELD_NUMBER: _ClassVar[int]
    USAGE_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    inline_payload: bytes
    artifacts: _containers.RepeatedCompositeFieldContainer[ArtifactRef]
    result_json: str
    usage: UsageReport
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ..., inline_payload: _Optional[bytes] = ..., artifacts: _Optional[_Iterable[_Union[ArtifactRef, _Mapping]]] = ..., result_json: _Optional[str] = ..., usage: _Optional[_Union[UsageReport, _Mapping]] = ...) -> None: ...

class TaskFailed(_message.Message):
    __slots__ = ("ref", "envelope", "error", "usage")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    USAGE_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    error: Error
    usage: UsageReport
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ..., error: _Optional[_Union[Error, _Mapping]] = ..., usage: _Optional[_Union[UsageReport, _Mapping]] = ...) -> None: ...

class TaskCancelAck(_message.Message):
    __slots__ = ("ref", "envelope")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ...) -> None: ...

class Pong(_message.Message):
    __slots__ = ("envelope", "nonce")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    NONCE_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    nonce: int
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., nonce: _Optional[int] = ...) -> None: ...

class WorkerGoodbye(_message.Message):
    __slots__ = ("envelope", "reason", "abandoning")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ABANDONING_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    reason: str
    abandoning: _containers.RepeatedCompositeFieldContainer[TaskRef]
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., reason: _Optional[str] = ..., abandoning: _Optional[_Iterable[_Union[TaskRef, _Mapping]]] = ...) -> None: ...

class CapabilityUpdate(_message.Message):
    __slots__ = ("envelope", "capabilities")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    capabilities: _containers.RepeatedCompositeFieldContainer[ModelCapability]
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., capabilities: _Optional[_Iterable[_Union[ModelCapability, _Mapping]]] = ...) -> None: ...

class DownloadProgress(_message.Message):
    __slots__ = ("envelope", "event_json")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    EVENT_JSON_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    event_json: str
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., event_json: _Optional[str] = ...) -> None: ...

class WorkerMessage(_message.Message):
    __slots__ = ("heartbeat", "accepted", "rejected", "model_loading", "started", "progress", "result", "failed", "cancel_ack", "capabilities", "goodbye", "pong", "download_progress", "register")
    HEARTBEAT_FIELD_NUMBER: _ClassVar[int]
    ACCEPTED_FIELD_NUMBER: _ClassVar[int]
    REJECTED_FIELD_NUMBER: _ClassVar[int]
    MODEL_LOADING_FIELD_NUMBER: _ClassVar[int]
    STARTED_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_FIELD_NUMBER: _ClassVar[int]
    RESULT_FIELD_NUMBER: _ClassVar[int]
    FAILED_FIELD_NUMBER: _ClassVar[int]
    CANCEL_ACK_FIELD_NUMBER: _ClassVar[int]
    CAPABILITIES_FIELD_NUMBER: _ClassVar[int]
    GOODBYE_FIELD_NUMBER: _ClassVar[int]
    PONG_FIELD_NUMBER: _ClassVar[int]
    DOWNLOAD_PROGRESS_FIELD_NUMBER: _ClassVar[int]
    REGISTER_FIELD_NUMBER: _ClassVar[int]
    heartbeat: Heartbeat
    accepted: TaskAccepted
    rejected: TaskRejected
    model_loading: TaskModelLoading
    started: TaskStarted
    progress: TaskProgress
    result: TaskResult
    failed: TaskFailed
    cancel_ack: TaskCancelAck
    capabilities: CapabilityUpdate
    goodbye: WorkerGoodbye
    pong: Pong
    download_progress: DownloadProgress
    register: RegisterRequest
    def __init__(self, heartbeat: _Optional[_Union[Heartbeat, _Mapping]] = ..., accepted: _Optional[_Union[TaskAccepted, _Mapping]] = ..., rejected: _Optional[_Union[TaskRejected, _Mapping]] = ..., model_loading: _Optional[_Union[TaskModelLoading, _Mapping]] = ..., started: _Optional[_Union[TaskStarted, _Mapping]] = ..., progress: _Optional[_Union[TaskProgress, _Mapping]] = ..., result: _Optional[_Union[TaskResult, _Mapping]] = ..., failed: _Optional[_Union[TaskFailed, _Mapping]] = ..., cancel_ack: _Optional[_Union[TaskCancelAck, _Mapping]] = ..., capabilities: _Optional[_Union[CapabilityUpdate, _Mapping]] = ..., goodbye: _Optional[_Union[WorkerGoodbye, _Mapping]] = ..., pong: _Optional[_Union[Pong, _Mapping]] = ..., download_progress: _Optional[_Union[DownloadProgress, _Mapping]] = ..., register: _Optional[_Union[RegisterRequest, _Mapping]] = ...) -> None: ...

class Deadlines(_message.Message):
    __slots__ = ("accept_seconds", "model_load_seconds", "execution_seconds", "progress_lease_seconds", "result_delivery_seconds")
    ACCEPT_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MODEL_LOAD_SECONDS_FIELD_NUMBER: _ClassVar[int]
    EXECUTION_SECONDS_FIELD_NUMBER: _ClassVar[int]
    PROGRESS_LEASE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    RESULT_DELIVERY_SECONDS_FIELD_NUMBER: _ClassVar[int]
    accept_seconds: int
    model_load_seconds: int
    execution_seconds: int
    progress_lease_seconds: int
    result_delivery_seconds: int
    def __init__(self, accept_seconds: _Optional[int] = ..., model_load_seconds: _Optional[int] = ..., execution_seconds: _Optional[int] = ..., progress_lease_seconds: _Optional[int] = ..., result_delivery_seconds: _Optional[int] = ...) -> None: ...

class TaskAssignment(_message.Message):
    __slots__ = ("ref", "envelope", "operation", "engine", "model_id", "params_json", "inputs", "deadlines", "priority_class", "attempt_number", "max_attempts", "metadata")
    class MetadataEntry(_message.Message):
        __slots__ = ("key", "value")
        KEY_FIELD_NUMBER: _ClassVar[int]
        VALUE_FIELD_NUMBER: _ClassVar[int]
        key: str
        value: str
        def __init__(self, key: _Optional[str] = ..., value: _Optional[str] = ...) -> None: ...
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    OPERATION_FIELD_NUMBER: _ClassVar[int]
    ENGINE_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    PARAMS_JSON_FIELD_NUMBER: _ClassVar[int]
    INPUTS_FIELD_NUMBER: _ClassVar[int]
    DEADLINES_FIELD_NUMBER: _ClassVar[int]
    PRIORITY_CLASS_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_NUMBER_FIELD_NUMBER: _ClassVar[int]
    MAX_ATTEMPTS_FIELD_NUMBER: _ClassVar[int]
    METADATA_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    operation: str
    engine: str
    model_id: str
    params_json: str
    inputs: _containers.RepeatedCompositeFieldContainer[ArtifactRef]
    deadlines: Deadlines
    priority_class: int
    attempt_number: int
    max_attempts: int
    metadata: _containers.ScalarMap[str, str]
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ..., operation: _Optional[str] = ..., engine: _Optional[str] = ..., model_id: _Optional[str] = ..., params_json: _Optional[str] = ..., inputs: _Optional[_Iterable[_Union[ArtifactRef, _Mapping]]] = ..., deadlines: _Optional[_Union[Deadlines, _Mapping]] = ..., priority_class: _Optional[int] = ..., attempt_number: _Optional[int] = ..., max_attempts: _Optional[int] = ..., metadata: _Optional[_Mapping[str, str]] = ...) -> None: ...

class TaskCancel(_message.Message):
    __slots__ = ("ref", "envelope", "reason")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    reason: str
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ..., reason: _Optional[str] = ...) -> None: ...

class ResultAckMessage(_message.Message):
    __slots__ = ("ref", "envelope")
    REF_FIELD_NUMBER: _ClassVar[int]
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ref: TaskRef
    envelope: Envelope
    def __init__(self, ref: _Optional[_Union[TaskRef, _Mapping]] = ..., envelope: _Optional[_Union[Envelope, _Mapping]] = ...) -> None: ...

class ConfigUpdate(_message.Message):
    __slots__ = ("envelope", "heartbeat_interval_seconds", "max_concurrent_tasks", "inline_result_threshold_bytes")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    HEARTBEAT_INTERVAL_SECONDS_FIELD_NUMBER: _ClassVar[int]
    MAX_CONCURRENT_TASKS_FIELD_NUMBER: _ClassVar[int]
    INLINE_RESULT_THRESHOLD_BYTES_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    heartbeat_interval_seconds: int
    max_concurrent_tasks: int
    inline_result_threshold_bytes: int
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., heartbeat_interval_seconds: _Optional[int] = ..., max_concurrent_tasks: _Optional[int] = ..., inline_result_threshold_bytes: _Optional[int] = ...) -> None: ...

class PrewarmRequest(_message.Message):
    __slots__ = ("envelope", "engine", "model_id", "download_if_missing")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    ENGINE_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    DOWNLOAD_IF_MISSING_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    engine: str
    model_id: str
    download_if_missing: bool
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., engine: _Optional[str] = ..., model_id: _Optional[str] = ..., download_if_missing: _Optional[bool] = ...) -> None: ...

class ModelInstallCancelRequest(_message.Message):
    __slots__ = ("envelope", "model_id")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    MODEL_ID_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    model_id: str
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., model_id: _Optional[str] = ...) -> None: ...

class Ping(_message.Message):
    __slots__ = ("envelope", "nonce")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    NONCE_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    nonce: int
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., nonce: _Optional[int] = ...) -> None: ...

class Drain(_message.Message):
    __slots__ = ("envelope", "deadline_seconds", "reconnect_to")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    DEADLINE_SECONDS_FIELD_NUMBER: _ClassVar[int]
    RECONNECT_TO_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    deadline_seconds: int
    reconnect_to: str
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., deadline_seconds: _Optional[int] = ..., reconnect_to: _Optional[str] = ...) -> None: ...

class Shutdown(_message.Message):
    __slots__ = ("envelope", "reason")
    ENVELOPE_FIELD_NUMBER: _ClassVar[int]
    REASON_FIELD_NUMBER: _ClassVar[int]
    envelope: Envelope
    reason: str
    def __init__(self, envelope: _Optional[_Union[Envelope, _Mapping]] = ..., reason: _Optional[str] = ...) -> None: ...

class ServerMessage(_message.Message):
    __slots__ = ("assignment", "cancel", "result_ack", "config", "ping", "drain", "shutdown", "prewarm", "registered", "model_install_cancel")
    ASSIGNMENT_FIELD_NUMBER: _ClassVar[int]
    CANCEL_FIELD_NUMBER: _ClassVar[int]
    RESULT_ACK_FIELD_NUMBER: _ClassVar[int]
    CONFIG_FIELD_NUMBER: _ClassVar[int]
    PING_FIELD_NUMBER: _ClassVar[int]
    DRAIN_FIELD_NUMBER: _ClassVar[int]
    SHUTDOWN_FIELD_NUMBER: _ClassVar[int]
    PREWARM_FIELD_NUMBER: _ClassVar[int]
    REGISTERED_FIELD_NUMBER: _ClassVar[int]
    MODEL_INSTALL_CANCEL_FIELD_NUMBER: _ClassVar[int]
    assignment: TaskAssignment
    cancel: TaskCancel
    result_ack: ResultAckMessage
    config: ConfigUpdate
    ping: Ping
    drain: Drain
    shutdown: Shutdown
    prewarm: PrewarmRequest
    registered: RegisterResponse
    model_install_cancel: ModelInstallCancelRequest
    def __init__(self, assignment: _Optional[_Union[TaskAssignment, _Mapping]] = ..., cancel: _Optional[_Union[TaskCancel, _Mapping]] = ..., result_ack: _Optional[_Union[ResultAckMessage, _Mapping]] = ..., config: _Optional[_Union[ConfigUpdate, _Mapping]] = ..., ping: _Optional[_Union[Ping, _Mapping]] = ..., drain: _Optional[_Union[Drain, _Mapping]] = ..., shutdown: _Optional[_Union[Shutdown, _Mapping]] = ..., prewarm: _Optional[_Union[PrewarmRequest, _Mapping]] = ..., registered: _Optional[_Union[RegisterResponse, _Mapping]] = ..., model_install_cancel: _Optional[_Union[ModelInstallCancelRequest, _Mapping]] = ...) -> None: ...

class ArtifactRef(_message.Message):
    __slots__ = ("artifact_id", "task_id", "attempt_id", "filename", "content_type", "size_bytes", "sha256", "session_token")
    ARTIFACT_ID_FIELD_NUMBER: _ClassVar[int]
    TASK_ID_FIELD_NUMBER: _ClassVar[int]
    ATTEMPT_ID_FIELD_NUMBER: _ClassVar[int]
    FILENAME_FIELD_NUMBER: _ClassVar[int]
    CONTENT_TYPE_FIELD_NUMBER: _ClassVar[int]
    SIZE_BYTES_FIELD_NUMBER: _ClassVar[int]
    SHA256_FIELD_NUMBER: _ClassVar[int]
    SESSION_TOKEN_FIELD_NUMBER: _ClassVar[int]
    artifact_id: str
    task_id: str
    attempt_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    session_token: str
    def __init__(self, artifact_id: _Optional[str] = ..., task_id: _Optional[str] = ..., attempt_id: _Optional[str] = ..., filename: _Optional[str] = ..., content_type: _Optional[str] = ..., size_bytes: _Optional[int] = ..., sha256: _Optional[str] = ..., session_token: _Optional[str] = ...) -> None: ...

class ArtifactChunk(_message.Message):
    __slots__ = ("ref", "offset", "data", "last")
    REF_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    LAST_FIELD_NUMBER: _ClassVar[int]
    ref: ArtifactRef
    offset: int
    data: bytes
    last: bool
    def __init__(self, ref: _Optional[_Union[ArtifactRef, _Mapping]] = ..., offset: _Optional[int] = ..., data: _Optional[bytes] = ..., last: _Optional[bool] = ...) -> None: ...

class ResultChunk(_message.Message):
    __slots__ = ("ref", "offset", "data", "last", "session_token")
    REF_FIELD_NUMBER: _ClassVar[int]
    OFFSET_FIELD_NUMBER: _ClassVar[int]
    DATA_FIELD_NUMBER: _ClassVar[int]
    LAST_FIELD_NUMBER: _ClassVar[int]
    SESSION_TOKEN_FIELD_NUMBER: _ClassVar[int]
    ref: ArtifactRef
    offset: int
    data: bytes
    last: bool
    session_token: str
    def __init__(self, ref: _Optional[_Union[ArtifactRef, _Mapping]] = ..., offset: _Optional[int] = ..., data: _Optional[bytes] = ..., last: _Optional[bool] = ..., session_token: _Optional[str] = ...) -> None: ...

class ResultAck(_message.Message):
    __slots__ = ("artifact_id", "bytes_received", "committed", "error")
    ARTIFACT_ID_FIELD_NUMBER: _ClassVar[int]
    BYTES_RECEIVED_FIELD_NUMBER: _ClassVar[int]
    COMMITTED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    artifact_id: str
    bytes_received: int
    committed: bool
    error: Error
    def __init__(self, artifact_id: _Optional[str] = ..., bytes_received: _Optional[int] = ..., committed: _Optional[bool] = ..., error: _Optional[_Union[Error, _Mapping]] = ...) -> None: ...

class ArtifactAck(_message.Message):
    __slots__ = ("artifact_id", "bytes_received", "committed", "error")
    ARTIFACT_ID_FIELD_NUMBER: _ClassVar[int]
    BYTES_RECEIVED_FIELD_NUMBER: _ClassVar[int]
    COMMITTED_FIELD_NUMBER: _ClassVar[int]
    ERROR_FIELD_NUMBER: _ClassVar[int]
    artifact_id: str
    bytes_received: int
    committed: bool
    error: Error
    def __init__(self, artifact_id: _Optional[str] = ..., bytes_received: _Optional[int] = ..., committed: _Optional[bool] = ..., error: _Optional[_Union[Error, _Mapping]] = ...) -> None: ...
