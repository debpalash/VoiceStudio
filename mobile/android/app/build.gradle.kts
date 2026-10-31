import groovy.json.JsonSlurper
plugins {
    alias(libs.plugins.android.app)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}
val repository = rootDir.resolve("../..")
val appVersion = (JsonSlurper().parse(repository.resolve("package.json")) as Map<*, *>)["version"].toString()
// Play accepts any versionName, but the code must be numeric; a beta's -beta.N maps to its X.Y.Z.
val (major, minor, patch) = Regex("""(\d+)\.(\d+)\.(\d+)(?:-beta\.\d+)?""").matchEntire(appVersion)?.destructured
    ?: error("Unsupported app version for Android: $appVersion")
// Play uploads pass a new, increasing code per upload; local builds derive it from the version.
val appVersionCode = providers.environmentVariable("VOICESTUDIO_ANDROID_VERSION_CODE").orNull?.toInt()
    ?: (major.toInt() * 1000000 + minor.toInt() * 1000 + patch.toInt())
// The Play upload key, supplied only by the Play workflow; other builds stay debug-signed.
val uploadKeystore = providers.environmentVariable("VOICESTUDIO_ANDROID_KEYSTORE").orNull
// Content reports (mobile/contract/SPEC.md) go to production unless VOICESTUDIO_CONTENT_REPORTS_URL,
// from the environment or mobile/.env, points this build elsewhere (e.g. https://dev-appapi.voicestudio.sh).
val mobileDotenv = repository.resolve("mobile/.env").takeIf { it.isFile }?.readLines().orEmpty()
    .mapNotNull { Regex("""\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*""").matchEntire(it)?.destructured }
    .associate { (key, value) -> key to value.trim('"', '\'') }
val contentReportsUrl = (providers.environmentVariable("VOICESTUDIO_CONTENT_REPORTS_URL").orNull?.takeIf { it.isNotBlank() }
    ?: mobileDotenv["VOICESTUDIO_CONTENT_REPORTS_URL"]).orEmpty().trim().trimEnd('/').ifEmpty { "https://appapi.voicestudio.sh" }
