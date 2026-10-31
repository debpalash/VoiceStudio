package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import okhttp3.OkHttpClient
import okhttp3.Request
import java.io.File
import java.net.URI
import java.security.MessageDigest
import java.util.concurrent.TimeUnit

@Serializable data class GalleryRecording(val url:String,val sha256:String,val ref_text:String)
@Serializable data class GalleryEntry(val id:String,val type:String,val name:String,val language:String,val author:String?=null,val license:String?=null,val instruct:String?=null,val sample_script:String?=null,val audio:GalleryRecording?=null) {
    val attribution get()=mapOf("source" to "community","id" to id,"name" to name,"language" to language,"url" to "https://github.com/debpalash/omnivoice-gallery")+listOfNotNull(author?.let {"author" to it},license?.let {"license" to it}).toMap()
}
@Serializable data class GalleryPack(val id:String,val name:String,val item_ids:List<String>)
@Serializable data class CommunityCatalogue(val schema_version:Int,val packs:List<GalleryPack>,val items:List<GalleryEntry>) {
    companion object {
        fun decode(bytes:ByteArray):CommunityCatalogue {
            require(bytes.size<=4*1024*1024)
            val value=LocalJson.decodeFromString<CommunityCatalogue>(bytes.toString(Charsets.UTF_8))
            require(value.schema_version==1&&value.items.size<=2000&&value.items.map {it.id}.toSet().size==value.items.size)
            value.items.forEach {item ->
                require(item.id.matches(Regex("[A-Za-z0-9_-]{1,128}"))&&item.name.isNotEmpty()&&item.name.length<=200&&item.language.length<=100&&(item.author?.length ?: 0)<=200&&(item.license?.length ?: 0)<=200)
                if(item.type=="preset") require(!item.instruct.isNullOrBlank()&&item.instruct.length<=1000&&!item.sample_script.isNullOrBlank()&&item.sample_script.length<=2000)
                else {val audio=requireNotNull(item.audio);require(item.type=="voice"&&CommunityGallery.allowed(audio.url)&&audio.sha256.matches(Regex("[a-f0-9]{64}"))&&audio.ref_text.isNotBlank()&&audio.ref_text.length<=4000)}
            }
            return value
        }
    }
}
/** No startup network: constructed only on Gallery entry. Retains a validated catalogue and recordings offline. */
class CommunityGallery(private val root:File,private val client:OkHttpClient=OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).callTimeout(30,TimeUnit.SECONDS).build()) {
    private val mutex=Mutex()
    suspend fun cached(seed:ByteArray):CommunityCatalogue=withContext(Dispatchers.IO) {
        runCatching {CommunityCatalogue.decode(File(root,"manifest.json").readBytes())}.getOrElse {CommunityCatalogue.decode(seed)}
    }
    suspend fun refresh():CommunityCatalogue=withContext(Dispatchers.IO) {mutex.withLock {
        val bytes=fetch(MANIFEST,4*1024*1024);val value=CommunityCatalogue.decode(bytes)
        atomicText(File(root,"manifest.json"),bytes.toString(Charsets.UTF_8));value
    }}
    suspend fun recording(entry:GalleryEntry):File=withContext(Dispatchers.IO) {mutex.withLock {
        val audio=requireNotNull(entry.audio);require(entry.type=="voice"&&allowed(audio.url)&&audio.sha256.matches(Regex("[a-f0-9]{64}")))
        val file=File(root,audio.sha256+".wav")
        if(file.isFile&&file.length()<=32*1024*1024&&hash(file.readBytes())==audio.sha256) return@withLock file
        file.delete();val bytes=fetch(audio.url,32*1024*1024)
        if(hash(bytes)!=audio.sha256) throw ClientFailure("mobile.errors.invalid_response")
        root.mkdirs();val pending=File(root,audio.sha256+".pending")
        try {pending.writeBytes(bytes);check(pending.renameTo(file))} finally {pending.delete()};file
    }}
    private suspend fun fetch(initial:String,limit:Int):ByteArray {
        var url=initial
        repeat(6) {
            require(allowed(url));currentCoroutineContext().ensureActive()
            val call=client.newCall(Request.Builder().url(url).build())
            val context=currentCoroutineContext()
            // Cancellation also interrupts blocking socket reads, rather than waiting for the timeout.
            val cancellation=CoroutineScope(context).launch(start=CoroutineStart.UNDISPATCHED) {try {awaitCancellation()} finally {call.cancel()}}
            try {call.execute().use {response ->
                if(response.code in listOf(301,302,303,307,308)) {url=URI(url).resolve(requireNotNull(response.header("Location"))).toString();return@repeat}
                if(response.code!=200) throw ClientFailure("mobile.errors.invalid_response")
                val body=requireNotNull(response.body);require(body.contentLength()<=limit)
                val out=java.io.ByteArrayOutputStream()
                body.byteStream().use {input ->val buffer=ByteArray(65536);while(true) {context.ensureActive();val size=input.read(buffer);if(size<0) break;require(out.size()<=limit-size);out.write(buffer,0,size)}}
                return out.toByteArray()
            }} finally {cancellation.cancel();call.cancel()}
        }
        throw ClientFailure("mobile.errors.invalid_response")
    }
    companion object {
        const val MANIFEST="https://cdn.jsdelivr.net/gh/debpalash/omnivoice-gallery@main/manifest.json"
        fun allowed(value:String):Boolean=runCatching {val url=URI(value);url.scheme=="https"&&url.rawUserInfo==null&&(url.port==-1||url.port==443)&&url.host in setOf("cdn.jsdelivr.net","github.com","raw.githubusercontent.com","objects.githubusercontent.com","release-assets.githubusercontent.com")}.getOrDefault(false)
        private fun hash(bytes:ByteArray)=MessageDigest.getInstance("SHA-256").digest(bytes).joinToString("") {"%02x".format(it)}
    }
}
