package com.voicestudio.mobile.local

import com.voicestudio.mobile.network.ClientFailure
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder
import java.io.File
import java.nio.file.Files
import java.nio.file.StandardCopyOption
import kotlin.math.*

class PhoneDubTest {
    @get:Rule val temporary=TemporaryFolder()
    private val models=listOf(fixtureModel("asr"),fixtureModel("translate"),fixtureModel("clone"))
    private val targets=listOf(DubTarget("fr","French"),DubTarget("de","German"))
    private val original=LocalAudio(FloatArray(72000) {.25f},24000)
    private var decodes=0
    private var exports=0
    private val decoder=object: AudioDecoder {
        override suspend fun decode(source: File,output: File,maxSeconds: Int,progress: (Float)->Unit): PcmFile {
            decodes++;writePcm(original,output);return PcmFile(output)
        }
    }
    private val media=object: DubMedia {
        override suspend fun finish(source: File,audio: PcmFile,markedWave: File,output: File,audioOnly: Boolean,progress: (Float)->Unit): File {
            exports++
            val result=File(output.path+".wav")
            Files.move(markedWave.toPath(),result.toPath(),StandardCopyOption.REPLACE_EXISTING);return result
        }
    }
    private fun store()=PhoneDubStore(File(temporary.root,"jobs"))
    private suspend fun request(store: PhoneDubStore,chosen: List<DubTarget> = targets,consent: Boolean = true): PhoneDubRequest {
        val source=File(temporary.root,"source.wav").apply {writeText("fixture media")}
        return store.create(source,"Source clip",chosen,models,consent)
    }
    private open class Engine: FakeLocalEngine() {
        var translated=0;var cloned=0;var transcribed=0
        override suspend fun transcribeSegments(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit): List<SpeechSegment> {
            transcribed++;return listOf(SpeechSegment(0.0,1.0,"First sentence"),SpeechSegment(2.0,3.0,"Second sentence"))
        }
        override suspend fun translate(model: PhoneModel,text: String,targetLanguage: String,progress: (Float)->Unit): String {translated++;return "$targetLanguage translation"}
        override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {
            assertEquals(24000,reference.sampleRate);assertTrue(referenceText.isNotBlank());assertTrue(reference.samples.all {abs(it-.25f)<.0001})
            cloned++;return LocalAudio(FloatArray(12000) {.5f},24000)
        }
    }
    @Test fun offlinePipelineProducesMarkedAudioWithGapsAndDurableHistory()=runTest {
        val store=store();val request=request(store);val engine=Engine()
        val events=mutableListOf<DubProgress>()
        val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        val result=pipeline.run(request.id,models,events::add)
        assertTrue(result.done);assertEquals(2,result.outputs.size)
        assertEquals(1,decodes);assertEquals(1,engine.transcribed);assertEquals(4,engine.translated);assertEquals(4,engine.cloned)
        val first=store.file(request.id,result.outputs.first().file)
        assertTrue(first.readBytes().toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
        assertEquals(3.0,result.outputs.first().duration,0.0)
        assertEquals(listOf(request),store.list().map {it.request})
        assertEquals(100,events.last().percent)
        pipeline.run(request.id,models)
        assertEquals(4,engine.cloned);assertEquals(4,engine.translated);assertEquals(2,exports)
        store.delete(request.id);assertTrue(store.list().isEmpty())
    }
    @Test fun cancelledCloneResumesWithoutRetranslatingCompletedText()=runTest {
        val store=store();val request=request(store,targets.take(1))
        val engine=object: Engine() {
            var cancelled=false
            override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {
                if(!cancelled) {cancelled=true;throw CancellationException("interrupted")}
                return super.clone(model,text,reference,referenceText,progress)
            }
        }
        val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        try {pipeline.run(request.id,models);fail()} catch(_:CancellationException) {}
        val saved=store.checkpoint(request.id)
        assertEquals("French translation",saved.segments.first().translations["fr"]);assertEquals("First sentence",saved.segments.first().referenceText)
        assertFalse(saved.done)
        pipeline.run(request.id,models)
        assertEquals(2,engine.translated);assertEquals(2,engine.cloned);assertEquals(1,decodes);assertEquals(1,engine.transcribed)
    }
    @Test fun damagedRenderedSegmentIsRebuiltAndNeverMixedTwice()=runTest {
        val store=store();val request=request(store,targets.take(1))
        val engine=object: Engine() {
            var calls=0
            override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {
                calls++;if(calls==2) throw CancellationException("after first segment")
                return super.clone(model,text,reference,referenceText,progress)
            }
        }
        val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        try {pipeline.run(request.id,models);fail()} catch(_:CancellationException) {}
        assertEquals(1,store.checkpoint(request.id).renders.size)
        store.file(request.id,requireNotNull(store.checkpoint(request.id).renders.first().file)).writeText("damaged")
        pipeline.run(request.id,models)
        assertEquals(4,engine.calls);assertEquals(2,engine.translated)
    }
    @Test fun changedModelOrWithdrawnLicenceBlocksEvenCachedWork()=runTest {
        val store=store();val request=request(store)
        val engine=Engine()
        try {PhoneDubPipeline(store,engine,decoder,media).run(request.id,models.map {it.copy(revision="c".repeat(40))});fail()}
        catch(e:ClientFailure) {assertEquals("mobile.local.model_incompatible",e.key)}
        try {PhoneDubPipeline(store,engine,decoder,media) {throw ClientFailure("mobile.local.licence_required")}.run(request.id,models);fail()}
        catch(e:ClientFailure) {assertEquals("mobile.local.licence_required",e.key)}
        assertEquals(0,decodes);assertEquals(0,engine.cloned)
    }
    @Test fun noConsentNoSpeechAndMalformedCheckpointsFailClosed()=runTest {
        val store=store()
        try {request(store,consent=false);fail()} catch(_:IllegalArgumentException) {}
        assertTrue(store.list().isEmpty())
        val request=request(store)
        val silent=object: FakeLocalEngine() {override suspend fun transcribeSegments(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit)=emptyList<SpeechSegment>()}
        try {PhoneDubPipeline(store,silent,decoder,media).run(request.id,models);fail()} catch(e:ClientFailure) {assertEquals("mobile.simple.no_speech",e.key)}
        store.file(request.id,"checkpoint.json").writeText("not json")
        try {store.checkpoint(request.id);fail()} catch(e:ClientFailure) {assertEquals("mobile.simple.checkpoint_failed",e.key)}
    }
    @Test fun unreadableDubDoesNotHideHealthyJobs()=runTest {
        val store=store();val healthy=request(store);val broken=request(store)
        store.file(broken.id,"checkpoint.json").writeText("broken")
        assertEquals(listOf(healthy),store.list().map {it.request})
    }
    @Test fun blankReferenceTranscriptionFallsBackAndResumeUsesSavedText()=runTest {
        val store=store();val request=request(store,targets.take(1))
        val engine=object: Engine() {
            var referenceCalls=0;var interrupt=true
            override suspend fun transcribe(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit): String {referenceCalls++;return " "}
            override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {
                assertEquals(if(cloned==0) "First sentence" else "Second sentence",referenceText)
                if(interrupt) {interrupt=false;throw CancellationException("resume")}
                return super.clone(model,text,reference,referenceText,progress)
            }
        }
        val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        try {pipeline.run(request.id,models);fail()} catch(_:CancellationException) {}
        assertEquals("First sentence",store.checkpoint(request.id).segments.first().referenceText)
        assertTrue(pipeline.run(request.id,models).done)
        assertEquals("Whole-segment clips already have exact transcripts",0,engine.referenceCalls)
    }
    @Test fun blankTranscriptionOfShortenedReferenceUsesSegmentText()=runTest {
        val longDecoder=object: AudioDecoder {
            override suspend fun decode(source: File,output: File,maxSeconds: Int,progress: (Float)->Unit): PcmFile {
                writePcm(LocalAudio(FloatArray(24000*10) {.25f},24000),output);return PcmFile(output)
            }
        }
        val engine=object: Engine() {
            var referenceCalls=0
            override suspend fun transcribeSegments(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit)=listOf(SpeechSegment(0.0,10.0,"Long sentence"))
            override suspend fun transcribe(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit): String {referenceCalls++;return " "}
            override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {
                assertEquals("Long sentence",referenceText);return super.clone(model,text,reference,referenceText,progress)
            }
        }
        val store=store();val request=request(store,targets.take(1))
        assertTrue(PhoneDubPipeline(store,engine,longDecoder,media).run(request.id,models).done)
        assertEquals(1,engine.referenceCalls)
        assertEquals("Long sentence",store.checkpoint(request.id).segments.single().referenceText)
    }
    @Test fun timingPadsSilenceAndCompressionPreservesPitch()=runTest {
        val padded=fitTiming(LocalAudio(floatArrayOf(.1f,.2f),24000),5)
        assertArrayEquals(floatArrayOf(.1f,.2f,0f,0f,0f),padded.samples,0f)
        val sine=LocalAudio(FloatArray(24000) {sin(2*PI*440*it/24000).toFloat()},24000)
        val fitted=fitTiming(sine,12000)
        val crossings=fitted.samples.indices.drop(1).count {fitted.samples[it-1]<0&&fitted.samples[it]>=0}
        assertEquals(12000,fitted.samples.size);assertEquals(220.0,crossings.toDouble(),15.0)
        assertTrue(fitted.samples.all {it.isFinite()&&abs(it)<=1})
    }
    @Test fun mixerKeepsGapsAndClampsOverlap()=runTest {
        val first=File(temporary.root,"first.pcm");writePcm(LocalAudio(FloatArray(2400) {.8f},24000),first)
        val mixed=mixTimeline(File(temporary.root,"mix.pcm"),7200,listOf(0L to PcmFile(first),0L to PcmFile(first),4800L to PcmFile(first)))
        val samples=mixed.read(0,7200).samples
        assertTrue(samples.take(2400).all {it==1f});assertTrue(samples.slice(2400 until 4800).all {it==0f});assertTrue(samples.drop(4800).all {it==.8f})
    }

    @Test fun editsInvalidateRendersAndOverrideDefaultVoice()=runTest {
        val store=store();val source=File(temporary.root,"source.wav").apply {writeText("fixture")}
        val request=store.create(source,"Source",targets.take(1),models,true,LocalAudio(FloatArray(4800) {.25f},24000),"Default voice",true)
        val references=mutableListOf<String>();var clones=0;var translations=0
        val engine=object: FakeLocalEngine() {
            override suspend fun transcribeSegments(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit)=listOf(SpeechSegment(0.0,1.0,"First"),SpeechSegment(2.0,3.0,"Second"))
            override suspend fun translate(model: PhoneModel,text: String,targetLanguage: String,progress: (Float)->Unit): String {translations++;return "Translated"}
            override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {clones++;references+=referenceText;return LocalAudio(FloatArray(12000) {.5f},24000)}
        }
        val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        val complete=pipeline.run(request.id,models)
        assertTrue(complete.done);assertTrue(store.file(request.id,"original.pcm").exists())
        val edits=complete.segments.toMutableList().also {it[0]=it[0].copy(speech=it[0].speech.copy(start=.05),translations=mapOf("fr" to "Edited translation"))}
        store.edit(request.id,edits,mapOf(0 to (LocalAudio(FloatArray(4800) {.3f},24000) to "Replacement voice")))
        assertEquals(complete.outputs,store.checkpoint(request.id).outputs)
        assertTrue(pipeline.run(request.id,models).done)
        assertEquals(3,clones);assertEquals(2,translations);assertEquals("Replacement voice",references[2])
        try {store.edit(request.id,edits.toMutableList().also {it[0]=it[0].copy(speech=it[0].speech.copy(start=-1.0))});fail()} catch(_:IllegalArgumentException) {}
        assertTrue(store.checkpoint(request.id).done)
    }
    @Test fun oneEditedTranslationReusesAllOtherSegmentsAndLanguageOutputs()=runTest {
        val store=store();val request=request(store);val engine=Engine();val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        val complete=pipeline.run(request.id,models)
        val edited=complete.segments.toMutableList().also {it[0]=it[0].copy(translations=it[0].translations+("fr" to "Edited French line"))}
        val originalPreview=store.preview(request.id,edited[0],"fr",false)
        assertFalse(originalPreview.file.readBytes().toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"))
        assertEquals(128,originalPreview.peaks.size);assertTrue(originalPreview.peaks.all {it>.2f});originalPreview.file.delete()
        try {store.preview(request.id,edited[0],"fr",true);fail()} catch(e:ClientFailure) {assertEquals("mobile.editing.preview_unavailable",e.key)}
        store.edit(request.id,edited)
        assertEquals(complete.outputs,store.checkpoint(request.id).outputs)
        val updated=pipeline.run(request.id,models)
        assertEquals(5,engine.cloned);assertEquals(4,engine.translated);assertEquals(3,exports)
        assertEquals(complete.outputs[1],updated.outputs[1]);assertNotEquals(complete.outputs[0].file,updated.outputs[0].file)
        val preview=store.preview(request.id,updated.segments[0],"fr",true)
        assertTrue(preview.file.readBytes().toString(Charsets.ISO_8859_1).contains("VoiceStudio synthetic audio"));preview.file.delete()
        store.edit(request.id,updated.segments)
        pipeline.run(request.id,models)
        assertEquals(5,engine.cloned);assertEquals(3,exports)
    }
    @Test fun cancelledRegenerationRetainsPublishedOutputsAndResumesOnlyTheChangedCue()=runTest {
        val store=store();val request=request(store)
        val engine=object: Engine() {
            var interrupt=false
            override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {
                if(interrupt) {interrupt=false;throw CancellationException("edited render")}
                return super.clone(model,text,reference,referenceText,progress)
            }
        }
        val pipeline=PhoneDubPipeline(store,engine,decoder,media);val complete=pipeline.run(request.id,models)
        val edits=complete.segments.toMutableList().also {it[0]=it[0].copy(translations=it[0].translations+("fr" to "Replacement"))}
        store.edit(request.id,edits);engine.interrupt=true
        try {pipeline.run(request.id,models);fail()} catch(_:CancellationException) {}
        val interrupted=store.checkpoint(request.id)
        assertFalse(interrupted.done);assertEquals(complete.outputs,interrupted.outputs)
        assertTrue(interrupted.outputs.all {sha256(store.file(request.id,it.file))==it.sha256})
        assertTrue(pipeline.run(request.id,models).done);assertEquals(5,engine.cloned);assertEquals(3,exports)
    }
    @Test fun structuralEditsRetainUnchangedCachesAndReextractionRetainsEdits()=runTest {
        val store=store();val request=request(store);val engine=Engine();val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        val complete=pipeline.run(request.id,models)
        val split=DubEditing.split(complete.segments,0,.5)
        assertEquals(3,split.size);assertNotEquals(split[0].id,split[1].id)
        store.edit(request.id,split)
        store.file(request.id,"original.pcm").writeText("interrupted extraction")
        val updated=pipeline.run(request.id,models)
        assertEquals(2,decodes);assertEquals(3,updated.segments.size);assertEquals(8,engine.cloned);assertEquals(4,engine.translated)
        assertEquals(split.map {it.speech},updated.segments.map {it.speech})
        val merged=DubEditing.merge(updated.segments,0);assertEquals(2,merged.size)
        store.edit(request.id,merged);assertTrue(pipeline.run(request.id,models).done)
        assertEquals(10,engine.cloned)
    }

    @Test fun unchangedTimelinesAvoidPerCueCheckpointSerialization()=runTest {
        val store=store();val request=request(store)
        val engine=object: Engine() {
            override suspend fun transcribeSegments(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit)=List(16) {index ->SpeechSegment(index*.15,index*.15+.1,"Source sentence $index")}
        }
        val pipeline=PhoneDubPipeline(store,engine,decoder,media);val complete=pipeline.run(request.id,models)
        assertEquals(32,engine.cloned)
        store.edit(request.id,complete.segments)
        val before=store.checkpointWrites.get()
        val unchanged=pipeline.run(request.id,models)
        assertEquals(1L,store.checkpointWrites.get()-before)
        assertEquals(32,engine.cloned);assertEquals(2,exports)
        val changed=unchanged.segments.toMutableList().also {it[0]=it[0].copy(translations=it[0].translations+("fr" to "Edited line"))}
        store.edit(request.id,changed)
        val changedBefore=store.checkpointWrites.get();pipeline.run(request.id,models)
        assertTrue("One changed cue must not serialize every other cue",store.checkpointWrites.get()-changedBefore<=8)
        assertEquals(33,engine.cloned);assertEquals(3,exports)
    }
    @Test fun verifiedRenderCannotReuseChangedReferenceBytesUnderTheSameFileName()=runTest {
        val store=store();val source=File(temporary.root,"source.wav").apply {writeText("fixture")}
        val request=store.create(source,"Source",targets.take(1),models,true,LocalAudio(FloatArray(4800) {.25f},24000),"Default voice",true)
        val used=mutableListOf<Float>();var clones=0
        val engine=object: FakeLocalEngine() {
            override suspend fun transcribeSegments(model: PhoneModel,audio: LocalAudio,progress: (Float)->Unit)=listOf(SpeechSegment(0.0,1.0,"First"),SpeechSegment(2.0,3.0,"Second"))
            override suspend fun translate(model: PhoneModel,text: String,targetLanguage: String,progress: (Float)->Unit)="Translated $text"
            override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {clones++;used+=reference.samples.first();return LocalAudio(FloatArray(12000) {.5f},24000)}
        }
        val pipeline=PhoneDubPipeline(store,engine,decoder,media);val complete=pipeline.run(request.id,models)
        store.edit(request.id,complete.segments,mapOf(0 to (LocalAudio(FloatArray(4800) {.3f},24000) to "Custom voice")))
        val custom=pipeline.run(request.id,models);assertEquals(3,clones)
        val path=store.file(request.id,requireNotNull(custom.segments[0].referenceFile))
        writePcm(LocalAudio(FloatArray(4800) {.4f},24000),path)
        store.edit(request.id,custom.segments)
        val regenerated=pipeline.run(request.id,models)
        assertEquals(4,clones);assertEquals(.4f,used.last(),0f)
        assertNotEquals(custom.outputs[0].file,regenerated.outputs[0].file)
        assertFalse(store.file(request.id,custom.outputs[0].file).exists())
        writePcm(LocalAudio(FloatArray(4800) {.45f},24000),store.file(request.id,"voice-reference.pcm"))
        store.edit(request.id,regenerated.segments);pipeline.run(request.id,models)
        assertEquals(5,clones);assertEquals(.45f,used.last(),0f)
    }

    @Test fun gainAndMuteOnlyRemixVerifiedCachedSpeech()=runTest {
        val store=store();val request=request(store,targets.take(1));val engine=Engine();val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        val complete=pipeline.run(request.id,models)
        val edits=complete.segments.mapIndexed {index,segment ->if(index==0) segment.copy(gain=.5) else segment.copy(muted=true)}
        store.edit(request.id,edits);val mixed=pipeline.run(request.id,models)
        assertEquals(2,engine.cloned);assertEquals(2,engine.translated);assertEquals(2,exports)
        assertNotEquals(complete.outputs[0].file,mixed.outputs[0].file);assertEquals(complete.renders.map {it.file},mixed.renders.map {it.file})
        assertEquals(.5,mixed.segments[0].gain,0.0);assertTrue(mixed.segments[1].muted)
        val preview=store.preview(request.id,mixed.segments[0],"fr",true)
        assertTrue(preview.peaks.max() in .249f.. .251f);preview.file.delete()
        val muted=store.preview(request.id,mixed.segments[1],"fr",true)
        assertTrue(muted.peaks.all {it==0f});muted.file.delete()
    }

    @Test fun castReferenceDedupAndVerifiedClipPackagesPreserveMarkersAndPublishedOutputs()=runTest {
        val store=store();val request=request(store,targets.take(1));val engine=Engine();val pipeline=PhoneDubPipeline(store,engine,decoder,media)
        val first=pipeline.run(request.id,models)
        val edits=first.segments.mapIndexed {index,cue ->cue.copy(speaker="../Alex",referenceOrigin="cast",voiceID="voice",gain=if(index==0) .5 else 1.0,muted=index==1)}
        val reference=LocalAudio(FloatArray(4800) {.25f},24000)
        store.edit(request.id,edits,mapOf(0 to (reference to "Shared cast"),1 to (reference to "Shared cast")))
        assertEquals(1,store.checkpoint(request.id).segments.mapNotNull {it.referenceFile}.distinct().size)
        val complete=pipeline.run(request.id,models)
        val clips=store.exportPackage(request.id,"fr",DubPackage.CLIPS,temporary.root)
        val names=ProjectArchive.entries(clips).map {it.path}
        assertTrue(names.containsAll(listOf("segments/00001.wav","segments/00002.wav","subtitles.ass")));assertFalse(names.any {it.contains("reference")||it.contains("..")})
        val files=ProjectArchive.extract(clips,File(temporary.root,"clips"))
        assertTrue(PhoneAudioProcessing.containsSyntheticMarker(files.getValue("segments/00001.wav")))
        assertTrue(files.getValue("manifest.json").readText().contains("../Alex"))
        val sensitive=listOf("voiceID","referenceFile","referenceText","referenceOrigin")
        for(key in sensitive) assertFalse(files.getValue("manifest.json").readText().contains("\"$key\""))
        assertEquals(complete.segments,store.checkpoint(request.id).segments)
        val stems=store.exportPackage(request.id,"fr",DubPackage.STEMS,temporary.root)
        val stemFiles=ProjectArchive.extract(stems,File(temporary.root,"stems"))
        assertTrue(PhoneAudioProcessing.containsSyntheticMarker(stemFiles.getValue("dubbed.wav")));assertFalse(PhoneAudioProcessing.containsSyntheticMarker(stemFiles.getValue("original.wav")))
        for(key in sensitive) assertFalse(stemFiles.getValue("manifest.json").readText().contains("\"$key\""))
        store.file(request.id,complete.renders.first().file!!).writeText("corrupt")
        assertTrue(runCatching {store.exportPackage(request.id,"fr",DubPackage.CLIPS,temporary.root)}.isFailure)
        assertEquals(complete.outputs,store.checkpoint(request.id).outputs);assertTrue(clips.isFile)
    }
}
