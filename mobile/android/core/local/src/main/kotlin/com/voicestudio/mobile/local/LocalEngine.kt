package com.voicestudio.mobile.local

import com.k2fsa.sherpa.onnx.*
import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.io.File
import java.io.FileOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/** PCM is mono float [-1,1]; reference cloning audio is explicitly 24 kHz. */
data class LocalAudio(val samples: FloatArray, val sampleRate: Int)
@kotlinx.serialization.Serializable data class SpeechSegment(val start: Double,val end: Double,val text: String)
data class Transcript(val text: String, val final: Boolean, val progress: Float)
interface LocalEngine {
    suspend fun design(model: PhoneModel,text: String,instruction: String,seed: Long,progress: (Float)->Unit = {}): LocalAudio = throw ClientFailure("mobile.local.model_incompatible")
    suspend fun synthesize(model: PhoneModel, text: String, speaker: Int = 0, speed: Float = 1f, language: String? = null, progress: (Float)->Unit = {}): LocalAudio
    suspend fun clone(model: PhoneModel, text: String, reference: LocalAudio, referenceText: String, progress: (Float)->Unit = {}): LocalAudio
    suspend fun transcribe(model: PhoneModel, audio: LocalAudio, progress: (Float)->Unit = {}): String
    suspend fun transcribeSegments(model: PhoneModel, audio: LocalAudio, progress: (Float)->Unit = {}): List<SpeechSegment> {
        val text=transcribe(model,audio,progress).trim()
        return if(text.isEmpty()) emptyList() else listOf(SpeechSegment(0.0,audio.samples.size.toDouble()/audio.sampleRate,text))
    }
    fun streamTranscribe(model: PhoneModel, audio: Flow<LocalAudio>): Flow<Transcript>
    suspend fun translate(model: PhoneModel, text: String, targetLanguage: String, progress: (Float)->Unit = {}): String
}
/** JNI callbacks never surface native diagnostic text or user audio to logs. */
@androidx.annotation.Keep
class NativeControl(private val job: Job, private val report: (Float)->Unit, private val allowed: ()->Boolean) {
    fun cancelled(): Boolean = !job.isActive || !allowed()
    fun progress(value: Float) {if(!cancelled()) report(value)}
}
@androidx.annotation.Keep
internal object NativeBridge {
    fun load() {System.loadLibrary("vs_omni");System.loadLibrary("vs_llama")}
    external fun omniAbi(): Int
    external fun llamaDevices(): Int
    external fun designAudio(model: String,codec: String,text: String,instruction: String,seed: Long,control: NativeControl): FloatArray?
    external fun cloneAudio(model: String, codec: String, text: String, referenceText: String, reference: FloatArray, control: NativeControl): FloatArray?
    external fun translateText(model: String, prompt: String, formatted: Boolean, control: NativeControl): ByteArray?
}

internal fun speechDataDirectory(model:PhoneModel,dir:File):String {
    if(model.files.none {it.role=="espeak-data"}) return ""
    val path=File(dir,model.option("data_dir","espeak-ng-data")).canonicalFile
    if(!path.path.startsWith(dir.canonicalPath+File.separator)) throw ClientFailure("mobile.local.model_incompatible")
    return path.path
}
/** Family-specific native configuration shared by real inference and contract tests. */
internal fun speechTtsConfig(model:PhoneModel,dir:File,language:String?):OfflineTtsConfig {
    val data=speechDataDirectory(model,dir)
    val config=OfflineTtsModelConfig(numThreads=2,provider="cpu")
    fun role(name:String)=model.role(dir,name)
    val lexicon=model.files.filter {it.role=="lexicon"}.joinToString(",") {File(dir,it.path).absolutePath}
    when(model.option("model_type",if(model.id.contains("kitten")) "kitten" else "kokoro")) {
        "kitten"->config.kitten=OfflineTtsKittenModelConfig(model=role("model"),voices=role("voices"),tokens=role("tokens"),dataDir=data)
        "kokoro"->config.kokoro=OfflineTtsKokoroModelConfig(model=role("model"),voices=role("voices"),tokens=role("tokens"),dataDir=data,lexicon=lexicon,lang=language ?: model.option("lang"),dictDir=role("dictionary"))
        "vits"->config.vits=OfflineTtsVitsModelConfig(model=role("model"),tokens=role("tokens"),dataDir=data,lexicon=lexicon)
        "supertonic"->config.supertonic=OfflineTtsSupertonicModelConfig(durationPredictor=role("duration-predictor"),textEncoder=role("text-encoder"),vectorEstimator=role("vector-estimator"),vocoder=role("vocoder"),ttsJson=role("tts-config"),unicodeIndexer=role("unicode-indexer"),voiceStyle=role("voices"))
        "pocket"->config.pocket=OfflineTtsPocketModelConfig(lmFlow=role("lm-flow"),lmMain=role("lm-main"),encoder=role("encoder"),decoder=role("decoder"),textConditioner=role("text-conditioner"),vocabJson=role("vocab"),tokenScoresJson=role("token-scores"),voiceEmbeddingCacheCapacity=1)
        "zipvoice"->config.zipvoice=OfflineTtsZipVoiceModelConfig(encoder=role("encoder"),decoder=role("decoder"),tokens=role("tokens"),vocoder=role("vocoder"),dataDir=data,lexicon=lexicon,featScale=.1f,tShift=.5f,targetRms=.1f,guidanceScale=1f)
        else->throw ClientFailure("mobile.local.model_incompatible")
    }
    return OfflineTtsConfig(model=config,maxNumSentences=1,ruleFsts=model.files.filter {it.role=="rule-fst"}.joinToString(",") {File(dir,it.path).absolutePath})
}

