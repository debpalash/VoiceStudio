package com.voicestudio.mobile.network

import java.io.File
import java.io.IOException
import java.net.SocketTimeoutException
import java.security.MessageDigest
import java.util.UUID
import java.util.concurrent.TimeUnit
import kotlinx.coroutines.*
import kotlinx.coroutines.Job as CoroutineJob
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.*
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.toRequestBody
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException

private fun diagnosticUTF8(bytes: ByteArray): String = Charsets.UTF_8.newDecoder()
    .onMalformedInput(java.nio.charset.CodingErrorAction.REPORT)
    .onUnmappableCharacter(java.nio.charset.CodingErrorAction.REPORT)
    .decode(java.nio.ByteBuffer.wrap(bytes)).toString()
private fun diagnosticString(value: JsonElement?): String? = (value as? JsonPrimitive)?.takeIf { it.isString }?.content
private fun diagnosticInteger(value: JsonElement?): Long? = (value as? JsonPrimitive)?.takeIf { !it.isString }?.longOrNull

internal fun diagnosticKillSwitch(value: String?): Boolean = value?.trim()?.lowercase(java.util.Locale.ROOT) in setOf("1", "true", "yes", "on")

private fun diagnosticDestinationURL(value:String):HttpUrl {
    val authority=requireNotNull(Regex("^([a-z][a-z0-9+.-]*)://([^/?#]*)",RegexOption.IGNORE_CASE).find(value))
    if(authority.groupValues[1].equals("http",ignoreCase=true)) {
        // Check the spelling before OkHttp percent-decodes hostnames or
        // canonicalizes IPv6. Only these explicit local exceptions are allowed.
        require(Regex("(?:localhost|127\\.0\\.0\\.1|\\[::1\\])(?::[0-9]+)?",RegexOption.IGNORE_CASE).matches(authority.groupValues[2]))
    }
    return value.toHttpUrl()
}

/** The canonical config/diagnostics.json, supplied by the host app's generated resources. */
class DiagnosticPolicy(json: String) {
    private val value = Json.parseToJsonElement(json).jsonObject
    init { require(diagnosticInteger(value["schema_version"]) == 1L) }
    private fun values(key: String): Set<String> = value[key]?.jsonArray?.mapNotNull {
        (it as? JsonPrimitive)?.takeIf(JsonPrimitive::isString)?.content
    }?.toSet().orEmpty()
    private val enums = listOf("categories", "stages", "error_codes", "error_types", "engine_ids", "reasons", "platforms", "arches", "device_families", "signals", "buckets")
        .associateWith(::values)
    private val paths = values("frame_paths")
    fun known(key: String, candidate: String?, fallback: String): String =
        candidate?.takeIf { it.length <= 64 && it in enums[key].orEmpty() } ?: fallback
    fun limit(key: String, ceiling: Int): Int = value["limits"]?.jsonObject?.get(key)?.jsonPrimitive?.intOrNull
        ?.takeIf { it > 0 }?.coerceAtMost(ceiling) ?: ceiling
    fun frames(frames: List<DiagnosticFrame>): String = buildList {
        var size = 0
        frames.take(24).forEach { frame ->
            if (frame.path.length > 512 || frame.path !in paths || frame.line !in 1..10_000_000 || frame.column != null && frame.column !in 1..10_000_000) return@forEach
            val line = "${frame.path}:${frame.line}" + (frame.column?.let { ":$it" } ?: "")
            val bytes = line.toByteArray(Charsets.UTF_8).size + if (isEmpty()) 0 else 1
            if (size + bytes <= limit("frame_bytes", 1800)) { add(line); size += bytes }
        }
    }.joinToString("\n")
    internal fun parseFrames(raw: String?): List<DiagnosticFrame> = raw?.takeIf { it.toByteArray().size <= limit("frame_bytes", 1800) }
        ?.split('\n')?.take(24)?.mapNotNull { line ->
            val match = Regex("([^:\\\\]+):(\\d{1,8})(?::(\\d{1,8}))?").matchEntire(line) ?: return@mapNotNull null
            DiagnosticFrame(match.groupValues[1], match.groupValues[2].toInt(), match.groupValues[3].toIntOrNull())
        }.orEmpty()
}

