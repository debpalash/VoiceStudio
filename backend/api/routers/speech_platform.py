"""Discovery contract for VoiceStudio's local speech platform.

Interfaces should discover this document instead of hard-coding whichever
dictation route the desktop happens to use.  Endpoint URLs are relative so the
same response works on loopback, a tailnet GPU host, and a reverse proxy.
"""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from core.version import APP_VERSION

router = APIRouter(tags=["Speech Platform"])

SPEECH_PROTOCOL = "voicestudio.speech.v1"
STREAM_PATH = "/v1/audio/transcriptions/stream"


class EndpointCapability(BaseModel):
    path: str
    transport: Literal["http", "websocket", "mcp-streamable-http", "mcp-stdio"]
    method: str | None = None
    protocol: str | None = None


class StreamInputCapability(BaseModel):
    framing: Literal["binary"] = "binary"
    formats: list[str]
    default_format: str
    sample_rate_query: str = "sr"
    end_control: dict[str, str]


class StreamOutputCapability(BaseModel):
    framing: Literal["json"] = "json"
    events: list[str]
    final_kinds: list[str]


class SpeechFeatureCapabilities(BaseModel):
    batch_transcription: bool = True
    streaming_transcription: bool = True
    partial_transcripts: bool = True
    utterance_finals: bool = True
    session_summary: bool = True
    word_timestamps: bool = True
    local_refinement: bool = True
    acoustic_echo_cancellation: bool = True
    native_dictation_control: bool = False


class SpeechAuthCapabilities(BaseModel):
    loopback: Literal["none"] = "none"
    remote: Literal["bearer"] = "bearer"
    header: str = "Authorization: Bearer <OMNIVOICE_API_KEY>"
    browser_session_endpoint: str = "/api/auth/session"
    websocket_ticket_endpoint: str = "/api/auth/ws-ticket"
    websocket_ticket_query_parameter: Literal["ws_ticket"] = "ws_ticket"


class SpeechCapabilities(BaseModel):
    schema_: Literal["voicestudio.speech-capabilities"] = Field(
        default="voicestudio.speech-capabilities",
        serialization_alias="schema",
    )
    protocol: Literal["voicestudio.speech.v1"] = SPEECH_PROTOCOL
    protocol_version: Literal["1.0"] = "1.0"
    service: str = "VoiceStudio"
    service_version: str = APP_VERSION
    local_first: bool = True
    endpoints: dict[str, EndpointCapability]
    stream_input: StreamInputCapability
    stream_output: StreamOutputCapability
    features: SpeechFeatureCapabilities
    authentication: SpeechAuthCapabilities


def speech_capabilities() -> SpeechCapabilities:
    """Return the stable, side-effect-free integration contract."""
    endpoints = {
        "capabilities": EndpointCapability(
            path="/.well-known/voicestudio-speech",
            transport="http",
            method="GET",
        ),
        "batch_transcription": EndpointCapability(
            path="/v1/audio/transcriptions",
            transport="http",
            method="POST",
            protocol="openai.audio.transcriptions",
        ),
        "streaming_transcription": EndpointCapability(
            path=STREAM_PATH,
            transport="websocket",
            protocol=SPEECH_PROTOCOL,
        ),
        "mcp": EndpointCapability(
            path="/mcp",
            transport="mcp-streamable-http",
            method="POST",
            protocol="mcp",
        ),
        "mcp_stdio": EndpointCapability(
            path="python -m backend.mcp_shim",
            transport="mcp-stdio",
            protocol="mcp",
        ),
    }
    return SpeechCapabilities(
        endpoints=endpoints,
        stream_input=StreamInputCapability(
            formats=[
                "audio/pcm;encoding=s16le;channels=1",
                "audio/webm;codecs=opus",
            ],
            default_format="audio/webm;codecs=opus",
            end_control={"type": "input_audio.end"},
        ),
        stream_output=StreamOutputCapability(
            events=["session.started", "status", "partial", "final", "error"],
            final_kinds=["utterance", "summary"],
        ),
        # Native dictation control was served by the retired Tauri shell;
        # the field stays in the v1 schema and is always false.
        features=SpeechFeatureCapabilities(native_dictation_control=False),
        authentication=SpeechAuthCapabilities(),
    )


@router.get(
    "/.well-known/voicestudio-speech",
    response_model=SpeechCapabilities,
    response_model_by_alias=True,
)
@router.get(
    "/v1/audio/capabilities",
    response_model=SpeechCapabilities,
    response_model_by_alias=True,
)
async def get_speech_capabilities() -> SpeechCapabilities:
    """Advertise batch, streaming, and agent-facing speech transports."""
    return speech_capabilities()
