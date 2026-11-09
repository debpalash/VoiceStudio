package com.voicestudio.mobile.data

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.content.pm.ServiceInfo
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import androidx.work.*
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.first
import kotlinx.serialization.json.*
import java.io.File
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Durable host-side pipeline. Checkpoints never contain credentials or API keys.
 * Re-entering after process death resumes polling an existing host task. */
class DubWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    private val store = SessionStore(context)
    private var stage = "mobile.android.stage_upload"
    private var percent = 0
    override suspend fun getForegroundInfo(): ForegroundInfo = notification(false)
    private suspend fun notification(finished: Boolean): ForegroundInfo {
        val prefs = store.preferences.first()
        val l = L10n.load(applicationContext, prefs.locale.takeUnless { it == "system" } ?: java.util.Locale.getDefault().toLanguageTag())
        val manager = applicationContext.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel("dub", l.t("mobile.tabs.dub"), NotificationManager.IMPORTANCE_LOW))
        val text = if(finished) l.t("mobile.dub.done_notification", mapOf("title" to inputData.getString("title").orEmpty())) else l.t(stage)
        val launch = applicationContext.packageManager.getLaunchIntentForPackage(applicationContext.packageName)
        val intent = launch?.let { android.app.PendingIntent.getActivity(applicationContext, 0, it, android.app.PendingIntent.FLAG_IMMUTABLE or android.app.PendingIntent.FLAG_UPDATE_CURRENT) }
        return ForegroundInfo(id.hashCode(), NotificationCompat.Builder(applicationContext, "dub")
            .setSmallIcon(android.R.drawable.stat_sys_upload).setContentTitle(l.t("mobile.tabs.dub")).setContentText(text)
            .setContentIntent(intent).setOngoing(!finished).setAutoCancel(finished).setProgress(if(finished) 0 else 100, percent, !finished && percent == 0).build(), ServiceInfo.FOREGROUND_SERVICE_TYPE_DATA_SYNC)
    }
    private suspend fun promoteIfAllowed() {
        val info = notification(false)
        try { setForeground(info) }
        catch(cancelled: CancellationException) { throw cancelled }
        catch(_: SecurityException) {
            throw ClientFailure("mobile.android.dub_foreground_failed")
        }
        catch(_: IllegalStateException) {
            // Android may refuse a foreground start. Keep the scheduled work
            // running under WorkManager's normal execution limits.
        }
    }
    private suspend fun report(key: String, value: Int = 0) {
        stage = key; percent = value
        setProgress(workDataOf("stage" to key, "percent" to value, "job" to inputData.getString("job")))
        promoteIfAllowed()
    }
    private suspend fun awaitTask(api: VoiceStudioApi, id: String) {
        var seq = 0
        while(currentCoroutineContext().isActive) {
            val events = api.jobEvents(id, seq)
            for(event in events.events) {
                seq = maxOf(seq, event.seq)
                val json = runCatching { api.json.parseToJsonElement(event.payload.removePrefix("data: ").trim()).jsonObject }.getOrNull()
                val total=json?.get("total")?.jsonPrimitive?.doubleOrNull
                val current=json?.get("current")?.jsonPrimitive?.doubleOrNull
                val p = json?.get("percent")?.jsonPrimitive?.doubleOrNull ?: if(total!=null&&total>0&&current!=null) current/total else null
                if(p != null) report(stage, (if(p <= 1.0) p * 100 else p).toInt().coerceIn(0, 100))
                // A failed event can coexist with a task manager "done" row.
                if(json?.get("type")?.jsonPrimitive?.content in setOf("error", "cancelled")) throw ClientFailure("mobile.android.job_failed")
            }
            when(events.job.status) {
                "done" -> return
                "failed", "cancelled" -> throw ClientFailure("mobile.android.job_failed")
            }
            delay(2000)
        }
        currentCoroutineContext().ensureActive()
    }
    override suspend fun doWork(): Result {
        val job = inputData.getString("job").orEmpty()
        try {
            val host = inputData.getString("host") ?: throw ClientFailure("mobile.android.dub_restart")
            if(job.isBlank()) throw ClientFailure("mobile.android.dub_restart")
            if(store.preferences.first().activeUrl != host) throw ClientFailure("mobile.android.dub_restart")
            return coroutineScope {
                val pipeline = currentCoroutineContext().job
                val watcher = launch {
                    store.preferences.first { it.activeUrl != host }
                    pipeline.cancel()
                }
                try { runPipeline(host, job) } finally { watcher.cancel() }
            }
        } catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) {
            val failure = error as? ClientFailure
            return if(isRetryable(error)) Result.retry() else Result.failure(failureData(failure ?: ClientFailure("mobile.android.dub_restart"),job))
        }
    }
    private suspend fun runPipeline(host: String, job: String): Result {
        val api = store.api(host)
        val codes = inputData.getStringArray("languages") ?: throw ClientFailure("mobile.android.dub_restart")
        val names = inputData.getStringArray("names") ?: throw ClientFailure("mobile.android.dub_restart")
        var checkpoint = store.checkpoint(host, job)
        if(codes.size != names.size) throw ClientFailure("mobile.android.dub_restart")
        authorizedTranslation(api, inputData)
        promoteIfAllowed()
        if(!checkpoint.uploaded) {
            report("mobile.android.stage_upload")
            val source = inputData.getString("file")
            val result = if(source != null) api.upload(File(source), inputData.getString("type") ?: "video", job, uploadName(inputData.getString("source_name"), File(source))) { p ->
                // Progress is throttled to integer percentages by the request body.
                setProgressAsync(workDataOf("stage" to stage, "percent" to p, "job" to job))
            } else api.ingest(inputData.getString("link") ?: throw ClientFailure("mobile.android.dub_restart"), job)
            checkpoint = checkpoint.copy(uploaded = true, prepTask = result.taskId)
            store.checkpoint(host, job, checkpoint)
            source?.let { File(it).delete() }
        }
        report("mobile.android.stage_prepare")
        awaitTask(api, checkpoint.prepTask)
        if(checkpoint.segments.isEmpty()) {
            report("mobile.android.stage_transcribe")
            val transcript = api.transcribe(job)
            if(transcript.segments.isEmpty()) throw ClientFailure("mobile.android.no_speech")
            checkpoint = checkpoint.copy(segments = transcript.segments, source = transcript.sourceLang)
            store.checkpoint(host, job, checkpoint)
        }
        for(index in checkpoint.nextLanguage until codes.size) {
            if(checkpoint.renderTask.isBlank()) {
                report("mobile.android.stage_translate")
                // Upload/transcription may take minutes. Recheck immediately
                // before each transcript transfer, not only when the job starts.
                val translationAuthorization = authorizedTranslation(api, inputData)
                val translation = api.translate(job, codes[index], checkpoint.source, checkpoint.segments, translationAuthorization)
                if(translation.translated.any { it.error != null } || translation.translated.size != checkpoint.segments.size)
                    throw ClientFailure("mobile.android.translation_failed")
                val textById = translation.translated.associate { it.id.jsonPrimitive.content to it.text }
                val segments = checkpoint.segments.mapIndexed { i, s ->
                    s.copy(text = textById[s.id?.jsonPrimitive?.content ?: i.toString()] ?: throw ClientFailure("mobile.android.translation_failed"))
                }
                report("mobile.android.stage_render")
                val task = api.startDub(job, codes[index], names[index], inputData.getString("voice").orEmpty(), segments)
                checkpoint = checkpoint.copy(renderTask = task.taskId)
                store.checkpoint(host, job, checkpoint)
            }
            report("mobile.android.stage_render")
            awaitTask(api, checkpoint.renderTask)
            checkpoint = checkpoint.copy(nextLanguage = index + 1, renderTask = "")
            store.checkpoint(host, job, checkpoint)
        }
        val prefs = store.preferences.first()
        if(prefs.notifications) {
            val completion=notification(true).notification
            // Recheck immediately before posting, after all suspension points.
            if(Build.VERSION.SDK_INT < 33 || ContextCompat.checkSelfPermission(applicationContext, Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) {
                NotificationManagerCompat.from(applicationContext).notify(id.hashCode() + 1, completion)
            }
        }
        return Result.success(workDataOf("job" to job, "stage" to "mobile.android.stage_done"))
    }

    companion object {
        fun failureData(error: ClientFailure, job: String): Data = workDataOf("error" to error.key,
            "error_params" to JsonObject(error.params.mapValues {JsonPrimitive(it.value)}).toString(), "job" to job)
        const val TAG = "voicestudio.dub"
        /** One definition of "temporary" for every request the worker makes. */
        fun isRetryable(error: Throwable): Boolean = (error as? ClientFailure)?.isTransient() == true
        suspend fun authorizedTranslation(api: VoiceStudioApi, data: Data): TranslationAuthorization {
            val engine = api.translationEngine()
            if(engine.id != data.getString("translation_provider") || engine.category != data.getString("translation_category") ||
                engine.routingIdentity != data.getString("translation_route").orEmpty())
                throw ClientFailure("mobile.dub.translation_changed")
            return engine.authorize(data.getBoolean("translation_confirmed", false))
                ?: throw ClientFailure("mobile.dub.translation_setup")
        }
        fun enqueue(context: Context, host: String, file: File?, link: String?, type: String, languages: List<Pair<String, String>>, voice: String, translation: TranslationAuthorization, sourceName: String? = file?.name): UUID {
            DubVoiceChoice.validate(voice)
            val job = UUID.randomUUID().toString()
            val request = OneTimeWorkRequestBuilder<DubWorker>().setInputData(workDataOf(
                "host" to host, "job" to job, "file" to file?.absolutePath, "source_name" to sourceName, "link" to link, "type" to type,
                "languages" to languages.map { it.first }.toTypedArray(), "names" to languages.map { it.second }.toTypedArray(),
                "translation_provider" to translation.provider, "translation_category" to translation.category,
                "translation_route" to translation.routingIdentity, "translation_confirmed" to translation.onlineConfirmed, "voice" to voice, "title" to (sourceName ?: link.orEmpty())))
                .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, 10, TimeUnit.SECONDS).addTag(TAG).addTag(host).build()
            WorkManager.getInstance(context).enqueue(request)
            return request.id
        }
    }
}

/**
 * The name shown to the user, with the cached copy's extension (derived from the
 * MIME type) appended when it lacks a matching one: /dub/upload rejects names
 * without a media extension, and document providers often omit it.
 */
internal fun uploadName(display: String?, cached: File): String {
    val name = display?.trim()?.takeIf { it.isNotEmpty() } ?: return cached.name
    val ext = cached.extension.lowercase()
    return if(ext.isEmpty() || name.substringAfterLast('.', "").lowercase() == ext) name else "$name.$ext"
}
