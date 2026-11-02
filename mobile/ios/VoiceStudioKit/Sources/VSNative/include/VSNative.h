#ifndef VS_NATIVE_H
#define VS_NATIVE_H
#include <stdint.h>
#include "BookArchive.h"
#include "Mp3Encoder.h"
#ifdef __cplusplus
extern "C" {
#endif
typedef struct VSCancel VSCancel;
VSCancel *vs_cancel_create(void);
void vs_cancel_set(VSCancel *token);
int vs_cancelled(VSCancel *token);
float vs_progress(VSCancel *token);
void vs_cancel_free(VSCancel *token);
typedef struct {
    const char *library;
    const char *family;
    const char *model;
    const char *encoder;
    const char *decoder;
    const char *joiner;
    const char *tokens;
    const char *voices;
    const char *data_dir;
    const char *lexicon;
    const char *rule_fsts;
    const char *language;
    const char *codec;
    int32_t threads;
    int32_t sample_rate;
    int32_t cpu_only; // 0 preserves foreground acceleration, 1 forbids GPU allocation.
    const char *duration_predictor;
    const char *text_encoder;
    const char *vector_estimator;
    const char *vocoder;
    const char *tts_config;
    const char *unicode_indexer;
    const char *lm_flow;
    const char *lm_main;
    const char *text_conditioner;
    const char *vocab;
    const char *token_scores;
    int32_t num_steps;
} VSConfig;
typedef struct { float *samples; int32_t count; int32_t sample_rate; } VSAudio;
void vs_audio_free(VSAudio *audio);
void vs_text_free(char *text);
int vs_runtime_available(const char *runtime, const char *library);
int vs_cpu_runtime_available(const char *runtime, const char *library);
int vs_synthesize(const VSConfig *config, const char *text, int32_t speaker, float speed, VSCancel *cancel, VSAudio *audio);
int vs_sherpa_clone(const VSConfig *config,const char *text,const char *reference_text,const float *reference,int32_t count,int32_t rate,VSCancel *cancel,VSAudio *audio);
int vs_transcribe(const VSConfig *config, const float *samples, int32_t count, int32_t rate, VSCancel *cancel, char **text);
/// token=1: one recognised token with window-relative start/end (end<=start when untimed); token=0: a whole window.
typedef struct { double start; double end; char *text; int32_t window; int32_t token; } VSSegment;
typedef struct { VSSegment *segments; int32_t count; } VSSegments;
void vs_segments_free(VSSegments *segments);
int vs_transcribe_segments(const VSConfig *config, const float *samples, int32_t count, int32_t rate, VSCancel *cancel, VSSegments *segments);
typedef struct VSStream VSStream;
VSStream *vs_stream_create(const VSConfig *config);
int vs_stream_accept(VSStream *stream, const float *samples, int32_t count, int32_t rate, int finished, VSCancel *cancel, char **text, int *endpoint);
void vs_stream_free(VSStream *stream);
int vs_clone(const VSConfig *config, const char *text, const char *reference_text, const float *reference, int32_t count, VSCancel *cancel, VSAudio *audio);
int vs_design(const VSConfig *config, const char *text, const char *instruct, uint64_t seed, VSCancel *cancel, VSAudio *audio);
int vs_translate(const VSConfig *config, const char *prompt, int32_t max_tokens, VSCancel *cancel, char **text);
#ifdef __cplusplus
}
#endif
#endif
