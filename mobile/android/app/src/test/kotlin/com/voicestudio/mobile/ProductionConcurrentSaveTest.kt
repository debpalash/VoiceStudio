package com.voicestudio.mobile

import com.voicestudio.mobile.local.ProductionProject
import com.voicestudio.mobile.local.ProductionStore
import java.io.File
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.CompletableFuture
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.mockito.Mockito
import org.mockito.MockMakers
import org.mockito.stubbing.Answer

class ProductionConcurrentSaveTest {
    @get:Rule val temporary=TemporaryFolder()

    @Test fun twoStoresCannotConsumeEachOthersPendingPublication() {
        val root=temporary.newFolder("production")
        val firstStore=ProductionStore(root)
        val secondStore=ProductionStore(root)
        val first=ProductionProject(kind="stories",title="First complete fixture",script="First synthetic sentence.\n".repeat(4000))
        val second=first.copy(title="Second complete fixture",script="Second synthetic sentence.\n".repeat(4000))
        val target=File(File(root,first.id),"project.json").toPath()
        val entered=CountDownLatch(1)
        val release=CountDownLatch(1)
        val held=AtomicBoolean(false)
        val firstResult=CompletableFuture<Throwable?>()
        val secondResult=CompletableFuture<Throwable?>()
        // Mockito static mocks are thread-local. Install this one on the same
        // IO context save() uses, and delegate every filesystem call to the JDK.
        // Only writer A pauses after its real write/fsync, just before move.
        val firstWriter=Thread({
            try {
                runBlocking(Dispatchers.IO) {
                    val observe=Answer<Any?> {call ->
                        if(call.method.name=="move"&&(call.arguments[1] as? Path)==target&&held.compareAndSet(false,true)) {
                            entered.countDown()
                            check(release.await(10,TimeUnit.SECONDS)) {"Controlled publication was not released"}
                        }
                        Mockito.CALLS_REAL_METHODS.answer(call)
                    }
                    Mockito.mockStatic(Files::class.java,Mockito.withSettings().mockMaker(MockMakers.INLINE).defaultAnswer(observe)).use {firstStore.save(first)}
                }
                firstResult.complete(null)
            } catch(error:Throwable) {firstResult.complete(error)}
        },"production-save-first").apply {isDaemon=true}
        var secondWriter:Thread?=null
        try {
            firstWriter.start()
            val reached=entered.await(10,TimeUnit.SECONDS)
            if(!reached) {
                val failure=firstResult.getNow(null)
                throw AssertionError("Writer A did not reach its real atomic move: completed=${firstResult.isDone}, failure=$failure, thread=${firstWriter.state}, frames=${firstWriter.stackTrace.take(8).joinToString()}",failure)
            }
            secondWriter=Thread({
                try {runBlocking {secondStore.save(second)};secondResult.complete(null)}
                catch(error:Throwable) {secondResult.complete(error)}
            },"production-save-second").apply {isDaemon=true;start()}
            assertNull("Writer B must publish while A is held",secondResult.get(10,TimeUnit.SECONDS))
            assertEquals("B's publication must contain its whole project",second,runBlocking {secondStore.list().single()})
            release.countDown()
            assertNull("A's staging file must survive B's complete save",firstResult.get(10,TimeUnit.SECONDS))
            assertEquals("The last publication must contain A's whole project",first,runBlocking {ProductionStore(root).list().single()})
            assertEquals(listOf("project.json"),File(root,first.id).listFiles()!!.map {it.name})
        } finally {
            release.countDown()
            firstWriter.join(5000)
            secondWriter?.join(5000)
        }
    }
}
