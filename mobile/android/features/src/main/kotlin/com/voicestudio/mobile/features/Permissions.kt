package com.voicestudio.mobile.features

import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.material3.*
import androidx.compose.ui.Modifier
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.runtime.*
import androidx.compose.ui.platform.LocalContext
import androidx.core.content.ContextCompat
import com.voicestudio.mobile.network.ClientFailure

@Composable fun permissionAction(permission:String,rationale:String,failure:(Throwable)->Unit,granted:()->Unit):()->Unit {
    val context=LocalContext.current
    var rationaleOpen by remember {mutableStateOf(false)}
    val latest by rememberUpdatedState(granted)
    val launcher=rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {allowed ->
        if(allowed) latest() else failure(ClientFailure("mobile.permissions.denied"))
    }
    if(rationaleOpen) AlertDialog(shape=MaterialTheme.shapes.large,onDismissRequest={rationaleOpen=false},text={
        // Match the iOS microphone purpose and routing explanation, using the same translated keys.
        val purpose=if(rationale=="mobile.permissions.microphone") tr("permissions.microphone_why")+"\n\n"+tr("mobile.local.privacy") else tr(rationale)
        Text(purpose,Modifier.verticalScroll(rememberScrollState()))
    },
        confirmButton={TextButton(shape=MaterialTheme.shapes.small,onClick={rationaleOpen=false;launcher.launch(permission)}) {Text(tr("mobile.android.continue"))}},
        dismissButton={TextButton(shape=MaterialTheme.shapes.small,onClick={rationaleOpen=false}) {Text(tr("common.cancel"))}})
    return { if(ContextCompat.checkSelfPermission(context,permission)==PackageManager.PERMISSION_GRANTED) latest() else rationaleOpen=true }
}
fun openSystemSettings(context:android.content.Context) {context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:${context.packageName}")))}
