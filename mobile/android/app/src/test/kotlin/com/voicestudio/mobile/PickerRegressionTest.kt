package com.voicestudio.mobile

import android.content.Context
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createComposeRule
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.layout.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.TextLayoutResult
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.designsystem.*
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.l10n.L10n
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class PickerRegressionTest {
    @get:Rule val compose=createComposeRule()
    private val context get()=ApplicationProvider.getApplicationContext<Context>()
    private fun content(block:@Composable ()->Unit) {
        val l=L10n.load(context,"en")
        compose.setContent {CompositionLocalProvider(LocalL10n provides l) {VoiceStudioTheme {block()}}}
    }
    @Test @GraphicsMode(GraphicsMode.Mode.NATIVE)
    fun longPickerValuesEllipsizeWithoutHidingAccessibleNames() {
        val title="OuteTTS (MLX Audio, Apple Silicon) with a long engine display name"
        content {
            Box(Modifier.width(320.dp)) {
                ChoicePicker("mobile.speak.engine","oute",listOf(Choice("oute",title))) {}
            }
        }
        val layouts=mutableListOf<TextLayoutResult>()
        compose.onNodeWithText(title,useUnmergedTree=true).performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.GetTextLayoutResult) {it(layouts)}
        assertEquals(1,layouts.single().lineCount)
        assertTrue(layouts.single().isLineEllipsized(0))
        compose.onNodeWithContentDescription(L10n.load(context,"en").t("mobile.android.selection",mapOf("label" to "Engine","value" to title))).assertExists()
        compose.onNodeWithText("Engine").performClick()
        layouts.clear()
        compose.onNode(hasText(title) and hasAnyAncestor(isDialog()),useUnmergedTree=true).performSemanticsAction(androidx.compose.ui.semantics.SemanticsActions.GetTextLayoutResult) {it(layouts)}
        assertTrue(layouts.single().lineCount<=2)
    }
    @Test fun enginePickerShowsDisabledReasonsAndSearchesNames() {
        var value="active"
        content {
            ChoicePicker("mobile.speak.engine",value,listOf(
                Choice("active","VoiceStudio"), Choice("kitten","KittenTTS",false,"Cannot use reference audio"))) {value=it}
        }
        compose.onNodeWithText("Engine").performClick()
        compose.onNodeWithText("Cannot use reference audio").assertExists()
        compose.onNodeWithText("KittenTTS").assertIsNotEnabled()
        compose.onNodeWithText("Search").performTextInput("Kitten")
        compose.onNode(hasText("VoiceStudio") and hasAnyAncestor(isDialog())).assertDoesNotExist()
        compose.onNodeWithText("KittenTTS").assertExists()
        assertEquals("active",value)
    }
    @Test fun dubPickerSearchConfirmAndRemoveChip() {
        val choices=LanguageCatalog.load(context).dubChoices()
        assertFalse(choices.any {it.second=="Auto"})
        content {
            var selected by remember {mutableStateOf(emptyList<String>())}
            MultiPicker("mobile.dub.target_languages",selected,choices) {selected=it}
        }
        compose.onNodeWithText("Target languages").performClick()
        compose.onNodeWithText("Search").performTextInput("French")
        compose.onNode(hasText("French") and !hasSetTextAction()).performClick()
        compose.onNodeWithText("Confirm").performClick()
        compose.onNodeWithContentDescription("Remove French").assertExists().performClick()
        compose.onNodeWithContentDescription("Remove French").assertDoesNotExist()
    }
    @Test fun themeUsesTokenControlAndCardRadii() {
        var control:Any?=null
        var card:Any?=null
        content {
            control=MaterialTheme.shapes.small
            card=MaterialTheme.shapes.large
            Action("mobile.speak.generate",onClick={})
        }
        compose.waitForIdle()
        assertEquals(RoundedCornerShape(VSTokens.IOS_FIELD_RADIUS.dp),control)
        assertEquals(RoundedCornerShape(VSTokens.IOS_CARD_RADIUS.dp),card)
    }
    @Test fun searchWithoutMatchesExplainsEmptyChoiceAndMultiChoiceLists() {
        content {ChoicePicker("mobile.speak.voice","one",listOf(Choice("one","Sample voice"))) {}}
        compose.onNodeWithText("Voice").performClick()
        compose.onNodeWithText("Search").performTextInput("missing")
        compose.onNodeWithText("No matches for “missing”.").assertExists()
    }
    @Test @GraphicsMode(GraphicsMode.Mode.NATIVE)
    fun pickerValueUsesSpaceLeftByIntrinsicLabel() {
        content {Box(Modifier.width(360.dp)) {Picker("mobile.speak.voice","default",listOf("default" to "Default voice (engine preset)")) {}}}
        val label=compose.onNodeWithText("Voice",useUnmergedTree=true).fetchSemanticsNode().boundsInRoot
        val value=compose.onNodeWithText("Default voice (engine preset)",useUnmergedTree=true).fetchSemanticsNode().boundsInRoot
        assertTrue("The value must get the unused label-column space: $label $value",value.width>label.width*2)
    }

}
