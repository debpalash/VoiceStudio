package com.voicestudio.mobile.network

import java.io.File
import java.io.IOException
import java.net.SocketTimeoutException
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.*
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody
import okhttp3.RequestBody.Companion.toRequestBody
import okio.BufferedSink
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

class VoiceStudioApi(val address: PairingAddress, token: String? = null, private val expired: () -> Unit = {}) {
    @Volatile var sessionToken: String? = token
        private set
    val json = Json { ignoreUnknownKeys = true; explicitNulls = false }
    // One guarded client for REST, WebSocket, uploads, downloads and ExoPlayer.
    // Redirects never carry credentials outside the paired origin.
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS).readTimeout(10, TimeUnit.MINUTES).writeTimeout(10, TimeUnit.MINUTES)
        .followRedirects(false).followSslRedirects(false)
        .dns(object : Dns { override fun lookup(hostname: String): List<java.net.InetAddress> {
            val result = Dns.SYSTEM.lookup(hostname)
            if (address.baseUrl.scheme == "http" && result.any { !HostPolicy.isPrivateAddress(it) })
                throw ClientFailure("mobile.connect.error_insecure")
            return result
        } })
        .addInterceptor { chain ->
            val req = chain.request()
            HostPolicy.validate(req.url)
            if (!HostPolicy.sameOrigin(address.baseUrl, req.url)) throw ClientFailure("mobile.connect.error_invalid_url")
            val builder = req.newBuilder()
            address.pin?.let { builder.header("X-OmniVoice-Pin", it) }
            if (req.header("Authorization") == null) sessionToken?.let { builder.header("Authorization", "Bearer $it") }
            val response = chain.proceed(builder.build())
            if (response.code == 401 && req.url.encodedPath.endsWith("/api/auth/session").not()) expired()
            if(!response.isSuccessful && !(response.code == 101 && req.header("Upgrade").equals("websocket",true))) response.use { throw failure(it) }
            response
        }.build()

    fun url(path: String): HttpUrl {
        // Paths remain inside an optional reverse-proxy prefix. Absolute response URLs
        // must point at exactly the paired host; URLs from media metadata are untrusted.
        val resolved = if (path.startsWith("http://") || path.startsWith("https://")) {
            path.toHttpUrl()
        } else address.baseUrl.resolve(path.removePrefix("/")) ?: throw ClientFailure("mobile.connect.error_invalid_url")
        if (!HostPolicy.sameOrigin(address.baseUrl, resolved)) throw ClientFailure("mobile.connect.error_invalid_url")
        return resolved
    }
    private fun failure(response: Response): ClientFailure {
        return BackendErrors.parse(response.code, response.body?.string().orEmpty())
    }
    private suspend fun response(request: Request): Response = suspendCancellableCoroutine { cont ->
        val call = client.newCall(request)
        cont.invokeOnCancellation { call.cancel() }
        call.enqueue(object : Callback {
            override fun onFailure(call: Call, e: IOException) {
                if (cont.isActive) cont.resumeWithException(when(e) {
                    is ClientFailure -> e
                    is SocketTimeoutException -> ClientFailure("mobile.errors.timeout")
                    else -> ClientFailure("mobile.connect.error_unreachable", mapOf("host" to address.baseUrl.host))
                })
            }
            override fun onResponse(call: Call, response: Response) {
                if (!cont.isActive) { response.close(); return }
                cont.resume(response) { _, value, _ -> value.close() }
            }
        })
    }
    suspend fun raw(path: String, method: String = "GET", body: RequestBody? = null, bearer: String? = null): Response {
        val builder = Request.Builder().url(url(path)).method(method, body)
        bearer?.let { builder.header("Authorization", "Bearer $it") }
        return response(builder.build())
    }
    private suspend inline fun <reified T> get(path: String): T = withContext(Dispatchers.IO) { raw(path).use { json.decodeFromString(it.body!!.string()) } }
    private suspend inline fun <reified T> post(path: String, body: JsonObject): T = withContext(Dispatchers.IO) {
        raw(path, "POST", body.toString().toRequestBody("application/json".toMediaType())).use { json.decodeFromString(it.body!!.string()) }
    }
    suspend fun community(offset: Int = 0): CommunityPage {require(offset>=0);return get("/community/items?limit=100&offset=$offset")}
    suspend fun useCommunity(id: String): CommunityVoice = withContext(Dispatchers.IO) {
        raw("/community/items/${segment(id)}/use","POST",ByteArray(0).toRequestBody()).use {json.decodeFromString<CommunityVoice>(it.body!!.string())}
    }
    suspend fun health(): Health = withContext(Dispatchers.IO) {
        raw("/health").use { r ->
            val health = runCatching { json.decodeFromString<Health>(r.body!!.string()) }.getOrNull()
            if (r.header("x-omnivoice-backend").isNullOrBlank() || health?.status != "ok" || health.version.isBlank())
                throw ClientFailure("mobile.connect.error_not_voicestudio", mapOf("host" to address.baseUrl.host))
            health
        }
    }
    suspend fun exchange(key: String): Session = withContext(Dispatchers.IO) {
        try {
            raw("/api/auth/session", "POST", "{\"transport\":\"bearer\"}".toRequestBody("application/json".toMediaType()), key)
                .use { json.decodeFromString<Session>(it.body!!.string()).also { s -> sessionToken = s.token } }
        } catch (e: ClientFailure) { if(e.status == 401) throw ClientFailure("mobile.connect.error_api_key"); throw e }
    }
    suspend fun revoke() {
        if(sessionToken == null) return
        raw("/api/auth/session", "DELETE").close()
        sessionToken = null
    }
    suspend fun profiles(): List<Profile> = get("/profiles")
    suspend fun engines(): Engines = get("/engines")
    suspend fun translationEngine(): TranslationEngine {
        val engine = get<TranslationEngines>("/engines/translation").activeEngine()
        if(engine.category != "llm") return engine
        // Desktop hosts restrict settings to loopback; server hosts may permit the
        // paired administrator session. Only a confirmed absent/forbidden route means
        // "unknown" (consent). A slow or failing host is transient: surface it as
        // retryable so a durable job isn't rejected as "translation changed".
        val lookup = kotlinx.coroutines.withTimeoutOrNull(4000) {
            RouteLookup(try {
                val skill = get<LlmSkills>("/api/settings/llm-skills").skills
                    .firstOrNull { it.id == "dub_translation" && it.ready && it.enabled }
                val provider = skill?.provider?.let { id ->
                    get<LlmProviders>("/api/settings/llm-providers").providers.firstOrNull { it.id == id }
                }
                provider?.let { LlmRoute(it.id, it.displayName, it.baseUrl) }
            } catch(cancelled: kotlinx.coroutines.CancellationException) { throw cancelled }
            catch(error: ClientFailure) { if(error.status == 401 || error.isTransient()) throw error; null }
            catch(_: kotlinx.serialization.SerializationException) { null })
        } ?: throw ClientFailure("mobile.errors.timeout")
        val route = lookup.route
        return engine.copy(llmRoute = route)
    }
    suspend fun history(): List<HistoryItem> = get("/history")
    suspend fun dubHistory(): List<DubHistory> = get("/dub/history")
    suspend fun readiness(): Readiness = get("/dictation/readiness")
    suspend fun jobEvents(id: String, seq: Int): JobEvents = get("/jobs/${segment(id)}/events?after_seq=$seq")
    suspend fun jobs(): List<Job> = get("/jobs")
    suspend fun job(taskId: String): Job = get("/jobs/${segment(taskId)}")
    suspend fun star(id: String, value: Boolean) {
        raw("/history/${segment(id)}/starred", "PUT", "{\"starred\":$value}".toRequestBody("application/json".toMediaType())).close()
    }
    suspend fun deleteHistory(id: String) { raw("/history/${segment(id)}", "DELETE").close() }
    suspend fun deleteProfile(id: String) { raw("/profiles/${segment(id)}", "DELETE").close() }
    suspend fun rename(id: String, name: String) {
        raw("/profiles/${segment(id)}", "PUT", buildJsonObject { put("name", name) }.toString().toRequestBody("application/json".toMediaType())).close()
    }
    suspend fun clone(name: String, audio: File): Profile = withContext(Dispatchers.IO) {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("name", name)
            .addFormDataPart("kind", "clone").addFormDataPart("ref_audio", audio.name, audio.asRequestBody("application/octet-stream".toMediaType())).build()
        raw("/profiles", "POST", body).use { json.decodeFromString(it.body!!.string()) }
    }
    suspend fun generate(text: String, profile: String, engine: String, language: String): Generation = withContext(Dispatchers.IO) {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("text", text)
            .apply { if(profile.isNotBlank()) addFormDataPart("profile_id", profile) }
            .addFormDataPart("engine", engine).addFormDataPart("language", language).build()
        raw("/generate", "POST", body).use { r ->
            // Backend returns binary WAV and metadata in headers. Playback streams
            // the canonical host file; do not interpret this response as JSON.
            val id = r.header("X-Audio-Id") ?: throw ClientFailure("mobile.android.invalid_response")
            val path = r.header("X-Audio-Path") ?: throw ClientFailure("mobile.android.invalid_response")
            Generation(id, path, r.header("X-Audio-Duration")?.toDoubleOrNull() ?: 0.0)
        }
    }
    suspend fun upload(file: File, inputType: String, jobId: String, filename: String = file.name, progress: (Int) -> Unit): UploadResult = withContext(Dispatchers.IO) {
        val body = MultipartBody.Builder().setType(MultipartBody.FORM).addFormDataPart("input_type", inputType)
            .addFormDataPart("job_id", jobId)
            .addFormDataPart("video", filename, ProgressBody(file, progress)).build()
        raw("/dub/upload", "POST", body).use { json.decodeFromString(it.body!!.string()) }
    }
    suspend fun ingest(link: String, jobId: String): UploadResult = post("/dub/ingest-url", buildJsonObject { put("url", link); put("job_id", jobId) })
    suspend fun transcribe(jobId: String): Transcription = post("/dub/transcribe/${segment(jobId)}", buildJsonObject {})
    suspend fun translate(jobId: String, target: String, source: String?, segments: List<Segment>, authorization: TranslationAuthorization): Translation = post("/dub/translate", buildJsonObject {
        if(authorization.provider.isBlank() || (authorization.requiresConsent && !authorization.onlineConfirmed))
            throw ClientFailure("mobile.dub.translation_setup")
        put("provider", authorization.provider)
        put("job_id", jobId); put("target_lang", target); source?.let { put("source_lang", it) }
        putJsonArray("segments") { segments.forEachIndexed { i, s -> add(buildJsonObject {
            put("id", s.id?.jsonPrimitive?.content ?: i.toString()); put("text", s.text); put("start", s.start); put("end", s.end)
        }) } }
    })
    suspend fun startDub(jobId: String, code: String, language: String, voice: String, segments: List<Segment>): TaskResult = post("/dub/generate/${segment(jobId)}", buildJsonObject {
        DubVoiceChoice.validate(voice)
        put("language_code", code); put("language", language)
        putJsonArray("segments") { segments.forEach { s -> add(buildJsonObject {
            put("start", s.start); put("end", s.end); put("text", s.text); put("profile_id", voice); put("target_lang", code); putRenderSettings(s)
        }) } }
    })
    suspend fun abort(jobId: String) { raw("/dub/abort/${segment(jobId)}", "POST", ByteArray(0).toRequestBody()).close() }
    suspend fun ticket(): String = post<Ticket>("/api/auth/ws-ticket", buildJsonObject { put("path", "/ws/transcribe") }).ticket
    suspend fun dictationRequest(): Request {
        val u = url("/ws/transcribe").newBuilder().addQueryParameter("pcm", "1").addQueryParameter("sr", "16000")
        if (sessionToken != null) u.addQueryParameter("ws_ticket", ticket())
        return Request.Builder().url(u.build()).build()
    }
    suspend fun download(path: String, file: File) = withContext(Dispatchers.IO) {
        raw(path).use { r -> r.body!!.byteStream().use { input -> file.outputStream().use { input.copyTo(it) } }; r.body?.contentType()?.toString()?.substringBefore(';') }
    }
    fun audioPath(filename: String) = "/audio/${segment(filename.substringAfterLast('/'))}"
    companion object {
        fun segment(value: String): String = HttpUrl.Builder().scheme("https").host("localhost").addPathSegment(value).build().encodedPath.removePrefix("/")
    }
}