data class DiagnosticFrame(val path: String, val line: Int, val column: Int? = null)
data class DiagnosticInput(val category: String, val stage: String = "other", val errorCode: String? = null,
    val errorType: String = "OtherError", val engineID: String? = null, val frames: List<DiagnosticFrame> = emptyList(),
    val reason: String? = null, val deviceFamily: String? = null, val exitCode: Long? = null, val signal: String? = null)
data class DiagnosticStatus(val enabled: Boolean = false, val optedIn: Boolean = false, val prompted: Boolean = false,
    val available: Boolean = true, val pendingCount: Int = 0, val droppedCount: Int = 0, val consentSaveFailed: Boolean = false)
data class DiagnosticRecoveryContext(val epoch: String, val enabledAtMs: Long, val appVersion: String)

data class DiagnosticDestination(val site: String, val host: String, val appUrl: String) {
    internal val endpoint: HttpUrl
    internal val origin: String
    internal val eventUrl: String
    init {
        require(Regex("[a-z0-9]{6,32}").matches(site))
        val server = diagnosticDestinationURL(host); val app = diagnosticDestinationURL(appUrl)
        for (url in listOf(server, app)) {
            require(url.username.isEmpty() && url.password.isEmpty() && url.query == null && url.fragment == null)
            // Literal loopback exceptions need no DNS and cannot expose metadata
            // to a LAN/public collector over an unencrypted connection.
            require(url.isHttps || url.host in setOf("localhost", "127.0.0.1", "::1"))
        }
        require(server.encodedPath == "/")
        endpoint = server.newBuilder().encodedPath("/api/public/v1/events").build()
        origin = "${app.scheme}://${app.host.let { if (':' in it) "[$it]" else it }}" + if (app.port == if (app.isHttps) 443 else 80) "" else ":${app.port}"
        eventUrl = app.toString().trimEnd('/') + "/diagnostics"
    }
}

/** Both methods are called only by the owned IO worker, never by capture. */
interface DiagnosticStorage {
    fun read(name: String, maxBytes: Int): ByteArray?
    fun write(name: String, bytes: ByteArray)
    fun remove(name: String)
}
class FileDiagnosticStorage(private val directory: File) : DiagnosticStorage {
    private fun target(name: String): File {
        require(name in setOf("consent.json", "outbox.json"))
        return File(directory, name)
    }
    override fun read(name: String, maxBytes: Int): ByteArray? {
        val file = target(name); if (!file.exists()) return null
        file.inputStream().use { stream ->
            val output = java.io.ByteArrayOutputStream()
            val buffer = ByteArray(8192)
            while (output.size() <= maxBytes) {
                val count = stream.read(buffer, 0, minOf(buffer.size, maxBytes + 1 - output.size()))
                if (count < 0) break
                output.write(buffer, 0, count)
            }
            require(output.size() <= maxBytes) { "Diagnostic store exceeded its bound" }
            return output.toByteArray()
        }
    }
    override fun write(name: String, bytes: ByteArray) {
        check(directory.isDirectory || directory.mkdirs())
        directory.setReadable(false, false); directory.setReadable(true, true)
        directory.setWritable(false, false); directory.setWritable(true, true)
        directory.setExecutable(false, false); directory.setExecutable(true, true)
        val file = target(name); val temporary = File.createTempFile(".diagnostic-", ".tmp", directory)
        try {
            temporary.setReadable(false, false); temporary.setReadable(true, true)
            temporary.setWritable(false, false); temporary.setWritable(true, true)
            temporary.outputStream().use { it.write(bytes); it.fd.sync() }
            // Same-directory rename replaces atomically on Android/Linux.
            java.nio.file.Files.move(temporary.toPath(), file.toPath(), java.nio.file.StandardCopyOption.REPLACE_EXISTING, java.nio.file.StandardCopyOption.ATOMIC_MOVE)
        } finally { temporary.delete() }
    }
    override fun remove(name: String) {
        val file = target(name); if (file.exists() && !file.delete()) throw IOException("Diagnostic cleanup failed")
    }
}

