package com.voicestudio.mobile.data

import android.app.ActivityManager
import android.app.Application
import android.app.ApplicationExitInfo
import android.content.Context
import android.os.Build
import com.voicestudio.mobile.network.*
import java.io.File
import java.util.concurrent.atomic.AtomicLong
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.serialization.json.*

data class AutomaticDiagnosticState(
    val ready: Boolean = false,
    val status: DiagnosticStatus = DiagnosticStatus(),
    val saving: Boolean = false,
    val saveFailed: Boolean = false,
)

/** Retains a failed load for withdrawal and one bounded, owned retry timer. */
internal class AutomaticDiagnosticInitialization(
    private val scope: CoroutineScope,
    private val create: () -> DiagnosticReports,
    private val retain: (DiagnosticReports) -> Unit,
    private val onLoaded: (DiagnosticReports) -> Unit,
    private val publish: (AutomaticDiagnosticState) -> Unit,
    private val revision: () -> Long,
    private val requested: () -> Boolean?,
) {
    private val lock = Any()
    private var reports: DiagnosticReports? = null
    private var attempt: kotlinx.coroutines.Job? = null
    private var generation = 0L
    private var state = AutomaticDiagnosticState()
    @Volatile var failed = false
        private set
    @Volatile var blocksCapture = true
        private set

    fun start() = synchronized(lock) {
        if (attempt?.isActive == true) return@synchronized
        launchLocked()
    }

    fun retry() = synchronized(lock) {
        attempt?.cancel()
        launchLocked()
    }

    /** OFF and newer choices retire pending loads as well as delayed retries. */
    fun cancel() = synchronized(lock) {
        generation++
        attempt?.cancel()
        attempt = null
        failed = false
        blocksCapture = false
    }

    private fun launchLocked() {
        blocksCapture = true
        val token = ++generation
        val choice = revision()
        fun current() = token == generation && choice == revision()
        val job = scope.launch(start = CoroutineStart.LAZY) {
            while (true) {
                try {
                    synchronized(lock) {
                        if (!current()) return@launch
                        if (state.ready) {
                            state = state.copy(saving = true)
                            publish(state)
                        }
                    }
                    val currentReports = synchronized(lock) { reports } ?: create().also {
                        synchronized(lock) {
                            if (!current()) return@launch
                            reports = it
                            retain(it)
                        }
                    }
                    // An OFF made before resources loaded must never restore a
                    // prior grant, even briefly, during initialization retry.
                    if (requested() == false) currentReports.setConsent(false) else currentReports.load()
                    currentCoroutineContext().ensureActive()
                    synchronized(lock) { if (!current()) return@launch }
                    // Android binder calls and recovery callbacks must never
                    // hold the lock needed to retire this load during OFF.
                    onLoaded(currentReports)
                    currentCoroutineContext().ensureActive()
                    synchronized(lock) {
                        if (!current()) return@launch
                        failed = false
                        blocksCapture = false
                        state = AutomaticDiagnosticState(ready = true, status = currentReports.status,
                            saveFailed = currentReports.status.consentSaveFailed)
                        publish(state)
                    }
                    return@launch
                } catch (cancelled: CancellationException) {
                    throw cancelled
                } catch (_: Exception) {
                    synchronized(lock) {
                        if (!current()) return@launch
                        failed = true
                        state = AutomaticDiagnosticState(ready = true,
                            status = DiagnosticStatus(available = false), saveFailed = true)
                        publish(state)
                        // Missing/invalid packaged policy or destination is not
                        // transient storage failure. Only explicit Retry rebuilds it.
                        if (reports == null) return@launch
                    }
                }
                delay(60_000)
            }
        }
        attempt = job
        job.start()
    }
}

/** The application owns consent and delivery, including OS-owned download jobs. */
object AutomaticDiagnostics {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutable = MutableStateFlow(AutomaticDiagnosticState())
    val state: StateFlow<AutomaticDiagnosticState> = mutable.asStateFlow()
    @Volatile private var reporter: DiagnosticReports? = null
    private var application: Application? = null
    private var initialized = false
    @Volatile private var initialization: AutomaticDiagnosticInitialization? = null
    private val choices = AtomicLong()
    @Volatile private var requestedConsent: Boolean? = null
    @Volatile private var declinedThisRun = false
    val observationRevision: Long get() = choices.get()