class PhoneLocalEngine(private val models: ModelDownloads): LocalEngine {
    companion object {private val nativeLease=Mutex()}
    internal suspend fun <T> execute(model: PhoneModel, capability: String, progress: (Float)->Unit, block: suspend (File,Job)->T): T =
        withContext(Dispatchers.Default) {
            if(!model.supports(capability)) throw ClientFailure("mobile.local.model_incompatible")
            try {
                nativeLease.withLock {models.withModel(model) { directory ->
                    currentCoroutineContext().ensureActive();progress(0f)
                    val result=block(directory,currentCoroutineContext().job)
                    currentCoroutineContext().ensureActive();models.licence.requireAccepted(model);progress(1f);result
                }}
            } catch(e:CancellationException) {throw e}
            catch(e:ClientFailure) {throw e}
            catch(e:LinkageError) {throw ClientFailure("mobile.local.runtime_failed")}
            catch(e:Exception) {throw ClientFailure("mobile.local.inference_failed")}
        }
    override suspend fun synthesize(model: PhoneModel,text: String,speaker: Int,speed: Float,language: String?,progress: (Float)->Unit): LocalAudio {
        if(model.requiresReference) throw ClientFailure("mobile.speak.reference_required")
        return sherpaSpeech(model,text,speaker,speed,language,null,"",progress)
    }
    private suspend fun sherpaSpeech(model:PhoneModel,text:String,speaker:Int,speed:Float,language:String?,reference:LocalAudio?,referenceText:String,progress:(Float)->Unit):LocalAudio = execute(model,if(reference==null) "tts" else "clone",progress) {dir,job ->
        require(text.isNotBlank()&&text.length<=20000&&speed in .5f..2f&&(language==null||language in model.languages))
        val tts=OfflineTts(config=speechTtsConfig(model,dir,language))
        try {
            if(reference==null&&speaker !in 0 until tts.numSpeakers()) throw ClientFailure("mobile.local.model_incompatible")
            var chunks=0
            val generation=GenerationConfig(sid=speaker,speed=speed,referenceAudio=reference?.samples ?: floatArrayOf(),referenceSampleRate=reference?.sampleRate ?: 0,referenceText=referenceText,numSteps=model.option("num_steps","8").toIntOrNull() ?: 8,extra=if(model.option("model_type")=="supertonic") mapOf("lang" to (language ?: model.languages.first())) else emptyMap())
            val audio=tts.generateWithConfigAndCallback(text,generation,NativeTtsCallback {
                if(!job.isActive||!models.licence.accepted(model)) 0 else {chunks++;progress((chunks.toFloat()/(chunks+1)).coerceAtMost(.95f));1}
            })
            job.ensureActive();LocalAudio(audio.samples,audio.sampleRate)
        } finally {tts.release()}
    }
    override suspend fun transcribe(model: PhoneModel, audio: LocalAudio, progress: (Float)->Unit): String =
        transcribeSegments(model,audio,progress).joinToString(" ") {it.text}
    override suspend fun transcribeSegments(model: PhoneModel, audio: LocalAudio, progress: (Float)->Unit): List<SpeechSegment> = execute(model,"asr",progress) {dir,job ->
        require(audio.sampleRate>0 && audio.samples.isNotEmpty())
        val recognizer=OfflineRecognizer(config=OfflineRecognizerConfig(
            featConfig=FeatureConfig(sampleRate=16000,featureDim=80),
            modelConfig=OfflineModelConfig(whisper=OfflineWhisperModelConfig(encoder=model.role(dir,"encoder"),decoder=model.role(dir,"decoder"),language=model.option("language"),task="transcribe",enableTokenTimestamps=true),
                tokens=model.role(dir,"tokens"),numThreads=2,provider="cpu")))
        try {
            val segments=mutableListOf<SpeechSegment>();val window=audio.sampleRate*20
            for(start in audio.samples.indices step window) {
                job.ensureActive();models.licence.requireAccepted(model)
                val limit=minOf(start+window,audio.samples.size)
                val stream=recognizer.createStream()
                try {
                    stream.acceptWaveform(audio.samples.copyOfRange(start,limit),audio.sampleRate);recognizer.decode(stream)
                    val result=recognizer.getResult(stream)
                    val text=result.text.trim()
                    if(text.isNotEmpty()) {
                        val duration=(limit-start).toDouble()/audio.sampleRate
                        val offset=start.toDouble()/audio.sampleRate
                        segments+=splitUtterances(text,result.tokens.toList(),result.timestamps.toList(),result.durations.toList(),duration).map {SpeechSegment(offset+it.start,offset+it.end,it.text)}
                    }
                } finally {stream.release()}
                progress(limit.toFloat()/audio.samples.size)
            }
            segments
        } finally {recognizer.release()}
    }
    override fun streamTranscribe(model: PhoneModel, audio: Flow<LocalAudio>): Flow<Transcript> = channelFlow {
        execute(model,"asr-stream",{}) {dir,job ->
            val recognizer=OnlineRecognizer(config=OnlineRecognizerConfig(featConfig=FeatureConfig(sampleRate=16000,featureDim=80),
                modelConfig=OnlineModelConfig(transducer=OnlineTransducerModelConfig(encoder=model.role(dir,"encoder"),decoder=model.role(dir,"decoder"),joiner=model.role(dir,"joiner")),
                    tokens=model.role(dir,"tokens"),numThreads=2,provider="cpu"),enableEndpoint=true))
            val stream=recognizer.createStream()
            var committed=""
            // sherpa ends the whole process if a stream's sample rate changes, so the
            // first chunk fixes the rate and anything else is rejected here instead.
            var rate=0
            try {
                audio.collect { chunk ->
                    job.ensureActive();models.licence.requireAccepted(model)
                    if(rate==0) rate=chunk.sampleRate
                    if(chunk.sampleRate!=rate || rate<=0) throw ClientFailure("mobile.local.model_incompatible")
                    stream.acceptWaveform(chunk.samples,chunk.sampleRate)
                    while(recognizer.isReady(stream)) {job.ensureActive();recognizer.decode(stream)}
                    val text=recognizer.getResult(stream).text
                    val endpoint=recognizer.isEndpoint(stream)
                    send(Transcript(listOf(committed,text).filter {it.isNotBlank()}.joinToString(" "),endpoint,0f))
                    if(endpoint) {committed=listOf(committed,text).filter {it.isNotBlank()}.joinToString(" ");recognizer.reset(stream)}
                }
                if(rate>0) stream.acceptWaveform(FloatArray(rate/2),rate)
                stream.inputFinished()
                while(recognizer.isReady(stream)) {job.ensureActive();recognizer.decode(stream)}
                send(Transcript(listOf(committed,recognizer.getResult(stream).text).filter {it.isNotBlank()}.joinToString(" "),true,1f))
            } finally {stream.release();recognizer.release()}
        }
    }.flowOn(Dispatchers.Default)
    override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress:(Float)->Unit): LocalAudio {
        require(text.isNotBlank()&&reference.sampleRate==24000&&reference.samples.size in 1..720000&&reference.samples.all {it.isFinite()})
        if(model.runtime=="sherpa-onnx") {
            require(model.supportsCloning&&(referenceText.isNotBlank()||model.option("model_type")=="pocket"))
            return sherpaSpeech(model,text,0,1f,null,reference,referenceText,progress)
        }
        return omniClone(model,text,reference,referenceText,progress)
    }
    private suspend fun omniClone(model:PhoneModel,text:String,reference:LocalAudio,referenceText:String,progress:(Float)->Unit):LocalAudio=execute(model,"clone",progress) {dir,job ->
        require(text.isNotBlank() && text.length<=20000 && reference.sampleRate==24000 && reference.samples.size in 1..720000 && reference.samples.all {it.isFinite()} && referenceText.isNotBlank())
        NativeBridge.load()
        val codec=model.role(dir,"codec").ifEmpty {model.role(dir,"audio-tokenizer").ifEmpty {model.role(dir,"tokenizer")}}
        if(codec.isBlank()) throw ClientFailure("mobile.local.model_incompatible")
        val result=NativeBridge.cloneAudio(model.role(dir,"model"),codec,text,referenceText,reference.samples,NativeControl(job,progress) {models.licence.accepted(model)})
        job.ensureActive();models.licence.requireAccepted(model)
        LocalAudio(result ?: throw ClientFailure("mobile.local.inference_failed"),24000)
    }
    override suspend fun design(model: PhoneModel,text: String,instruction: String,seed: Long,progress: (Float)->Unit): LocalAudio = execute(model,"clone",progress) {dir,job ->
        require(model.supportsDesign)
        require(text.isNotBlank()&&text.length<=2000&&instruction.length<=2000&&seed>=0)
        NativeBridge.load()
        val codec=model.role(dir,"codec").ifEmpty {model.role(dir,"audio-tokenizer").ifEmpty {model.role(dir,"tokenizer")}}
        if(codec.isBlank()) throw ClientFailure("mobile.local.model_incompatible")
        val result=NativeBridge.designAudio(model.role(dir,"model"),codec,text,instruction,seed,NativeControl(job,progress) {models.licence.accepted(model)})
        job.ensureActive();models.licence.requireAccepted(model)
        LocalAudio(result ?: throw ClientFailure("mobile.local.inference_failed"),24000)
    }
    override suspend fun translate(model: PhoneModel, text: String, targetLanguage: String, progress: (Float)->Unit): String = execute(model,"translate",progress) {dir,job ->
        require(text.isNotBlank() && targetLanguage.length in 2..120 && targetLanguage.none {it.isISOControl()})
        NativeBridge.load()
        val template=model.option("prompt_template","Translate the following text into {{target_language}}. Return only the translation.\n{{text}}")
        val prompt=template.replace("\\n","\n").replace("{{target_language}}",targetLanguage).replace("{target_language}",targetLanguage).replace("{source_language}","auto").replace("{{text}}",text).replace("{text}",text)
        val result=NativeBridge.translateText(model.role(dir,"model"),prompt,model.option("prompt_template").contains("<|im_start|>"),NativeControl(job,progress) {models.licence.accepted(model)})
        job.ensureActive();models.licence.requireAccepted(model)
        result?.toString(Charsets.UTF_8) ?: throw ClientFailure("mobile.local.inference_failed")
    }

}
/** Every local synthetic export passes this marker (RIFF INFO provenance). */
fun markSynthetic(audio: LocalAudio, file: File) {
    require(audio.sampleRate>0 && audio.samples.isNotEmpty())
    val marker="VoiceStudio synthetic audio\u0000".toByteArray(Charsets.US_ASCII)
    val padded=marker.size+(marker.size%2)
    val dataBytes=audio.samples.size*2
    val buffer=ByteBuffer.allocate(12+24+8+dataBytes+8+4+8+padded).order(ByteOrder.LITTLE_ENDIAN)
    fun tag(value: String) {buffer.put(value.toByteArray(Charsets.US_ASCII))}
    tag("RIFF");buffer.putInt(buffer.capacity()-8);tag("WAVE");tag("fmt ");buffer.putInt(16);buffer.putShort(1);buffer.putShort(1)
    buffer.putInt(audio.sampleRate);buffer.putInt(audio.sampleRate*2);buffer.putShort(2);buffer.putShort(16)
    tag("data");buffer.putInt(dataBytes);audio.samples.forEach {buffer.putShort((it.coerceIn(-1f,1f)*32767).toInt().toShort())}
    tag("LIST");buffer.putInt(4+8+padded);tag("INFO");tag("ICMT");buffer.putInt(marker.size);buffer.put(marker);if(marker.size%2!=0) buffer.put(0)
    file.parentFile!!.mkdirs();FileOutputStream(file).use {it.write(buffer.array());it.fd.sync()}
}

