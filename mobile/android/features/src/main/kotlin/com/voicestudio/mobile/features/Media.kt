package com.voicestudio.mobile.features

import android.content.Context
import android.content.Intent
import android.media.MediaRecorder
import android.net.Uri
import androidx.core.content.FileProvider
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.background
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material3.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.ui.unit.dp
import androidx.compose.ui.Alignment
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.*
import androidx.media3.common.MediaItem
import androidx.media3.common.Player
import androidx.media3.datasource.DefaultDataSource
import androidx.media3.datasource.okhttp.OkHttpDataSource
import androidx.media3.exoplayer.ExoPlayer
import androidx.media3.exoplayer.source.DefaultMediaSourceFactory
import com.voicestudio.mobile.designsystem.VSTheme
import com.voicestudio.mobile.designsystem.role
import com.voicestudio.mobile.network.*
import kotlinx.coroutines.*
import java.io.File

/** Provider metadata is separate from the private, collision-free storage path. */
fun mediaDisplayName(context: Context, uri: Uri): String? = context.contentResolver.query(
    uri,arrayOf(android.provider.OpenableColumns.DISPLAY_NAME),null,null,null)?.use { cursor ->
        val column=cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
        if(column>=0 && cursor.moveToFirst()) cursor.getString(column)?.takeIf {it.isNotBlank()} else null
    }

suspend fun copyMedia(context: Context, uri: Uri, extension: String, maxBytes: Long = Long.MAX_VALUE): File {
    var pending: File?=null
    try {return withContext(Dispatchers.IO) {
        val original=mediaDisplayName(context,uri)
        val suffix=original?.substringAfterLast('.', "")?.lowercase()?.takeIf {Regex("[a-z0-9]{1,10}").matches(it)}
            ?: android.webkit.MimeTypeMap.getSingleton().getExtensionFromMimeType(context.contentResolver.getType(uri)) ?: extension
        val file=File(context.filesDir,"uploads/${java.util.UUID.randomUUID()}.$suffix").apply {parentFile!!.mkdirs()}
        pending=file
        requireNotNull(context.contentResolver.openInputStream(uri)).use {input ->file.outputStream().use {out ->
            val buffer=ByteArray(262144);var total=0L
            while(true) {currentCoroutineContext().ensureActive();val count=input.read(buffer);if(count<0) break;total+=count;if(total>maxBytes) throw ClientFailure("mobile.errors.file");out.write(buffer,0,count)}
        }}
        currentCoroutineContext().ensureActive();file
    }} catch(e:Exception) {pending?.delete();throw e}
}
fun formatDuration(milliseconds: Long): String {
    val seconds = milliseconds.coerceAtLeast(0) / 1000
    return "%d:%02d".format(java.util.Locale.ROOT, seconds / 60, seconds % 60)
}
fun shareText(context: Context, text: String) { context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT,text),null)) }
fun shareFile(context: Context, file: File, type: String) {
    val uri=FileProvider.getUriForFile(context, "${context.packageName}.files",file)
    context.startActivity(Intent.createChooser(Intent(Intent.ACTION_SEND).setType(type).putExtra(Intent.EXTRA_STREAM,uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION),null))
}
suspend fun shareLocalFile(context: Context, file: File, type: String) {
    val shared=withContext(Dispatchers.IO) {
        File(context.cacheDir,"shared/${java.util.UUID.randomUUID()}.${file.extension}").apply {parentFile!!.mkdirs();file.copyTo(this)}
    }
    shareFile(context,shared,type)
}
suspend fun sharedDownload(context: Context, api: VoiceStudioApi, path: String, extension: String): File {
    val dir=File(context.cacheDir,"shared").apply {mkdirs()}
    val file=File(dir,"${java.util.UUID.randomUUID()}.$extension")
    try {
        val type=api.download(path,file)
        val suffix=when(type) {"audio/wav", "audio/x-wav" -> "wav";"audio/mp4", "audio/x-m4a" -> "m4a";"video/mp4" -> "mp4";"video/x-matroska", "video/matroska" -> "mkv";"audio/mpeg" -> "mp3";else -> extension}
        if(suffix==extension) return file
        val renamed=File(dir,"${java.util.UUID.randomUUID()}.$suffix")
        check(file.renameTo(renamed));return renamed
    } catch(e:Exception) {file.delete();throw e}
}
class SampleRecorder(private val context: Context) {
    private var recorder: MediaRecorder? = null
    var file: File? = null
        private set
    @Suppress("DEPRECATION")
    fun start(): File {
        val output=File(context.filesDir,"uploads/${java.util.UUID.randomUUID()}.m4a").apply {parentFile!!.mkdirs()}
        val recording=if(android.os.Build.VERSION.SDK_INT>=31) MediaRecorder(context) else MediaRecorder()
        recorder=recording;file=output
        try {
            recording.setAudioSource(MediaRecorder.AudioSource.MIC);recording.setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            recording.setAudioEncoder(MediaRecorder.AudioEncoder.AAC);recording.setAudioSamplingRate(44100);recording.setAudioEncodingBitRate(128000)
            recording.setOutputFile(output.absolutePath);recording.prepare();recording.start()
        } catch(e:Exception) { discard();throw e }
        return output
    }
    fun stop(): File {
        val output=file ?: error("No recording")
        try {recorder?.stop()} catch(e:Exception) {output.delete();throw e} finally {recorder?.release();recorder=null}
        return output
    }
    fun discard() {runCatching {recorder?.stop()};recorder?.release();recorder=null;file?.delete();file=null}
}

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
val LocalHostPlayerFactory=staticCompositionLocalOf<(Context,VoiceStudioApi)->Player> {
    { context, api -> ExoPlayer.Builder(context).setMediaSourceFactory(DefaultMediaSourceFactory(DefaultDataSource.Factory(context,OkHttpDataSource.Factory(api.client)))).build() }
}
@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
private class PlaybackListenerOwner(private val player:Player) {
    private var listener:Player.Listener?=null
    private var generation=0L
    var preparing=false
        private set
    fun stateChanged(state:Int) {
        if(state==Player.STATE_READY || state==Player.STATE_ENDED) preparing=false
        else if(state==Player.STATE_BUFFERING) preparing=true
    }
    fun failed() {preparing=false}
    fun bind(preparation:Boolean=false,create:(()->Boolean)->Player.Listener) {
        preparing=preparation
        val owner=++generation
        listener?.let(player::removeListener)
        listener=create {owner==generation}.also(player::addListener)
    }
    fun close() {generation++;listener?.let(player::removeListener);listener=null}
}

