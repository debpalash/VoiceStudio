package com.voicestudio.mobile.features

import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.compose.runtime.*
import androidx.compose.material3.*
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.speak.SpeakScreen
import com.voicestudio.mobile.local.*
import kotlinx.coroutines.*
import java.io.File

/** Receiving content opens a review screen; it never starts recording, inference or a download. */
data class PhoneIncoming(val text: String?=null,val file: File?=null,val route: String?=null) {
    companion object {
        suspend fun read(context: Context,intent: Intent): PhoneIncoming?=withContext(Dispatchers.IO) {
            val uri=intent.data
            if(intent.action==Intent.ACTION_VIEW&&uri?.scheme=="voicestudio"&&uri.host=="workspace") {
                val route=uri.pathSegments.singleOrNull()?.takeIf {it in setOf("speak","dictate","workflows","dub") } ?: return@withContext null
                return@withContext PhoneIncoming(text=uri.getQueryParameter("text")?.take(20000),route=route)
            }
            if(intent.action!=Intent.ACTION_SEND) return@withContext null
            val type=intent.type.orEmpty()
            if(type.startsWith("text/")) return@withContext intent.getCharSequenceExtra(Intent.EXTRA_TEXT)?.toString()?.take(20000)?.let {PhoneIncoming(text=it)}
            if(!type.startsWith("audio/")&&!type.startsWith("video/")) return@withContext null
            val source=if(android.os.Build.VERSION.SDK_INT>=33) intent.getParcelableExtra(Intent.EXTRA_STREAM,Uri::class.java) else @Suppress("DEPRECATION") (intent.getParcelableExtra(Intent.EXTRA_STREAM) as? Uri)
            if(source?.scheme!="content") return@withContext null
            val suffix=android.webkit.MimeTypeMap.getSingleton().getExtensionFromMimeType(type)?.takeIf {it.all(Char::isLetterOrDigit)} ?: "media"
            val target=File.createTempFile("shared-",".$suffix",context.cacheDir)
            try {
                context.contentResolver.openInputStream(source).use {input ->requireNotNull(input);target.outputStream().use {output ->
                    val buffer=ByteArray(65536);var bytes=0L
                    while(true) {currentCoroutineContext().ensureActive();val count=input.read(buffer);if(count<0) break;bytes+=count;require(bytes<=512L*1024*1024);output.write(buffer,0,count)};require(bytes>0)
                }}
                PhoneIncoming(file=target)
            } catch(e:Exception) {target.delete();throw e}
        }
    }
}
@Composable fun PhoneIncomingScreen(vm: StudioViewModel,incoming: PhoneIncoming) {
    var target by remember(incoming) {mutableStateOf<String?>(null)}
    var workflow by remember(incoming) {mutableStateOf<PhoneWorkflow?>(null)}
    val scope=rememberCoroutineScope()
    val title=tr("mobile.integration.received")
    when(target) {
        "speak"->SpeakScreen(vm,initialText=incoming.text)
        "workflows"->workflow?.let {WorkflowsScreen(vm,it)}
        else->Screen {
            ScreenTitle("mobile.integration.received")
            Text(incoming.text ?: incoming.file?.name.orEmpty())
            Text(tr("mobile.integration.review"))
            if(incoming.text!=null) Action("mobile.tabs.speak") {target="speak"}
            Action("workflows.title") {scope.launch {try {
                val draft=PhoneWorkflow(name=title,steps=(if(incoming.file!=null) listOf("transcribe","normalize") else listOf("normalize","speak")).map {PhoneWorkflowStep(kind=it)},inputText=incoming.text)
                workflow=incoming.file?.let {vm.phone.workflows.importAudio(it,draft)} ?: draft
                target="workflows"
            } catch(e:Exception) {vm.fail(e)}}}
        }
    }
}
