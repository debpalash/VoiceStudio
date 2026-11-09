package com.voicestudio.mobile.data

import org.junit.Assert.assertEquals
import org.junit.Test

class StoreJsonTest {
    @Test fun preferencesFromAnOlderOrNewerBuildKeepKnownFields() {
        val saved = """{"consent":false,"activeUrl":"http://127.0.0.1:3900/","darkPalette":"signal","removedField":1,"hosts":[{"url":"http://127.0.0.1:3900/","label":"Desk","lastSeen":1,"future":"x"}]}"""
        val prefs = StoreJson.decodeFromString(Preferences.serializer(), saved)
        assertEquals(false, prefs.consent)
        assertEquals("http://127.0.0.1:3900/", prefs.activeUrl)
        assertEquals("signal", prefs.darkPalette)
        assertEquals("Desk", prefs.hosts.single().label)
    }
}
