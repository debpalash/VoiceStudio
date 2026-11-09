plugins {
    alias(libs.plugins.android.app) apply false
    alias(libs.plugins.android.library) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
    alias(libs.plugins.kotlin.serialization) apply false
}

subprojects { tasks.withType<Test>().configureEach { maxParallelForks=1; maxHeapSize="1g"; jvmArgs("-XX:ActiveProcessorCount=2") } }
