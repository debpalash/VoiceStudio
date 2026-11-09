package com.voicestudio.mobile.local

import java.io.File
import java.util.UUID
import kotlinx.coroutines.*
import kotlinx.serialization.json.*
import com.voicestudio.mobile.network.ClientFailure

/** Explicit stored-bundle import. No inference or model download. */
object PhoneVoiceBundle {
    suspend fun install(source: File,store: PhoneVoiceStore,attribution: Map<String,String>,consent: Boolean,decoder: AudioDecoder=AndroidAudioDecoder()): PhoneVoice=withContext(Dispatchers.IO) {
        if(!consent) throw ClientFailure("mobile.handoff.voices_required")
        fun unavailable(): Nothing=throw ClientFailure("mobile.galleryCopy.unavailable")
        val entries=ProjectArchive.entries(source);val extensions=setOf("wav","flac","mp3","m4a","ogg","opus","webm","caf","aiff","aif")
        if(entries.size>4||entries.sumOf {it.size}>64L*1024*1024||entries.any {it.size>32L*1024*1024||(it.path!="metadata.json"&&(File(it.path).nameWithoutExtension !in setOf("ref_audio","locked_audio")||File(it.path).extension.lowercase() !in extensions||File(it.path).name!=it.path))}||(entries.firstOrNull {it.path=="metadata.json"}?.size ?: Long.MAX_VALUE)>65536||entries.count {File(it.path).nameWithoutExtension=="ref_audio"}!=1) unavailable()
        val workspace=File(source.parentFile,"voice-bundle-${UUID.randomUUID()}")
        try {
            val files=ProjectArchive.extract(source,workspace)
            val metadataFile=files["metadata.json"] ?: unavailable();val reference=files.entries.firstOrNull {File(it.key).nameWithoutExtension=="ref_audio"}?.value ?: unavailable()
            val metadata=LocalJson.parseToJsonElement(metadataFile.readText()).jsonObject
            val transcript=metadata["ref_text"]?.jsonPrimitive?.contentOrNull ?: unavailable();val name=metadata["profile_name"]?.jsonPrimitive?.contentOrNull ?: unavailable()
            if(metadata["bundle_version"]?.jsonPrimitive?.intOrNull!=1||transcript.isBlank()||transcript.length>4000||name.isBlank()||name.length>200) unavailable()
            val tagged=JsonObject(metadata+("sourceAttribution" to JsonObject(attribution.mapValues {JsonPrimitive(it.value)})))
            val pcm=decoder.decode(reference,File(workspace,"reference.pcm"),30)
            if(pcm.frames !in 2400..720000) unavailable()
            store.installHandoff(name,transcript,pcm.read(0,pcm.frames.toInt()),tagged,(metadata["kind"] as? JsonPrimitive)?.contentOrNull=="design"||PhoneAudioProcessing.containsSyntheticMarker(reference),consent)
        } finally {workspace.deleteRecursively()}
    }
}
