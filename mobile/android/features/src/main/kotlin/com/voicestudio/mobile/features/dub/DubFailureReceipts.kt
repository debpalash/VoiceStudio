package com.voicestudio.mobile.features.dub

import java.util.UUID
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.collect

internal suspend fun collectDubFailureReceipts(
    failures: Flow<Set<UUID>>,
    consume: suspend (UUID) -> Boolean,
    display: (UUID) -> Unit,
    report: (Exception) -> Unit,
) {
    val handled = mutableSetOf<UUID>()
    // Finish persisting and displaying each receipt before observing the next worker snapshot.
    failures.collect { ids ->
        for (id in ids) if (id !in handled) {
            try {
                if (consume(id)) display(id)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                // A failed receipt write must never hide the actual job error.
                display(id)
                report(error)
            }
            handled += id
        }
    }
}