/** Model-free device test: all native dependencies must resolve on ART. */
suspend fun verifyNativeRuntimes(): Map<String,Int> = withContext(Dispatchers.IO) {
    System.loadLibrary("sherpa-onnx-jni")
    NativeBridge.load()
    mapOf("sherpa-onnx" to 1,"omnivoice.cpp" to NativeBridge.omniAbi(),"llama.cpp" to NativeBridge.llamaDevices())
}

/**
 * sherpa-onnx's JNI looks the callback's method up by its exact signature,
 * `invoke([F)Ljava/lang/Integer;`. A Kotlin lambda (compiled via invokedynamic and
 * desugared by D8) exposes only the erased `invoke(Object)`, so the lookup throws
 * NoSuchMethodError and the next JNI call aborts the process. A named class keeps
 * the typed method. Pass every native callback through a class like this.
 */
internal class NativeTtsCallback(private val onChunk: () -> Int) : (FloatArray) -> Int {
    override fun invoke(samples: FloatArray): Int = onChunk()
}

/** Pause that starts a new utterance; shorter after sentence-final punctuation. */
private const val UTTERANCE_GAP=1.0
private const val SENTENCE_GAP=.4
/**
 * One recognition window can hold several sentences with long pauses. Dubbing speaks each segment at its
 * start, so later sentences would play early; split at pauses so each keeps its own timing.
 * Falls back to one segment when tokens and timestamps do not line up.
 */
