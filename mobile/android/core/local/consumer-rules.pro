# JNI looks these up by name and exact signature (see NativeTtsCallback).
-keep class com.k2fsa.sherpa.onnx.** { *; }
-keep class com.voicestudio.mobile.local.NativeControl { *; }
-keep class com.voicestudio.mobile.local.NativeTtsCallback { *; }

# Portable source-built MP3 exporter JNI.
-keep class com.voicestudio.mobile.local.NativeMp3Bridge { *; }