private class ProgressBody(val file: File, val progress: (Int) -> Unit) : RequestBody() {
    override fun contentType() = "application/octet-stream".toMediaType()
    override fun contentLength() = file.length()
    override fun writeTo(sink: BufferedSink) {
        file.inputStream().use { stream ->
            val buffer = ByteArray(64 * 1024); var sent = 0L; var previous = -1
            while (true) {
                val count = stream.read(buffer); if(count < 0) break
                sink.write(buffer, 0, count); sent += count
                val percent = (sent * 100 / file.length().coerceAtLeast(1)).toInt()
                if(percent != previous) { progress(percent); previous = percent }
            }
        }
    }
}

private class RouteLookup(val route: LlmRoute?)

/** Failures worth retrying: the host is slow, unreachable or temporarily overloaded. */
fun ClientFailure.isTransient(): Boolean =
    key == "mobile.connect.error_unreachable" || key == "mobile.errors.timeout" || (status ?: 0) in setOf(429, 500, 502, 503, 504)

@kotlinx.serialization.Serializable data class CommunityItem(val id: String,val name: String,val author: String? = null,val license: String? = null,val language: String? = null)
@kotlinx.serialization.Serializable data class CommunityPage(val total: Int,val items: List<CommunityItem>)
@kotlinx.serialization.Serializable data class CommunityVoice(val profile_id: String,val name: String)
