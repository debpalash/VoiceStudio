package com.voicestudio.mobile.designsystem

import kotlinx.serialization.json.*
import org.junit.Test
import org.junit.Assert.*
import java.io.File

class TokenParityTest {
    @Test fun everyPaletteAppearanceAndRoleMatchesGeneratedContract() {
        val tokens=Json.parseToJsonElement(File(System.getProperty("repository"),"mobile/contract/tokens.json").readText()).jsonObject
        val palettes=tokens.getValue("palettes").jsonArray
        assertEquals(palettes.map {it.jsonObject.getValue("id").jsonPrimitive.content}.toSet(),VSTokens.palettes.map {it.id}.toSet())
        for(palette in palettes) {
            val obj=palette.jsonObject;val id=obj.getValue("id").jsonPrimitive.content
            for(appearance in listOf("light","dark")) {
                val expected=obj.getValue("colors").jsonObject[appearance] as? JsonObject ?: continue
                val actual=VSTheme.roles(id,appearance=="dark")
                assertEquals(VSTokens.roles.toSet(),actual.keys)
                expected.forEach {(role,value) ->
                    val rgba=value.jsonPrimitive.content.removePrefix("#");val argb=(rgba.takeLast(2)+rgba.dropLast(2)).toLong(16)
                    assertEquals("$id $appearance $role",argb,actual.getValue(role))
                }
            }
        }
        assertEquals(VSTheme.roles("default",true),VSTheme.roles("missing",true))
    }
}
