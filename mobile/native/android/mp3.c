#include <jni.h>
#include <stdint.h>
#include "Mp3Encoder.h"

JNIEXPORT jlong JNICALL Java_com_voicestudio_mobile_local_NativeMp3Bridge_create(JNIEnv *env,jobject object,jbyteArray path,jlong frames,jboolean synthetic) {
    (void)object; if (!path || frames < 1) return 0;
    jsize count=(*env)->GetArrayLength(env,path); if (count < 1 || count > 4096) return 0;
    char name[4097]; (*env)->GetByteArrayRegion(env,path,0,count,(jbyte *)name);
    if ((*env)->ExceptionCheck(env)) return 0;
    for (jsize index=0; index<count; index++) if (!name[index]) return 0;
    name[count]=0;
    return (jlong)(intptr_t)vs_mp3_create(name,24000,(uint64_t)frames,synthetic ? 1 : 0);
}
JNIEXPORT jint JNICALL Java_com_voicestudio_mobile_local_NativeMp3Bridge_write(JNIEnv *env,jobject object,jlong handle,jfloatArray data) {
    (void)object; if (!handle || !data) return -1;
    jsize count=(*env)->GetArrayLength(env,data); if (count < 1 || count > 8192) return -1;
    jfloat *samples=(*env)->GetFloatArrayElements(env,data,NULL); if (!samples) return -1;
    int status=vs_mp3_write((VSMp3 *)(intptr_t)handle,samples,count);
    (*env)->ReleaseFloatArrayElements(env,data,samples,JNI_ABORT); return status;
}
JNIEXPORT jint JNICALL Java_com_voicestudio_mobile_local_NativeMp3Bridge_finish(JNIEnv *env,jobject object,jlong handle) {
    (void)env;(void)object; return vs_mp3_finish((VSMp3 *)(intptr_t)handle);
}
JNIEXPORT void JNICALL Java_com_voicestudio_mobile_local_NativeMp3Bridge_close(JNIEnv *env,jobject object,jlong handle) {
    (void)env;(void)object; vs_mp3_free((VSMp3 *)(intptr_t)handle);
}
