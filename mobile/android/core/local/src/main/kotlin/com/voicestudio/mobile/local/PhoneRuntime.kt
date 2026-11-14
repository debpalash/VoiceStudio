package com.voicestudio.mobile.local

import android.app.ActivityManager
import android.content.Context
import android.net.ConnectivityManager
import java.io.File

/** UI and foreground jobs share model leases and licence withdrawal state. */
class PhoneRuntime private constructor(context: Context) {
    private val app=context.applicationContext
    val ramBytes=ActivityManager.MemoryInfo().also {app.getSystemService(ActivityManager::class.java).getMemoryInfo(it)}.totalMem
    @Suppress("DEPRECATION")
    val licence=LicenceGate(File(app.noBackupFilesDir,"model-licences.json"),app.packageManager.getPackageInfo(app.packageName,0).versionName.orEmpty())
    private val downloadNetwork=PhoneDownloadNetwork(app.getSystemService(ConnectivityManager::class.java))
    val downloads=ModelDownloads(File(app.noBackupFilesDir,"models"),licence,ramBytes,{path->app.assets.open(path)}) {
        downloadNetwork.wifiAllowed
    }
    val engine: LocalEngine=PhoneLocalEngine(downloads)
    companion object {
        // Only the application context survives; activities are never retained.
        @android.annotation.SuppressLint("StaticFieldLeak")
        @Volatile private var instance: PhoneRuntime?=null
        fun get(context: Context): PhoneRuntime {
            val application=context.applicationContext
            return instance?.takeIf {it.app===application} ?: synchronized(this) {
                instance?.takeIf {it.app===application} ?: PhoneRuntime(application).also {instance=it}
            }
        }
    }
}
