package com.voicestudio.mobile

import org.junit.Assert.*
import org.junit.Test
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

class BrandResourcesTest {
    @Test fun adaptiveAndLaunchArtworkUseCanonicalLogoPathsAndCanvas() {
        val root=File(requireNotNull(System.getProperty("androidRoot")))
        val factory=DocumentBuilderFactory.newInstance().apply {isNamespaceAware=true}
        fun paths(file:File)=factory.newDocumentBuilder().parse(file).getElementsByTagName("path").let {nodes ->
            (0 until nodes.length).map {index ->
                val item=nodes.item(index) as org.w3c.dom.Element
                item.getAttribute("d").ifBlank {item.getAttributeNS("http://schemas.android.com/apk/res/android","pathData")}
            }
        }
        val canonical=paths(root.resolve("../../docs/logo.svg"))
        assertEquals(2,canonical.size)
        val generated=root.resolve("app/build/generated/brandResources")
        for(name in listOf("vs_logo","vs_launcher_foreground","vs_launcher_monochrome")) {
            assertEquals(canonical,paths(generated.resolve("drawable/$name.xml")))
        }
        val adaptive=generated.resolve("mipmap-anydpi/ic_launcher.xml").readText()
        assertTrue(adaptive.contains("<monochrome"))
        assertTrue(adaptive.contains("@color/vs_canvas"))
        val colors=generated.resolve("values/brand_colors.xml").readText()
        val tokens=root.resolve("../contract/tokens.json").readText()
        val rgba=Regex("\\\"dark\\\"\\s*:\\s*\\{\\s*\\\"canvas\\\"\\s*:\\s*\\\"(#[a-fA-F0-9]{8})").find(tokens)!!.groupValues[1]
        assertTrue(colors.contains("#"+rgba.substring(7)+rgba.substring(1,7)))
        assertTrue(root.resolve("app/src/main/AndroidManifest.xml").readText().contains("@mipmap/ic_launcher"))
        assertTrue(root.resolve("app/src/main/res/values-v31/styles.xml").readText().contains("@drawable/vs_launcher_foreground"))
    }
}
