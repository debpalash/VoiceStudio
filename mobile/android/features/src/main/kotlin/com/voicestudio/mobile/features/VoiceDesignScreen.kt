package com.voicestudio.mobile.features

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.models.MissingModelsCard
import com.voicestudio.mobile.features.models.TaskModelPicker
import com.voicestudio.mobile.local.*
import com.voicestudio.mobile.network.ReportSubject
import kotlinx.coroutines.*
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID

@Serializable private data class DesignCatalogue(val categories: List<DesignCategory>)
@Serializable private data class DesignCategory(val id: String,val label: String,val options: List<DesignOption>)
@Serializable private data class DesignOption(val token: String,val label: String)
private data class DesignedTake(val audio: LocalAudio,val text: String,val instruction: String,val seed: Long,val model: PhoneModel,val file: File)

@Composable fun VoiceDesignScreen(vm: StudioViewModel,communityRecipe: GalleryEntry?=null) {
    val phone by vm.phone.state.collectAsState()
    val prefs by vm.preferences.collectAsState()
    val context=LocalContext.current
    val scope=rememberCoroutineScope()
    var categories by remember {mutableStateOf(emptyList<DesignCategory>())}
    var traits by rememberSaveable {mutableStateOf(hashMapOf<String,String>())}
    var seed by rememberSaveable {mutableStateOf(42L)}
    var script by rememberSaveable {mutableStateOf(communityRecipe?.sample_script.orEmpty())}
    var name by rememberSaveable {mutableStateOf(communityRecipe?.name.orEmpty())}
    var take by remember {mutableStateOf<DesignedTake?>(null)}
    var saved by remember {mutableStateOf(false)}
    var sourceAttribution by rememberSaveable {mutableStateOf(communityRecipe?.attribution?.let {HashMap(it)})}
    var recipeID by rememberSaveable {mutableStateOf("")}
    var extraInstruction by rememberSaveable {mutableStateOf("")}
    var work by remember {mutableStateOf<Job?>(null)}
    var progress by remember {mutableStateOf(0f)}
    val running=work?.isActive==true
    val item=phone.models.filter {it.supportsDesign}.let {models -> models.firstOrNull {it.id==prefs.phoneModels["design"]} ?: models.firstOrNull {it.id in phone.installed} ?: models.firstOrNull()}
    LaunchedEffect(Unit) {
        try {
            vm.phone.refresh()
            categories=withContext(Dispatchers.IO) {context.assets.open("design.json").bufferedReader().use {Json.decodeFromString<DesignCatalogue>(it.readText()).categories}}
            if(communityRecipe!=null&&traits.isEmpty()&&extraInstruction.isEmpty()) {
                val tokens=communityRecipe.instruct.orEmpty().split(", ")
                traits=HashMap(categories.mapNotNull {category->category.options.firstOrNull {it.token in tokens}?.let {category.id to it.token}}.toMap())
                val known=categories.flatMap {it.options}.map {it.token}.toSet();extraInstruction=tokens.filter {it !in known}.joinToString(", ")
            }
        } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
    }
    Screen {
        ScreenTitle("nav.design")
        communityRecipe?.let {Text(it.name,style=MaterialTheme.typography.titleMedium)}
        Text(tr("mobile.local.this_phone"),color=role("textMuted"))
        if(!running) {
            ChoicePicker("mobile.books.restore_recipe",recipeID,listOf(Choice("",tr("common.not_set")))+phone.voices.filter {it.designInstruction!=null}.map {Choice(it.id,it.name)}) {id->
                recipeID=id
                phone.voices.firstOrNull {it.id==id}?.let {voice->
                    val tokens=voice.designInstruction.orEmpty().split(", ")
                    traits=HashMap(categories.mapNotNull {category->category.options.firstOrNull {it.token in tokens}?.let {category.id to it.token}}.toMap())
                    val known=categories.flatMap {it.options}.map {it.token}.toSet();extraInstruction=tokens.filter {it !in known}.joinToString(", ")
                    sourceAttribution=voice.sourceAttribution?.let {HashMap(it)}
                    seed=voice.designSeed ?: 42L;script=voice.transcript;name=voice.name;take=null;saved=false
                    phone.models.firstOrNull {it.capability=="clone"&&it.repo==voice.modelRepo&&it.revision==voice.modelRevision}?.let {vm.preference {current->current.copy(phoneModels=current.phoneModels+("design" to it.id))}}
                }
            }
            if(extraInstruction.isNotEmpty()) Field("clone.describe_label",extraInstruction,{extraInstruction=it},multiline=true)
            categories.forEach {category ->
                ChoicePicker(category.label,traits[category.id].orEmpty(),category.options.map {Choice(it.token,tr(it.label))}) {traits=HashMap(traits+(category.id to it))}
            }
            Field("clone.seed_label",seed.toString(),{value->value.toLongOrNull()?.takeIf {it>=0}?.let {seed=it}})
            Secondary("clone.seed_reroll") {seed=kotlin.random.Random.nextLong(0,0x1_0000_0000L)}
            ChoicePicker("mobile.local.model",item?.id.orEmpty(),phone.models.filter {it.supportsDesign}.map {Choice(it.id,it.name,enabled=it.fits(vm.phone.ramBytes))}) {id->vm.preference {it.copy(phoneModels=it.phoneModels+("design" to id))}}
        }
        Field("clone.script",script,{if(!running) script=it},multiline=true)
        if(item!=null&&!phone.canRun(item,vm.phone.ramBytes)) MissingModelsCard(vm,"nav.design",listOf(item))
        if(running) {
            LinearProgressIndicator(progress={progress},modifier=Modifier.fillMaxWidth())
            Secondary("common.cancel") {work?.cancel()}
        } else Action("mobile.speak.generate",script.isNotBlank()&&script.length<=2000&&categories.isNotEmpty()&&item?.let {phone.canRun(it,vm.phone.ramBytes)}==true) {
            val text=script;val instruction=(categories.mapNotNull {traits[it.id]}.filter {it.isNotEmpty()}+listOf(extraInstruction).filter {it.isNotEmpty()}).joinToString(", ");val selectedSeed=seed
            val producer=requireNotNull(item)
            take=null;saved=false;progress=0f
            work=scope.launch {
                try {
                    val audio=vm.phone.engine.design(producer,text,instruction,selectedSeed) {progress=it}
                    val file=File(context.cacheDir,"voice-design/${UUID.randomUUID()}.wav")
                    withContext(Dispatchers.IO) {file.parentFile!!.mkdirs();markSynthetic(audio,file)}
                    take=DesignedTake(audio,text,instruction,selectedSeed,producer,file)
                } catch(e:CancellationException) {throw e} catch(e:Exception) {vm.fail(e)}
            }
        }
        take?.let {result ->
            LocalAudioPlayer(result.file,vm::fail)
            Secondary("mobile.speak.share") {scope.launch {try {shareLocalFile(context,result.file,"audio/wav")} catch(e:Exception) {vm.fail(e)}}}
            ReportButton(vm) {ReportSubject("speak:phone:"+result.file.name,"speak","phone",result.model.repo,result.model.revision,result.text)}
            Field("mobile.voices.name_label",name,{name=it})
            Action(if(recipeID.isEmpty()) "clone.save_design_as_profile" else "mobile.books.save_recipe_copy",name.isNotBlank()&&!saved&&!running) {
                vm.run {
                    vm.phone.voices.saveDesign(name,result.text,result.audio,result.model,result.instruction,result.seed,sourceAttribution)
                    vm.phone.refresh();saved=true
                }
            }
        }
    }
}
