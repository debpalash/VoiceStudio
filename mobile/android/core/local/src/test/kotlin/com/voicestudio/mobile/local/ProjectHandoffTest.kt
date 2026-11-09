package com.voicestudio.mobile.local

import java.io.File
import java.nio.file.Files
import kotlinx.coroutines.*
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.*
import org.junit.*
import org.junit.Assert.*
import org.junit.rules.TemporaryFolder

class ProjectHandoffTest {
    @get:Rule val temporary=TemporaryFolder()
    private val fixtures get()=generateSequence(File(System.getProperty("user.dir"))) {it.parentFile}.map {File(it,"mobile/contract/project-fixtures")}.first {it.isDirectory}
    private fun stores(): Triple<ProductionStore,PhoneDubStore,PhoneVoiceStore> = Triple(ProductionStore(File(temporary.root,"production")),PhoneDubStore(File(temporary.root,"dubs")),PhoneVoiceStore(File(temporary.root,"voices")))
    @Test fun sharedDesktopBookCreatesNewCopiesAndPhoneEditsPreserveForeignSettings()=runTest {
        val(production,dubs,voices)=stores();val source=File(fixtures,"desktop-book.vsproject")
        assertEquals(0,PhoneProjectHandoff.preview(source).voices)
        val result=PhoneProjectHandoff.restore(source,production,dubs,voices,emptyList(),"fixture")
        var project=production.list().single();assertEquals(result.id,project.id);assertEquals(1.25f,project.speed);assertEquals("port able",project.pronunciations["portable"])
        project=project.copy(script="# First chapter\nEdited on this phone.",speed=.75f);production.save(project)
        val file=File(temporary.root,"phone.vsproject");PhoneProjectHandoff.exportProduction(project,production,voices,"fixture",file)
        val unpacked=ProjectArchive.extract(file,File(temporary.root,"unpacked"));val manifest=Json.parseToJsonElement(unpacked.getValue("manifest.json").readText()).jsonObject
        val doc=manifest.getValue("document").jsonObject;assertEquals(project.script,doc.getValue("script").jsonPrimitive.content);assertEquals(.75,doc.getValue("speed").jsonPrimitive.double,0.0)
        val draft=doc.getValue("desktop").jsonObject.getValue("draft").jsonObject
        assertEquals("Fixture author",draft.getValue("metadata").jsonObject.getValue("author").jsonPrimitive.content)
        assertEquals(42,draft.getValue("overrides").jsonObject.getValue("numStep").jsonPrimitive.int)
        assertEquals(7,draft.getValue("customFutureSetting").jsonObject.getValue("value").jsonPrimitive.int)
        val second=PhoneProjectHandoff.restore(file,production,dubs,voices,emptyList(),"fixture");assertNotEquals(result.id,second.id);assertEquals(2,production.list().size)
        assertEquals(project.script,production.list().first {it.id==second.id}.script)
    }
    @Test fun compatibleNativeRenderCacheSurvivesProjectIDChange()=runTest {
        val(production,dubs,voices)=stores();var calls=0
        val project=ProductionProject(kind="stories",title="Cached",script="One line")
        val speak: suspend(ProductionSegment,String)->LocalAudio={_,_->calls++;LocalAudio(FloatArray(2400) {.2f},24000)}
        production.render(project,"fixture",synthesize=speak)
        val file=File(temporary.root,"cache.vsproject");PhoneProjectHandoff.exportProduction(project,production,voices,"fixture",file)
        val result=PhoneProjectHandoff.restore(file,production,dubs,voices,emptyList(),"fixture");val copy=production.list().first {it.id==result.id}
        assertNotNull(production.output(copy,"fixture"));production.render(copy,"fixture",synthesize=speak);assertEquals(1,calls)
        val unpacked=ProjectArchive.extract(file,File(temporary.root,"edit-cache"));val manifestFile=unpacked.getValue("manifest.json");val original=Json.parseToJsonElement(manifestFile.readText()).jsonObject
        val patches=listOf(mapOf("script" to JsonPrimitive("Edited manuscript")),mapOf("speed" to JsonPrimitive(.75)),mapOf("pronunciations" to buildJsonObject {put("One","Won")}),mapOf("defaultVoice" to JsonPrimitive("preset:1")))
        for((index,patch) in patches.withIndex()) {
            val document=JsonObject(original.getValue("document").jsonObject+patch);manifestFile.writeText(JsonObject(original+("document" to document)).toString())
            val modified=File(temporary.root,"edited-$index.vsproject");ProjectArchive.write(unpacked.map {(path,source)->ProjectArchiveFile(path,source)},modified)
            val changed=PhoneProjectHandoff.restore(modified,production,dubs,voices,emptyList(),"fixture");val current=production.list().first {it.id==changed.id}
            assertNull(production.output(current,"fixture"))
        }
    }
    @Test fun importedVoiceNeedsPermissionAndHasFreshIdentityAndLocalConsent()=runTest {
        val(production,dubs,voices)=stores();val original=voices.saveDesign("Recipe","Reference words",LocalAudio(FloatArray(4800) {.2f},24000),fixtureModel("clone"),"calm",77)
        val project=ProductionProject(kind="stories",title="Voiced",script="Words",defaultVoice="phone:${original.id}");production.save(project)
        val file=File(temporary.root,"voice.vsproject");PhoneProjectHandoff.exportProduction(project,production,voices,output=file)
        try {PhoneProjectHandoff.restore(file,production,dubs,voices,emptyList(),allowVoices=false);fail()} catch(_:Exception) {}
        assertEquals(1,voices.list().size);assertEquals(1,production.list().size)
        val result=PhoneProjectHandoff.restore(file,production,dubs,voices,emptyList(),allowVoices=true)
        val imported=production.list().first {it.id==result.id};assertNotEquals(project.defaultVoice,imported.defaultVoice)
        val voice=voices.list().first {it.id!=original.id};assertEquals(77L,voice.designSeed);assertEquals("calm",voice.designInstruction);assertEquals(original.transcript,voice.transcript);assertTrue(voice.consentAt>=original.consentAt)
        assertArrayEquals(voices.reference(original).samples,voices.reference(voice).samples,0f)
    }
    @Test fun sharedDubPreservesStableIDsSourceTimingTranslationAndReference()=runTest {
        val(production,dubs,voices)=stores();val models=listOf(fixtureModel(),fixtureModel("clone"),fixtureModel("translate"))
        val result=PhoneProjectHandoff.restore(File(fixtures,"desktop-dub.vsproject"),production,dubs,voices,models,allowVoices=true)
        val cp=dubs.checkpoint(result.id);assertEquals(24000L,cp.frames);assertEquals(24000L,cp.nextFrame)
        val segment=cp.segments.single();assertEquals("segment-stable",segment.id);assertEquals(.1,segment.speech.start,0.0);assertEquals("Translated words",segment.translations["fr"]);assertEquals("Narrator",segment.speaker);assertEquals(.8,segment.gain,0.0);assertNotNull(segment.referenceFile)
        val file=File(temporary.root,"dub.vsproject");PhoneProjectHandoff.exportDub(result.id,dubs,models,voices,file)
        val files=ProjectArchive.extract(file,File(temporary.root,"unpacked"));val doc=Json.parseToJsonElement(files.getValue("manifest.json").readText()).jsonObject.getValue("document").jsonObject
        assertEquals("Preserve this instruction",doc.getValue("desktop").jsonObject.getValue("state").jsonObject.getValue("translationInstructions").jsonPrimitive.content)
    }
    @Test fun bundledVoiceBindingBecomesActualReferenceWithoutExplicitClip()=runTest {
        val(production,dubs,voices)=stores();val models=listOf(fixtureModel(),fixtureModel("translate"),fixtureModel("clone"))
        val voice=voices.saveDesign("Selected","Selected reference transcript",LocalAudio(FloatArray(4800) {.4f},24000),models[2],"calm",9)
        val fixture=ProjectArchive.extract(File(fixtures,"desktop-dub.vsproject"),File(temporary.root,"fixture"))
        val request=dubs.create(fixture.getValue("assets/source.wav"),"Selected voice",listOf(DubTarget("fr","French")),models,true,audioOnly=true)
        fixture.getValue("assets/original.pcm").copyTo(dubs.file(request.id,"original.pcm"))
        val cp=PhoneDubCheckpoint(frames=24000,nextFrame=24000,originalHash=sha256(fixture.getValue("assets/original.pcm")),segments=listOf(DubSegmentState(SpeechSegment(.1,.8,"Original words"),translations=mapOf("fr" to "Translated words"),id="selected-line",voiceID=voice.id)))
        dubs.save(request.id,cp)
        val archive=File(temporary.root,"selected.vsproject");PhoneProjectHandoff.exportDub(request.id,dubs,models,voices,archive)
        val result=PhoneProjectHandoff.restore(archive,production,dubs,voices,models,allowVoices=true)
        val restored=dubs.checkpoint(result.id).segments.single();assertNotNull(restored.referenceFile);assertEquals(voice.transcript,restored.referenceText);assertNotEquals(voice.id,restored.voiceID)
        val pcm=PcmFile(dubs.file(result.id,restored.referenceFile!!));assertEquals(4800L,pcm.frames);assertTrue(pcm.read(0,4800).samples.all {kotlin.math.abs(it-.4f)<.001f})
    }
    @Test fun archiveRejectsPathsSymlinksCorruptionAndOverwriteWithoutPartialFiles()=runTest {
        val source=File(temporary.root,"file.txt").apply {writeText("test")}
        for(name in listOf("../outside","/absolute","C:/outside","assets/CON.wav","assets/one/../outside","assets/file.")) assertThrows(IllegalArgumentException::class.java) {ProjectArchive.path(name)}
        val archive=File(temporary.root,"file.zip");ProjectArchive.write(listOf(ProjectArchiveFile("assets/file.txt",source)),archive)
        try {ProjectArchive.write(listOf(ProjectArchiveFile("assets/another.txt",source)),archive);fail()} catch(_:IllegalArgumentException) {}
        val link=File(temporary.root,"link.txt");Files.createSymbolicLink(link.toPath(),source.toPath())
        try {ProjectArchive.write(listOf(ProjectArchiveFile("assets/link.txt",link)),File(temporary.root,"link.zip"));fail()} catch(_:IllegalArgumentException) {}
        val payloadOffset=ProjectArchive.entries(archive).single().offset.toInt()
        val bytes=archive.readBytes();bytes[payloadOffset]=(bytes[payloadOffset].toInt() xor 1).toByte();archive.writeBytes(bytes)
        val staging=File(temporary.root,"partial")
        try {ProjectArchive.extract(archive,staging);fail()} catch(_:IllegalArgumentException) {}
        assertFalse(staging.exists())
    }
    @Test fun nativeDubHandoffKeepsVerifiedPackagesAndInvalidatesEditedInputs()=runTest {
        val(production,dubs,voices)=stores();val models=listOf(fixtureModel(),fixtureModel("translate"),fixtureModel("clone"))
        for(globalReference in listOf(false,true)) {
            val root=File(temporary.root,"rendered-$globalReference").apply {mkdirs()}
            val original=PhoneProjectHandoff.restore(File(fixtures,"desktop-dub.vsproject"),production,dubs,voices,models,allowVoices=true)
            var id=original.id
            if(globalReference) {
                val initial=dubs.request(id);val cp=dubs.checkpoint(id)
                val reference=PcmFile(dubs.file(id,cp.segments.single().referenceFile!!)).read(0,4800)
                val request=dubs.create(dubs.file(id,initial.sourceFile),"Global reference",initial.targets,models,true,reference,"Reference words",true)
                dubs.file(id,"original.pcm").copyTo(dubs.file(request.id,"original.pcm"))
                dubs.save(request.id,cp.copy(segments=cp.segments.map {it.copy(referenceFile=null,referenceText=null)}));id=request.id
            }
            var clones=0
            val engine=object: FakeLocalEngine() {
                override suspend fun clone(model: PhoneModel,text: String,reference: LocalAudio,referenceText: String,progress: (Float)->Unit): LocalAudio {clones++;return LocalAudio(FloatArray(4800) {.3f},24000)}
            }
            val decoder=object: AudioDecoder {override suspend fun decode(source: File,output: File,maxSeconds: Int,progress: (Float)->Unit): PcmFile=error("Unexpected decode")}
            val media=object: DubMedia {override suspend fun finish(source: File,audio: PcmFile,markedWave: File,output: File,audioOnly: Boolean,progress: (Float)->Unit): File=markedWave.copyTo(File(output.path+".wav"))}
            val pipeline=PhoneDubPipeline(dubs,engine,decoder,media);val rendered=pipeline.run(id,models)
            assertTrue(rendered.done);assertEquals(1,clones)
            val archive=File(root,"rendered.vsproject");PhoneProjectHandoff.exportDub(id,dubs,models,voices,archive)
            val restored=PhoneProjectHandoff.restore(archive,production,dubs,voices,models,allowVoices=true);val cp=dubs.checkpoint(restored.id)
            assertTrue(cp.done);assertEquals(rendered.renders.map {it.file},cp.renders.map {it.file})
            pipeline.run(restored.id,models);assertEquals(1,clones)
            for(kind in listOf(DubPackage.CLIPS,DubPackage.STEMS)) {
                val archive=dubs.exportPackage(restored.id,"fr",kind,root)
                assertTrue(ProjectArchive.entries(archive).any {it.path=="manifest.json"})
            }
            val files=ProjectArchive.extract(archive,File(root,"editing"));val manifestFile=files.getValue("manifest.json");val originalManifest=Json.parseToJsonElement(manifestFile.readText()).jsonObject
            val patches=listOf(mapOf("translations" to buildJsonObject {put("fr","Edited translation")}),mapOf("reference" to JsonPrimitive("native/dub/original.pcm")),mapOf("referenceText" to JsonPrimitive("Edited reference transcript")),mapOf("referenceNeedsTranscription" to JsonPrimitive(true)))
            for((index,patch) in patches.withIndex()) {
                val doc=originalManifest.getValue("document").jsonObject
                val segments=doc.getValue("segments").jsonArray.toMutableList();segments[0]=JsonObject(segments[0].jsonObject+patch)
                manifestFile.writeText(JsonObject(originalManifest+("document" to JsonObject(doc+("segments" to JsonArray(segments))))).toString())
                val modified=File(root,"edited-$index.vsproject");ProjectArchive.write(files.map {(path,file)->ProjectArchiveFile(path,file)},modified)
                val changed=PhoneProjectHandoff.restore(modified,production,dubs,voices,models,allowVoices=true);val changedCheckpoint=dubs.checkpoint(changed.id)
                assertFalse(changedCheckpoint.done);assertTrue(changedCheckpoint.renders.isEmpty());assertTrue(changedCheckpoint.outputs.isEmpty())
            }
            for(key in listOf("scope","sha256","referenceHash","clonePin")) {
                val doc=originalManifest.getValue("document").jsonObject;val native=doc.getValue("native").jsonObject
                val checkpoint=native.getValue("checkpoint").jsonObject;val renders=checkpoint.getValue("renders").jsonArray.toMutableList()
                renders[0]=JsonObject(renders[0].jsonObject+(key to JsonPrimitive("stale")))
                val changedNative=JsonObject(native+("checkpoint" to JsonObject(checkpoint+("renders" to JsonArray(renders)))))
                manifestFile.writeText(JsonObject(originalManifest+("document" to JsonObject(doc+("native" to changedNative)))).toString())
                val modified=File(root,"stale-$key.vsproject");ProjectArchive.write(files.map {(path,file)->ProjectArchiveFile(path,file)},modified)
                val changed=PhoneProjectHandoff.restore(modified,production,dubs,voices,models,allowVoices=true);val changedCheckpoint=dubs.checkpoint(changed.id)
                assertFalse(changedCheckpoint.done);assertTrue(changedCheckpoint.renders.isEmpty());assertTrue(changedCheckpoint.outputs.isEmpty())
            }
            for(damage in listOf("missing-renders","duplicate-render","missing-outputs","output-hash")) {
                val doc=originalManifest.getValue("document").jsonObject;val native=doc.getValue("native").jsonObject
                val checkpoint=native.getValue("checkpoint").jsonObject.toMutableMap()
                when(damage) {
                    "missing-renders"->checkpoint["renders"]=JsonArray(emptyList())
                    "duplicate-render"->checkpoint["renders"]=JsonArray(checkpoint.getValue("renders").jsonArray.let {it+it.first()})
                    "missing-outputs"->checkpoint["outputs"]=JsonArray(emptyList())
                    else->checkpoint["outputs"]=JsonArray(checkpoint.getValue("outputs").jsonArray.map {JsonObject(it.jsonObject+("sha256" to JsonPrimitive("stale")))})
                }
                val changedNative=JsonObject(native+("checkpoint" to JsonObject(checkpoint)))
                manifestFile.writeText(JsonObject(originalManifest+("document" to JsonObject(doc+("native" to changedNative)))).toString())
                val modified=File(root,"$damage.vsproject");ProjectArchive.write(files.map {(path,file)->ProjectArchiveFile(path,file)},modified)
                val changed=PhoneProjectHandoff.restore(modified,production,dubs,voices,models,allowVoices=true);val changedCheckpoint=dubs.checkpoint(changed.id)
                assertFalse(changedCheckpoint.done);assertTrue(changedCheckpoint.renders.isEmpty());assertTrue(changedCheckpoint.outputs.isEmpty())
            }
        }
    }
}
