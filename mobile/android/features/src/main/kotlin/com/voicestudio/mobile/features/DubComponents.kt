package com.voicestudio.mobile.features

import androidx.compose.animation.core.*
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.PathMeasure
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.clearAndSetSemantics
import androidx.compose.ui.semantics.role
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.voicestudio.mobile.designsystem.*
import kotlin.math.PI
import kotlin.math.max
import kotlin.math.sin

/**
 * Vector dub mark: the frame draws itself once, then the waveform breathes.
 * Infinite transitions stop when the system animation scale is 0 (Reduce Motion).
 */
@Composable fun DubGlyph(size: Dp = 72.dp, active: Boolean = false) {
    val accent = role("messageAction")
    val phase by rememberInfiniteTransition(label = "dub-glyph").animateFloat(0f, (2 * PI).toFloat(),
        infiniteRepeatable(tween(if (active) 1050 else 2600, easing = LinearEasing)), label = "phase")
    val drawn = remember { Animatable(0f) }
    LaunchedEffect(Unit) { drawn.animateTo(1f, tween(900, easing = FastOutSlowInEasing)) }
    val base = remember { floatArrayOf(.35f, .62f, 1f, .62f, .35f) }
    Canvas(Modifier.size(size).clearAndSetSemantics {}) {
        val w = this.size.width; val h = this.size.height
        val corner = CornerRadius(w * .3f)
        drawRoundRect(accent.copy(alpha = .10f), cornerRadius = corner)
        val outline = Path().apply { addRoundRect(RoundRect(0f, 0f, w, h, corner)) }
        val measure = PathMeasure().apply { setPath(outline, false) }
        val partial = Path()
        measure.getSegment(0f, measure.length * drawn.value, partial, true)
        drawPath(partial, accent.copy(alpha = .85f), style = Stroke(max(1.5.dp.toPx(), w * .035f), cap = StrokeCap.Round))
        val areaW = w * .46f; val areaH = h * .4f
        val barW = areaW / 9f
        for (index in base.indices) {
            val wave = .72f + .28f * sin(phase * (if (active) 2f else 1f) + index * .9f)
            val barH = areaH * base[index] * wave
            drawRoundRect(accent, Offset((w - areaW) / 2f + barW * index * 2f, (h - barH) / 2f), Size(barW, barH), CornerRadius(barW / 2f))
        }
    }
}

/** The one big call to action on Dub: pick the media to dub. */
@Composable fun DubSourceCard(enabled: Boolean = true, onClick: () -> Unit) {
    val accent = role("messageAction")
    val shape = MaterialTheme.shapes.large
    Surface(shape = shape, color = role("surface"), modifier = Modifier.fillMaxWidth().heightIn(min = 168.dp)
        .drawBehind {
            val stroke = Stroke(1.5.dp.toPx(), pathEffect = PathEffect.dashPathEffect(floatArrayOf(7.dp.toPx(), 7.dp.toPx())))
            drawRoundRect(accent.copy(alpha = .35f), cornerRadius = CornerRadius(16.dp.toPx()), style = stroke)
        }
        .clickable(enabled = enabled, onClick = onClick).semantics { role = Role.Button }) {
        Column(Modifier.fillMaxWidth().padding(VSTheme.inset), horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.spacedBy(VSTheme.gap, Alignment.CenterVertically)) {
            DubGlyph(76.dp)
            Text(tr("mobile.dub.pick_media"), style = MaterialTheme.typography.titleMedium)
        }
    }
}