@androidx.annotation.OptIn(androidx.media3.common.util.UnstableApi::class)
@Composable fun HostPlayer(api: VoiceStudioApi, path: String, failure: ()->(Throwable)->Unit) {
    val context=LocalContext.current
    val factory=LocalHostPlayerFactory.current
    val player=remember(api,path) {factory(context,api)}
    val observeFailure by rememberUpdatedState(failure)
    val listeners=remember(player) {PlaybackListenerOwner(player)}
    var playing by remember(player) { mutableStateOf(false) }
    var duration by remember(player) { mutableLongStateOf(0L) }
    var position by remember(player) { mutableLongStateOf(0L) }
    var error by remember(player) { mutableStateOf(false) }
    fun observePlayback(preparation:Boolean=false): (Throwable)->Unit {
        val failed=observeFailure()
        listeners.bind(preparation) {current ->object: Player.Listener {
            override fun onIsPlayingChanged(isPlaying:Boolean) {if(current()) playing=isPlaying}
            override fun onPlaybackStateChanged(state:Int) {if(current()) listeners.stateChanged(state)}
            override fun onPlayerError(e:androidx.media3.common.PlaybackException) {if(current()) {listeners.failed();error=true;failed(generateSequence(e as Throwable?) {it.cause}.filterIsInstance<ClientFailure>().firstOrNull() ?: ClientFailure("mobile.android.playback_failed"))}}
        }}
        return failed
    }
    DisposableEffect(player) {
        onDispose {listeners.close();player.release()}
    }
    LaunchedEffect(path,api) {
        val failed=observePlayback(preparation=true)
        try {error=false;player.setMediaItem(MediaItem.fromUri(api.url(path).toString()));player.prepare()}
        catch(e:Exception) {listeners.failed();error=true;failed(e)}
    }
    LaunchedEffect(player) {while(isActive) {duration=player.duration.coerceAtLeast(0);position=player.currentPosition.coerceAtLeast(0);delay(250)}}
    Card(Modifier.fillMaxWidth(),colors=CardDefaults.cardColors(containerColor=role("surface")),shape=MaterialTheme.shapes.large) {
        Column(Modifier.padding(VSTheme.inset),verticalArrangement=Arrangement.spacedBy(VSTheme.gap)) {
            Text(tr("player.generated_audio"),style=MaterialTheme.typography.titleMedium)
            Row(verticalAlignment=Alignment.CenterVertically) {
                IconAction(if(playing) "player.pause" else "player.play",if(playing) VSIcons.Pause else Icons.Default.PlayArrow) {
                    if(playing) player.pause() else {
                        // Play during prepare continues that load; it cannot
                        // replace the owner of a later Media3 prepare error.
                        val retry=error
                        if(!listeners.preparing) observePlayback(preparation=retry)
                        if(retry) {error=false;player.prepare()}
                        if(player.playbackState==Player.STATE_ENDED) player.seekTo(0)
                        player.play()
                    }
                }
                // Seeking does not start a new load or replace its diagnostic owner.
                PlaybackSeek(position,duration,Modifier.weight(1f)) {player.seekTo(it)}
            }
            Text(tr("mobile.android.playback_time",mapOf("position" to formatDuration(position),"duration" to formatDuration(duration))),style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
        }
    }
}

val LocalAudioPlayerFactory=staticCompositionLocalOf<(Context)->Player> {
    { context -> ExoPlayer.Builder(context).build() }
}
@Composable fun LocalAudioPlayer(file: File, failure: ()->(Throwable)->Unit) { LocalAudioPlayer(file,false,failure=failure) }

@Composable fun LocalAudioPlayer(file: File, autoPlay:Boolean, playRequest:Long=0, failure: ()->(Throwable)->Unit) {
    val context=LocalContext.current
    val factory=LocalAudioPlayerFactory.current
    val player=remember(file) {factory(context)}
    val observeFailure by rememberUpdatedState(failure)
    val listeners=remember(player) {PlaybackListenerOwner(player)}
    var playing by remember(file) {mutableStateOf(false)}
    var position by remember(file) {mutableLongStateOf(0)}
    var duration by remember(file) {mutableLongStateOf(0)}
    var videoRatio by remember(file) {mutableFloatStateOf(16f/9)}
    var failedPreparation by remember(file) {mutableStateOf(false)}
    fun observePlayback(preparation:Boolean=false): (Throwable)->Unit {
        val failed=observeFailure()
        listeners.bind(preparation) {current ->object: Player.Listener {
            override fun onIsPlayingChanged(isPlaying: Boolean) {if(current()) playing=isPlaying}
            override fun onPlaybackStateChanged(state:Int) {if(current()) listeners.stateChanged(state)}
            override fun onPlayerError(error: androidx.media3.common.PlaybackException) {if(current()) {listeners.failed();failedPreparation=true;failed(ClientFailure("mobile.android.playback_failed"))}}
            override fun onVideoSizeChanged(videoSize: androidx.media3.common.VideoSize) {if(current()&&videoSize.width>0&&videoSize.height>0) videoRatio=videoSize.width*videoSize.pixelWidthHeightRatio/videoSize.height}
        }}
        return failed
    }
    fun play(restart:Boolean=false) {
        val retry=failedPreparation
        if(!listeners.preparing) observePlayback(preparation=retry)
        if(retry) {failedPreparation=false;player.prepare()}
        if(restart || player.playbackState==Player.STATE_ENDED) player.seekTo(0)
        player.play()
    }
    DisposableEffect(player) {
        val failed=observePlayback(preparation=true)
        try {player.setMediaItem(MediaItem.fromUri(Uri.fromFile(file)));player.prepare()}
        catch(e:Exception) {listeners.failed();failedPreparation=true;failed(e)}
        onDispose {listeners.close();player.release()}
    }
    LaunchedEffect(player,autoPlay,playRequest) {if(autoPlay) play(restart=true)}
    LaunchedEffect(player) {while(isActive) {position=player.currentPosition.coerceAtLeast(0);duration=player.duration.coerceAtLeast(0);delay(250)}}
    Group {
        Column(Modifier.fillMaxWidth().padding(VSTheme.inset)) {
            if(file.extension=="mp4") Box(Modifier.fillMaxWidth().heightIn(max=(com.voicestudio.mobile.designsystem.VSTokens.SPACING_GAP*25).dp),contentAlignment=Alignment.Center) {
                androidx.compose.ui.viewinterop.AndroidView(factory={android.view.SurfaceView(it)},update={player.setVideoSurfaceView(it)},modifier=Modifier.aspectRatio(videoRatio,matchHeightConstraintsFirst=true))
            }
            Row(verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)) {
                IconAction(if(playing) "player.pause" else "player.play",if(playing) VSIcons.Pause else Icons.Default.PlayArrow) {
                    if(playing) player.pause() else play()
                }
                // Seeking does not start a new load or replace its diagnostic owner.
                PlaybackSeek(position,duration,Modifier.weight(1f)) {player.seekTo(it)}
            }
            Text(tr("mobile.android.playback_time",mapOf("position" to formatDuration(position),"duration" to formatDuration(duration))),
                style=MaterialTheme.typography.bodySmall,color=role("textMuted"))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable private fun PlaybackSeek(position:Long,duration:Long,modifier:Modifier=Modifier,seek:(Long)->Unit) {
    val label=tr("player.seek")
    Slider(value=if(duration>0) (position.toFloat()/duration).coerceIn(0f,1f) else 0f,
        onValueChange={seek((it*duration).toLong())},enabled=duration>0,
        modifier=modifier.semantics {contentDescription=label},
        thumb={Box(Modifier.size(12.dp).background(role("messageAction"),CircleShape))},
        track={SliderDefaults.Track(it,modifier=Modifier.height(4.dp),thumbTrackGapSize=0.dp,drawStopIndicator=null)})
}
