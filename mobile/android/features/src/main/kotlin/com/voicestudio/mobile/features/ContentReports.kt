package com.voicestudio.mobile.features

import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.*
import androidx.compose.runtime.staticCompositionLocalOf
import kotlinx.coroutines.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.*

/** The app's report client (base URL per build type, root package.json version); null hides Report. */
val LocalContentReportClient = staticCompositionLocalOf<ContentReportClient?> { null }

enum class ReportStatus { IDLE, SENDING, FAILED, RATE_LIMITED, INVALID }
data class ReportSheet(val subject: ReportSubject, val draft: ReportDraft, val status: ReportStatus = ReportStatus.IDLE) {
    val canSend get() = draft.reason != null && status != ReportStatus.SENDING && status != ReportStatus.INVALID
}

/**
 * Drafts live in memory per result until they are sent, so closing and reopening
 * the sheet keeps the reason, note, checkbox and idempotency key.
 */
class ContentReports(private val scope: CoroutineScope) {
    private val drafts = mutableMapOf<String, ReportDraft>()
    private val inFlight = mutableSetOf<String>()
    private val mutable = MutableStateFlow<ReportSheet?>(null)
    val sheet: StateFlow<ReportSheet?> = mutable.asStateFlow()
    private val sentEvents = MutableSharedFlow<Unit>(extraBufferCapacity = 1)
    /** One event per report the server stored. */
    val sent: SharedFlow<Unit> = sentEvents.asSharedFlow()

    fun draft(id: String): ReportDraft? = drafts[id]
    fun open(subject: ReportSubject) {
        val draft = drafts.getOrPut(subject.id) { ReportDraft() }
        mutable.value = ReportSheet(subject, draft, if (subject.id in inFlight) ReportStatus.SENDING else ReportStatus.IDLE)
    }
    fun close() { mutable.value = null }
    fun edit(transform: (ReportDraft) -> ReportDraft) {
        val current = mutable.value ?: return
        if (current.status == ReportStatus.SENDING) return
        val changed = transform(current.draft)
        val draft = changed.copy(note = changed.note.take(ContentReport.NOTE_LIMIT),
            includeText = changed.includeText && current.subject.hasTypedText)
        drafts[current.subject.id] = draft
        // A changed draft may be sent again; a failed one keeps its Retry.
        mutable.value = current.copy(draft = draft, status = if (current.status == ReportStatus.FAILED) ReportStatus.FAILED else ReportStatus.IDLE)
    }
    fun send(client: ContentReportClient): Job? {
        val current = mutable.value ?: return null
        if (!current.canSend) return null
        val id = current.subject.id
        val draft = current.draft
        inFlight += id
        mutable.value = current.copy(status = ReportStatus.SENDING)
        return scope.launch {
            val result = try { client.send(current.subject, draft) } finally { inFlight -= id }
            if (result.outcome == ReportOutcome.SENT) {
                drafts.remove(id)
                if (mutable.value?.subject?.id == id) mutable.value = null
                sentEvents.tryEmit(Unit)
                return@launch
            }
            // Edits are blocked while sending, so the stored draft is the one sent.
            val kept = (drafts[id] ?: draft).copy(idempotencyKey = result.idempotencyKey)
            drafts[id] = kept
            val status = when (result.outcome) {
                ReportOutcome.RATE_LIMITED -> ReportStatus.RATE_LIMITED
                ReportOutcome.INVALID -> ReportStatus.INVALID
                else -> ReportStatus.FAILED
            }
            mutable.update { if (it?.subject?.id == id) it.copy(draft = kept, status = status) else it }
        }
    }
}

/** What each generated result reports as: task, where it ran and the model that made it. */
object ReportSubjects {
    fun phoneSpeak(item: PhoneGeneration) = ReportSubject("speak:phone:${item.id}", "speak", "phone",
        item.modelRepo ?: ContentReport.UNKNOWN_MODEL, item.modelRevision.takeIf { item.modelRepo != null }, item.text)
    /** [engine] is the host engine id that made the take, when known. */
    fun computerSpeak(audioPath: String, text: String, engine: String?) =
        ReportSubject("speak:computer:$audioPath", "speak", "computer", ContentReport.modelId(engine), typedText = text)
    fun phoneClone(voice: PhoneVoice) = ReportSubject("clone:phone:${voice.id}", "clone", "phone",
        voice.modelRepo ?: ContentReport.UNKNOWN_MODEL, voice.modelRevision.takeIf { voice.modelRepo != null })
    fun computerClone(profile: Profile) = ReportSubject("clone:computer:${profile.id}", "clone", "computer", ContentReport.UNKNOWN_MODEL)
    /** The phone dub's speech comes from its pinned voice model; a changed catalogue entry keeps only the repo. */
    fun phoneDub(request: PhoneDubRequest, models: List<PhoneModel>): ReportSubject {
        val clones = models.filter { it.capability == "clone" }
        val exact = clones.firstOrNull { modelPin(it) in request.models }
        val named = exact ?: clones.firstOrNull { model -> request.models.any { it.id == model.id } }
        return ReportSubject("dub:phone:${request.id}", "dub", "phone", named?.repo ?: ContentReport.UNKNOWN_MODEL, exact?.revision)
    }
    fun computerDub(job: String) = ReportSubject("dub:computer:$job", "dub", "computer", ContentReport.UNKNOWN_MODEL)
}
