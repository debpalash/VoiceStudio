#include "control.h"
#include "omnivoice.h"
#include <memory>
#include <vector>
extern "C" JNIEXPORT jint JNICALL Java_com_voicestudio_mobile_local_NativeBridge_omniAbi(JNIEnv*,jobject) {return OV_ABI_VERSION;}
extern "C" JNIEXPORT jfloatArray JNICALL Java_com_voicestudio_mobile_local_NativeBridge_cloneAudio(JNIEnv* e,jobject,jstring model,jstring codec,jstring text,jstring refText,jfloatArray reference,jobject control) {
    Utf8 m(e,model),c(e,codec),t(e,text),r(e,refText); Control ctl(e,control);
    ov_log_set([](ov_log_level,const char*,void*){},nullptr);
    ov_init_params init{}; ov_init_default_params(&init); init.model_path=m.chars;init.codec_path=c.chars;init.use_fa=false;
    if(Control::cancelled(&ctl))return nullptr;
    std::unique_ptr<ov_context,decltype(&ov_free)> context(ov_init(&init),ov_free);
    if(!context){fail(e);return nullptr;} ctl.progress(0.2f);
    std::vector<float> ref(e->GetArrayLength(reference));e->GetFloatArrayRegion(reference,0,ref.size(),ref.data());
    ov_tts_params params{};ov_tts_default_params(&params);params.text=t.chars;params.ref_text=r.chars;
    params.ref_audio_24k=ref.data();params.ref_n_samples=ref.size();params.cancel=Control::cancelled;params.cancel_user_data=&ctl;
    ov_audio audio{};auto status=ov_synthesize(context.get(),&params,&audio);
    jfloatArray result=nullptr;
    if(status==OV_STATUS_OK&&!Control::cancelled(&ctl)){result=e->NewFloatArray(audio.n_samples);e->SetFloatArrayRegion(result,0,audio.n_samples,audio.samples);ctl.progress(1.f);}
    else if(status!=OV_STATUS_CANCELLED&&!Control::cancelled(&ctl))fail(e);
    ov_audio_free(&audio);if(ctl.failed)fail(e);return result;
}

extern "C" JNIEXPORT jfloatArray JNICALL Java_com_voicestudio_mobile_local_NativeBridge_designAudio(JNIEnv* e,jobject,jstring model,jstring codec,jstring text,jstring instruction,jlong seed,jobject control) {
    Utf8 m(e,model),c(e,codec),t(e,text),i(e,instruction); Control ctl(e,control);
    ov_log_set([](ov_log_level,const char*,void*){},nullptr);
    ov_init_params init{}; ov_init_default_params(&init);init.model_path=m.chars;init.codec_path=c.chars;init.use_fa=false;
    if(Control::cancelled(&ctl)) return nullptr;
    std::unique_ptr<ov_context,decltype(&ov_free)> context(ov_init(&init),ov_free);
    if(!context){fail(e);return nullptr;} ctl.progress(.2f);
    ov_tts_params params{};ov_tts_default_params(&params);params.text=t.chars;params.instruct=i.chars;params.mg_seed=static_cast<uint64_t>(seed);
    params.cancel=Control::cancelled;params.cancel_user_data=&ctl;
    ov_audio audio{};auto status=ov_synthesize(context.get(),&params,&audio);
    jfloatArray result=nullptr;
    if(status==OV_STATUS_OK&&!Control::cancelled(&ctl)){result=e->NewFloatArray(audio.n_samples);e->SetFloatArrayRegion(result,0,audio.n_samples,audio.samples);ctl.progress(1.f);}
    else if(status!=OV_STATUS_CANCELLED&&!Control::cancelled(&ctl))fail(e);
    ov_audio_free(&audio);if(ctl.failed)fail(e);return result;
}
