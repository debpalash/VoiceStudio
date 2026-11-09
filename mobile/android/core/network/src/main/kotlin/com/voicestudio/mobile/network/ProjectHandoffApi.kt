package com.voicestudio.mobile.network

import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.RequestBody.Companion.asRequestBody

@Serializable data class HandoffRemote(val id: String,val title: String,val kind: String,val createdAt: Double,val incoming: Boolean)
suspend fun VoiceStudioApi.handoffInbox(): List<HandoffRemote> = withContext(Dispatchers.IO) {raw("/project-bundles").use {json.decodeFromString(it.body!!.string())}}
suspend fun VoiceStudioApi.sendHandoff(file: File,allowVoices: Boolean): Unit=withContext(Dispatchers.IO) {
    require(file.isFile&&file.length()<=1_082_130_432L)
    raw("/project-bundles/import?allow_voices=$allowVoices","POST",file.asRequestBody("application/zip".toMediaType())).use {require(it.isSuccessful)}
}
suspend fun VoiceStudioApi.receiveHandoff(id: String,output: File,maxBytes: Long=1_082_130_432L): File=withContext(Dispatchers.IO) {
    require(UUID.fromString(id).toString()==id.lowercase()&&!output.exists())
    val pending=File(output.parentFile,".receive-${UUID.randomUUID()}")
    try {
        raw("/project-bundles/$id").use {response->
            val body=requireNotNull(response.body);require(body.contentLength()<=maxBytes)
            body.byteStream().use {input->pending.outputStream().use {writer->
                val bytes=ByteArray(262144);var count=0L
                while(true) {currentCoroutineContext().ensureActive();val n=input.read(bytes);if(n<0) break;count+=n;require(count<=maxBytes);writer.write(bytes,0,n)}
            }}
        }
        currentCoroutineContext().ensureActive();Files.move(pending.toPath(),output.toPath(),StandardCopyOption.ATOMIC_MOVE);output
    } finally {pending.delete()}
}
