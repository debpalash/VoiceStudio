#include "include/Mp3Encoder.h"
#include "Lame/include/lame.h"
#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <unistd.h>
#include <limits.h>

struct VSMp3 { lame_t lame; FILE *file; uint64_t expected; uint64_t written; int finished; int failed; };
static void quiet(const char *format, va_list arguments) { (void)format; (void)arguments; }
VSMp3 *vs_mp3_create(const char *output, int32_t rate, uint64_t frames, int32_t synthetic) {
    if (!output || !*output || rate != 24000 || frames < 1 || frames > 345600000) return NULL;
    VSMp3 *value = calloc(1, sizeof(*value)); if (!value) return NULL;
    value->lame = lame_init(); value->expected = frames;
    if (!value->lame) { free(value); return NULL; }
    lame_set_errorf(value->lame, quiet); lame_set_debugf(value->lame, quiet); lame_set_msgf(value->lame, quiet);
    id3tag_init(value->lame); id3tag_add_v2(value->lame);
    id3tag_set_comment(value->lame, synthetic ? "VoiceStudio synthetic audio" : "VoiceStudio audio");
    id3tag_set_artist(value->lame, "VoiceStudio");
    if (lame_set_num_channels(value->lame,1) < 0 || lame_set_in_samplerate(value->lame,rate) < 0 ||
        lame_set_out_samplerate(value->lame,rate) < 0 || lame_set_mode(value->lame,MONO) < 0 ||
        lame_set_brate(value->lame,64) < 0 || lame_set_quality(value->lame,3) < 0 ||
        lame_set_VBR(value->lame,vbr_off) < 0 || lame_set_num_samples(value->lame,(unsigned long)frames) < 0 ||
        lame_init_params(value->lame) < 0) { vs_mp3_free(value); return NULL; }
    value->file = fopen(output,"wb+"); if (!value->file) { vs_mp3_free(value); return NULL; }
    return value;
}
int32_t vs_mp3_write(VSMp3 *value, const float *samples, int32_t count) {
    if (!value || !value->file || value->finished || value->failed || !samples || count < 1 || count > 8192 || value->written + (uint64_t)count > value->expected) return -1;
    float bounded[8192]; unsigned char encoded[18000];
    for (int32_t i=0; i<count; i++) { if (!isfinite(samples[i])) { value->failed=1; return -1; }; bounded[i] = fminf(1,fmaxf(-1,samples[i])); }
    int size = lame_encode_buffer_ieee_float(value->lame,bounded,bounded,count,encoded,sizeof(encoded));
    if (size < 0 || fwrite(encoded,1,(size_t)size,value->file) != (size_t)size) { value->failed=1; return -1; }
    value->written += (uint64_t)count; return 0;
}
int32_t vs_mp3_finish(VSMp3 *value) {
    if (!value || !value->file || value->finished || value->failed || value->written != value->expected) return -1;
    unsigned char encoded[18000]; int size = lame_encode_flush(value->lame,encoded,sizeof(encoded));
    if (size < 0 || fwrite(encoded,1,(size_t)size,value->file) != (size_t)size) { value->failed=1; return -1; }
    lame_mp3_tags_fid(value->lame,value->file);
    if (ferror(value->file) || fflush(value->file) != 0 || fsync(fileno(value->file)) != 0) { value->failed=1; return -1; }
    value->finished=1; return 0;
}
void vs_mp3_free(VSMp3 *value) { if (!value) return; if (value->file) fclose(value->file); if (value->lame) lame_close(value->lame); free(value); }
const char *vs_mp3_version(void) { return get_lame_version(); }
