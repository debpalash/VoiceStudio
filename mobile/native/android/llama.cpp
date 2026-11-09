#include "control.h"
#include "llama.h"
#include <memory>
#include <vector>
#include <algorithm>
extern "C" JNIEXPORT jint JNICALL Java_com_voicestudio_mobile_local_NativeBridge_llamaDevices(JNIEnv*,jobject) {return static_cast<jint>(llama_max_devices());}
extern "C" JNIEXPORT jbyteArray JNICALL Java_com_voicestudio_mobile_local_NativeBridge_translateText(JNIEnv* e,jobject,jstring path,jstring prompt,jboolean preformatted,jobject control) {
    Utf8 p(e,path),t(e,prompt);Control ctl(e,control);
    llama_log_set([](ggml_log_level,const char*,void*){},nullptr);
    llama_backend_init();
    auto mp=llama_model_default_params();mp.n_gpu_layers=0;mp.progress_callback=[](float progress,void* data){auto c=static_cast<Control*>(data);c->progress(progress*.1f);return !Control::cancelled(data);};mp.progress_callback_user_data=&ctl;
    std::unique_ptr<llama_model,decltype(&llama_model_free)> model(llama_model_load_from_file(p.chars,mp),llama_model_free);
    if(!model){fail(e);return nullptr;}ctl.progress(.1f);
    auto cp=llama_context_default_params();cp.n_ctx=2048;cp.n_batch=256;cp.n_threads=2;cp.n_threads_batch=2;
    std::unique_ptr<llama_context,decltype(&llama_free)> context(llama_init_from_model(model.get(),cp),llama_free);
    if(!context){fail(e);return nullptr;}
    llama_set_abort_callback(context.get(),Control::cancelled,&ctl);
    const auto vocab=llama_model_get_vocab(model.get());
    std::string input=t.chars;
    if(!preformatted) {
        const auto tmpl=llama_model_chat_template(model.get(),nullptr);
        llama_chat_message message{"user",t.chars};
        int size=llama_chat_apply_template(tmpl,&message,1,true,nullptr,0);
        if(size<0){fail(e);return nullptr;}
        std::vector<char> formatted(size+1);
        size=llama_chat_apply_template(tmpl,&message,1,true,formatted.data(),formatted.size());
        input.assign(formatted.data(),size);
    }
    int count=-llama_tokenize(vocab,input.data(),input.size(),nullptr,0,true,true);
    if(count<=0||count>1500){fail(e);return nullptr;}
    std::vector<llama_token> tokens(count);
    llama_tokenize(vocab,input.data(),input.size(),tokens.data(),count,true,true);
    for(int offset=0;offset<count;offset+=256) {
        if(Control::cancelled(&ctl))return nullptr;
        if(llama_decode(context.get(),llama_batch_get_one(tokens.data()+offset,std::min(256,count-offset)))){if(!Control::cancelled(&ctl))fail(e);return nullptr;}
    }
    std::unique_ptr<llama_sampler,decltype(&llama_sampler_free)> sampler(llama_sampler_init_greedy(),llama_sampler_free);
    std::string result;
    for(int i=0;i<512&&!Control::cancelled(&ctl);++i) {
        auto token=llama_sampler_sample(sampler.get(),context.get(),-1);
        if(llama_vocab_is_eog(vocab,token))break;
        char small[256];int n=llama_token_to_piece(vocab,token,small,sizeof(small),0,true);
        if(n<0){std::vector<char> large(-n);n=llama_token_to_piece(vocab,token,large.data(),large.size(),0,true);if(n>0)result.append(large.data(),n);}
        else result.append(small,n);
        ctl.progress(.2f+.8f*static_cast<float>(i+1)/512.f);
        if(llama_decode(context.get(),llama_batch_get_one(&token,1))){if(!Control::cancelled(&ctl))fail(e);return nullptr;}
    }
    if(ctl.failed){fail(e);return nullptr;}
    if(Control::cancelled(&ctl))return nullptr;
    auto bytes=e->NewByteArray(result.size());e->SetByteArrayRegion(bytes,0,result.size(),reinterpret_cast<const jbyte*>(result.data()));ctl.progress(1.f);return bytes;
}
