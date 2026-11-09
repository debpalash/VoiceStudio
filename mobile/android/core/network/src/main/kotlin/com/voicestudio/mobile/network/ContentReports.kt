package com.voicestudio.mobile.network

import java.io.IOException
import java.security.SecureRandom
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import kotlin.coroutines.resume

/** One generated result the person can report; nothing here is editable by them. */
data class ReportSubject(val id: String, val task: String, val runsOn: String, val modelId: String,
    val modelRevision: String? = null, val typedText: String? = null) {
    val hasTypedText get() = !typedText.isNullOrBlank()
}

/** The person's unsent answers. The key is made with the draft and reused on retry. */
data class ReportDraft(val reason: String? = null, val note: String = "", val includeText: Boolean = false,
    val idempotencyKey: String = ContentReport.newKey())

enum class ReportOutcome { SENT, RATE_LIMITED, INVALID, FAILED }
/** [idempotencyKey] is the key to keep on the draft: a 409 replaces it. */
data class ReportResult(val outcome: ReportOutcome, val idempotencyKey: String)

/** The content-report contract of mobile/contract/SPEC.md ("Reporting generated content"). */
object ContentReport {
    const val PATH = "v1/content-reports"
    const val NOTE_LIMIT = 1000
    const val PROMPT_LIMIT = 5000
    const val MODEL_ID_LIMIT = 128
    const val UNKNOWN_MODEL = "unknown"
    val REASONS = listOf("sexual", "hateful", "impersonation", "misleading", "other")
    private const val KEY_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    private val random = SecureRandom()

    fun newKey(source: SecureRandom = random): String =
        buildString(32) { repeat(32) { append(KEY_ALPHABET[source.nextInt(KEY_ALPHABET.length)]) } }

    /** Characters outside `0-9A-Za-z._/:-` are removed and the result capped; nothing left is `unknown`. */
    fun modelId(raw: String?): String = raw.orEmpty()
        .filter { it in '0'..'9' || it in 'A'..'Z' || it in 'a'..'z' || it in "._/:-" }
        .take(MODEL_ID_LIMIT).ifEmpty { UNKNOWN_MODEL }

    private fun revision(raw: String?): String? = raw.orEmpty()
        .filter { it in '0'..'9' || it in 'A'..'Z' || it in 'a'..'z' || it in "._-" }.take(64).ifEmpty { null }

    /** Cut at [limit] UTF-16 units without splitting a surrogate pair. */
    internal fun cap(text: String, limit: Int): String {
        if (text.length <= limit) return text
        val end = if (Character.isHighSurrogate(text[limit - 1])) limit - 1 else limit
        return text.substring(0, end)
    }

    /** Exactly the documented fields; empty optional fields are omitted. */
    fun body(subject: ReportSubject, draft: ReportDraft, appVersion: String): JsonObject = buildJsonObject {
        put("reason", requireNotNull(draft.reason) { "reason required" }.also { require(it in REASONS) })
        cap(draft.note.trim(), NOTE_LIMIT).trim().takeIf { it.isNotEmpty() }?.let { put("note", it) }
        if (draft.includeText) subject.typedText?.let { cap(it, PROMPT_LIMIT) }?.takeIf { it.isNotBlank() }?.let { put("prompt_text", it) }
        put("platform", "android")
        put("app_version", appVersion)
        put("task", subject.task)
        put("model_id", modelId(subject.modelId))
        if (subject.runsOn == "phone") revision(subject.modelRevision)?.let { put("model_revision", it) }
        put("runs_on", subject.runsOn)
    }
}

/**
 * Sends reports to VoiceStudio's app API. It has its own client: the paired
 * host's PIN, session and Authorization headers never reach this server.
 */
class ContentReportClient(baseUrl: String, private val appVersion: String, client: OkHttpClient? = null) {
    val endpoint: HttpUrl = baseUrl.toHttpUrl().also(HostPolicy::validate).let {
        it.newBuilder().encodedPath(it.encodedPath.trimEnd('/') + "/").build().resolve(ContentReport.PATH)
            ?: throw ClientFailure("mobile.connect.error_invalid_url")
    }
    private val client: OkHttpClient = client ?: OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(30, TimeUnit.SECONDS).writeTimeout(30, TimeUnit.SECONDS)
        // A redirect would repeat the report elsewhere; treat it as a failure.
        .followRedirects(false).followSslRedirects(false).build()

    /** Never throws for HTTP or connection failures; cancellation still propagates. */
    suspend fun send(subject: ReportSubject, draft: ReportDraft): ReportResult = withContext(Dispatchers.IO) {
        val body = ContentReport.body(subject, draft, appVersion).toString()
        var key = draft.idempotencyKey
        var status = post(body, key)
        if (status == 409) {
            // The key was used for a different body: send once more with a new one.
            key = ContentReport.newKey()
            status = post(body, key)
        }
        val outcome = when (status) {
            201 -> ReportOutcome.SENT
            429 -> ReportOutcome.RATE_LIMITED
            400 -> ReportOutcome.INVALID
            else -> ReportOutcome.FAILED
        }
        ReportResult(outcome, key)
    }

    /** The response status, or null when there was no response. */
    private suspend fun post(body: String, key: String): Int? {
        val request = Request.Builder().url(endpoint)
            .header("Idempotency-Key", key)
            .post(body.toRequestBody("application/json".toMediaType()))
            .build()
        return suspendCancellableCoroutine { cont ->
            val call = client.newCall(request)
            cont.invokeOnCancellation { call.cancel() }
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) { if (cont.isActive) cont.resume(null) }
                override fun onResponse(call: Call, response: Response) { response.use { if (cont.isActive) cont.resume(it.code) } }
            })
        }
    }
}
