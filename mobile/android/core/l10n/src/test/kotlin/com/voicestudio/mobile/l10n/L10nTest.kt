package com.voicestudio.mobile.l10n

import kotlinx.serialization.json.*
import org.junit.Assert.*
import org.junit.Test

class L10nTest {
    private fun obj(value:String)=Json.parseToJsonElement(value).jsonObject
    @Test fun nestedInterpolationPluralAndFallback() {
        val catalogs=mapOf("en" to obj("""{"greeting":"Hello {{name}}", "count_one":"{{count}} item", "count_other":"{{count}} items", "nested":{"label":"English"}}"""),"fr" to obj("""{"nested":{"label":"French"}}"""),"fr-CA" to obj("""{"greeting":"Bonjour {{name}}"}"""))
        val l=L10n(catalogs,"fr-CA")
        assertEquals("Bonjour Ada",l.t("greeting",mapOf("name" to "Ada")))
        assertEquals("French",l.t("nested.label"))
        assertEquals("1 item",l.t("count",mapOf("count" to 1)))
        assertEquals("3 items",l.t("count",mapOf("count" to 3)))
        assertEquals("missing.key",l.t("missing.key"))
        assertEquals("Hello Ada",L10n(catalogs,"de-DE").t("greeting",mapOf("name" to "Ada")))
    }
    @Test fun pluralCategoriesAndCaseInsensitiveLocale() {
        val l=L10n(mapOf("en" to obj("""{"count_one":"one","count_other":"other"}"""),"ru" to obj("""{"count_one":"one","count_few":"few","count_many":"many","count_other":"other"}""")),"ru-RU")
        assertEquals("few",l.t("count",mapOf("count" to 2)));assertEquals("many",l.t("count",mapOf("count" to 11)))
    }
    @Test fun chineseScriptsAndRegionsResolveBundledCatalogsBeforeEnglish() {
        val catalogs=mapOf("en" to obj("""{"label":"English"}"""),
            "zh-CN" to obj("""{"label":"Simplified"}"""), "zh-TW" to obj("""{"label":"Traditional"}"""))
        for(tag in listOf("zh-Hans", "zh-Hans-CN", "zh_Hans_SG", "ZH-hans-cn"))
            assertEquals(tag,"Simplified",L10n(catalogs,tag).t("label"))
        for(tag in listOf("zh-Hant", "zh-Hant-TW", "zh-Hant-HK", "zh-HK", "zh-MO", "zh_HK"))
            assertEquals(tag,"Traditional",L10n(catalogs,tag).t("label"))
        assertEquals("Simplified",L10n(catalogs,"zh-CN").t("label"))
        assertEquals("Traditional",L10n(catalogs,"zh-TW").t("label"))
    }

    @Test fun loaderReadsOnlyFallbackCatalogsAndParsesEachOnce() {
        val reads=mutableListOf<String>()
        val loader=L10n.Companion.CatalogLoader(read={name ->
            reads.add(name)
            when(name) {
                "locales/fr.json" -> obj("""{"label":"French"}""")
                "locales/zh-TW.json" -> obj("""{"label":"Traditional"}""")
                "locales/en.json" -> obj("""{"label":"English","base":"Base"}""")
                "strings.mobile.en.json" -> obj("""{"mobile":{"label":"Shared"}}""")
                "strings.android-additions.en.json" -> obj("""{"mobile":{"label":"Android"}}""")
                else -> error("Unneeded catalog read: $name")
            }
        },additions=listOf("strings.mobile.en.json","strings.android-additions.en.json"))
        val french=loader.load("fr-CA")
        assertEquals("French",french.t("label"))
        assertEquals("Base",french.t("base"))
        assertEquals("Android",french.t("mobile.label"))
        assertEquals(listOf("locales/fr.json","locales/en.json","strings.mobile.en.json","strings.android-additions.en.json"),reads)
        assertSame(french,loader.load("fr_CA"))
        assertEquals("French",loader.load("fr-FR").t("label"))
        assertEquals("Traditional",loader.load("zh-Hant-HK").t("label"))
        assertSame(loader.load("zh-TW"),loader.load("zh-HK"))
        assertEquals(5,reads.size)
        assertEquals(reads.distinct(),reads)
    }

}
