package com.voicestudio.mobile.features

import androidx.compose.material3.Text
import androidx.compose.runtime.Composable

/** Android grants runtime through the actual foreground worker; it has no iOS GPU entitlement preference. */
@Composable fun PhoneJobStatus(running: Boolean) {
    if(running) Text(tr("mobile.jobs.foreground_service"))
}
