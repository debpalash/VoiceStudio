package com.voicestudio.mobile.features

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.path
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*

object VSIcons {
    private fun symbol(name: String, data: String): ImageVector = ImageVector.Builder(name,24.dp,24.dp,24f,24f)
        .addPath(pathData=androidx.compose.ui.graphics.vector.PathParser().parsePathString(data).toNodes(),
            fill=SolidColor(Color(VSTheme.roles("default",true).getValue("text"))),pathFillType=androidx.compose.ui.graphics.PathFillType.EvenOdd).build()
    val Stop by lazy {symbol("Stop","M6 6h12v12H6z")}
    val Wave by lazy {symbol("Wave","M3 10h2v4H3z M7 6h2v12H7z M11 2h2v20h-2z M15 6h2v12h-2z M19 10h2v4h-2z")}
    val Computer by lazy {symbol("Computer","M2 3h20v14H2z M4 5v10h16V5z M11 17h2v3h5v2H6v-2h5z")}
    val Folder by lazy {symbol("Folder","M2 4h8l2 2h10v15H2z M4 8v11h16V8z")}
    val Link by lazy {symbol("Link","M3.9 12a4 4 0 0 1 4-4h3V6h-3a6 6 0 0 0 0 12h3v-2h-3a4 4 0 0 1-4-4z M8 11h8v2H8z M13 6h3a6 6 0 0 1 0 12h-3v-2h3a4 4 0 0 0 0-8h-3z")}
    val Globe by lazy {symbol("Globe","M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z M4.3 10h3a17 17 0 0 0 0 4h-3a8 8 0 0 1 0-4z M5.1 8a8 8 0 0 1 4-3.5A15 15 0 0 0 7.7 8z M5.1 16h2.6a15 15 0 0 0 1.4 3.5A8 8 0 0 1 5.1 16z M9.4 10h5.2a15 15 0 0 1 0 4H9.4a15 15 0 0 1 0-4z M9.8 8A13 13 0 0 1 12 4a13 13 0 0 1 2.2 4z M9.8 16h4.4A13 13 0 0 1 12 20a13 13 0 0 1-2.2-4z M16.3 8a15 15 0 0 0-1.4-3.5 8 8 0 0 1 4 3.5z M16.7 10h3a8 8 0 0 1 0 4h-3a17 17 0 0 0 0-4z M16.3 16h2.6a8 8 0 0 1-4 3.5 15 15 0 0 0 1.4-3.5z")}
    val Contrast by lazy {symbol("Contrast","M12 2a10 10 0 1 0 0 20 10 10 0 0 0 0-20z M12 4v16a8 8 0 0 1 0-16z")}
    val Palette by lazy {symbol("Palette","M12 2a10 10 0 0 0 0 20h1a3 3 0 0 0 2-5c-.5-.5-.1-1 .5-1H17a5 5 0 0 0 5-5c0-5-5-9-10-9z M7 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0z M12 5a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0z M17 7a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0z M7 13a1.5 1.5 0 1 1-3 0 1.5 1.5 0 0 1 3 0z")}
    val Bell by lazy {symbol("Bell","M12 2a2 2 0 0 0-2 2v.3C7.6 5.1 6 7.3 6 10v6l-2 2v1h16v-1l-2-2v-6c0-2.7-1.6-4.9-4-5.7V4a2 2 0 0 0-2-2z M10 20a2 2 0 0 0 4 0z")}
    val Shield by lazy {symbol("Shield","M12 2 3 6v6c0 5.5 4 9 9 11 5-2 9-5.5 9-11V6z M12 4.2 19 7v5c0 4-3 7-7 8.8z")}
    val Video by lazy {symbol("Video","M3 4h14a2 2 0 0 1 2 2v3l4-3v12l-4-3v3a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z M8 8v8l6-4z")}

    private fun glyph(name: String, draw: androidx.compose.ui.graphics.vector.PathBuilder.()->Unit): ImageVector =
        ImageVector.Builder(name,24.dp,24.dp,24f,24f).path(fill=SolidColor(Color(VSTheme.roles("default",true).getValue("text"))),pathBuilder=draw).build()
    val Qr by lazy {glyph("QR") {
        moveTo(3f,3f);lineTo(10f,3f);lineTo(10f,10f);lineTo(3f,10f);close()
        moveTo(14f,3f);lineTo(21f,3f);lineTo(21f,10f);lineTo(14f,10f);close()
        moveTo(3f,14f);lineTo(10f,14f);lineTo(10f,21f);lineTo(3f,21f);close()
        moveTo(14f,14f);lineTo(17f,14f);lineTo(17f,17f);lineTo(21f,17f);lineTo(21f,21f);lineTo(14f,21f);close()
    }}
    val Mic by lazy {glyph("Mic") {
        moveTo(9f,3f);lineTo(15f,3f);lineTo(15f,14f);lineTo(9f,14f);close()
        moveTo(6f,11f);lineTo(8f,11f);lineTo(8f,16f);lineTo(16f,16f);lineTo(16f,11f);lineTo(18f,11f);lineTo(18f,18f);lineTo(13f,18f);lineTo(13f,21f);lineTo(11f,21f);lineTo(11f,18f);lineTo(6f,18f);close()
    }}
    val Copy by lazy {glyph("Copy") {
        moveTo(4f,3f);lineTo(16f,3f);lineTo(16f,5f);lineTo(6f,5f);lineTo(6f,17f);lineTo(4f,17f);close()
        moveTo(8f,7f);lineTo(20f,7f);lineTo(20f,21f);lineTo(8f,21f);close()
    }}
    val Download: ImageVector by lazy {
        val size=VSTokens.SPACING_INSET
        ImageVector.Builder("Download",size.dp,size.dp,size,size).path(fill=SolidColor(Color(VSTheme.roles("default",true).getValue("text")))) {
            moveTo(size*5/12,size/8);lineTo(size*7/12,size/8);lineTo(size*7/12,size/2)
            lineTo(size*3/4,size/2);lineTo(size/2,size*3/4);lineTo(size/4,size/2);lineTo(size*5/12,size/2);close()
            moveTo(size/4,size*7/8);lineTo(size*3/4,size*7/8);lineTo(size*3/4,size);lineTo(size/4,size);close()
        }.build()
    }
    // Material's core icon artifact omits Pause; keep this simple glyph local.
    val Pause: ImageVector by lazy {
        val size=VSTokens.SPACING_INSET
        ImageVector.Builder("Pause",size.dp,size.dp,size,size).path(fill=SolidColor(Color(VSTheme.roles("default",true).getValue("text")))) {
            moveTo(size/4,size/4);lineTo(size*5/12,size/4);lineTo(size*5/12,size*3/4);lineTo(size/4,size*3/4);close()
            moveTo(size*7/12,size/4);lineTo(size*3/4,size/4);lineTo(size*3/4,size*3/4);lineTo(size*7/12,size*3/4);close()
        }.build()
    }
}