internal fun splitUtterances(text: String,tokens: List<String>,timestamps: List<Float>,durations: List<Float>,windowSeconds: Double): List<SpeechSegment> {
    val valid=tokens.size==timestamps.size&&tokens.isNotEmpty()&&timestamps.all {it.isFinite()&&it>=0&&it<windowSeconds}
    fun single(): List<SpeechSegment> {
        val times=timestamps.filter {it.isFinite()&&it>=0&&it<windowSeconds}
        val first=(times.minOrNull()?.toDouble() ?: 0.0).coerceIn(0.0,(windowSeconds-.02).coerceAtLeast(0.0))
        val tail=durations.lastOrNull()?.takeIf {it.isFinite()&&it>0}?.toDouble() ?: .5
        val end=times.maxOrNull()?.let {(it+tail).coerceIn(minOf(first+.01,windowSeconds),windowSeconds)} ?: windowSeconds
        return listOf(SpeechSegment(first,end,text))
    }
    if(!valid) return single()
    fun endOf(i: Int): Double {
        val tail=durations.getOrNull(i)?.takeIf {it.isFinite()&&it>0}?.toDouble() ?: .5
        return (timestamps[i]+tail).coerceAtMost(windowSeconds)
    }
    val groups=mutableListOf(mutableListOf(0))
    for(i in 1 until tokens.size) {
        val gap=timestamps[i]-(if(durations.size==tokens.size) endOf(i-1) else timestamps[i-1].toDouble())
        val sentenceEnd=tokens[i-1].trimEnd().lastOrNull()?.let {it in ".!?\u3002\uFF01\uFF1F"}==true
        if(gap>=UTTERANCE_GAP||(sentenceEnd&&gap>=SENTENCE_GAP)) groups+=mutableListOf(i) else groups.last()+=i
    }
    val pieces=groups.mapIndexedNotNull {n,g ->
        val piece=g.joinToString("") {tokens[it]}.trim()
        if(piece.isEmpty()) null else {
            val start=timestamps[g.first()].toDouble()
            val nextStart=groups.getOrNull(n+1)?.let {timestamps[it.first()].toDouble()} ?: windowSeconds
            SpeechSegment(start,endOf(g.last()).coerceIn(start+.01,maxOf(start+.01,nextStart)).coerceAtMost(windowSeconds),piece)
        }
    }
    return if(pieces.size<=1) single() else pieces
}
