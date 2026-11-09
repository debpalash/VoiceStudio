package com.voicestudio.mobile.network

import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonObjectBuilder
import kotlinx.serialization.json.put

@Serializable data class Health(val status: String, val version: String, val device: String? = null)
@Serializable data class Session(val token: String, @SerialName("expires_at") val expiresAt: Double, @SerialName("expires_in") val expiresIn: Int)
@Serializable data class Ticket(val ticket: String)
@Serializable data class Profile(val id: String, val name: String,
    @SerialName("audio_url") val audioUrl: String? = null, val language: String? = null,
    @SerialName("ref_audio_path") val refAudioPath: String? = null,
    @SerialName("locked_audio_path") val lockedAudioPath: String? = null) {
    val hasReferenceAudio get() = !refAudioPath.isNullOrBlank() || !lockedAudioPath.isNullOrBlank() || !audioUrl.isNullOrBlank()
}
@Serializable data class Engine(val id: String, @SerialName("display_name") val displayName: String = "", val available: Boolean = true,
    val reason: String? = null, val hint: String? = null,
    @SerialName("supports_cloning") val supportsCloning: Boolean? = null,
    @SerialName("supported_language_names") val languages: List<String> = emptyList())
@Serializable data class EngineFamily(val active: String = "", val backends: List<Engine> = emptyList())
@Serializable data class Engines(val tts: EngineFamily)
@Serializable data class HistoryItem(val id: String, val text: String = "", @SerialName("audio_path") val audioPath: String = "", val starred: JsonElement? = null) {
    val isStarred get() = starred.toString() in setOf("true", "1")
}
data class Generation(val id: String, val audioPath: String, val duration: Double)
@Serializable data class UploadResult(@SerialName("job_id") val jobId: String, @SerialName("task_id") val taskId: String, val filename: String = "")
@Serializable data class TaskResult(@SerialName("task_id") val taskId: String)
@Serializable data class Job(val id: String = "", val status: String, val error: String? = null,
    @SerialName("last_event") val lastEvent: JsonObject? = null)
@Serializable data class Segment(val id: JsonElement? = null, val start: Double, val end: Double, val text: String, @SerialName("speaker_id") val speakerId: String? = null,@SerialName("text_original") val textOriginal: String? = null, val gain: Double? = null, val speed: Double? = null, val direction: String? = null, @SerialName("effect_preset") val effectPreset: String? = null, val instruct: String? = null)
/** Keep saved rendering overrides while callers choose new words, voice and language. */
internal fun JsonObjectBuilder.putRenderSettings(segment: Segment) {
    segment.gain?.let {put("gain",it)}
    segment.speed?.let {put("speed",it)}
    segment.direction?.let {put("direction",it)}
    segment.effectPreset?.let {put("effect_preset",it)}
    segment.instruct?.let {put("instruct",it)}
}
@Serializable data class Transcription(val segments: List<Segment>, @SerialName("source_lang") val sourceLang: String? = null)
@Serializable data class TranslatedSegment(val id: JsonElement, val text: String, val error: String? = null)
@Serializable data class Translation(val translated: List<TranslatedSegment>)
@Serializable data class DubHistory(val id: String, @SerialName("job_id") val jobId: String = "", val filename: String = "",
    val language: String? = null, val duration: Double? = null)
@Serializable data class RecommendedModel(val label: String = "", @SerialName("size_gb") val sizeGb: Double? = null)
@Serializable data class MissingModel(val error: String = "", val recommended: RecommendedModel? = null,
    @SerialName("missing_repo_id") val missingRepoId: String? = null, val message: String? = null)
@Serializable data class Readiness(val ready: Boolean, val missing: MissingModel? = null)
@Serializable data class DictationEvent(val type: String, val text: String = "", val message: String? = null,
    @SerialName("final_kind") val finalKind: String? = null,
    @SerialName("refined_text") val refinedText: String? = null)

@Serializable data class JobEvent(val seq: Int, val payload: String)
@Serializable data class JobEvents(val job: Job, val events: List<JobEvent>)
