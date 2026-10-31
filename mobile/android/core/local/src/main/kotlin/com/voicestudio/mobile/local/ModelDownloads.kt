package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import okhttp3.*
import okhttp3.HttpUrl.Companion.toHttpUrl
import java.io.File
import java.io.IOException
import java.io.RandomAccessFile
import java.security.MessageDigest
import java.util.concurrent.ConcurrentHashMap

internal fun sha256(file: File, check: ()->Unit = {}): String = file.inputStream().use { input ->
    val digest=MessageDigest.getInstance("SHA-256");val bytes=ByteArray(262144)
    while(true) {check();val n=input.read(bytes);if(n<0) break;digest.update(bytes,0,n)}
    digest.digest().joinToString("") {"%02x".format(it)}
}
data class DownloadProgress(val received: Long = 0, val total: Long = 0, val running: Boolean = false, val error: String? = null)
/** Dedicated public-download client never carries paired-host PINs or sessions. */
class ModelDownloads internal constructor(
    private val root: File, val licence: LicenceGate, private val ramBytes: Long,
    private val wifi: () -> Boolean, private val freeBytes: () -> Long,
    private val client: OkHttpClient = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false).build(),
    private val endpoints: List<String> = listOf("https://huggingface.co", "https://hf-mirror.com"),
    private val allowTestHttp: Boolean = false,
    private val bundledAssets: ((String)->java.io.InputStream)? = null,
    private val digest: (File, ()->Unit)->String = ::sha256
) {
    constructor(root: File, licence: LicenceGate, ramBytes: Long, wifi: () -> Boolean) : this(root,licence,ramBytes,wifi,{root.apply {mkdirs()}.usableSpace})
    constructor(root:File,licence:LicenceGate,ramBytes:Long,bundledAssets:(String)->java.io.InputStream,wifi:()->Boolean):this(root,licence,ramBytes,wifi,{root.apply {mkdirs()}.usableSpace},bundledAssets=bundledAssets)
    private data class VerifiedStamp(val size: Long, val modified: Long, val catalogue: String)
    private val verified=ConcurrentHashMap<File,VerifiedStamp>()
    private fun stamp(file: File, catalogue: String)=VerifiedStamp(file.length(),file.lastModified(),catalogue)
    private fun verifiedFile(file: File, expected: ModelFile, catalogue: String, check: ()->Unit): Boolean {
        check()
        if(!file.isFile||file.length()!=expected.size) {verified.remove(file);return false}
        val before=stamp(file,catalogue)
        if(verified[file]==before) return true
        verified.remove(file)
        if(digest(file,check)!=expected.sha256||stamp(file,catalogue)!=before) return false
        verified[file]=before
        return true
    }
    private fun invalidate(model: PhoneModel) {model.files.forEach {verified.remove(File(dir(model),it.path))}}
    private val locks=ConcurrentHashMap<String,Mutex>()
    private val reservations=HashMap<String,()->Long>()
    private fun lock(model: PhoneModel)=locks.getOrPut(model.id) {Mutex()}
    private val mutable=MutableStateFlow<Map<String,DownloadProgress>>(emptyMap())
    val progress=mutable.asStateFlow()
    private fun dir(model: PhoneModel)=File(root,model.id)
    private fun identity(model: PhoneModel)=model.revision+model.files.joinToString {it.path+it.sha256+it.size}
    fun installed(model: PhoneModel): Boolean = runCatching {
        val directory=dir(model)
        File(directory,".verified").readText()==identity(model) && model.files.all {File(directory,it.path).length()==it.size}
    }.getOrDefault(false)
    fun usedBytes(model: PhoneModel): Long = dir(model).walkTopDown().filter {it.isFile}.sumOf {it.length()}
    fun usedBytes(): Long = root.walkTopDown().filter {it.isFile}.sumOf {it.length()}
    private fun networkAllowed(allowMobile: Boolean) {if(!allowMobile&&!wifi()) throw ClientFailure("mobile.local.wifi_required")}
    private fun fit(model: PhoneModel) {if(!model.fits(ramBytes)) throw ClientFailure("mobile.local.ram_required",mapOf("ram" to model.minRamGb.toString()))}
    suspend fun download(model: PhoneModel, allowMobile: Boolean) = withContext(Dispatchers.IO) {
        lock(model).withLock {
            fit(model);licence.requireAccepted(model)
            val job=currentCoroutineContext().job
            val directory=dir(model).apply {mkdirs()}
            val catalogue=modelPin(model).identity
            val completedFiles=model.files.filter {verifiedFile(File(directory,it.path),it,catalogue) {job.ensureActive()}}.toMutableSet()
            fun remainingBytes()=model.files.filterNot {it in completedFiles}.sumOf { f ->
                val partial=File(directory,f.path+".part").length().takeIf {it<=f.size} ?: 0L
                f.size-partial
            }
            val needed=remainingBytes()
            if(model.files.any {it.bundled==null&&it !in completedFiles&&File(directory,it.path+".part").length()<it.size}) networkAllowed(allowMobile)
            // Read current partial lengths so retries and completed writes are not counted twice.
            // completedFiles is updated under this same monitor after each successful install.
            synchronized(reservations) {
                if(needed>0 && freeBytes()-reservations.values.sumOf {it()}<needed+16*1024*1024) throw ClientFailure("mobile.local.storage_required")
                reservations[model.id]=::remainingBytes
            }
            mutable.update {it+(model.id to DownloadProgress(total=model.totalBytes,running=true))}
            try {
                var completed=0L
                model.files.forEach { f ->
                    currentCoroutineContext().ensureActive();licence.requireAccepted(model)
                    val target=File(directory,f.path).apply {parentFile!!.mkdirs()}
                    if(f in completedFiles) {completed+=f.size;return@forEach}
                    verified.remove(target);target.delete()
                    val part=File(directory,f.path+".part")
                    if(part.length()>f.size) part.delete()
                    var last: IOException?=null
                    if(f.bundled!=null) {
                        require(f.url==null&&ModelAssetSource.validBundled(f.bundled))
                        val load=bundledAssets ?: throw ClientFailure("mobile.local.catalogue_failed")
                        load(f.bundled).use {input->part.outputStream().use {output->input.copyTo(output)}}
                    }
                    else for(endpoint in endpoints) {
                        try {
                            transfer(endpoint,model,f,part,allowMobile) { received -> mutable.update {it+(model.id to DownloadProgress(completed+received,model.totalBytes,true))} }
                            last=null;break
                        } catch(e:IOException) {last=e;currentCoroutineContext().ensureActive()}
                    }
                    if(last!=null) throw ClientFailure("mobile.local.download_failed")
                    currentCoroutineContext().ensureActive()
                    if(part.length()!=f.size||digest(part) {job.ensureActive()}!=f.sha256) {part.delete();throw ClientFailure("mobile.local.checksum_failed")}
                    licence.requireAccepted(model)
                    synchronized(reservations) {
                        if(!part.renameTo(target)) throw ClientFailure("mobile.local.storage_required")
                        completedFiles.add(f)
                    }
                    verified[target]=stamp(target,catalogue)
                    completed+=f.size
                }
                licence.requireAccepted(model)
                val marker=File(directory,".verified.tmp");marker.writeText(identity(model))
                if(!marker.renameTo(File(directory,".verified"))) throw ClientFailure("mobile.local.storage_required")
                mutable.update {it+(model.id to DownloadProgress(model.totalBytes,model.totalBytes))}
            } catch(e:CancellationException) {mutable.update {it-model.id};throw e}
            catch(e:Exception) {
                val failure=e as? ClientFailure ?: ClientFailure("mobile.local.download_failed")
                mutable.update {it+(model.id to DownloadProgress(total=model.totalBytes,error=failure.key))};throw failure
            } finally {synchronized(reservations) {reservations.remove(model.id)}}
        }
    }
    // Keep in step with iOS ModelRedirectDelegate.allowed (SPEC M2): mirror CDN
    // subdomains download like the apex host, and other ports are refused, on every platform.
    internal fun allowed(url: HttpUrl): Boolean = allowTestHttp || (url.scheme=="https" && url.port==443 && url.username.isEmpty() && url.password.isEmpty() &&
        (url.host in setOf("huggingface.co","hf-mirror.com","hf.co","github.com","release-assets.githubusercontent.com","objects.githubusercontent.com") || url.host.endsWith(".huggingface.co") || url.host.endsWith(".hf.co") || url.host.endsWith(".hf-mirror.com")))
    private suspend fun transfer(endpoint: String, model: PhoneModel, file: ModelFile, partial: File, allowMobile: Boolean, report: (Long)->Unit) {
        if(partial.length()==file.size) return
        if(file.url!=null&&!ModelAssetSource.validUrl(file.url)) throw ClientFailure("mobile.local.catalogue_failed")
        var url=if(file.url!=null) file.url.toHttpUrl().let {fixed->if(fixed.host=="huggingface.co") fixed.newBuilder().host(endpoint.toHttpUrl().host).build() else fixed} else endpoint.toHttpUrl().newBuilder().addPathSegments(model.repo).addPathSegment("resolve").addPathSegment(model.revision).addPathSegments(file.path).build()
        repeat(10) {
            networkAllowed(allowMobile);licence.requireAccepted(model);currentCoroutineContext().ensureActive()
            if(!allowed(url)) throw ClientFailure("mobile.local.download_failed")
            val offset=partial.length()
            val request=Request.Builder().url(url).header("Accept-Encoding","identity").apply {if(offset>0) header("Range","bytes=$offset-")}.build()
            val call=client.newCall(request)
            coroutineScope {
                val cancellation=launch {try {awaitCancellation()} finally {call.cancel()}}
                try {
                    call.execute().use { response ->
                        if(response.code in listOf(301,302,303,307,308)) {
                            url=url.resolve(response.header("Location") ?: throw IOException()) ?: throw IOException()
                        } else {
                            if(response.code !in listOf(200,206)) throw IOException("Download HTTP ${response.code}")
                            val start=if(response.code==206) offset else 0L
                            if(response.code==206 && response.header("Content-Range")?.startsWith("bytes $offset-")!=true) throw IOException("Invalid range")
                            if(response.code==206 && response.header("Content-Range")?.substringAfter('/')!=file.size.toString()) throw IOException("Invalid total")
                            val body=response.body ?: throw IOException()
                            RandomAccessFile(partial,"rw").use { output ->
                                if(start==0L) output.setLength(0)
                                output.seek(start);var count=start
                                body.byteStream().use { input ->
                                    val bytes=ByteArray(65536)
                                    while(true) {
                                        currentCoroutineContext().ensureActive();networkAllowed(allowMobile);licence.requireAccepted(model)
                                        val n=input.read(bytes);if(n<0) break
                                        if(count+n>file.size) {partial.delete();throw ClientFailure("mobile.local.checksum_failed")}
                                        output.write(bytes,0,n);count+=n;report(count)
                                    }
                                }
                                output.fd.sync()
                                if(count!=file.size) throw IOException("Incomplete model file")
                            }
                            return@coroutineScope
                        }
                    }
                } finally {cancellation.cancelAndJoin()}
            }
            if(partial.length()==file.size) return
        }
        throw IOException("Too many download redirects")
    }
    suspend fun delete(model: PhoneModel) = withContext(Dispatchers.IO) {lock(model).withLock {
        invalidate(model)
        if(dir(model).exists()&&!dir(model).deleteRecursively()) throw ClientFailure("mobile.local.storage_required")
        mutable.update {it-model.id}
    }}
    /** Deletion and inference share a lease. Rehash only changed files or after a failed native use. */
    suspend fun <T> withModel(model: PhoneModel, use: suspend (File)->T): T = withContext(Dispatchers.IO) {lock(model).withLock {
        fit(model);licence.requireAccepted(model)
        if(!installed(model)) throw ClientFailure("mobile.local.model_required")
        val job=currentCoroutineContext().job
        val catalogue=modelPin(model).identity
        model.files.forEach { f ->
            currentCoroutineContext().ensureActive()
            if(!verifiedFile(File(dir(model),f.path),f,catalogue) {job.ensureActive()}) {
                invalidate(model);File(dir(model),".verified").delete();throw ClientFailure("mobile.local.checksum_failed")
            }
        }
        licence.requireAccepted(model)
        try {use(dir(model))}
        catch(e:CancellationException) {throw e}
        catch(e:Throwable) {invalidate(model);throw e}
    }}
}