    @Synchronized fun initialize(app: Application, version: String) {
        if (initialized) return
        initialized = true
        application = app
        scope.launch {
            try {
                if (listOf("OMNIVOICE_DIAGNOSTICS_DISABLED", "OMNIVOICE_ANALYTICS_DISABLED").any {
                    System.getenv(it)?.trim()?.lowercase(java.util.Locale.ROOT) in setOf("1", "true", "yes", "on")
                }) {
                    requestedConsent = false
                    val saved = DiagnosticReports.revokeStoredConsent(File(app.noBackupFilesDir, "diagnostics"))
                    if(Build.VERSION.SDK_INT >= 30) runCatching {app.getSystemService(ActivityManager::class.java).setProcessStateSummary(null)}
                    mutable.value = AutomaticDiagnosticState(ready = true,
                        status = DiagnosticStatus(available = false, consentSaveFailed = !saved), saveFailed = !saved)
                    return@launch
                }
                val owner = AutomaticDiagnosticInitialization(scope, create = {
                    fun asset(name: String) = app.assets.open(name).bufferedReader(Charsets.UTF_8).use { it.readText() }
                    val config = Json.parseToJsonElement(asset("diagnostics-destination.json")).jsonObject
                    val destination = DiagnosticDestination(System.getenv("SUPERMETRY_SITE_ID") ?: config.getValue("site").jsonPrimitive.content,
                        System.getenv("SUPERMETRY_HOST") ?: config.getValue("host").jsonPrimitive.content,
                        System.getenv("SUPERMETRY_APP_URL") ?: config.getValue("appUrl").jsonPrimitive.content)
                    DiagnosticReports(File(app.noBackupFilesDir, "diagnostics"),
                        DiagnosticPolicy(asset("diagnostics.json")), destination, version,
                        arch = when (Build.SUPPORTED_ABIS.firstOrNull()) { "arm64-v8a" -> "arm64"; "x86_64" -> "x64"; else -> "unknown" })
                }, retain = { reporter = it }, onLoaded = { reports ->
                    recoverExits(app, reports)
                    publishProcessConsent(app, reports)
                    if(reports.status.consentSaveFailed) requestedConsent = false
                }, publish = { mutable.value = it }, revision = choices::get, requested = { requestedConsent })
                initialization = owner
                owner.start()
            } catch (_: Exception) {
                // Missing resources or unavailable storage never prevent offline use.
                mutable.value = AutomaticDiagnosticState(ready = true, status = DiagnosticStatus(available = false))
            }
        }
    }

    fun setConsent(enabled: Boolean) {
        val choice = choices.incrementAndGet()
        requestedConsent = enabled
        if (!enabled) declinedThisRun = true
        initialization?.cancel()
        val reports = reporter
        if(reports == null) {
            if(!enabled) application?.let {app -> scope.launch {
                if (choice != choices.get()) return@launch
                mutable.update {it.copy(saving = true, status = it.status.copy(enabled = false, optedIn = false, prompted = true))}
                val saved = DiagnosticReports.revokeStoredConsent(File(app.noBackupFilesDir, "diagnostics"))
                if (choice == choices.get()) mutable.update {it.copy(saving = false, saveFailed = !saved, status = it.status.copy(consentSaveFailed = !saved))}
            }}
            return
        }
        // UNDISPATCHED enters the core's synchronous invalidation before returning
        // to the UI; a withdrawal cannot wait behind a busy IO dispatcher.
        scope.launch(start = CoroutineStart.UNDISPATCHED) {
            mutable.update { it.copy(saving = true, saveFailed = false,
                status = if(enabled) it.status else it.status.copy(enabled = false, optedIn = false, prompted = true)) }
            try {
                reports.setConsent(enabled)
                withContext(Dispatchers.IO) { application?.let { publishProcessConsent(it, reports) } }
                if(choice == choices.get()) mutable.update { it.copy(ready = true, status = reports.status.copy(prompted = reports.status.prompted || declinedThisRun), saving = false) }
            } catch (_: Exception) {
                withContext(Dispatchers.IO) { application?.let { publishProcessConsent(it, reports) } }
                if(choice == choices.get()) mutable.update { it.copy(ready = true, status = reports.status.copy(prompted = reports.status.prompted || declinedThisRun), saving = false, saveFailed = true) }
            }
        }
    }

    fun retryConsent() {
        val owner = initialization
        if (owner != null && (owner.failed || reporter == null)) owner.retry()
        else requestedConsent?.let(::setConsent)
    }

    // Retention permits OFF during IO, but a partially loaded grant cannot
    // authorize intake or a new task's observation before load succeeds.
    private fun captureReporter(): DiagnosticReports? = reporter?.takeUnless { initialization?.blocksCapture == true }

    /** A failure is projected immediately; no Error, message or params are retained. */
    fun captureFailure(error: Throwable, stage: String = "other", engineID: String? = null) {
        val reports = captureReporter() ?: return
        val input = automaticDiagnosticInput(error, stage, engineID) ?: return
        reports.capture(input, error)
    }

    fun observeConsent(): CompanionDiagnosticObservation? {
        val revision=choices.get()
        val context=captureReporter()?.recoveryContext ?: return null
        if(choices.get()!=revision) return null
        return CompanionDiagnosticObservation(revision,context.epoch)
    }
    fun captureObservedFailure(error: Throwable, observation: CompanionDiagnosticObservation?, stage: String = "dub") {
        if(observation == null || observation != observeConsent()) return
        val input=automaticDiagnosticInput(error,stage) ?: return
        captureReporter()?.capture(input,error,expectedEpoch=observation.epoch)
    }

