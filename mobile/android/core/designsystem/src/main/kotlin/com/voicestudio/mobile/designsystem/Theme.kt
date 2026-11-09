package com.voicestudio.mobile.designsystem

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

data class ThemePreferences(val mode: String = "dark", val lightPalette: String = "default", val darkPalette: String = "default")
object VSTheme {
    val borderWidth = (VSTokens.SPACING_GAP / VSTokens.TEXT_EDITOR_SIZE).dp
    val gap = VSTokens.SPACING_GAP.dp
    val inset = minOf(VSTokens.SPACING_INSET, VSTokens.SPACING_GAP).dp
    val touchTarget = (VSTokens.SPACING_GAP * 3).dp
    fun roles(palette: String, dark: Boolean): Map<String, Long> {
        val chosen = VSTokens.palettes.firstOrNull { it.id == palette }
        val fallback = VSTokens.palettes.first { it.id == "default" }
        return (if(dark) chosen?.dark ?: fallback.dark else chosen?.light ?: fallback.light)!!
    }
    fun scheme(roles: Map<String, Long>, dark: Boolean): ColorScheme {
        fun c(role: String) = Color(roles.getValue(role))
        // Give every Material role an explicit desktop token, including all tonal surfaces.
        val base = if(dark) darkColorScheme() else lightColorScheme()
        return base.copy(
            primary = c("messageAction"), onPrimary = c("messageActionForeground"), primaryContainer = c("accentSurface"), onPrimaryContainer = c("accentSurfaceForeground"), inversePrimary = c("focus"),
            secondary = c("secondary"), onSecondary = c("secondaryForeground"), secondaryContainer = c("messageSurface"), onSecondaryContainer = c("messageForeground"),
            tertiary = c("warning"), onTertiary = c("warningForeground"), tertiaryContainer = c("warningSurface"), onTertiaryContainer = c("warningForeground"),
            primaryFixed=c("accentSurface"), primaryFixedDim=c("messageSurface"), onPrimaryFixed=c("accentSurfaceForeground"), onPrimaryFixedVariant=c("textMuted"),
            secondaryFixed=c("secondary"), secondaryFixedDim=c("muted"), onSecondaryFixed=c("secondaryForeground"), onSecondaryFixedVariant=c("mutedForeground"),
            tertiaryFixed=c("warningSurface"), tertiaryFixedDim=c("warningSurface"), onTertiaryFixed=c("warningForeground"), onTertiaryFixedVariant=c("warningForeground"),
            background = c("canvas"), onBackground = c("text"), surface = c("surface"), onSurface = c("text"), surfaceVariant = c("input"), onSurfaceVariant = c("textMuted"), surfaceTint = c("surface"),
            inverseSurface = c("messageAction"), inverseOnSurface = c("messageActionForeground"),
            error = c("error"), onError = c("errorForeground"), errorContainer = c("errorSurface"), onErrorContainer = c("errorForeground"),
            outline = c("border"), outlineVariant = c("border"), scrim = c("canvas"),
            surfaceBright = c("surfaceOverlay"), surfaceDim = c("canvas"), surfaceContainer = c("surface"), surfaceContainerHigh = c("surfaceOverlay"), surfaceContainerHighest = c("surfaceOverlay"), surfaceContainerLow = c("surface"), surfaceContainerLowest = c("canvas"),
        )
    }
}
val LocalRoles = staticCompositionLocalOf { VSTheme.roles("default", true) }
@Composable fun role(name: String) = Color(LocalRoles.current.getValue(name))
@Composable fun VoiceStudioTheme(preferences: ThemePreferences = ThemePreferences(), content: @Composable () -> Unit) {
    val dark = when(preferences.mode) { "light" -> false; "system" -> isSystemInDarkTheme(); else -> true }
    val roles = VSTheme.roles(if(dark) preferences.darkPalette else preferences.lightPalette, dark)
    val font = FontFamily(Font(R.font.inter_variable))
    val body = TextStyle(fontFamily = font, fontSize = VSTokens.TEXT_EDITOR_SIZE.sp, lineHeight = (VSTokens.TEXT_EDITOR_SIZE * 1.5f).sp)
    val control = TextStyle(fontFamily = font, fontSize = VSTokens.TEXT_UI_SIZE.sp, lineHeight = VSTokens.TEXT_UI_LINE_HEIGHT.sp)
    val caption = TextStyle(fontFamily = font, fontSize = VSTokens.TEXT_CAPTION_SIZE.sp, lineHeight = VSTokens.TEXT_CAPTION_LINE_HEIGHT.sp)
    val title = body.copy(fontSize = 17.sp, lineHeight = 22.sp, fontWeight = FontWeight.SemiBold)
    val heading = title.copy(fontSize = VSTokens.IOS_HOME_WORDMARK_SIZE.sp, lineHeight = (VSTokens.IOS_HOME_WORDMARK_SIZE * 1.2f).sp)
    // Both native apps use the generated mobile presentation radii.
    val shape = RoundedCornerShape(VSTokens.IOS_FIELD_RADIUS.dp)
    CompositionLocalProvider(LocalRoles provides roles) {
        MaterialTheme(colorScheme = VSTheme.scheme(roles, dark),
            shapes = Shapes(shape, shape, shape, RoundedCornerShape(VSTokens.IOS_CARD_RADIUS.dp), RoundedCornerShape(VSTokens.IOS_CARD_RADIUS.dp)),
            typography = Typography(displayLarge=title, displayMedium=title, displaySmall=title, headlineLarge=heading, headlineMedium=heading, headlineSmall=heading,
                titleLarge=title, titleMedium=body.copy(fontWeight=FontWeight.SemiBold), titleSmall=control.copy(fontWeight=FontWeight.SemiBold), bodyLarge=body, bodyMedium=control, bodySmall=caption,
                labelLarge=control, labelMedium=control, labelSmall=caption), content=content)
    }
}
