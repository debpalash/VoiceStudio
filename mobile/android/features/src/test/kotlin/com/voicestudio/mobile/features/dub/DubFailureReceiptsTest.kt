package com.voicestudio.mobile.features.dub

import java.util.UUID
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import org.junit.Assert.*
import org.junit.Test

@OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
class DubFailureReceiptsTest {
    @Test fun workerUpdatesCannotHideAnErrorWhoseReceiptWasJustPersisted() = runTest {
        val first = UUID.randomUUID()
        val second = UUID.randomUUID()
        val failures = MutableStateFlow(emptySet<UUID>())
        val persisted = mutableSetOf<UUID>()
        val visible = mutableListOf<UUID>()
        val receiptReturn = CompletableDeferred<Unit>()
        backgroundScope.launch {
            collectDubFailureReceipts(failures, consume = { id ->
                val unseen = persisted.add(id)
                // DataStore can commit before its suspended caller resumes.
                if (id == first && unseen) receiptReturn.await()
                unseen
            }, display = { visible += it }, report = { throw it })
        }
        failures.value = setOf(first)
        runCurrent()
        assertEquals(setOf(first), persisted)
        assertTrue(visible.isEmpty())
        failures.value = setOf(first, second)
        runCurrent()
        receiptReturn.complete(Unit)
        runCurrent()
        assertEquals(listOf(first, second), visible)
        failures.value = setOf(second)
        runCurrent()
        failures.value = setOf(first, second)
        runCurrent()
        assertEquals("Redisplayed worker snapshots must not repeat a receipt", listOf(first, second), visible)
    }
}
