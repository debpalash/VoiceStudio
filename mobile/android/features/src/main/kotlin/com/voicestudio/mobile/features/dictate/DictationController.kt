package com.voicestudio.mobile.features.dictate

import android.media.*
import com.voicestudio.mobile.features.guidance
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.*
import kotlinx.serialization.decodeFromString
import okhttp3.*
import okio.ByteString.Companion.toByteString

class DictationController(private val api:VoiceStudioApi,private val scope:CoroutineScope,private val failure:(Throwable)->Unit,
    private val audio: DictationAudioSource = MicrophoneSource(), private val completed: (String)->Unit = {}) : AutoCloseable {
    private val mutableText=MutableStateFlow("")
    val text=mutableText.asStateFlow()
    private val mutableActive=MutableStateFlow(false)
    val active=mutableActive.asStateFlow()
    private var capture:Job?=null
    private var socket:WebSocket?=null
    @Volatile private var recording=false
    private var committed=""
    @Volatile private var generation=0L
    @Suppress("MissingPermission")
    fun start() {
        if(mutableActive.value) return
        val session=++generation
        mutableActive.value=true;committed="";mutableText.value="";recording=true
        capture=scope.launch {
            try {
                val readiness=api.readiness()
                if(!readiness.ready) {
                    val (key,params)=readiness.guidance()
                    throw ClientFailure(key,params.mapValues {it.value.toString()})
                }
                val opened=CompletableDeferred<Unit>()
                val ended=CompletableDeferred<Unit>()
                socket=api.client.newWebSocket(api.dictationRequest(),object:WebSocketListener() {
                    override fun onOpen(ws:WebSocket,response:Response) {if(session==generation) opened.complete(Unit)}
                    override fun onMessage(ws:WebSocket,message:String) {
                        if(session!=generation || ended.isCompleted) return
                        try {
                            val event=api.json.decodeFromString<DictationEvent>(message)
                            when(event.type) {
                                "partial" -> mutableText.value=listOf(committed,event.text).filter {it.isNotBlank()}.joinToString(" ")
                                "final" -> {
                                    val text=event.refinedText?.takeIf {it.isNotBlank()} ?: event.text
                                    when(event.finalKind) {
                                        "utterance" -> {
                                            // Empty utterances and unknown final kinds are ignored,
                                            // matching desktop's classifySherpaFinal.
                                            if(event.text.isNotBlank()) {
                                                committed=listOf(committed,text).filter {it.isNotBlank()}.joinToString(" ")
                                                mutableText.value=committed
                                            }
                                        }
                                        "summary", null -> {
                                            // The summary is the authoritative full transcript, not
                                            // another phrase. Empty EOF preserves committed speech.
                                            if(text.isNotBlank()) committed=text
                                            mutableText.value=committed
                                            recording=false;ended.complete(Unit)
                                        }
                                    }
                                }
                                "error" -> {
                                    val e=event.message?.takeIf {it.isNotBlank()}?.let {ClientFailure("mobile.errors.server",mapOf("message" to it))} ?: ClientFailure("mobile.dictate.setup")
                                    failure(e);opened.completeExceptionally(e);ended.complete(Unit);recording=false
                                }
                            }
                        } catch(e:Exception) {failure(e);recording=false;ended.complete(Unit)}
                    }
                    override fun onFailure(ws:WebSocket,t:Throwable,response:Response?) {
                        if(session!=generation || ended.isCompleted) return
                        val e=t as? ClientFailure ?: ClientFailure(if(response?.code in listOf(401,403)) "mobile.dictate.authorization" else "mobile.dictate.setup")
                        opened.completeExceptionally(e);ended.complete(Unit);recording=false
                        if(mutableActive.value) failure(e)
                    }
                    override fun onClosing(ws:WebSocket,code:Int,reason:String) {ws.close(code,null);if(session==generation) {ended.complete(Unit);recording=false}}
                    override fun onClosed(ws:WebSocket,code:Int,reason:String) {if(session==generation) {ended.complete(Unit);recording=false}}
                })
                withTimeout(20000) {opened.await()}
                if(!recording) return@launch
                audio.record({recording}) {buffer,n ->
                    if(socket?.send(buffer.toByteString(0,n))!=true) throw ClientFailure("mobile.dictate.setup")
                    if((socket?.queueSize() ?: 0)>1024*1024) throw ClientFailure("mobile.errors.timeout")
                }
                if(!ended.isCompleted) socket?.send("{\"type\":\"input_audio.end\"}")
                withTimeout(60000) {ended.await()}
            } catch(e:TimeoutCancellationException) {failure(ClientFailure("mobile.errors.timeout"))} catch(e:CancellationException) {throw e} catch(e:Exception) {failure(e)}
            finally {if(session==generation) {recording=false;socket?.close(1000,null);socket=null;completed(mutableText.value);mutableActive.value=false}}
        }
    }
    fun finish() {recording=false}
    override fun close() {if(mutableActive.value) completed(mutableText.value);generation++;recording=false;capture?.cancel();socket?.cancel();socket=null;mutableActive.value=false}
}

/** Capture is replaceable in protocol tests; production always uses AudioRecord. */
fun interface DictationAudioSource {
    suspend fun record(keepRecording: () -> Boolean, send: (ByteArray, Int) -> Unit)
}
internal class MicrophoneSource : DictationAudioSource {
    @Suppress("MissingPermission")
    override suspend fun record(keepRecording: () -> Boolean, send: (ByteArray, Int) -> Unit) = withContext(Dispatchers.IO) {
        val size=AudioRecord.getMinBufferSize(16000,AudioFormat.CHANNEL_IN_MONO,AudioFormat.ENCODING_PCM_16BIT).coerceAtLeast(6400)
        val device=AudioRecord.Builder().setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            .setAudioFormat(AudioFormat.Builder().setSampleRate(16000).setChannelMask(AudioFormat.CHANNEL_IN_MONO).setEncoding(AudioFormat.ENCODING_PCM_16BIT).build())
            .setBufferSizeInBytes(size).build()
        try {
            check(device.state==AudioRecord.STATE_INITIALIZED)
            device.startRecording();val buffer=ByteArray(3200)
            while(keepRecording()&&isActive) {
                val n=device.read(buffer,0,buffer.size,AudioRecord.READ_BLOCKING)
                if(n<0) throw ClientFailure("mobile.android.recording_failed")
                if(n>0) send(buffer,n)
            }
        } finally {runCatching {device.stop()};device.release()}
    }
}
