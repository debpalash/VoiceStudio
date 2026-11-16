package com.voicestudio.mobile

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.view.ViewGroup
import androidx.activity.ComponentActivity
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.test.*
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.unit.dp
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.semantics.SemanticsProperties
import androidx.core.graphics.Insets
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.test.platform.app.InstrumentationRegistry
import androidx.test.core.app.ApplicationProvider
import com.voicestudio.mobile.designsystem.VoiceStudioTheme
import com.voicestudio.mobile.features.*
import com.voicestudio.mobile.l10n.L10n
import org.junit.*
import org.junit.Assert.*
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import org.robolectric.util.ReflectionHelpers
import org.robolectric.util.ReflectionHelpers.ClassParameter

@RunWith(RobolectricTestRunner::class)
@Config(sdk=[34])
class KeyboardRegressionTest {
    @get:Rule val compose=createAndroidComposeRule<ComponentActivity>()
    @Before fun phoneWindow() {
        // The phone uses touch mode and an edge-to-edge Activity. Without touch
        // mode Android deliberately transfers cleared focus to its first field.
        InstrumentationRegistry.getInstrumentation().setInTouchMode(true)
        compose.runOnUiThread {
            compose.activity.enableEdgeToEdge()
            // Robolectric's Instrumentation updates the window session flag,
            // but does not deliver that transition to an already attached root.
            val root=ReflectionHelpers.callInstanceMethod<Any>(compose.activity.window.decorView,"getViewRootImpl")
            ReflectionHelpers.callInstanceMethod<Boolean>(root,"ensureTouchMode",ClassParameter.from(java.lang.Boolean.TYPE,true))
        }
    }
    private fun content(block:@Composable ()->Unit) {
        val l=L10n.load(ApplicationProvider.getApplicationContext<Context>(),"en")
        compose.setContent {CompositionLocalProvider(LocalL10n provides l) {VoiceStudioTheme {block()}}}
    }
    @Test fun singleLineSubmitAndOutsideTapDismissFocus() {
        content {
            Screen {
                ScreenTitle("mobile.tabs.settings")
                var text by remember {mutableStateOf("")}
                Field("mobile.android.host_label",text,{text=it})
            }
        }
        val field=compose.onNodeWithText("Server label")
        field.performClick().assertIsFocused().performTextInput("Local host")
        compose.runOnIdle {assertTrue("Phone window must be in touch mode",compose.activity.window.decorView.isInTouchMode)}
        field.performImeAction()
        field.assertIsNotFocused()
        field.performClick().assertIsFocused()
        compose.onNodeWithText("Settings").performTouchInput {click()}
        field.assertIsNotFocused()
    }
    @Test fun multilineDoneAndGenerateDismissFocus() {
        var generated=false
        content {
            Screen {
                var text by remember {mutableStateOf("")}
                Field("mobile.speak.placeholder",text,{text=it},multiline=true)
                Action("mobile.speak.generate") {generated=true}
            }
        }
        val editor=compose.onNode(hasSetTextAction())
        editor.performClick().performTextInput("First line\nSecond line")
        compose.onNodeWithText("Done").performClick()
        editor.assertIsNotFocused()
        editor.performClick()
        compose.onNodeWithText("Generate").performScrollTo().performClick()
        editor.assertIsNotFocused()
        assertTrue(generated)
    }
    @Test fun imeShrinksScrollViewportAndPrimaryActionIsReachable() {
        content {
            Screen {
                Field("mobile.speak.placeholder","Text",{},multiline=true)
                Spacer(Modifier.height(900.dp))
                Action("mobile.speak.generate") {}
            }
        }
        val scroller=compose.onNode(hasScrollAction())
        val initialHeight=scroller.fetchSemanticsNode().boundsInRoot.height
        compose.runOnIdle {
            val insets=WindowInsetsCompat.Builder()
                .setInsets(WindowInsetsCompat.Type.ime(),Insets.of(0,0,0,300))
                .setVisible(WindowInsetsCompat.Type.ime(),true).build()
            val content=compose.activity.findViewById<android.view.ViewGroup>(android.R.id.content)
            ViewCompat.dispatchApplyWindowInsets(content.getChildAt(0),insets)
        }
        compose.waitForIdle()
        val resizedHeight=scroller.fetchSemanticsNode().boundsInRoot.height
        assertTrue("IME must resize the scroll viewport: $initialHeight -> $resizedHeight",resizedHeight<initialHeight)
        compose.onNodeWithText("Generate").performScrollTo().assertIsDisplayed()
    }
    @Test fun pickerDialogBackgroundTapDismissesItsOwnSearchFocus() {
        content {ChoicePicker("mobile.speak.engine","test",listOf(Choice("test","Local engine"))) {}}
        compose.onNodeWithText("Engine").performClick()
        val search=compose.onNodeWithText("Search")
        search.performClick().assertIsFocused()
        compose.onNode(hasText("Engine") and hasAnyAncestor(isDialog())).performTouchInput {click()}
        search.assertIsNotFocused()
    }
    @Test @GraphicsMode(GraphicsMode.Mode.NATIVE)
    fun largeManuscriptStaysEditableInsideScrollingScreen() {
        val manuscript="A manuscript line with complete text.\n".repeat(12_000)+"Original ending"
        assertTrue(manuscript.length>100_000)
        var draft=manuscript
        var generated=false
        content {
            Box(Modifier.width(360.dp).height(640.dp)) {
                Screen {
                    var text by remember {mutableStateOf(manuscript)}
                    Field("audiobook.script",text,{text=it;draft=it},multiline=true)
                    Action("mobile.speak.generate") {generated=true}
                }
            }
        }
        val editor=compose.onNode(hasSetTextAction())
        val stored=editor.fetchSemanticsNode().config[SemanticsProperties.EditableText].text
        assertTrue("The complete imported manuscript must remain in the editor",stored==manuscript)
        val bounds=editor.getUnclippedBoundsInRoot()
        assertTrue("The editor must have a finite viewport inside Screen",bounds.bottom-bounds.top<400.dp)
        editor.performClick().performTextInputSelection(TextRange(manuscript.length))
        editor.performTextInput("\nAn edited ending")
        compose.runOnIdle {assertTrue("Editing must preserve every original character",draft==manuscript+"\nAn edited ending")}
        val ending=manuscript.length+"\nAn edited ending".length
        assertEquals(TextRange(ending),editor.fetchSemanticsNode().config[SemanticsProperties.TextSelectionRange])
        compose.onNodeWithText("Done").performClick()
        editor.assertIsNotFocused()
        // No blinking caret or focused-border animation: compare the actual
        // rendered text before/after dragging, while its viewport stays fixed.
        compose.waitForIdle()
        val viewport=editor.getUnclippedBoundsInRoot()
        fun renderedPixels():IntArray {
            val area=editor.fetchSemanticsNode().boundsInRoot
            val width=kotlin.math.ceil(area.width).toInt()
            val height=kotlin.math.ceil(area.height).toInt()
            val pixels=IntArray(width*height)
            compose.runOnIdle {
                // Robolectric renders real Compose drawing to a native Canvas,
                // but does not dispatch PixelCopy's window-redraw callbacks.
                val host=compose.activity.findViewById<ViewGroup>(android.R.id.content).getChildAt(0)
                val bitmap=Bitmap.createBitmap(width,height,Bitmap.Config.ARGB_8888)
                try {
                    val canvas=Canvas(bitmap)
                    canvas.translate(-area.left,-area.top)
                    host.draw(canvas)
                    bitmap.getPixels(pixels,0,width,0,0,width,height)
                } finally {bitmap.recycle()}
            }
            return pixels
        }
        val tailPixels=renderedPixels()
        editor.performTouchInput {swipeDown()}
        compose.waitForIdle()
        editor.assertIsNotFocused()
        assertEquals(viewport,editor.getUnclippedBoundsInRoot())
        val scrolledPixels=renderedPixels()
        assertFalse("Dragging must move the full manuscript within the fixed editor",tailPixels.contentEquals(scrolledPixels))
        compose.runOnIdle {assertTrue(draft==manuscript+"\nAn edited ending")}
        compose.onNodeWithText("Generate").performScrollTo().assertIsDisplayed().performClick()
        assertTrue(generated)
    }
}
