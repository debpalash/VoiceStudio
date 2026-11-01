package com.voicestudio.mobile.data

import android.content.Context
import android.text.format.DateUtils
import android.text.format.Formatter
import com.voicestudio.mobile.l10n.L10n
import com.voicestudio.mobile.local.DownloadProgress
import java.text.NumberFormat

fun modelDownloadDescription(context: Context,l: L10n,p: DownloadProgress): String {
    val percent=NumberFormat.getPercentInstance(java.util.Locale.forLanguageTag(l.locale)).format(if(p.total>0) p.received.toDouble()/p.total else 0.0)
    val bytes=l.t("mobile.local.transfer",mapOf("received" to Formatter.formatFileSize(context,p.received),"total" to Formatter.formatFileSize(context,p.total)))
    val state=when(p.stage) {
        "paused","waiting","connecting","verifying" -> l.t("mobile.downloads.${p.stage}")
        "retrying" -> l.t("mobile.downloads.retrying",mapOf("seconds" to p.retrySeconds))
        else -> {
            val speed=l.t("mobile.downloads.speed",mapOf("speed" to Formatter.formatFileSize(context,p.bytesPerSecond.toLong())))
            val eta=p.etaSeconds?.takeIf {it.isFinite()&&it>=0}?.let {l.t("mobile.downloads.eta",mapOf("time" to DateUtils.formatElapsedTime(kotlin.math.ceil(it).toLong())))} ?: l.t("mobile.downloads.estimating")
            "$speed · $eta"
        }
    }
    return "$percent · $bytes\n$state"
}
