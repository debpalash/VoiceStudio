package com.voicestudio.mobile.features

import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.compose.viewModel
import com.voicestudio.mobile.local.ProductionProject
import java.io.File
import java.lang.ref.WeakReference
import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** The editable value and loading gate survive composition and configuration changes. */
class ProductionProjectState internal constructor(initial: ProductionProject,private val edit: (ProductionProject)->Unit,private val retry: ()->Unit,private val fresh: ()->Unit): MutableState<ProductionProject> {
    private var project by mutableStateOf(initial)
    var loading by mutableStateOf(true)
        internal set
    var loadFailed by mutableStateOf(false)
        internal set
    override var value: ProductionProject
        get()=project
        set(value) {if(!loading) edit(value)}
    internal fun replace(value: ProductionProject) {project=value}
    fun retryRestore()=retry()
    fun startNew()=fresh()
    override fun component1()=value
    override fun component2(): (ProductionProject)->Unit = {value=it}
}

/** New drafts save a reference; existing legacy JSON stays until migration is durable. */
private data class DraftReference(val saved: String?)

class ProductionDraftSession(private val repository: ProductionDraftRepository,private val kind: String,private val untitled: String): ViewModel() {
    val state=ProductionProjectState(ProductionProject(kind=kind,title=untitled),::edit,::retryRestore,::startNew)
    @Volatile private var durable: String?=null
    private var written: ProductionProject?=null
    private var activeReferenceReady=false
    @Volatile private var activityReference: String?=null
    private val retired=linkedSetOf<String>()
    @Volatile private var initialized=false
    @Volatile private var selected=false
    private var saved: String?=null
    private var legacyRecovery: String?=null
    private var loadRunning=false
    private var closed=false
    private var failure: Pair<Any,(Throwable)->Unit>?=null
    private var pendingFailure: Throwable?=null
    private val changes=Channel<Unit>(Channel.CONFLATED)
    private val owner: ProductionDraftRepository.Owner

    init {
        val weak=WeakReference(this)
        owner=repository.acquireOwner(kind,references={weak.get()?.referenceIds() ?: emptySet()}) {weak.get()?.snapshot()}
        viewModelScope.launch {
            for(ignored in changes) {
                if(state.loading||!selected||!repository.owns(owner)) continue
                val project=state.value
                try {
                    if(project!=written) {
                        // A checkpoint can commit before active publication fails
                        // or ownership changes. The old ACK no longer proves disk.
                        written=null
                        val handle=withContext(Dispatchers.IO) {
                            repository.saveOwned(project,owner) {state.value==project}
                        }
                        if(handle!=null&&repository.owns(owner)) {
                            durable=handle;written=project;activeReferenceReady=true
                            if(legacyRecovery!=null) {saved=handle;legacyRecovery=null}
                        }
                    } else if(!activeReferenceReady) {
                        activeReferenceReady=withContext(Dispatchers.IO) {
                            repository.activateOwned(project,owner) {state.value==project}
                        }
                    }
                    cleanup()
                } catch(error:CancellationException) {throw error}
                catch(error:Exception) {if(repository.owns(owner)) report(error)}
            }
        }
    }

    private fun snapshot(): ProductionDraftRepository.EditorSnapshot? =
        if(initialized&&selected&&!state.loading) ProductionDraftRepository.EditorSnapshot(state.value,durable,activityReference,retired.toSet(),legacyRecovery) else null
    private fun referenceIds()=if(initialized&&selected&&!state.loading) setOfNotNull(state.value.id,durable,activityReference) else setOfNotNull(durable,activityReference)

    fun initialize(reference: String?) {
        if(initialized) return
        initialized=true;saved=reference
        legacyRecovery=reference?.takeIf {it.trimStart().startsWith("{")}
        val inherited=owner.previous
        owner.previous=null
        if(inherited!=null) {
            legacyRecovery=inherited.legacyRecovery
            saved=legacyRecovery ?: inherited.durable
            state.replace(inherited.project);durable=inherited.durable;activityReference=inherited.activityReference
            retired.addAll(inherited.retired);selected=true;state.loading=false;flush();return
        }
        activityReference=reference?.takeIf(::isHandle)
        retryRestore()
    }

    fun retryRestore() {
        val reference=saved
        if(!initialized||loadRunning||!repository.owns(owner)) return
        loadRunning=true;state.loading=true;state.loadFailed=false
        viewModelScope.launch {
            try {
                val restored=withContext(Dispatchers.IO) {
                    if(reference==null) repository.restoreActive(kind) else repository.restore(reference)
                }
                if(repository.owns(owner)) {
                    if(restored!=null) {
                        require(restored.kind==kind)
                        state.replace(restored)
                        if(reference==null||reference==restored.id) {durable=restored.id;written=restored}
                        activeReferenceReady=reference==null
                        selected=true
                    }
                    state.loading=false;flush()
                }
            } catch(error:CancellationException) {throw error}
            catch(error:Exception) {if(repository.owns(owner)) {state.loadFailed=true;report(error)}}
            finally {loadRunning=false}
        }
    }

