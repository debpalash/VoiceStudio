package com.voicestudio.mobile.features

import androidx.compose.runtime.Composable
import androidx.compose.runtime.MutableState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.Saver
import androidx.compose.runtime.saveable.rememberSaveable
import com.voicestudio.mobile.local.ProductionProject
import java.io.File

/** Save the latest edit before publishing a small activity-state handle. */
@Composable
fun rememberProductionProject(
    kind: String,
    untitled: String,
    draftDirectory: File,
    failure: (Throwable) -> Unit,
): MutableState<ProductionProject> {
    val repository=remember(draftDirectory) {ProductionDraftRepository(draftDirectory)}
    val latestFailure=rememberUpdatedState(failure)
    val saver=remember(repository,kind) {Saver<ProductionProject,String>(
        // The library's debounce/onDispose is not a process-death boundary.
        // Never put a full manuscript back in the Bundle if storage fails.
        save={project ->
            try {repository.save(project)} catch(error:Exception) {latestFailure.value(error);null}
        },
        restore={saved ->
            try {repository.restore(saved).also {require(it.kind==kind)}}
            catch(error:Exception) {latestFailure.value(error);null}
        },
    )}
    return rememberSaveable(kind,stateSaver=saver) {mutableStateOf(ProductionProject(kind=kind,title=untitled))}
}