require(Regex("""https://[A-Za-z0-9.-]+(?::\d+)?(?:/[A-Za-z0-9._~/-]*)?""").matches(contentReportsUrl)) {
    "VOICESTUDIO_CONTENT_REPORTS_URL must be an https URL, got $contentReportsUrl"
}
android {
    namespace = "com.voicestudio.mobile"
    compileSdk = 36
    ndkVersion = "27.1.12297006"
    defaultConfig {
        // Store identity (voicestudio.sh); the Kotlin namespace stays com.voicestudio.mobile.
        applicationId = "sh.voicestudio.mobile"
        minSdk = 29
        ndk { abiFilters += listOf("arm64-v8a", "x86_64") }
        targetSdk = 36
        versionCode = appVersionCode
        versionName = appVersion
        buildConfigField("String", "CONTENT_REPORTS_URL", "\"$contentReportsUrl\"")
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
    }
    if (uploadKeystore != null) {
        signingConfigs.create("upload") {
            storeFile = file(uploadKeystore)
            storePassword = providers.environmentVariable("VOICESTUDIO_ANDROID_KEYSTORE_PASSWORD").get()
            keyAlias = providers.environmentVariable("VOICESTUDIO_ANDROID_KEY_ALIAS").get()
            keyPassword = providers.environmentVariable("VOICESTUDIO_ANDROID_KEY_PASSWORD").get()
        }
        buildTypes.getByName("release").signingConfig = signingConfigs.getByName("upload")
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    testOptions { unitTests.isIncludeAndroidResources = true }
    sourceSets["main"].assets.srcDir(rootDir.resolve("../native/android/build/assets"))
    sourceSets["main"].jniLibs.srcDir(rootDir.resolve("../native/android/build/jniLibs"))
    sourceSets["main"].assets.srcDir(layout.buildDirectory.dir("generated/catalogs"))
    sourceSets["main"].res.srcDir(layout.buildDirectory.dir("generated/catalogResources"))
    sourceSets["main"].res.srcDir(layout.buildDirectory.dir("generated/brandResources"))
}
kotlin { jvmToolchain(21); compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }
val bundleCatalogs by tasks.registering {
    val catalogs = repository.resolve("electron/src/renderer/src/i18n/locales")
    val contracts = repository.resolve("mobile/contract")
    inputs.dir(contracts.resolve("tts"))
    inputs.dir(catalogs)
    inputs.file(repository.resolve("electron/public/signal-field.webp"))
    inputs.file(repository.resolve("electron/src/shared/languages.json"))
    inputs.file(repository.resolve("LICENSE"))
    inputs.dir(repository.resolve("electron/src/renderer/src/features/home/contributors"))
    inputs.files(fileTree(contracts) { include("strings*.en.json", "tokens.json", "openapi.json", "models.json", "design.json", "community-gallery.json", "maintainer-cards.json") })
    outputs.dir(layout.buildDirectory.dir("generated/catalogs"))
    outputs.dir(layout.buildDirectory.dir("generated/catalogResources"))
    doLast {
        val assets = layout.buildDirectory.dir("generated/catalogs").get().asFile
        assets.deleteRecursively()
        assets.mkdirs()
        repository.resolve("electron/public/signal-field.webp").copyTo(assets.resolve("signal-field.webp"), overwrite=true)
        repository.resolve("electron/src/shared/languages.json").copyTo(assets.resolve("languages.json"), overwrite=true)
        val avatars = repository.resolve("electron/src/renderer/src/features/home/contributors")
        avatars.listFiles()!!.forEach { it.copyTo(assets.resolve("contributors/" + it.name).apply { parentFile.mkdirs() }, overwrite=true) }
        val out = assets.resolve("locales").apply { mkdirs() }
        assets.resolve("licenses").mkdirs()
        repository.resolve("LICENSE").copyTo(assets.resolve("licenses/VoiceStudio-AGPL-3.0.txt"), overwrite=true)
        catalogs.listFiles()!!.filter { it.extension == "json" }.forEach { it.copyTo(out.resolve(it.name), overwrite = true) }
        contracts.listFiles()!!.filter { it.name.startsWith("strings") && it.name.endsWith(".en.json") }.forEach { it.copyTo(assets.resolve(it.name), overwrite = true) }
        contracts.resolve("models.json").copyTo(assets.resolve("models.json"), overwrite = true)
        contracts.resolve("tts").copyRecursively(assets.resolve("tts"), overwrite=true)
        contracts.resolve("design.json").copyTo(assets.resolve("design.json"), overwrite = true)
        contracts.resolve("community-gallery.json").copyTo(assets.resolve("community-gallery.json"), overwrite = true)
        contracts.resolve("maintainer-cards.json").copyTo(assets.resolve("maintainer-cards.json"), overwrite = true)
        contracts.resolve("tokens.json").copyTo(assets.resolve("tokens.json"), overwrite = true)
        val en = JsonSlurper().parse(catalogs.resolve("en.json")) as Map<*, *>
        val label = (en["app"] as Map<*, *>)["name"].toString()
        val res = layout.buildDirectory.dir("generated/catalogResources/values").get().asFile.apply { mkdirs() }
        res.resolve("strings.xml").writeText("<resources><string name=\"app_name\">$label</string></resources>")
    }
}
tasks.named("preBuild") { dependsOn(bundleCatalogs) }
tasks.register("printAppVersion") { doLast { println("versionName=$appVersion versionCode=$appVersionCode") } }
dependencies {
    implementation(files(rootDir.resolve("../native/android/build/downloads/sherpa.aar")).builtBy(":core:local:fetchRuntimes"))
    implementation(project(":features"))
    implementation(libs.activity)
    implementation(libs.lifecycle)
    implementation(libs.viewmodel)
    implementation(libs.navigation)
    implementation(libs.coroutines)
    implementation(libs.serialization)
    implementation(platform(libs.compose.bom))
    implementation(libs.compose.ui)
    implementation(libs.compose.foundation)
    implementation(libs.compose.material)
    implementation(libs.compose.icons)
    testImplementation(libs.junit)
    testImplementation(libs.robolectric)
    testImplementation(libs.test.core)
    testImplementation(libs.compose.test)
    testImplementation(libs.mockwebserver)
    testImplementation(libs.mockito)
    testImplementation(libs.media)
    testImplementation(libs.coroutines.test)
    testImplementation(libs.work.testing)
    testImplementation(libs.datastore)
    androidTestImplementation(libs.test.runner)
    androidTestImplementation(libs.test.ext.junit)
    androidTestImplementation(libs.test.core)
    androidTestImplementation(platform(libs.compose.bom))
    androidTestImplementation(libs.compose.test)
    debugImplementation(libs.compose.test.manifest)
}

tasks.withType<Test>().configureEach { systemProperty("androidRoot", rootDir.absolutePath) }

// Resolve the SDK through Gradle, then let Robolectric read it offline. Its
// default Maven resolver writes a lock in user.home, outside sandbox roots.
val robolectricSdk by configurations.creating
 dependencies { robolectricSdk(libs.robolectric.sdk) }
val prepareRobolectricSdk by tasks.registering(Sync::class) {
    from(robolectricSdk)
    into(layout.buildDirectory.dir("robolectric-sdk"))
}
tasks.withType<Test>().configureEach {
    dependsOn(prepareRobolectricSdk)
    systemProperty("robolectric.offline", "true")
    systemProperty("robolectric.dependency.dir", layout.buildDirectory.dir("robolectric-sdk").get().asFile.absolutePath)
}

val buildNativeRuntimes by tasks.registering(Exec::class) {
    dependsOn(":core:local:fetchRuntimes")
    val nativeRoot = rootDir.resolve("../native/android")
    inputs.files(fileTree(nativeRoot) { include("*.cpp", "*.c", "*.h", "*.json", "*.py", "*.sh", "CMakeLists.txt") }, nativeRoot.resolve("../pins.json"))
    inputs.files(fileTree("src/main/assets/licenses") { include("native-*") })
    inputs.files(fileTree(rootDir.resolve("../ios/VoiceStudioKit/Sources/VSNative")) { include("Mp3Encoder.c","include/Mp3Encoder.h","Lame/**") })
    inputs.files(fileTree(nativeRoot.resolve("../mp3")) { include("*.py") })
    outputs.dir(nativeRoot.resolve("build/jniLibs"))
    outputs.dir(nativeRoot.resolve("build/assets"))
    commandLine("bash", nativeRoot.resolve("build.sh"), "--skip-fetch")
}
tasks.named("preBuild") { dependsOn(buildNativeRuntimes) }

val generateBrandResources by tasks.registering(Exec::class) {
    val generator = rootDir.resolve("scripts/generate_brand_resources.py")
    inputs.files(generator, repository.resolve("docs/logo.svg"), repository.resolve("mobile/contract/tokens.json"),
        repository.resolve("scripts/prepare_mobile_contributors.py"),
        repository.resolve("electron/src/renderer/src/components/brand-icons.tsx"))
    outputs.dir(layout.buildDirectory.dir("generated/brandResources"))
    commandLine("python3", generator, "--output", layout.buildDirectory.dir("generated/brandResources").get().asFile)
}
tasks.named("preBuild") { dependsOn(generateBrandResources) }