    private fun startNew() {
        if(closed||loadRunning||!state.loadFailed||!repository.owns(owner)) return
        // Explicit recovery starts another UUID. The unreadable old file is
        // not a retirement candidate and is never overwritten or deleted.
        saved=null;legacyRecovery=null;durable=null;written=null;activeReferenceReady=false
        state.replace(ProductionProject(kind=kind,title=untitled))
        selected=true;state.loadFailed=false;state.loading=false;flush()
    }

    private fun edit(project: ProductionProject) {
        if(closed||!repository.owns(owner)) return
        require(project.kind==kind)
        val old=state.value
        if(old.id!=project.id&&legacyRecovery!=null) {legacyRecovery=null;saved=null}
        if(old.id!=project.id&&old.title.isBlank()) retired.add(old.id)
        retired.remove(project.id)
        selected=true;state.replace(project);flush()
    }

    fun savedHandle(): String? {
        // Older apps already placed this exact JSON in Activity state. Keep
        // that recovery source through parse/write waits and live handoffs;
        // dropping it before the first owned checkpoint ACK loses the old draft.
        legacyRecovery?.let {flush();return it}
        // A selected project's UUID stays stable even if its first write is
        // still pending. After that write finishes, a cold process can recover
        // it without a live ViewModel. The reference is not a durability ACK;
        // missing files retain the explicit Retry/New recovery path.
        val handle=if(state.loading) saved?.takeIf(::isHandle) else state.value.id.takeIf {selected&&isHandle(it)}
        if(handle!=null) activityReference=handle
        // A later Activity save also retries a previously failed replacement.
        // The written-snapshot guard avoids rewriting unchanged checkpoints.
        flush()
        return handle
    }

    fun attach(callback: (Throwable)->Unit): Any {
        val token=Any();failure=token to callback
        pendingFailure?.let {pendingFailure=null;callback(it)}
        return token
    }
    fun detach(token: Any) {if(failure?.first===token) failure=null;flush()}
    private fun report(error: Throwable) {val callback=failure?.second;if(callback==null) pendingFailure=error else callback(error)}
    private fun flush() {changes.trySend(Unit)}

    private suspend fun cleanup() {
        val protected=setOfNotNull(state.value.id,durable,activityReference)
        for(id in retired.toList()) {
            if(id in protected) continue
            val removed=withContext(Dispatchers.IO) {
                repository.deleteOwned(id,owner) {id!=state.value.id&&id!=durable&&id!=activityReference}
            }
            if(removed) retired.remove(id)
        }
    }

    override fun onCleared() {
        closed=true;failure=null
        val latest=snapshot()
        val alreadyWritten=latest?.project==written
        val alreadyActive=activeReferenceReady
        repository.handoff(owner,latest)
        // The last queued edit outlives the UI. A newer session takes over the
        // handoff and invalidates this generation before it can publish.
        if(latest!=null&&(!alreadyWritten||!alreadyActive||latest.retired.isNotEmpty()||latest.activityReference!=latest.project.id)) CoroutineScope(SupervisorJob()+Dispatchers.IO).launch {
            try {
                val handle=if(alreadyWritten) {
                    latest.project.id.takeIf {alreadyActive||repository.activateOwned(latest.project,owner) {true}}
                } else repository.saveOwned(latest.project,owner) {true}
                if(handle!=null) {
                    for(id in latest.retired) {
                        repository.deleteOwned(id,owner) {id!=latest.project.id&&id!=latest.activityReference}
                    }
                    if(latest.activityReference==latest.project.id) repository.release(owner)
                    else repository.handoff(owner,latest.copy(durable=handle,legacyRecovery=null))
                }
            } catch(_:Exception) { /* Keep the bounded handoff for a later editor. */ }
        } else repository.release(owner)
        super.onCleared()
    }

    private fun isHandle(value: String)=runCatching {UUID.fromString(value).toString()==value}.getOrDefault(false)
}

@Composable
fun rememberProductionProject(kind: String,untitled: String,draftDirectory: File,failure: (Throwable)->Unit): ProductionProjectState {
    val repository=remember(draftDirectory) {ProductionDraftRepository(draftDirectory)}
    val latestFailure=rememberUpdatedState(failure)
    val session: ProductionDraftSession=viewModel(
        key="production-draft:$kind:${draftDirectory.absolutePath}",
        factory=remember(repository,kind) {object:ViewModelProvider.Factory {
            override fun <T:ViewModel> create(modelClass: Class<T>): T {
                require(modelClass.isAssignableFrom(ProductionDraftSession::class.java))
                @Suppress("UNCHECKED_CAST")
                return ProductionDraftSession(repository,kind,untitled) as T
            }
        }},
    )
    val saver=remember(session) {Saver<DraftReference,String>(save={session.savedHandle()},restore={DraftReference(it)})}
    // Keep the previous MutableState saver envelope so existing UUID/legacy
    // JSON instance state reaches the lightweight reference decoder unchanged.
    val reference by rememberSaveable(kind,stateSaver=saver) {mutableStateOf(DraftReference(null))}
    DisposableEffect(session) {
        val token=session.attach {latestFailure.value(it)}
        onDispose {session.detach(token)}
    }
    LaunchedEffect(session,reference) {session.initialize(reference.saved)}
    return session.state
}