    fun capture(input: DiagnosticInput) { captureReporter()?.capture(input) }
    fun capture(input: DiagnosticInput, observation: CompanionDiagnosticObservation?) {
        if (observation == null || observation != observeConsent()) return
        captureReporter()?.capture(input, expectedEpoch = observation.epoch)
    }


    private fun publishProcessConsent(app: Application, reports: DiagnosticReports) {
        if (Build.VERSION.SDK_INT < 30) return
        val recovery = reports.recoveryContext
        val bytes = recovery?.let {
            buildJsonObject { put("v", 1); put("e", it.epoch); put("a", it.appVersion) }.toString().toByteArray(Charsets.UTF_8)
        }?.takeIf { it.size <= 128 }
        runCatching { app.getSystemService(ActivityManager::class.java).setProcessStateSummary(bytes) }
    }

    private fun recoverExits(app: Application, reports: DiagnosticReports) {
        if (Build.VERSION.SDK_INT < 30 || reports.recoveryContext == null) return
        val history = runCatching { app.getSystemService(ActivityManager::class.java)
            .getHistoricalProcessExitReasons(app.packageName, 0, 16) }.getOrDefault(emptyList())
        for (exit in history) {
            val code = when (exit.reason) {
                ApplicationExitInfo.REASON_CRASH -> "unhandled_exception" to "UNKNOWN"
                ApplicationExitInfo.REASON_CRASH_NATIVE -> "crashed" to "NATIVE_CRASH"
                ApplicationExitInfo.REASON_ANR -> "no_progress" to "UNKNOWN"
                ApplicationExitInfo.REASON_INITIALIZATION_FAILURE -> "launch_failed" to "UNKNOWN"
                else -> continue // Background reclamation, user exits and updates are expected.
            }
            val summary = parseDiagnosticExitSummary(exit.processStateSummary) ?: continue
            reports.captureRecovered(DiagnosticInput(category = "app_crash", stage = if (code.first == "launch_failed") "startup" else "other",
                errorCode = code.second, reason = code.first), occurredAtMs = exit.timestamp,
                consentEpoch = summary.first, recoveryKey = "android:${exit.timestamp}:${code.first}", reportedAppVersion = summary.second)
        }
    }
}

internal fun parseDiagnosticExitSummary(bytes: ByteArray?): Pair<String, String>? = try {
    require(bytes != null && bytes.size <= 128)
    val value = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)).jsonObject
    require(value.keys == setOf("v", "e", "a"))
    val version = value.getValue("v").jsonPrimitive
    require(!version.isString && version.intOrNull == 1)
    val epoch = value.getValue("e").jsonPrimitive
    val app = value.getValue("a").jsonPrimitive
    require(epoch.isString && Regex("[a-f0-9]{32}").matches(epoch.content) && app.isString)
    require(app.content.length <= 64 && Regex("\\d{1,4}\\.\\d{1,4}\\.\\d{1,4}(?:-[a-z0-9]+(?:[.-][a-z0-9]+)*)?").matches(app.content))
    epoch.content to app.content
} catch (_: Exception) { null }

internal fun automaticDiagnosticInput(error: Throwable, stage: String = "other", engineID: String? = null): DiagnosticInput? {
    if (error is CancellationException) return null
    val failure = error as? ClientFailure
    if (failure?.status?.let { it in 400..499 } == true) return null
    val key = failure?.key
    if (key in setOf("mobile.local.licence_required", "mobile.local.model_required", "mobile.local.wifi_required",
            "mobile.android.operation_busy", "mobile.permissions.denied", "mobile.errors.file") || key?.startsWith("mobile.connect.") == true) return null
    val (category, effectiveStage, code) = when (key) {
        "mobile.local.model_incompatible" -> Triple("incompatibility", "model_load", "UNSUPPORTED_PLATFORM")
        "mobile.local.runtime_failed" -> Triple("engine_failed", "engine", "ENGINE_DEPENDENCY_MISSING")
        "mobile.local.inference_failed" -> Triple("generation_failed", if (stage == "other") "generation" else stage, "UNKNOWN")
        "mobile.local.download_failed" -> Triple("download_failed", "download", "MODEL_DOWNLOAD_INTERRUPTED")
        "mobile.local.checksum_failed" -> Triple("download_failed", "verify", "MODEL_CACHE_CORRUPT")
        "mobile.local.storage_required" -> Triple(if(stage == "download") "download_failed" else "operation_failed", stage, "DISK_FULL")
        else -> Triple(when(stage) {
            "generation" -> "generation_failed"
            "model_load" -> "model_load_failed"
            "engine" -> "engine_failed"
            "download" -> "download_failed"
            "install", "setup" -> "install_failed"
            else -> "operation_failed"
        }, stage, "UNKNOWN")
    }
    return DiagnosticInput(category = category, stage = effectiveStage, errorCode = code, engineID = engineID)
}
