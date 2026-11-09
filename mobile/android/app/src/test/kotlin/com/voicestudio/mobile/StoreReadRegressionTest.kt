package com.voicestudio.mobile

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.*
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.data.*
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import java.io.IOException

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class StoreReadRegressionTest {
    private val vault=object:CredentialVault {
        override fun read(host:String):Secret?=null
        override fun write(host:String,secret:Secret?) {}
    }
    private fun store(data:DataStore<androidx.datastore.preferences.core.Preferences>) =
        SessionStore(ApplicationProvider.getApplicationContext<Context>(),vault,data)
    private class MemoryStore(initial:androidx.datastore.preferences.core.Preferences):DataStore<androidx.datastore.preferences.core.Preferences> {
        val values=MutableStateFlow(initial)
        override val data:Flow<androidx.datastore.preferences.core.Preferences> = values
        override suspend fun updateData(transform:suspend (androidx.datastore.preferences.core.Preferences)->androidx.datastore.preferences.core.Preferences):androidx.datastore.preferences.core.Preferences {
            val next=transform(values.value);values.value=next;return next
        }
    }
    @Test fun unreadableStoreFallsBackWithoutSwallowingCancellationOrProgrammingErrors()=runBlocking {
        for(error in listOf(IOException("disk"),CancellationException("cancel"),IllegalStateException("bug"))) {
            val disk=object:DataStore<androidx.datastore.preferences.core.Preferences> {
                override val data=flow<androidx.datastore.preferences.core.Preferences> {throw error}
                override suspend fun updateData(transform:suspend (androidx.datastore.preferences.core.Preferences)->androidx.datastore.preferences.core.Preferences)=error("unused")
            }
            try {
                assertEquals(com.voicestudio.mobile.data.Preferences(),store(disk).preferences.first())
                assertTrue("Only I/O is recoverable",error is IOException)
            } catch(actual:Exception) {if(error is IOException) throw actual;assertSame(error,actual)}
        }
    }
    @Test fun corruptPreferencesAndCheckpointsFallBackAndCanBeReplaced()=runBlocking {
        val disk=MemoryStore(preferencesOf(stringPreferencesKey("preferences") to "{broken"))
        val session=store(disk)
        assertEquals(com.voicestudio.mobile.data.Preferences(),session.preferences.first())
        session.update {it.copy(consent=false,activeUrl="host")}
        assertEquals(false,session.preferences.first().consent)
        disk.updateData {it.toMutablePreferences().apply {this[stringPreferencesKey("dub.host.job")]="{broken"}}
        assertEquals(DubCheckpoint(),session.checkpoint("host","job"))
        session.checkpoint("host","job",DubCheckpoint(renderTask="resumed"))
        assertEquals("resumed",session.checkpoint("host","job").renderTask)
    }
}
