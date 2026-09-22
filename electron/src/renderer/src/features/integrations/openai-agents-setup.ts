import { backendEndpoint } from './mcp-setup';

/**
 * OpenAI Agents SDK voice pipeline pointed at this backend's OpenAI-compatible
 * API. The key is read from the environment at run time and never exported.
 */
export function openaiAgentsSetup(slug: string, baseUrl: string) {
  if (slug !== 'openai-agents') return null;
  const url = backendEndpoint(baseUrl, '/v1');
  if (!url) return null;
  const text = `# pip install "openai-agents[voice]"
import os

from agents import Agent, OpenAIChatCompletionsModel, set_tracing_disabled
from agents.voice import (
    OpenAIVoiceModelProvider,
    SingleAgentVoiceWorkflow,
    STTModelSettings,
    TTSModelSettings,
    VoicePipeline,
    VoicePipelineConfig,
)
from openai import AsyncOpenAI

set_tracing_disabled(True)  # keep traces on this machine

voicestudio = AsyncOpenAI(
    base_url=${JSON.stringify(url)},
    api_key=os.environ.get("OMNIVOICE_API_KEY", "not-needed-locally"),
)

# The agent's language model is yours to choose. Point it at a local
# OpenAI-compatible server (Ollama, LM Studio, llama.cpp, vLLM, ...); nothing
# falls back to a hosted model when these are unset.
llm = AsyncOpenAI(
    base_url=os.environ["AGENT_LLM_BASE_URL"],  # e.g. http://localhost:11434/v1
    api_key=os.environ.get("AGENT_LLM_API_KEY", "not-needed-locally"),
)
agent = Agent(
    name="Assistant",
    instructions="Be brief.",
    model=OpenAIChatCompletionsModel(model=os.environ["AGENT_LLM_MODEL"], openai_client=llm),
)

pipeline = VoicePipeline(
    workflow=SingleAgentVoiceWorkflow(agent),
    stt_model="gpt-4o-transcribe",  # served by VoiceStudio's active speech-recognition engine
    tts_model="gpt-4o-mini-tts",  # served by VoiceStudio's active voice engine
    config=VoicePipelineConfig(
        model_provider=OpenAIVoiceModelProvider(openai_client=voicestudio),
        stt_settings=STTModelSettings(language="en"),  # omit language to auto-detect
        tts_settings=TTSModelSettings(voice="alloy"),  # or a VoiceStudio voice-profile id
    ),
)
`;
  return {
    file: 'voicestudio_agents.py',
    docs: 'https://github.com/debpalash/VoiceStudio/blob/main/docs/agentic-voice.md#openai-agents-sdk',
    text,
  };
}