interface DiagnosticClock {
    fun nowMs(): Long
    suspend fun sleep(ms: Long)
}
object SystemDiagnosticClock : DiagnosticClock {
    override fun nowMs() = System.currentTimeMillis()
    override suspend fun sleep(ms: Long) = delay(ms)
}
interface DiagnosticTransport {
    suspend fun send(destination: DiagnosticDestination, body: ByteArray, allowed: () -> Boolean): Int
    fun cancelAll()
}
/** Dedicated client: paired-host headers, cookies, authentication and redirects never participate. */
class OkHttpDiagnosticTransport : DiagnosticTransport {
    private val client = OkHttpClient.Builder().cookieJar(CookieJar.NO_COOKIES)
        .authenticator(Authenticator.NONE).proxyAuthenticator(Authenticator.NONE)
        .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false)
        .connectTimeout(2, TimeUnit.SECONDS).readTimeout(2, TimeUnit.SECONDS).writeTimeout(2, TimeUnit.SECONDS)
        .callTimeout(2, TimeUnit.SECONDS).build()
    private val lock = Any()
    private val calls = mutableSetOf<Call>()
    override suspend fun send(destination: DiagnosticDestination, body: ByteArray, allowed: () -> Boolean): Int = suspendCancellableCoroutine { continuation ->
        val request = Request.Builder().url(destination.endpoint).header("Origin", destination.origin)
            .post(body.toRequestBody("application/json".toMediaType())).build()
        val call = client.newCall(request)
        continuation.invokeOnCancellation { call.cancel() }
        synchronized(lock) {
            if (!continuation.isActive || !allowed()) { continuation.cancel(); return@synchronized }
            calls.add(call)
            call.enqueue(object : Callback {
                override fun onFailure(call: Call, e: IOException) {
                    synchronized(lock) { calls.remove(call) }
                    if (continuation.isActive) continuation.resumeWithException(e)
                }
                override fun onResponse(call: Call, response: Response) {
                    response.use {
                        synchronized(lock) { calls.remove(call) }
                        if (continuation.isActive) continuation.resume(response.code)
                    }
                }
            })
        }
    }
    override fun cancelAll() = synchronized(lock) { calls.toList().forEach(Call::cancel); calls.clear() }
}

/**
 * Separate default-OFF diagnostic permission. capture only projects bounded
 * strings/numbers into memory; no Throwable, stack frames, messages or IO survive.
 */
