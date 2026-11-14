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

/** The application owns consent and delivery, including OS-owned download jobs. */
object AutomaticDiagnostics {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val mutable = MutableStateFlow(AutomaticDiagnosticState())
    val state: StateFlow<AutomaticDiagnosticState> = mutable.asStateFlow()
    @Volatile private var reporter: DiagnosticReports? = null
    private var application: Application? = null
    private var initialized = false
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
                fun asset(name: String) = app.assets.open(name).bufferedReader(Charsets.UTF_8).use { it.readText() }
                val config = Json.parseToJsonElement(asset("diagnostics-destination.json")).jsonObject
                val destination = DiagnosticDestination(System.getenv("SUPERMETRY_SITE_ID") ?: config.getValue("site").jsonPrimitive.content,
                    System.getenv("SUPERMETRY_HOST") ?: config.getValue("host").jsonPrimitive.content,
                    System.getenv("SUPERMETRY_APP_URL") ?: config.getValue("appUrl").jsonPrimitive.content)
                val reports = DiagnosticReports(File(app.noBackupFilesDir, "diagnostics"),
                    DiagnosticPolicy(asset("diagnostics.json")), destination, version,
                    arch = when (Build.SUPPORTED_ABIS.firstOrNull()) { "arm64-v8a" -> "arm64"; "x86_64" -> "x64"; else -> "unknown" })
                reports.load()
                reporter = reports
                recoverExits(app, reports)
                publishProcessConsent(app, reports)
                if(reports.status.consentSaveFailed) requestedConsent = false
                mutable.value = AutomaticDiagnosticState(ready = true, status = reports.status, saveFailed = reports.status.consentSaveFailed)
            } catch (_: Exception) {
                // Missing resources or unavailable storage never prevent offline use.
                mutable.value = AutomaticDiagnosticState(ready = true, status = DiagnosticStatus(available = false))
            }
        }
    }

    fun setConsent(enabled: Boolean) {
        val reports = reporter
        if(reports == null) {
            if(!enabled) application?.let {app -> scope.launch {
                mutable.update {it.copy(saving = true)}
                val saved = DiagnosticReports.revokeStoredConsent(File(app.noBackupFilesDir, "diagnostics"))
                mutable.update {it.copy(saving = false, saveFailed = !saved, status = it.status.copy(consentSaveFailed = !saved))}
            }}
            return
        }
        val choice = choices.incrementAndGet()
        requestedConsent = enabled
        if (!enabled) declinedThisRun = true
        // UNDISPATCHED enters the core's synchronous invalidation before returning
        // to the UI; a withdrawal cannot wait behind a busy IO dispatcher.
        scope.launch(start = CoroutineStart.UNDISPATCHED) {
            mutable.update { it.copy(saving = true, saveFailed = false,
                status = if(enabled) it.status else it.status.copy(enabled = false, optedIn = false, prompted = true)) }
            try {
                reports.setConsent(enabled)
                withContext(Dispatchers.IO) { application?.let { publishProcessConsent(it, reports) } }
                if(choice == choices.get()) mutable.update { it.copy(status = reports.status.copy(prompted = reports.status.prompted || declinedThisRun), saving = false) }
            } catch (_: Exception) {
                withContext(Dispatchers.IO) { application?.let { publishProcessConsent(it, reports) } }
                if(choice == choices.get()) mutable.update { it.copy(status = reports.status.copy(prompted = reports.status.prompted || declinedThisRun), saving = false, saveFailed = true) }
            }
        }
    }

    fun retryConsent() { requestedConsent?.let(::setConsent) }

    /** A failure is projected immediately; no Error, message or params are retained. */
    fun captureFailure(error: Throwable, stage: String = "other", engineID: String? = null) {
        val input = automaticDiagnosticInput(error, stage, engineID) ?: return
        reporter?.capture(input, error)
    }

    fun capture(input: DiagnosticInput) { reporter?.capture(input) }

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
