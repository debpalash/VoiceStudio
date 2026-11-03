package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
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
data class DownloadProgress(val received: Long = 0, val total: Long = 0, val running: Boolean = false, val error: String? = null, val bytesPerSecond: Double = 0.0, val etaSeconds: Double? = null, val stage: String = "downloading", val retrySeconds: Long = 0)
/** Dedicated public-download client never carries paired-host PINs or sessions.
 * ramBytes remains a compatible constructor argument; RAM advice is not authorization. */
class ModelDownloads internal constructor(
    private val root: File, val licence: LicenceGate, @Suppress("UNUSED_PARAMETER") ramBytes: Long,
    private val wifi: () -> Boolean, private val freeBytes: () -> Long,
    private val client: OkHttpClient = OkHttpClient.Builder().followRedirects(false).followSslRedirects(false)
        .connectTimeout(30,java.util.concurrent.TimeUnit.SECONDS).readTimeout(120,java.util.concurrent.TimeUnit.SECONDS).build(),
    private val endpoints: List<String> = listOf("https://huggingface.co", "https://hf-mirror.com"),
    private val allowTestHttp: Boolean = false,
    private val bundledAssets: ((String)->java.io.InputStream)? = null,
    private val digest: (File, ()->Unit)->String = ::sha256,
    private val retryDelay: suspend (Long)->Unit = {delay(it)},
    private val chunkSize: Long = 16L*1024*1024
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
    private val sourceRates=ConcurrentHashMap<String,Double>()
    private val sourceCooldowns=ConcurrentHashMap<String,Long>()
    private val connections=Semaphore(4)
    private val locks=ConcurrentHashMap<String,Mutex>()
    private val reservations=HashMap<String,()->Long>()
    private val wholeDownloads=ConcurrentHashMap<File,File>()
    private fun wholeFile(partial: File,file: ModelFile)=File(partial.parentFile,partial.name+".whole-"+file.sha256)
    private fun replacePartial(whole: File,partial: File,catalogue: String,checked: Boolean=true) {
        try {java.nio.file.Files.move(whole.toPath(),partial.toPath(),java.nio.file.StandardCopyOption.REPLACE_EXISTING)}
        catch(e:IOException) {throw ClientFailure("mobile.local.storage_required")}
        verified.remove(whole);verified.remove(partial)
        if(checked) verified[partial]=stamp(partial,catalogue)
    }
    private fun lock(model: PhoneModel)=locks.getOrPut(model.id) {Mutex()}
    private val mutable=MutableStateFlow<Map<String,DownloadProgress>>(emptyMap())
    val progress=mutable.asStateFlow()
    fun scheduled(id: String,total: Long,stage: String="waiting") {mutable.update {if(it[id]?.running==true) it else it+(id to (it[id] ?: DownloadProgress(total=total)).copy(total=total,running=true,stage=stage,error=null))}}
    fun paused(id: String,total: Long?=null,error: String?=null) {mutable.update {it+(id to (it[id] ?: DownloadProgress()).let {p ->p.copy(total=total ?: p.total,running=false,stage=if(error==null) "paused" else "failed",error=error,bytesPerSecond=0.0,etaSeconds=null)})}}
    private fun dir(model: PhoneModel)=File(root,model.id)
    private fun identity(model: PhoneModel)=model.revision+model.files.joinToString {it.path+it.sha256+it.size}
    fun installed(model: PhoneModel): Boolean = runCatching {
        val directory=dir(model)
        File(directory,".verified").readText()==identity(model) && model.files.all {File(directory,it.path).length()==it.size}
    }.getOrDefault(false)
    fun usedBytes(model: PhoneModel): Long = dir(model).walkTopDown().filter {it.isFile}.sumOf {it.length()}
    fun usedBytes(): Long = root.walkTopDown().filter {it.isFile}.sumOf {it.length()}
    private fun networkAllowed(allowMobile: Boolean) {if(!allowMobile&&!wifi()) throw ClientFailure("mobile.local.wifi_required")}
    suspend fun download(model: PhoneModel, allowMobile: Boolean) = withContext(Dispatchers.IO) {
        lock(model).withLock {
            licence.requireAccepted(model)
            val job=currentCoroutineContext().job
            val directory=dir(model).apply {mkdirs()}
            val catalogue=modelPin(model).identity
            val completedFiles=model.files.filter {verifiedFile(File(directory,it.path),it,catalogue) {job.ensureActive()}}.toMutableSet()
            model.files.filterNot {it in completedFiles}.forEach {
                val partial=File(directory,it.path+".part")
                if(partial.length()>it.size&&!partial.delete()) throw ClientFailure("mobile.local.storage_required")
                val whole=wholeFile(partial,it)
                if(whole.length()==it.size) {
                    if(verifiedFile(whole,it,catalogue) {job.ensureActive()}) replacePartial(whole,partial,catalogue)
                    else whole.delete()
                } else if(whole.isFile) {
                    // A failed whole response is still a contiguous prefix of this
                    // immutable file. Keep the longer prefix; hash before activation.
                    if(whole.length() in 1 until it.size&&whole.length()>partial.length()) replacePartial(whole,partial,catalogue,checked=false)
                    else whole.delete()
                }
                recoverRanges(partial,it)
            }
            fun remainingBytes()=model.files.filterNot {it in completedFiles}.sumOf { f ->
                val path=File(directory,f.path+".part")
                val whole=wholeDownloads[path]
                if(whole!=null) (f.size-whole.length()).coerceAtLeast(0)
                else {
                    val partial=path.length().takeIf {it<=f.size} ?: 0L
                    f.size-partial-savedRanges(path,f.size,f.sha256)
                }
            }
            val needed=remainingBytes()
            val assembly=if(model.files.any {it !in completedFiles&&it.bundled==null&&it.size-File(directory,it.path+".part").length()>=chunkSize*2}) chunkSize else 0L
            if(model.files.any {it.bundled==null&&it !in completedFiles&&File(directory,it.path+".part").length()<it.size}) networkAllowed(allowMobile)
            // Read current partial lengths so retries and completed writes are not counted twice.
            // completedFiles is updated under this same monitor after each successful install.
            synchronized(reservations) {
                if(needed+assembly>0 && freeBytes()-reservations.values.sumOf {it()}<needed+assembly+16*1024*1024) throw ClientFailure("mobile.local.storage_required")
                reservations[model.id]={remainingBytes()+assembly}
            }
            val meter=DownloadMeter(model.totalBytes)
            model.files.forEach { f -> meter.seed(f.path,if(f in completedFiles) f.size else (File(directory,f.path+".part").length()+savedRanges(File(directory,f.path+".part"),f.size,f.sha256)).coerceAtMost(f.size)) }
            var publishedAt=0L
            var publishedStage=""
            fun report(file: ModelFile, bytes: Long, stage: String="downloading", retry: Long=0) {
                synchronized(meter) {
                    val value=meter.update(file.path,bytes,stage,retry)
                    val now=System.nanoTime()
                    if(now-publishedAt>=500_000_000||stage!=publishedStage||value.received==model.totalBytes) {
                        mutable.update {it+(model.id to value)};publishedAt=now;publishedStage=stage
                    }
                }
            }
            mutable.update {it+(model.id to meter.update("",0))}
            try {
                var completed=0L
                model.files.forEach { f ->
                    currentCoroutineContext().ensureActive();licence.requireAccepted(model)
                    val target=File(directory,f.path).apply {parentFile!!.mkdirs()}
                    if(f in completedFiles) {completed+=f.size;return@forEach}
                    verified.remove(target);target.delete()
                    val part=File(directory,f.path+".part")
                    if(part.length()>f.size) part.delete()
                    if(f.bundled!=null) {
                        require(f.url==null&&ModelAssetSource.validBundled(f.bundled))
                        val load=bundledAssets ?: throw ClientFailure("mobile.local.catalogue_failed")
                        load(f.bundled).use {input->part.outputStream().use {output->input.copyTo(output)}}
                    }
                    else resilientTransfer(model,f,part,allowMobile) {bytes,stage,retry -> report(f,bytes,stage,retry)}
                    currentCoroutineContext().ensureActive()
                    report(f,part.length(),"verifying")
                    if(!verifiedFile(part,f,catalogue) {job.ensureActive()}) {part.delete();throw ClientFailure("mobile.local.checksum_failed")}
                    licence.requireAccepted(model)
                    synchronized(reservations) {
                        if(!part.renameTo(target)) throw ClientFailure("mobile.local.storage_required")
                        completedFiles.add(f)
                    }
                    verified.remove(part);verified[target]=stamp(target,catalogue)
                    completed+=f.size
                }
                licence.requireAccepted(model)
                val marker=File(directory,".verified.tmp");marker.writeText(identity(model))
                if(!marker.renameTo(File(directory,".verified"))) throw ClientFailure("mobile.local.storage_required")
                mutable.update {it+(model.id to DownloadProgress(model.totalBytes,model.totalBytes))}
            } catch(e:CancellationException) {paused(model.id);throw e}
            catch(e:Exception) {
                val failure=e as? ClientFailure ?: ClientFailure("mobile.local.download_failed")
                mutable.update {it+(model.id to DownloadProgress(total=model.totalBytes,error=failure.key))};throw failure
            } finally {synchronized(reservations) {reservations.remove(model.id)}}
        }
    }
    private fun rangesDir(partial: File)=File(partial.parentFile,partial.name+".ranges")
    private fun savedRanges(partial: File,total: Long,hash: String): Long {
        val directory=rangesDir(partial)
        if(runCatching {File(directory,"identity").readText()}.getOrNull()!=hash) return 0
        return directory.listFiles().orEmpty().groupBy {it.name.removeSuffix(".tmp")}.values.sumOf { copies ->
            copies.maxOf {chunk ->
                val bounds=chunk.name.removeSuffix(".tmp").split("-").mapNotNull {it.toLongOrNull()}
                if(bounds.size==2&&bounds[0]>=partial.length()&&bounds[1]<total&&chunk.length()<=bounds[1]-bounds[0]+1) chunk.length() else 0L
            }
        }
    }
    private fun checkReservedSpace() = synchronized(reservations) {
        val reserved=reservations.values.sumOf {it()}
        if(reserved>0&&freeBytes()<reserved+16*1024*1024) throw ClientFailure("mobile.local.storage_required")
    }
    private fun recoverRanges(partial: File,file: ModelFile) {
        val directory=rangesDir(partial)
        if(runCatching {File(directory,"identity").readText()}.getOrNull()!=file.sha256) return
        while(partial.length()<file.size) {
            val offset=partial.length()
            val saved=directory.listFiles().orEmpty().firstOrNull {chunk ->
                val bounds=chunk.name.split('-').mapNotNull {it.toLongOrNull()}
                bounds.size==2&&bounds[0]<=offset&&bounds[1]>=offset&&bounds[1]<file.size&&chunk.length()==bounds[1]-bounds[0]+1
            } ?: return
            val lower=saved.name.substringBefore('-').toLong()
            synchronized(reservations) {
                if(freeBytes()<reservations.values.sumOf {it()}+saved.length()-(offset-lower)+16*1024*1024) throw ClientFailure("mobile.local.storage_required")
            }
            RandomAccessFile(saved,"r").use {input ->RandomAccessFile(partial,"rw").use {output ->
                input.seek(offset-lower);output.seek(offset)
                val buffer=ByteArray(65536)
                while(true) {val n=input.read(buffer);if(n<0) break;output.write(buffer,0,n)}
                output.fd.sync()
            }}
            saved.delete()
        }
    }
    private class RangeUnsupported: IOException()
    private suspend fun resilientTransfer(model: PhoneModel,file: ModelFile,partial: File,allowMobile: Boolean,report: (Long,String,Long)->Unit) {
        val directory=rangesDir(partial)
        var width=2
        var previousRate=0.0
        try {
            if(file.size-partial.length()<chunkSize*2) {retryTransfer(model,file,partial,allowMobile,report);return}
            directory.mkdirs()
            // Bind reusable ranges to this exact immutable file, even after catalogue updates.
            val identity=File(directory,"identity")
            if(!identity.isFile||identity.readText()!=file.sha256) {directory.listFiles().orEmpty().forEach {it.delete()};identity.writeText(file.sha256)}
            while(partial.length()<file.size) {
                checkReservedSpace()
                val start=partial.length()
                val ranges=(0 until width).mapNotNull { index ->
                    val lower=start+index*chunkSize
                    if(lower>=file.size) null else lower to minOf(file.size-1,lower+chunkSize-1)
                }
                val counts=ConcurrentHashMap<Long,Long>()
                ranges.forEach {(lower,upper)->val chunk=File(directory,"$lower-$upper");counts[lower]=if(chunk.length()==upper-lower+1) chunk.length() else 0}
                val rangeNames=ranges.map {"${it.first}-${it.second}"}.toSet()
                val otherSaved=directory.listFiles().orEmpty().filter {it.name !in rangeNames&&it.name.matches(Regex("[0-9]+-[0-9]+"))}.sumOf {it.length()}
                val before=System.nanoTime()
                coroutineScope {
                    ranges.map {(lower,upper)->async {
                        val chunk=File(directory,"$lower-$upper")
                        if(chunk.length()!=upper-lower+1) {
                            val temporary=File(directory,"$lower-$upper.tmp")
                            run {
                                retryTransfer(model,file,temporary,allowMobile,{bytes,stage,retry ->
                                    counts[lower]=bytes
                                    report(start+counts.values.sum()+otherSaved,stage,retry)
                                },lower,upper)
                            }
                            if(!temporary.renameTo(chunk)) throw ClientFailure("mobile.local.storage_required")
                        }
                    }}.awaitAll()
                }
                // Release each source only after its copy is durable, keeping peak
                // assembly overhead to one chunk rather than the entire batch.
                checkReservedSpace()
                RandomAccessFile(partial,"rw").use {output ->
                    output.seek(start)
                    ranges.forEach {(lower,upper)->
                        val saved=File(directory,"$lower-$upper")
                        saved.inputStream().use {input->
                            val buffer=ByteArray(65536)
                            while(true) {currentCoroutineContext().ensureActive();val n=input.read(buffer);if(n<0) break;output.write(buffer,0,n)}
                        }
                        output.fd.sync()
                        if(!saved.delete()) throw ClientFailure("mobile.local.storage_required")
                    }
                }
                report(partial.length(),"downloading",0)
                val rate=(partial.length()-start)/((System.nanoTime()-before)/1e9).coerceAtLeast(0.001)
                width=if(previousRate>0&&rate<previousRate*0.85) 2 else 4
                previousRate=rate
            }
            directory.deleteRecursively()
        } catch(e:RangeUnsupported) {
            directory.deleteRecursively()
            // Keep the saved prefix until a plain whole-file response is complete
            // and verified. Its staging bytes count toward the shared reservation.
            val whole=wholeFile(partial,file)
            wholeDownloads[partial]=whole
            try {
                checkReservedSpace()
                retryTransfer(model,file,whole,allowMobile,report,wholeFile=true)
                report(file.size,"verifying",0)
                val catalogue=modelPin(model).identity
                val job=currentCoroutineContext().job
                if(!verifiedFile(whole,file,catalogue) {job.ensureActive()}) {
                    whole.delete();throw ClientFailure("mobile.local.checksum_failed")
                }
                currentCoroutineContext().ensureActive();licence.requireAccepted(model)
                replacePartial(whole,partial,catalogue)
            } finally {wholeDownloads.remove(partial)}
        }
    }
    private fun diagnostic(model: PhoneModel,event: String,host: String,status: Int?) {
        // Bounded, local-only records. No paths, signed URLs, credentials or user content.
        synchronized(sourceRates) {runCatching {
            val log=File(dir(model),".download-events")
            val records=(if(log.isFile) log.readLines().takeLast(31) else emptyList())+"${System.currentTimeMillis()} $event $host ${status ?: 0}"
            val temporary=File(log.parentFile,log.name+".tmp");temporary.writeText(records.joinToString("\n"));temporary.renameTo(log)
        }}
    }
    private class PermanentDownloadFailure(message: String): IOException(message)
    private class RetryDownloadFailure(val status: Int, val retryAfter: String?): IOException("HTTP $status")
    private suspend fun retryTransfer(model: PhoneModel,file: ModelFile,partial: File,allowMobile: Boolean,report: (Long,String,Long)->Unit,lower: Long=0,upper: Long=file.size-1,wholeFile: Boolean=false) {
        var failure: IOException?=null
        val permanent=mutableSetOf<String>()
        val deliveryEndpoints=if(file.url?.toHttpUrl()?.host?.let {it!="huggingface.co"}==true) endpoints.take(1) else endpoints
        repeat(5) { attempt ->
            val sources=deliveryEndpoints.distinct().let { list ->
                if(list.all {sourceRates.containsKey(it)}) list.sortedByDescending {sourceRates[it] ?: 0.0}
                else if(list.size>1&&lower>0) list.drop((lower/chunkSize%list.size).toInt())+list.take((lower/chunkSize%list.size).toInt())
                else list
            }
            for(endpoint in sources.filterNot {it in permanent}) {
                currentCoroutineContext().ensureActive()
                val cooldown=((sourceCooldowns[endpoint] ?: 0)-System.currentTimeMillis()+999)/1000
                for(seconds in cooldown downTo 1) {report(partial.length(),"retrying",seconds);retryDelay(1000)}
                val before=System.nanoTime();val initial=partial.length()
                try {
                    transfer(endpoint,model,file,partial,allowMobile,{report(it,"downloading",0)},lower,upper,wholeFile)
                    val elapsed=(System.nanoTime()-before)/1e9
                    if(elapsed>=0.1&&partial.length()>initial) sourceRates[endpoint]=(partial.length()-initial)/elapsed
                    diagnostic(model,"received",endpoint.toHttpUrl().host,null)
                    return
                }
                catch(e:RangeUnsupported) {throw e}
                catch(e:PermanentDownloadFailure) {if(failure==null) failure=e;permanent+=endpoint}
                catch(e:IOException) {
                    currentCoroutineContext().ensureActive();failure=e
                    diagnostic(model,e.javaClass.simpleName,endpoint.toHttpUrl().host,(e as? RetryDownloadFailure)?.status)
                    if(e is RetryDownloadFailure&&e.status==429) {
                        sourceCooldowns[endpoint]=System.currentTimeMillis()+(e.retryAfter?.toLongOrNull()?.coerceIn(1,3600) ?: 1)*1000
                        break
                    }
                }
            }
            if(permanent.containsAll(deliveryEndpoints)) throw failure ?: IOException("Download failed")
            if(attempt<4) {
                val retry=(failure as? RetryDownloadFailure)?.retryAfter?.let { value ->
                    value.toLongOrNull() ?: runCatching { (java.time.ZonedDateTime.parse(value,java.time.format.DateTimeFormatter.RFC_1123_DATE_TIME).toInstant().toEpochMilli()-System.currentTimeMillis()+999)/1000 }.getOrNull()
                }?.coerceIn(1,3600) ?: ((1L shl attempt)+kotlin.random.Random.nextLong(0,2))
                for(seconds in retry downTo 1) {report(partial.length(),"retrying",seconds);retryDelay(1000)}
            }
        }
        throw failure ?: IOException("Download failed")
    }
    // Keep in step with iOS ModelRedirectDelegate.allowed (SPEC M2): mirror CDN
    // subdomains download like the apex host, and other ports are refused, on every platform.
    internal fun allowed(url: HttpUrl): Boolean = allowTestHttp || (url.scheme=="https" && url.port==443 && url.username.isEmpty() && url.password.isEmpty() &&
        (url.host in setOf("huggingface.co","hf-mirror.com","hf.co","github.com","release-assets.githubusercontent.com","objects.githubusercontent.com") || url.host.endsWith(".huggingface.co") || url.host.endsWith(".hf.co") || url.host.endsWith(".hf-mirror.com")))
    private suspend fun transfer(endpoint: String, model: PhoneModel, file: ModelFile, partial: File, allowMobile: Boolean, report: (Long)->Unit, lower: Long=0, upper: Long=file.size-1,wholeFile: Boolean=false) = connections.withPermit {
        exchange(endpoint,model,file,partial,allowMobile,report,lower,upper,wholeFile)
    }
    private suspend fun exchange(endpoint: String, model: PhoneModel, file: ModelFile, partial: File, allowMobile: Boolean, report: (Long)->Unit, lower: Long, upper: Long,wholeFile: Boolean) {
        if(partial.length()>upper-lower+1) partial.delete()
        if(partial.length()==upper-lower+1) return
        if(file.url!=null&&!ModelAssetSource.validUrl(file.url)) throw ClientFailure("mobile.local.catalogue_failed")
        var url=if(file.url!=null) file.url.toHttpUrl().let {fixed->if(fixed.host=="huggingface.co") fixed.newBuilder().host(endpoint.toHttpUrl().host).build() else fixed} else endpoint.toHttpUrl().newBuilder().addPathSegments(model.repo).addPathSegment("resolve").addPathSegment(model.revision).addPathSegments(file.path).build()
        repeat(10) {
            networkAllowed(allowMobile);licence.requireAccepted(model);currentCoroutineContext().ensureActive()
            if(!allowed(url)) throw ClientFailure("mobile.local.download_failed")
            val offset=if(wholeFile) 0L else partial.length()
            val ranged=lower>0||upper<file.size-1
            val request=Request.Builder().url(url).header("Accept-Encoding","identity").apply {if(!wholeFile) {if(ranged) header("Range","bytes=${lower+offset}-$upper") else if(offset>0) header("Range","bytes=$offset-")}}.build()
            val call=client.newCall(request)
            coroutineScope {
                val cancellation=launch {try {awaitCancellation()} finally {call.cancel()}}
                try {
                    call.execute().use { response ->
                        if(response.code in listOf(301,302,303,307,308)) {
                            url=url.resolve(response.header("Location") ?: throw IOException()) ?: throw IOException()
                        } else {
                            if(response.code !in listOf(200,206)) {
                                if(response.code==429||response.code in 500..599||response.code in listOf(408,403)) throw RetryDownloadFailure(response.code,response.header("Retry-After"))
                                throw PermanentDownloadFailure("Download HTTP ${response.code}")
                            }
                            if(!wholeFile&&(ranged||offset>0)&&response.code==200) throw RangeUnsupported()
                            val start=if(response.code==206) offset else 0L
                            if(response.code==206 && response.header("Content-Range")!="bytes ${lower+offset}-$upper/${file.size}") throw PermanentDownloadFailure("Invalid range")
                            val body=response.body ?: throw IOException()
                            RandomAccessFile(partial,"rw").use { output ->
                                if(start==0L) output.setLength(0)
                                output.seek(start);var count=start
                                body.byteStream().use { input ->
                                    val bytes=ByteArray(65536)
                                    while(true) {
                                        currentCoroutineContext().ensureActive();networkAllowed(allowMobile);licence.requireAccepted(model)
                                        val n=input.read(bytes);if(n<0) break
                                        if(count+n>upper-lower+1) {partial.delete();throw ClientFailure("mobile.local.checksum_failed")}
                                        output.write(bytes,0,n);count+=n;report(count)
                                    }
                                }
                                output.fd.sync()
                                if(count!=upper-lower+1) throw IOException("Incomplete model file")
                            }
                            return@coroutineScope
                        }
                    }
                } finally {cancellation.cancelAndJoin()}
            }
            if(partial.length()==upper-lower+1) return
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
        licence.requireAccepted(model)
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
