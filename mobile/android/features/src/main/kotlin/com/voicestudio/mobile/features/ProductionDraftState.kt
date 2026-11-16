package com.voicestudio.mobile.features

import androidx.compose.runtime.Composable
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.snapshotFlow
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import com.voicestudio.mobile.local.ProductionProject
import java.io.File
import java.util.concurrent.atomic.AtomicReference
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.collect
import kotlinx.coroutines.withContext

/** Checkpoint edits off-thread; activity state contains only a durable handle. */
@Composable
fun rememberProductionProject(
    kind: String,
    untitled: String,
    draftDirectory: File,
    failure: (Throwable) -> Unit,
): MutableState<ProductionProject> {
    val repository=remember(draftDirectory) {ProductionDraftRepository(draftDirectory)}
    val latestFailure=rememberUpdatedState(failure)
    val checkpoint=remember(repository,kind) {AtomicReference<String?>(null)}
    val saver=remember(repository,kind) {Saver<ProductionProject,String>(
        // Saving the Activity must never serialize or fsync a manuscript on
        // the UI thread. A failed edit retains this project's last checkpoint;
        // switching projects cannot borrow the preceding project's handle.
        save={project ->checkpoint.get()?.takeIf {it==project.id}},
        restore={saved ->
            try {repository.restore(saved).also {
                require(it.kind==kind)
                // A legacy JSON value has not yet acquired a disk checkpoint.
                if(saved==it.id) checkpoint.set(saved)
            }}
            catch(error:Exception) {latestFailure.value(error);null}
        },
    )}
    val state=rememberSaveable(kind,stateSaver=saver) {mutableStateOf(ProductionProject(kind=kind,title=untitled))}
    LaunchedEffect(repository,kind,state) {
        // A single collector finishes each AtomicFile publication before the
        // next snapshot. snapshotFlow conflates edits made during disk I/O.
        snapshotFlow {state.value}.collect {project ->
            try {
                withContext(Dispatchers.IO) {
                    repository.saveIfCurrent(project) {state.value==project}?.let(checkpoint::set)
                }
            } catch(error:CancellationException) {throw error}
            catch(error:Exception) {latestFailure.value(error)}
        }
    }
    return state
}
