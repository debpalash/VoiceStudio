package com.voicestudio.mobile.features

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import androidx.work.WorkManager
import com.voicestudio.mobile.data.*
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.*
import java.io.File

 data class StudioState(val connectionId: Long = 0, val hostUrl: String? = null, val loading: Boolean = true, val connected: Boolean = false, val busy: Boolean = false, val catalogLoading: Boolean = false,
    val error: ClientFailure? = null, val canRetry: Boolean = false, val profiles: List<Profile> = emptyList(), val engines: EngineFamily = EngineFamily(),
    val history: List<HistoryItem> = emptyList(), val dubHistory: List<DubHistory> = emptyList(), val audio: String? = null)

class StudioViewModel @JvmOverloads constructor(application: Application, val store: SessionStore = SessionStore(application)) : AndroidViewModel(application) {
    val preferences = store.preferences.stateIn(viewModelScope, SharingStarted.Eagerly, Preferences())
    private val mutable = MutableStateFlow(StudioState())
    val state: StateFlow<StudioState> = mutable.asStateFlow()
    var api: VoiceStudioApi? = null
        private set
    private var operation: Job? = null
    private var cloneOperation: Job? = null
    private var clonePending = false
    private var retryAction: (() ->Unit)? = null
    val phone = PhoneStudio(application,viewModelScope,::fail) {error,retry -> fail(error,retry)}
    val reports = ContentReports(viewModelScope)
    // Host takes made in this session: playback path -> engine id and full typed text, for reports.
    private val computerTakes = mutableMapOf<String,Pair<String,String>>()
    init {
        viewModelScope.launch {
            val prefs = store.preferences.first()
            prefs.activeUrl?.let { host ->
                api = sessionApi(host)
                mutable.update { it.copy(connected = true, hostUrl = host) }
            }
            mutable.update { it.copy(loading = false) }
            if(api != null) refresh()
        }
    }
    private suspend fun sessionApi(host: String): VoiceStudioApi {
        lateinit var client: VoiceStudioApi
        client = store.api(host) { expired(client) }
        return client
    }
    private suspend fun stopHostWork(host: String?) {
        if(host != null) withContext(Dispatchers.IO) {
            WorkManager.getInstance(getApplication()).cancelAllWorkByTag(host).result.get()
        }
    }
    private fun clearHostState(host: String? = null, profiles: List<Profile> = emptyList(), error: ClientFailure? = null) {
        computerTakes.clear()
        mutable.value = StudioState(connectionId = mutable.value.connectionId + 1, hostUrl = host,
            loading = false, connected = host != null, busy = mutable.value.busy, catalogLoading = host != null, profiles = profiles, error = error)
    }
    private fun expired(source: VoiceStudioApi) {
        viewModelScope.launch {
            // A delayed 401 from A must not disconnect a newly paired host B.
            if(api !== source) return@launch
            operation?.cancelAndJoin()
            if(api !== source) return@launch
            mutable.update { it.copy(busy = true) }
            try {
                stopHostWork(mutable.value.hostUrl)
                store.disconnect(); api = null
                clearHostState(error = ClientFailure("mobile.connect.error_session_expired"))
            } finally { mutable.update { it.copy(busy = false) } }
        }
    }
    fun clearError() { retryAction=null;mutable.update { it.copy(error = null,canRetry=false) } }
    fun retryError() {val retry=retryAction;clearError();retry?.invoke()}
    fun fail(error: Throwable) = fail(error,null)
    fun fail(error: Throwable,retry: (() ->Unit)?) {
        if(error is CancellationException) return
        retryAction=retry
        mutable.update {it.copy(error=error as? ClientFailure ?: ClientFailure("mobile.android.operation_failed"),canRetry=retry!=null)}
    }
    fun run(block: suspend () -> Unit) {
        if(mutable.value.busy) return
        operation = viewModelScope.launch(start = CoroutineStart.LAZY) {
            mutable.update { it.copy(busy = true, error = null) }
            try { block() } catch(e: CancellationException) { throw e } catch(e: Exception) { fail(e) {run(block)} }
            finally { if(operation === currentCoroutineContext()[Job]) mutable.update { it.copy(busy = false) } }
        }
        operation?.start()
    }
    fun cancel() { operation?.cancel() }
    fun consent(value: Boolean) { viewModelScope.launch { store.update { it.copy(consent = value) } } }
    fun preference(transform: (Preferences) -> Preferences) { viewModelScope.launch { store.update(transform) } }
    fun connect(raw: String, pin: String, key: String) = run {
        val address = PairingAddress.parse(raw, pin)
        val candidate = VoiceStudioApi(address)
        candidate.health()
        if(key.isNotBlank()) candidate.exchange(key)
        // Health is public: a protected consumption endpoint must also succeed.
        val profiles = candidate.profiles()
        if(mutable.value.hostUrl != address.baseUrl.toString()) stopHostWork(mutable.value.hostUrl)
        store.pair(address, Secret(candidate.sessionToken, address.pin))
        api = sessionApi(address.baseUrl.toString())
        clearHostState(address.baseUrl.toString(), profiles)
        reload()
    }
    fun reconnect(host: SavedHost) = run {
        val candidate = store.api(host.url)
        candidate.health(); val profiles = candidate.profiles()
        val secret = withContext(Dispatchers.IO) {store.vault.read(host.url)} ?: Secret()
        if(mutable.value.hostUrl != host.url) stopHostWork(mutable.value.hostUrl)
        store.pair(candidate.address, secret)
        api = sessionApi(host.url)
        clearHostState(host.url, profiles); reload()
    }
    fun disconnect() = run {
        // Dispose host UI/capture and stop work immediately, including offline.
        // Revocation is best effort; the host's bounded session also expires.
        val current = api
        stopHostWork(mutable.value.hostUrl)
        store.disconnect(); api = null; clearHostState()
        try { withTimeout(15000) { current?.revoke() } }
        catch(e: TimeoutCancellationException) { fail(ClientFailure("mobile.android.disconnected_offline")) }
        catch(e: ClientFailure) { if(e.status != 401) fail(ClientFailure("mobile.android.disconnected_offline")) }
    }
    private suspend fun reload() {
        val current = api ?: return
        mutable.update {it.copy(catalogLoading=true)}
        try {
            val profiles = current.profiles()
            val engines = current.engines().tts
            val history = current.history()
            val dubs = current.dubHistory()
            if(api === current) mutable.update { it.copy(profiles = profiles, engines = engines, history = history, dubHistory = dubs) }
        } finally {
            if(api === current) mutable.update {it.copy(catalogLoading=false)}
        }
    }

