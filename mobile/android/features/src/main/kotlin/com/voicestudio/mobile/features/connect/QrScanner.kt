package com.voicestudio.mobile.features.connect

import androidx.camera.core.*
import androidx.camera.lifecycle.ProcessCameraProvider
import androidx.camera.view.PreviewView
import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.ContextCompat
import androidx.lifecycle.compose.LocalLifecycleOwner
import com.google.zxing.*
import com.google.zxing.common.HybridBinarizer
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.network.ClientFailure
import java.util.concurrent.Executors
import androidx.compose.ui.unit.dp

@Composable fun QrScanner(onCode:(String)->Unit,onClose:()->Unit,onError:(Throwable)->Unit) {
    val context=LocalContext.current
    val lifecycle=LocalLifecycleOwner.current
    val preview=remember {PreviewView(context)}
    val latestCode by rememberUpdatedState(onCode)
    val latestError by rememberUpdatedState(onError)
    DisposableEffect(lifecycle) {
        val executor=Executors.newSingleThreadExecutor()
        val future=ProcessCameraProvider.getInstance(context)
        var provider:ProcessCameraProvider?=null
        var active=true
        var delivered=false
        future.addListener({
            if(active) try {
                provider=future.get()
                val cameraPreview=Preview.Builder().build().also {it.surfaceProvider=preview.surfaceProvider}
                val analysis=ImageAnalysis.Builder().setBackpressureStrategy(ImageAnalysis.STRATEGY_KEEP_ONLY_LATEST).build()
                analysis.setAnalyzer(executor) {image ->
                    try {
                        if(!delivered) {
                            val plane=image.planes.first();val bytes=ByteArray(image.width*image.height)
                            for(y in 0 until image.height) for(x in 0 until image.width) bytes[y*image.width+x]=plane.buffer.get(y*plane.rowStride+x*plane.pixelStride)
                            var source:LuminanceSource=PlanarYUVLuminanceSource(bytes,image.width,image.height,0,0,image.width,image.height,false)
                            val reader=MultiFormatReader().apply {setHints(mapOf(DecodeHintType.POSSIBLE_FORMATS to listOf(BarcodeFormat.QR_CODE),DecodeHintType.TRY_HARDER to true))}
                            repeat(4) {
                                if(!delivered) try {
                                    val result=reader.decode(BinaryBitmap(HybridBinarizer(source)))
                                    delivered=true;ContextCompat.getMainExecutor(context).execute {if(active) latestCode(result.text)}
                                } catch(_:NotFoundException) {if(source.isRotateSupported) source=source.rotateCounterClockwise()}
                            }
                        }
                    } catch(_:Exception) { /* An undecodable frame is expected. */ } finally {image.close()}
                }
                provider!!.bindToLifecycle(lifecycle,CameraSelector.DEFAULT_BACK_CAMERA,cameraPreview,analysis)
            } catch(_:Exception) {latestError(ClientFailure("mobile.android.camera_failed"))}
        },ContextCompat.getMainExecutor(context))
        onDispose {active=false;provider?.unbindAll();executor.shutdown()}
    }
    AlertDialog(shape=MaterialTheme.shapes.large,onDismissRequest=onClose,title={Text(tr("mobile.connect.scan_qr"))},
        text={AndroidView(factory={preview},modifier=Modifier.fillMaxWidth().height((VSTokens.SPACING_GAP*16).dp))},
        confirmButton={TextButton(shape=MaterialTheme.shapes.small,onClick=onClose) {Text(tr("common.cancel"))}})
}
