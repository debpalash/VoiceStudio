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

@Composable fun permissionActionWithFailure(permission:String,rationale:String,failure:()->(Throwable)->Unit,restoreFailure:(Throwable)->Unit,granted:((Throwable)->Unit)->Unit):()->Unit {
    val context=LocalContext.current
    var rationaleOpen by remember {mutableStateOf(false)}
    val latest by rememberUpdatedState(granted)
    val observeFailure by rememberUpdatedState(failure)
    val restoredFailure by rememberUpdatedState(restoreFailure)
    var pendingFailure by remember {mutableStateOf<((Throwable)->Unit)?>(null)}
    val launcher=rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {allowed ->
        // Activity-result delivery survives recreation, whereas its original
        // consent observation does not. Restore feedback without a new grant.
        val failed=pendingFailure ?: restoredFailure
        pendingFailure=null
        if(allowed) latest(failed) else failed(ClientFailure("mobile.permissions.denied"))
    }
    if(rationaleOpen) AlertDialog(shape=MaterialTheme.shapes.large,onDismissRequest={rationaleOpen=false},text={
        // Match the iOS microphone purpose and routing explanation, using the same translated keys.
        val purpose=if(rationale=="mobile.permissions.microphone") tr("permissions.microphone_why")+"\n\n"+tr("mobile.local.privacy") else tr(rationale)
        Text(purpose,Modifier.verticalScroll(rememberScrollState()))
    },
        confirmButton={TextButton(shape=MaterialTheme.shapes.small,onClick={rationaleOpen=false;pendingFailure=observeFailure();launcher.launch(permission)}) {Text(tr("mobile.android.continue"))}},
        dismissButton={TextButton(shape=MaterialTheme.shapes.small,onClick={rationaleOpen=false}) {Text(tr("common.cancel"))}})
    return { if(ContextCompat.checkSelfPermission(context,permission)==PackageManager.PERMISSION_GRANTED) latest(observeFailure()) else rationaleOpen=true }
}
/** Permission-gated work can retain its diagnostic owner through recording. */
@Composable fun permissionAction(permission:String,rationale:String,failure:()->(Throwable)->Unit,restoreFailure:(Throwable)->Unit,granted:()->Unit):()->Unit =
    permissionActionWithFailure(permission,rationale,failure,restoreFailure) {granted()}

fun openSystemSettings(context:android.content.Context) {context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS,Uri.parse("package:${context.packageName}")))}
