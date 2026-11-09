package com.voicestudio.mobile.network

import java.io.File
import kotlinx.coroutines.*
import okhttp3.RequestBody.Companion.toRequestBody

/** Explicit request only, using the paired client's redirect/authentication guard. */
suspend fun VoiceStudioApi.downloadPhoneVoiceBundle(profileID: String,target: File): File=withContext(Dispatchers.IO) {
    try {
        raw("/marketplace/export/${VoiceStudioApi.segment(profileID)}?stored=true","POST",ByteArray(0).toRequestBody(null)).use {response ->
            val body=response.body ?: throw ClientFailure("mobile.galleryCopy.unavailable")
            if(body.contentLength()>64L*1024*1024) throw ClientFailure("mobile.galleryCopy.unavailable")
            body.byteStream().use {input ->target.outputStream().use {output ->val bytes=ByteArray(65536);var total=0L
                while(true) {currentCoroutineContext().ensureActive();val count=input.read(bytes);if(count<0) break;total+=count;if(total>64L*1024*1024) throw ClientFailure("mobile.galleryCopy.unavailable");output.write(bytes,0,count)}
                if(total==0L) throw ClientFailure("mobile.galleryCopy.unavailable")
            }}
        };target
    } catch(e:Exception) {target.delete();throw e}
}
