plugins {
    alias(libs.plugins.android.library)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.serialization)
}
android {
    namespace = "com.voicestudio.mobile.local"
    compileSdk = 36
    ndkVersion = "27.1.12297006"
    defaultConfig {
        minSdk = 29
        // Native code finds these classes and methods by name; keep them if the app is shrunk.
        consumerProguardFiles("consumer-rules.pro")
    }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
}
kotlin { jvmToolchain(21); compilerOptions { jvmTarget.set(org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17) } }
val nativeRoot = rootDir.resolve("../native/android")
val fetchRuntimes by tasks.registering(Exec::class) {
    inputs.files(nativeRoot.resolve("fetch.py"), nativeRoot.resolve("source-checksums.json"), nativeRoot.resolve("../pins.json"))
    outputs.files(nativeRoot.resolve("build/downloads/sherpa.aar"), layout.buildDirectory.file("sherpa/classes.jar"))
    outputs.dir(nativeRoot.resolve("build/sources"))
    commandLine("python3", nativeRoot.resolve("fetch.py"))
    doLast {
        copy { from(zipTree(nativeRoot.resolve("build/downloads/sherpa.aar"))); include("classes.jar"); into(layout.buildDirectory.dir("sherpa")) }
    }
}
tasks.named("preBuild") { dependsOn(fetchRuntimes) }
dependencies {
    implementation("com.tom-roush:pdfbox-android:2.0.27.0")
    compileOnly(files(layout.buildDirectory.file("sherpa/classes.jar")).builtBy(fetchRuntimes))
    implementation(libs.coroutines)
    implementation(libs.serialization)
    implementation(libs.okhttp)
    implementation(libs.core)
    api(project(":core:network"))
    testImplementation("net.sf.kxml:kxml2:2.3.0")
    testImplementation(libs.junit)
    testImplementation(libs.coroutines.test)
    testImplementation(libs.mockwebserver)
}