    suspend fun refreshVoiceLibrary() { val current=api ?: return; val profiles=current.profiles(); if(api===current) mutable.update {it.copy(profiles=profiles)} }
    fun refresh() = run { reload() }
    fun generate(text: String, voice: String, engine: String, language: String) = run {
        val current = api ?: return@run
        val result = current.generate(text, voice, engine, language)
        if(api !== current) return@run
        val audio = current.audioPath(result.audioPath)
        computerTakes[audio] = engine to text
        mutable.update { it.copy(audio = audio) }
        val history = current.history()
        if(api === current) mutable.update { it.copy(history = history) }
    }
    fun play(path: String) { mutable.update { it.copy(audio = path) } }
    /** A host take: the engine this app asked for when it made it, else unknown. */
    fun computerSpeakSubject(audioPath: String, fallbackText: String = ""): com.voicestudio.mobile.network.ReportSubject {
        val take = computerTakes[audioPath]
        return ReportSubjects.computerSpeak(audioPath, take?.second ?: fallbackText, take?.first)
    }
    fun star(item: HistoryItem) = run { api?.star(item.id, !item.isStarred); reload() }
    fun deleteHistory(item: HistoryItem) = run { api?.deleteHistory(item.id); reload() }
    fun clone(name: String, file: File, hasConsent: ()->Boolean, created: ()->Unit = {}) {
        if(mutable.value.busy) return
        clonePending=true
        run {
            val job=currentCoroutineContext()[Job]
            cloneOperation=job
            try {
                // Retry re-enters this block: never reuse a withdrawn UI consent.
                if(!hasConsent()) {clonePending=false;return@run}
                val current=api ?: throw ClientFailure("mobile.android.disconnected_offline")
                val profile=current.clone(name,file)
                currentCoroutineContext().ensureActive()
                if(api!==current) return@run
                if(!hasConsent()) {clonePending=false;return@run}
                mutable.update {it.copy(profiles=it.profiles.filterNot {saved -> saved.id==profile.id}+profile)}
                file.delete()
                clonePending=false
                created()
            } catch(error:CancellationException) {clonePending=false;throw error}
            finally {if(cloneOperation===job) cloneOperation=null}
        }
        // Also own the job before its first dispatch, and rebind above on retry.
        cloneOperation=operation
    }
    fun cancelClone() {
        if(!clonePending) return
        clonePending=false
        cloneOperation?.cancel()
        cloneOperation=null
        clearError()
    }
    fun rename(id: String, name: String) = run { api?.rename(id, name); reload() }
    fun deleteProfile(id: String) = run { api?.deleteProfile(id); reload() }
    fun forget(url: String) = run {
        if(mutable.value.hostUrl == url) {
            stopHostWork(url); store.disconnect(); api = null; clearHostState()
        }
        store.forget(url)
    }
}
