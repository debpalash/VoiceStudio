#ifndef VS_MP3_ENCODER_H
#define VS_MP3_ENCODER_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
/* One encoder per operation; caller owns cancellation and atomic publication.
 * Chunks are normalized mono float PCM, at most 8192 frames. No global state. */
typedef struct VSMp3 VSMp3;
VSMp3 *vs_mp3_create(const char *output, int32_t sample_rate, uint64_t frames, int32_t synthetic);
int32_t vs_mp3_write(VSMp3 *encoder, const float *samples, int32_t count);
int32_t vs_mp3_finish(VSMp3 *encoder);
void vs_mp3_free(VSMp3 *encoder);
const char *vs_mp3_version(void);
#ifdef __cplusplus
}
#endif
#endif