class DiagnosticReports(
    directory: File, private val policy: DiagnosticPolicy, private val destination: DiagnosticDestination,
    private val appVersion: String, private val buildCommit: String? = null, private val arch: String = "unknown",
    private val transport: DiagnosticTransport = OkHttpDiagnosticTransport(),
    private val clock: DiagnosticClock = SystemDiagnosticClock,
    private val storage: DiagnosticStorage = FileDiagnosticStorage(directory),
    dispatcher: CoroutineDispatcher = Dispatchers.IO,
    private val disabled: () -> Boolean = { diagnosticKillSwitch(System.getenv("OMNIVOICE_DIAGNOSTICS_DISABLED")) || diagnosticKillSwitch(System.getenv("OMNIVOICE_ANALYTICS_DISABLED")) },
) {
    private data class Consent(val enabled: Boolean, val epoch: String, val profile: String, val enabledAt: Long?)
    companion object {
        /** Bootstrap cleanup, independent of resources/config. Invalidate live reporters first. */
        suspend fun revokeStoredConsent(directory: File, storage: DiagnosticStorage = FileDiagnosticStorage(directory)): Boolean =
            withContext(Dispatchers.IO) { !revokeGrant(storage).second }
        private fun revokeGrant(storage: DiagnosticStorage, hint: Boolean? = null): Pair<Consent?,Boolean> {
            val hadGrant=hint ?: try { storage.read("consent.json",4096)!=null }catch(_:Exception){ true }
            var failed=false
            try { storage.remove("outbox.json") }catch(_:Exception){ failed=true }
            fun uuid()=UUID.randomUUID().toString().replace("-","")
            val revoked=if(hadGrant) Consent(false,uuid(),"install_${uuid()}",null) else null
            if(revoked!=null) {
                try { storage.remove("consent.json") }catch(_:Exception){}
                val record=buildJsonObject { put("version",1);put("enabled",false);put("epoch",revoked.epoch);put("profile_id",revoked.profile) }
                try { storage.write("consent.json",record.toString().toByteArray()) }catch(_:Exception){ failed=true }
            }
            return revoked to failed
        }
    }
    private data class Pending(val epoch: String, val props: JsonObject, val id: String?, val timestamp: Long)
    private val stateLock = Any()
    private val worker = Mutex()
    private val scope = CoroutineScope(SupervisorJob() + dispatcher)
    private var consent: Consent? = null
    private var state = DiagnosticStatus()
    private var sequence = 0L
    private var closed = false
    private var loaded = false
    private val intake = ArrayDeque<Pending>()
    private val records = mutableListOf<JsonObject>()
    private var autoJob: CoroutineJob? = null
    private var retryJob: CoroutineJob? = null
    private var retryWakeAt: Long? = null
    private var storageRetryAt: Long? = null
    private val retryAt = mutableMapOf<String,Long>()
    val status: DiagnosticStatus get() = synchronized(stateLock) {
        val killed=disabled()
        state.copy(enabled = state.enabled && !killed, optedIn = state.optedIn && !killed, available = !killed)
    }
    val recoveryContext: DiagnosticRecoveryContext? get() = synchronized(stateLock) {
        consent?.takeIf { state.enabled && !disabled() }?.let { c -> c.enabledAt?.let { DiagnosticRecoveryContext(c.epoch, it, appVersion) } }
    }
    private fun id() = UUID.randomUUID().toString().replace("-", "")
    private fun hash(value: String) = MessageDigest.getInstance("SHA-256").digest(value.toByteArray()).joinToString("") { "%02x".format(it) }
    private fun allowed(epoch: String, generation: Long): Boolean = synchronized(stateLock) {
        !closed && loaded && state.enabled && !disabled() && consent?.epoch == epoch && sequence == generation
    }
    private val versionPattern = Regex("\\d{1,4}\\.\\d{1,4}\\.\\d{1,4}(?:(?:-(?:alpha|beta|rc|dev)(?:[.-]\\d{1,6})?)|(?:(?:a|b|rc|dev)\\d{1,6}))?")
    private fun props(input: DiagnosticInput, version: String? = appVersion, commit: String? = buildCommit): JsonObject {
        val fields = sortedMapOf<String, JsonElement>(
            "category" to JsonPrimitive(policy.known("categories", input.category, "operation_failed")),
            "source" to JsonPrimitive("android"), "platform" to JsonPrimitive("android"),
            "stage" to JsonPrimitive(policy.known("stages", input.stage, "other")),
            "error_code" to JsonPrimitive(policy.known("error_codes", input.errorCode, "UNKNOWN")),
            "error_type" to JsonPrimitive(policy.known("error_types", input.errorType, "OtherError")),
            "engine_id" to JsonPrimitive(policy.known("engine_ids", input.engineID, "unknown")),
            "reason" to JsonPrimitive(policy.known("reasons", input.reason, "unknown")),
            "device_family" to JsonPrimitive(policy.known("device_families", input.deviceFamily, "unknown")),
            "arch" to JsonPrimitive(policy.known("arches", arch, "unknown")),
            "signal" to JsonPrimitive(policy.known("signals", input.signal, "unknown")),
        )
        version?.takeIf { versionPattern.matches(it) && it.length <= 64 }?.let { fields["app_version"] = JsonPrimitive(it) }
        commit?.takeIf { Regex("[a-f0-9]{7,40}").matches(it) }?.let { fields["build_commit"] = JsonPrimitive(it) }
        policy.frames(input.frames).takeIf(String::isNotEmpty)?.let { fields["frames"] = JsonPrimitive(it) }
        input.exitCode?.takeIf { it in Int.MIN_VALUE.toLong()..4_294_967_295L }?.let { fields["exit_code"] = JsonPrimitive(it) }
        fields["fingerprint"] = JsonPrimitive(hash(JsonObject(fields).toString()))
        fields["schema_version"] = JsonPrimitive(1); fields["occurrences"] = JsonPrimitive(1)
        return JsonObject(fields)
    }
    private fun normalized(error: Throwable): String = when (error) {
        is SocketTimeoutException, is java.util.concurrent.TimeoutException -> "TimeoutError"
        is java.net.ConnectException, is java.net.UnknownHostException -> "ConnectionError"
        is OutOfMemoryError -> "MemoryError"
        is java.io.FileNotFoundException -> "FileNotFoundError"
        is SecurityException -> "PermissionError"
        is IOException -> "OSError"
        is IllegalArgumentException -> "ValueError"
        is IndexOutOfBoundsException -> "IndexError"
        is UnsupportedOperationException -> "NotImplementedError"
        is AssertionError -> "AssertionError"
        is StackOverflowError -> "RecursionError"
        else -> "OtherError"
    }
    fun capture(input: DiagnosticInput, error: Throwable? = null): Boolean {
        val current = synchronized(stateLock) { if (closed || !state.enabled || disabled()) null else consent } ?: return false
        if (error is CancellationException || error is InterruptedException) return false
        val projected = props(if (error != null) input.copy(errorType = normalized(error)) else input)
        synchronized(stateLock) {
            if (closed || !state.enabled || disabled() || consent?.epoch != current.epoch) return false
            if (intake.size >= policy.limit("queue_count", 64)) { state = state.copy(droppedCount = state.droppedCount + 1); return false }
            intake.add(Pending(current.epoch, projected, null, clock.nowMs()))
        }
        schedule()
        return true
    }
    fun captureRecovered(input: DiagnosticInput, occurredAtMs: Long, consentEpoch: String, recoveryKey: String, reportedAppVersion: String? = null, occurrenceCount: Int = 1): Boolean {
        if (occurrenceCount !in 1..1000000) return false
        val context = recoveryContext ?: return false
        val parts = recoveryKey.split(':')
        if (parts.size != 3 || parts[0] != "android" || parts[1].toLongOrNull() != occurredAtMs || policy.known("reasons", parts[2], "") != parts[2]) return false
        if (consentEpoch != context.epoch || occurredAtMs < context.enabledAtMs || occurredAtMs < clock.nowMs() - policy.limit("ttl_ms", 604800000) || occurredAtMs > clock.nowMs() + 60000 || reportedAppVersion != null && !versionPattern.matches(reportedAppVersion)) return false
        val projected = props(input, reportedAppVersion, null).let { JsonObject(it + ("occurrences" to JsonPrimitive(occurrenceCount))) }
        val fingerprint=projected["fingerprint"]!!.jsonPrimitive.content
        synchronized(stateLock) {
            if (!state.enabled || closed || disabled() || consent?.epoch != consentEpoch) return false
            if (intake.size >= policy.limit("queue_count",64)) { state=state.copy(droppedCount=state.droppedCount+1);return false }
            intake.add(Pending(consentEpoch, projected, hash("$consentEpoch:$recoveryKey:$fingerprint").take(32), occurredAtMs))
        }
        schedule(); return true
    }
    private fun schedule(): Unit = synchronized(stateLock) {
        if (autoJob?.isActive == true || closed || !state.enabled) return@synchronized
        autoJob = scope.launch {
            try { flush() } catch (_: Exception) { /* Durable queue remains; feature failures stay isolated. */ }
            finally {
                synchronized(stateLock) { autoJob = null }
                if (synchronized(stateLock) { intake.isNotEmpty() && state.enabled && !closed }) schedule()
            }
        }
    }
    private fun cancelRetryLocked() {
        retryJob?.cancel();retryJob=null;retryWakeAt=null
    }
    /** One owned wake for durable work, not a polling service or a new report. */
    private fun scheduleRetry(epoch:String,generation:Long) {
        // Called under worker: retryAt and records belong to that worker.
        // Storage publication blocks all delivery, including already-due
        // network retries. An old network deadline must not cause a busy loop.
        val next=storageRetryAt ?: retryAt.values.minOrNull()
        synchronized(stateLock) {
            if(next==null || !allowed(epoch,generation)) { cancelRetryLocked();return }
            if(retryJob?.isActive==true && retryWakeAt==next)return
            cancelRetryLocked()
            val job=scope.launch(start=CoroutineStart.LAZY) {
                delay((next-clock.nowMs()).coerceAtLeast(1))
                val running=currentCoroutineContext()[CoroutineJob]
                val owned=synchronized(stateLock) {
                    if(retryJob!==running)false
                    else { retryJob=null;retryWakeAt=null;true }
                }
                if(owned && allowed(epoch,generation)) {
                    try { flush() }catch(_:Exception){ /* Durable work remains for the next explicit intake/load. */ }
                }
            }
            retryJob=job;retryWakeAt=next;job.start()
        }
    }
    private fun consentJson(c: Consent) = buildJsonObject {
        put("version", 1); put("enabled", c.enabled); put("epoch", c.epoch); put("profile_id", c.profile)
        c.enabledAt?.let { put("enabled_at_ms", it) }
    }
    private fun parseConsent(data: ByteArray?): Consent? = try {
        data?.let { Json.parseToJsonElement(diagnosticUTF8(it)).jsonObject }?.let {
            require(diagnosticInteger(it["version"]) == 1L)
            val enabled = (it["enabled"] as? JsonPrimitive)?.takeIf { value -> !value.isString }?.booleanOrNull ?: error("Invalid consent")
            val epoch = diagnosticString(it["epoch"]) ?: ""
            val profile = diagnosticString(it["profile_id"]) ?: ""
            require(Regex("[a-f0-9]{32}").matches(epoch) && Regex("install_[a-f0-9]{32}").matches(profile))
            val rawEnabledAt = it["enabled_at_ms"]
            require(rawEnabledAt == null || rawEnabledAt == JsonNull || diagnosticInteger(rawEnabledAt) != null)
            val enabledAt = diagnosticInteger(rawEnabledAt)?.takeIf { stamp -> stamp > 0 && stamp <= clock.nowMs() + 60000 }
            Consent(enabled, epoch, profile, enabledAt)
        }
    } catch (_: Exception) { null }
    suspend fun load(): DiagnosticStatus {
        if (disabled()) {
            synchronized(stateLock) { sequence++; state=state.copy(enabled=false,optedIn=false);intake.clear();cancelRetryLocked() }
            transport.cancelAll()
        }
        return withContext(scope.coroutineContext) {
        worker.withLock {
            if (closed || loaded && !disabled()) return@withLock status
            val generation = synchronized(stateLock) { sequence }
            var hadGrant=synchronized(stateLock) { consent!=null }
            val saved = try { storage.read("consent.json",4096).let { hadGrant=hadGrant || it!=null;parseConsent(it) } } catch (_: Exception) { hadGrant=true;null }
            if (disabled()) {
                val current=synchronized(stateLock) {
                    if (generation!=sequence || closed) false
                    else { loaded=true;state=state.copy(enabled=false,optedIn=false,prompted=hadGrant,pendingCount=0);intake.clear();true }
                }
                if (!current) return@withLock status
                records.clear();retryAt.clear();storageRetryAt=null
                val revoked=revokeGrant(storage,hadGrant)
                synchronized(stateLock) { if(generation==sequence && !closed){ consent=revoked.first;state=state.copy(consentSaveFailed=revoked.second) } }
                return@withLock status
            }
            val applied = synchronized(stateLock) {
                if (generation != sequence || closed) false
                else {
                    consent = saved; loaded = true
                    state = state.copy(enabled = saved?.enabled == true && !disabled(), optedIn = saved?.enabled == true && !disabled(), prompted = saved != null)
                    true
                }
            }
            if (!applied) return@withLock status
            records.clear()
            if (status.enabled) {
                val data = try { storage.read("outbox.json", policy.limit("queue_bytes", 524288)) }
                catch (error:Exception) {
                    // An unreadable file is not proven corrupt. Preserve it and
                    // permit a later explicit load without collecting meanwhile.
                    synchronized(stateLock) { loaded=false;state=state.copy(enabled=false) }
                    throw error
                }
                try {
                    data?.let { Json.parseToJsonElement(diagnosticUTF8(it)).jsonArray }?.take(policy.limit("queue_count", 64))?.forEach { element ->
                        sanitizeRecord(element as? JsonObject, saved!!.epoch)?.let(records::add)
                    }
                } catch (_: Exception) { records.clear(); try { storage.remove("outbox.json") } catch (_: Exception) {} }
                persistOrRetry(saved!!.epoch,generation)
            } else { try { storage.remove("outbox.json") } catch (_: Exception) {} }
            updateCount()
            if (status.enabled && records.isNotEmpty()) schedule()
            status
        }
    }
    }
    suspend fun setConsent(enabled: Boolean): DiagnosticStatus {
        if (enabled && disabled()) { setConsent(false);throw IllegalStateException("Diagnostic reports are disabled") }
        if (enabled && status.enabled) return status
        val generation = synchronized(stateLock) { sequence++; state = state.copy(enabled = false, optedIn = false); intake.clear();cancelRetryLocked(); sequence }
        transport.cancelAll()
        return withContext(scope.coroutineContext) {
            worker.withLock {
                if (generation != synchronized(stateLock) { sequence } || closed) return@withLock status
                val fresh = Consent(enabled, id(), "install_${id()}", if (enabled) clock.nowMs() else null)
                records.clear(); retryAt.clear();storageRetryAt=null
                try { storage.remove("outbox.json") } catch (_: Exception) {}
                // A failed OFF replacement must not resurrect an older grant
                // after relaunch when deleting that old grant still works.
                if (!enabled) try { storage.remove("consent.json") } catch (_: Exception) {}
                try { storage.write("consent.json", consentJson(fresh).toString().toByteArray()) }
                catch(error:Exception){ synchronized(stateLock){ state=state.copy(consentSaveFailed=true) };throw error }
                synchronized(stateLock) {
                    if (generation == sequence && !closed) {
                        consent = fresh; loaded = true
                        state = state.copy(enabled = enabled && !disabled(), optedIn = enabled && !disabled(), prompted = true, pendingCount = 0, consentSaveFailed = false)
                    }
                }
                status
            }
        }
    }
    private fun sanitizeRecord(raw: JsonObject?, epoch: String): JsonObject? = try {
        requireNotNull(raw)
        require(raw.keys == setOf("version", "id", "timestamp_ms", "epoch", "props"))
        val identity = requireNotNull(diagnosticString(raw["id"]))
        val stamp = requireNotNull(diagnosticInteger(raw["timestamp_ms"]))
        require(diagnosticInteger(raw["version"]) == 1L && diagnosticString(raw["epoch"]) == epoch && Regex("[a-f0-9]{32}").matches(identity))
        require(stamp in (clock.nowMs() - policy.limit("ttl_ms", 604800000))..(clock.nowMs() + 60000))
        val fields = raw["props"]!!.jsonObject
        fun value(key: String) = (fields[key] as? JsonPrimitive)?.takeIf { it.isString }?.content
        val safe = props(DiagnosticInput(value("category") ?: "operation_failed", value("stage") ?: "other", value("error_code"), value("error_type") ?: "OtherError", value("engine_id"), policy.parseFrames(value("frames")), value("reason"), value("device_family"), fields["exit_code"]?.jsonPrimitive?.longOrNull, value("signal")), value("app_version"), value("build_commit"))
        val count = fields["occurrences"]?.jsonPrimitive?.intOrNull?.coerceIn(1, 1000000) ?: 1
        buildJsonObject {
            put("version",1);put("id",identity);put("timestamp_ms",stamp);put("epoch",epoch)
            put("props",JsonObject(safe + ("occurrences" to JsonPrimitive(count))))
        }
    } catch (_: Exception) { null }
    private fun updateCount() = synchronized(stateLock) { state = state.copy(pendingCount = records.size + intake.size) }
    private fun persist() {
        val bytes = JsonArray(records).toString().toByteArray()
        require(bytes.size <= policy.limit("queue_bytes", 524288))
        if (records.isEmpty()) storage.remove("outbox.json") else storage.write("outbox.json", bytes)
        updateCount()
    }
    private fun persistOrRetry(epoch:String,generation:Long):Boolean {
        try {
            persist();storageRetryAt=null
            return true
        } catch (_:Exception) {
            // Retain both the valid original file and the bounded in-memory
            // update. Never send new records before their durable publication.
            updateCount()
            storageRetryAt=clock.nowMs()+60000
            scheduleRetry(epoch,generation)
            return false
        }
    }
    private fun drain(epoch: String) {
        val pending = synchronized(stateLock) { intake.toList().also { intake.clear() } }
        pending.filter { it.epoch == epoch }.forEach { item ->
            if (item.id != null) {
                val oldIndex=records.indexOfFirst { it["id"]?.jsonPrimitive?.content == item.id }
                if (oldIndex>=0) {
                    val old=records[oldIndex];val fields=old["props"]!!.jsonObject
                    val count=maxOf(fields["occurrences"]!!.jsonPrimitive.int,item.props["occurrences"]!!.jsonPrimitive.int)
                    records[oldIndex]=JsonObject(old+("props" to JsonObject(fields+("occurrences" to JsonPrimitive(count)))))
                    return@forEach
                }
            }
            val fingerprint = item.props["fingerprint"]
            val index = if (item.id == null) records.indexOfFirst { it["props"]?.jsonObject?.get("fingerprint") == fingerprint && kotlin.math.abs(item.timestamp - it["timestamp_ms"]!!.jsonPrimitive.long) <= policy.limit("dedupe_ms",60000) } else -1
            if (index >= 0) {
                val old = records[index]; val fields = old["props"]!!.jsonObject
                val count = (fields["occurrences"]!!.jsonPrimitive.int + 1).coerceAtMost(1000000)
                records[index] = JsonObject(old + ("props" to JsonObject(fields + ("occurrences" to JsonPrimitive(count)))))
            } else if (records.size < policy.limit("queue_count", 64)) {
                val record = buildJsonObject { put("version", 1); put("id", item.id ?: id()); put("timestamp_ms", item.timestamp); put("epoch", epoch); put("props", item.props) }
                if (JsonArray(records + record).toString().toByteArray().size <= policy.limit("queue_bytes", 524288)) records.add(record)
                else synchronized(stateLock) { state = state.copy(droppedCount = state.droppedCount + 1) }
            } else synchronized(stateLock) { state = state.copy(droppedCount = state.droppedCount + 1) }
        }
        updateCount()
    }
    suspend fun flush() = withContext(scope.coroutineContext) {
        worker.withLock {
            val snapshot = synchronized(stateLock) { consent?.let { it to sequence } } ?: return@withLock
            val (current, generation) = snapshot
            if (!allowed(current.epoch, generation)) return@withLock
            records.replaceAll { sanitizeRecord(it, current.epoch) ?: buildJsonObject {} }
            records.removeAll { it.isEmpty() }
            drain(current.epoch)
            if((storageRetryAt ?: 0)>clock.nowMs()) {
                scheduleRetry(current.epoch,generation)
                return@withLock
            }
            if(!persistOrRetry(current.epoch,generation))return@withLock
            retryAt.keys.retainAll(records.map { it["id"]!!.jsonPrimitive.content }.toSet())
            for (record in records.toList()) {
                val identity=record["id"]!!.jsonPrimitive.content
                if ((retryAt[identity] ?: 0)>clock.nowMs()) continue
                if (!allowed(current.epoch, generation)) break
                val body = buildJsonObject {
                    put("v", 1); put("id", record["id"]!!); put("ts", record["timestamp_ms"]!!); put("site", destination.site)
                    put("name", "diagnostic_report"); put("url", destination.eventUrl); put("profile_id", current.profile); put("props", record["props"]!!)
                }.toString().toByteArray()
                if (body.size > policy.limit("body_bytes", 8192)) {
                    records.remove(record)
                    if(!persistOrRetry(current.epoch,generation))return@withLock
                    continue
                }
                var terminal = false
                repeat(policy.limit("max_attempts", 3)) { attempt ->
                    if (terminal || !allowed(current.epoch, generation)) return@repeat
                    val code = try { transport.send(destination, body) { allowed(current.epoch, generation) } } catch (_: Exception) { null }
                    terminal = code != null && code in 200..499 && code != 429
                    if (!terminal && attempt < policy.limit("max_attempts", 3) - 1) {
                        var remaining = 500L shl attempt
                        while (remaining > 0 && allowed(current.epoch, generation)) { clock.sleep(minOf(remaining, 25)); remaining -= 25 }
                    }
                }
                if (allowed(current.epoch,generation)) {
                    if (terminal) {
                        records.remove(record);retryAt.remove(identity)
                        if(!persistOrRetry(current.epoch,generation))return@withLock
                    }
                    else retryAt[identity]=clock.nowMs()+60000
                }
            }
            scheduleRetry(current.epoch,generation)
        }
    }
    suspend fun close() {
        synchronized(stateLock) { closed = true; sequence++; state = state.copy(enabled = false, optedIn = false);cancelRetryLocked() }
        transport.cancelAll()
        try {
            withContext(Dispatchers.IO) { worker.withLock { consent?.let { drain(it.epoch);persist() } } }
        } catch (_:Exception) { /* A final best-effort write must not leave the owned scope alive. */ }
        finally { scope.cancel() }
    }
}
