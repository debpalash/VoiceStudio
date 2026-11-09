/* -*- mode: C; mode: fold -*- */
/*
 *      LAME MP3 encoding engine
 *
 *      Copyright (c) 1999-2000 Mark Taylor
 *      Copyright (c) 2003 Olcios
 *      Copyright (c) 2008 Robert Hegemann
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more details.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

/* $Id: mpglib_interface.c,v 1.44 2012/02/18 13:09:00 robert Exp $ */

#ifdef HAVE_CONFIG_H
# include <config.h>
#endif

#ifdef HAVE_MPGLIB
#define hip_global_struct mpstr_tag 
#include "lame.h"
#include "machine.h"
#include "encoder.h"
#include "interface.h"

#include "util.h"



#if DEPRECATED_OR_OBSOLETE_CODE_REMOVED
/*
 * OBSOLETE:
 * - kept to let it link
 * - forward declaration to silence compiler
 */
int CDECL lame_decode_init(void);
int CDECL lame_decode(
        unsigned char *  mp3buf,
        int              len,
        short            pcm_l[],
        short            pcm_r[] );
int CDECL lame_decode_headers(
        unsigned char*   mp3buf,
        int              len,
        short            pcm_l[],
        short            pcm_r[],
        mp3data_struct*  mp3data );
int CDECL lame_decode1(
        unsigned char*  mp3buf,
        int             len,
        short           pcm_l[],
        short           pcm_r[] );
int CDECL lame_decode1_headers(
        unsigned char*   mp3buf,
        int              len,
        short            pcm_l[],
        short            pcm_r[],
        mp3data_struct*  mp3data );
int CDECL lame_decode1_headersB(
        unsigned char*   mp3buf,
        int              len,
        short            pcm_l[],
        short            pcm_r[],
        mp3data_struct*  mp3data,
        int              *enc_delay,
        int              *enc_padding );
int CDECL lame_decode_exit(void);
#endif


static MPSTR   mp;

int
lame_decode_exit(void)
{
    ExitMP3(&mp);
    return 0;
}


int
lame_decode_init(void)
{
    (void) InitMP3(&mp);
    return 0;
}




/* copy mono samples */
#define COPY_MONO(DST_TYPE, SRC_TYPE)                                                           \
    DST_TYPE *pcm_l = (DST_TYPE *)pcm_l_raw;                                                    \
    SRC_TYPE const *p_samples = (SRC_TYPE const *)p;                                            \
    for (i = 0; i < processed_samples; i++)                                                     \
      *pcm_l++ = (DST_TYPE)(*p_samples++);

/* copy stereo samples */
#define COPY_STEREO(DST_TYPE, SRC_TYPE)                                                         \
    DST_TYPE *pcm_l = (DST_TYPE *)pcm_l_raw, *pcm_r = (DST_TYPE *)pcm_r_raw;                    \
    SRC_TYPE const *p_samples = (SRC_TYPE const *)p;                                            \
    for (i = 0; i < processed_samples; i++) {                                                   \
      *pcm_l++ = (DST_TYPE)(*p_samples++);                                                      \
      *pcm_r++ = (DST_TYPE)(*p_samples++);                                                      \
    }



/*
 * For lame_decode:  return code
 * -1     error
 *  0     ok, but need more data before outputing any samples
 *  n     number of samples output.  either 576 or 1152 depending on MP3 file.
 */

static int
decode1_headersB_clipchoice(PMPSTR pmp, unsigned char *buffer, size_t len,
                            char pcm_l_raw[], char pcm_r_raw[], mp3data_struct * mp3data,
                            int *enc_delay, int *enc_padding,
                            char *p, size_t psize, int decoded_sample_size,
                            int (*decodeMP3_ptr) (PMPSTR, unsigned char *, int, char *, int,
                            int *))
{
    static const int smpls[2][4] = {
        /* Layer   I    II   III */
        {0, 384, 1152, 1152}, /* MPEG-1     */
        {0, 384, 1152, 576} /* MPEG-2(.5) */
    };

    int     processed_bytes;
    int     processed_samples; /* processed samples per channel */
    int     ret;
    int     i;
    int const len_l = len < INT_MAX ? (int) len : INT_MAX;
    int const psize_l = psize < INT_MAX ? (int) psize : INT_MAX;

    mp3data->header_parsed = 0;
    ret = (*decodeMP3_ptr) (pmp, buffer, len_l, p, psize_l, &processed_bytes);
    /* three cases:  
     * 1. headers parsed, but data not complete
     *       pmp->header_parsed==1 
     *       pmp->framesize=0           
     *       pmp->fsizeold=size of last frame, or 0 if this is first frame
     *
     * 2. headers, data parsed, but ancillary data not complete
     *       pmp->header_parsed==1 
     *       pmp->framesize=size of frame           
     *       pmp->fsizeold=size of last frame, or 0 if this is first frame
     *
     * 3. frame fully decoded:  
     *       pmp->header_parsed==0 
     *       pmp->framesize=0           
     *       pmp->fsizeold=size of frame (which is now the last frame)
     *
     */
    if (pmp->header_parsed || pmp->fsizeold > 0 || pmp->framesize > 0) {
        mp3data->header_parsed = 1;
        mp3data->stereo = pmp->fr.stereo;
        mp3data->samplerate = freqs[pmp->fr.sampling_frequency];
        mp3data->mode = pmp->fr.mode;
        mp3data->mode_ext = pmp->fr.mode_ext;
        mp3data->framesize = smpls[pmp->fr.lsf][pmp->fr.lay];

        /* free format, we need the entire frame before we can determine
         * the bitrate.  If we haven't gotten the entire frame, bitrate=0 */
        if (pmp->fsizeold > 0) /* works for free format and fixed, no overrun, temporal results are < 400.e6 */
            mp3data->bitrate = 8 * (4 + pmp->fsizeold) * mp3data->samplerate /
                (1.e3 * mp3data->framesize) + 0.5;
        else if (pmp->framesize > 0)
            mp3data->bitrate = 8 * (4 + pmp->framesize) * mp3data->samplerate /
                (1.e3 * mp3data->framesize) + 0.5;
        else
            mp3data->bitrate = tabsel_123[pmp->fr.lsf][pmp->fr.lay - 1][pmp->fr.bitrate_index];



        if (pmp->num_frames > 0) {
            /* Xing VBR header found and num_frames was set */
            mp3data->totalframes = pmp->num_frames;
            mp3data->nsamp = mp3data->framesize * pmp->num_frames;
            *enc_delay = pmp->enc_delay;
            *enc_padding = pmp->enc_padding;
        }
    }

    switch (ret) {
    case MP3_OK:
        switch (pmp->fr.stereo) {
        case 1:
            processed_samples = processed_bytes / decoded_sample_size;
            if (decoded_sample_size == sizeof(short)) {
                COPY_MONO(short, short)
            }
            else {
                COPY_MONO(sample_t, FLOAT)
            }
            break;
        case 2:
            processed_samples = (processed_bytes / decoded_sample_size) >> 1;
            if (decoded_sample_size == sizeof(short)) {
                COPY_STEREO(short, short)
            }
            else {
                COPY_STEREO(sample_t, FLOAT)
            }
            break;
        default:
            processed_samples = -1;
            assert(0);
            break;
        }
        break;

    case MP3_NEED_MORE:
        processed_samples = 0;
        break;

    case MP3_ERR:
        processed_samples = -1;
        break;

    default:
        processed_samples = -1;
        assert(0);
        break;
    }

    /*fprintf(stderr,"ok, more, err:  %i %i %i\n", MP3_OK, MP3_NEED_MORE, MP3_ERR ); */
    /*fprintf(stderr,"ret = %i out=%i\n", ret, processed_samples ); */
    return processed_samples;
}


#define OUTSIZE_CLIPPED   (4096*sizeof(short))

int
lame_decode1_headersB(unsigned char *buffer,
                      int len,
                      short pcm_l[], short pcm_r[], mp3data_struct * mp3data,
                      int *enc_delay, int *enc_padding)
{
    static char out[OUTSIZE_CLIPPED];

    return decode1_headersB_clipchoice(&mp, buffer, len, (char *) pcm_l, (char *) pcm_r, mp3data,
                                       enc_delay, enc_padding, out, OUTSIZE_CLIPPED,
                                       sizeof(short), decodeMP3);
}





/*
 * For lame_decode:  return code
 *  -1     error
 *   0     ok, but need more data before outputing any samples
 *   n     number of samples output.  Will be at most one frame of
 *         MPEG data.  
 */

int
lame_decode1_headers(unsigned char *buffer,
                     int len, short pcm_l[], short pcm_r[], mp3data_struct * mp3data)
{
    int     enc_delay, enc_padding;
    return lame_decode1_headersB(buffer, len, pcm_l, pcm_r, mp3data, &enc_delay, &enc_padding);
}


int
lame_decode1(unsigned char *buffer, int len, short pcm_l[], short pcm_r[])
{
    mp3data_struct mp3data;

    return lame_decode1_headers(buffer, len, pcm_l, pcm_r, &mp3data);
}


/*
 * For lame_decode:  return code
 *  -1     error
 *   0     ok, but need more data before outputing any samples
 *   n     number of samples output.  a multiple of 576 or 1152 depending on MP3 file.
 */

int
lame_decode_headers(unsigned char *buffer,
                    int len, short pcm_l[], short pcm_r[], mp3data_struct * mp3data)
{
    int     ret;
    int     totsize = 0;     /* number of decoded samples per channel */

    for (;;) {
        switch (ret = lame_decode1_headers(buffer, len, pcm_l + totsize, pcm_r + totsize, mp3data)) {
        case -1:
            return ret;
        case 0:
            return totsize;
        default:
            totsize += ret;
            len = 0;    /* future calls to decodeMP3 are just to flush buffers */
            break;
        }
    }
}


int
lame_decode(unsigned char *buffer, int len, short pcm_l[], short pcm_r[])
{
    mp3data_struct mp3data;

    return lame_decode_headers(buffer, len, pcm_l, pcm_r, &mp3data);
}




hip_t hip_decode_init(void)
{
    hip_t hip = lame_calloc(hip_global_flags, 1);
    InitMP3(hip);
    return hip;
}


int hip_decode_exit(hip_t hip)
{
    if (hip) {
        ExitMP3(hip);
        free(hip);
    }
    return 0;
}


/* we forbid input with more than 1152 samples per channel for output in the unclipped mode */
#define OUTSIZE_UNCLIPPED (1152*2*sizeof(FLOAT))

int
hip_decode1_unclipped(hip_t hip, unsigned char *buffer, size_t len, sample_t pcm_l[], sample_t pcm_r[])
{
    static char out[OUTSIZE_UNCLIPPED];
    mp3data_struct mp3data;
    int     enc_delay, enc_padding;

    if (hip) {
        return decode1_headersB_clipchoice(hip, buffer, len, (char *) pcm_l, (char *) pcm_r, &mp3data,
                                           &enc_delay, &enc_padding, out, OUTSIZE_UNCLIPPED,
                                           sizeof(FLOAT), decodeMP3_unclipped);
    }
    return 0;
}

/*
 * For hip_decode:  return code
 *  -1     error
 *   0     ok, but need more data before outputing any samples
 *   n     number of samples output.  Will be at most one frame of
 *         MPEG data.  
 */

int
hip_decode1_headers(hip_t hip, unsigned char *buffer,
                     size_t len, short pcm_l[], short pcm_r[], mp3data_struct * mp3data)
{
    int     enc_delay, enc_padding;
    return hip_decode1_headersB(hip, buffer, len, pcm_l, pcm_r, mp3data, &enc_delay, &enc_padding);
}


int
hip_decode1(hip_t hip, unsigned char *buffer, size_t len, short pcm_l[], short pcm_r[])
{
    mp3data_struct mp3data;
    return hip_decode1_headers(hip, buffer, len, pcm_l, pcm_r, &mp3data);
}


/*
 * For hip_decode:  return code
 *  -1     error
 *   0     ok, but need more data before outputing any samples
 *   n     number of samples output.  a multiple of 576 or 1152 depending on MP3 file.
 */

int
hip_decode_headers(hip_t hip, unsigned char *buffer,
                    size_t len, short pcm_l[], short pcm_r[], mp3data_struct * mp3data)
{
    int     ret;
    int     totsize = 0;     /* number of decoded samples per channel */

    for (;;) {
        switch (ret = hip_decode1_headers(hip, buffer, len, pcm_l + totsize, pcm_r + totsize, mp3data)) {
        case -1:
            return ret;
        case 0:
            return totsize;
        default:
            totsize += ret;
            len = 0;    /* future calls to decodeMP3 are just to flush buffers */
            break;
        }
    }
}


int
hip_decode(hip_t hip, unsigned char *buffer, size_t len, short pcm_l[], short pcm_r[])
{
    mp3data_struct mp3data;
    return hip_decode_headers(hip, buffer, len, pcm_l, pcm_r, &mp3data);
}


int
hip_decode1_headersB(hip_t hip, unsigned char *buffer,
                      size_t len,
                      short pcm_l[], short pcm_r[], mp3data_struct * mp3data,
                      int *enc_delay, int *enc_padding)
{
    static char out[OUTSIZE_CLIPPED];
    if (hip) {
        return decode1_headersB_clipchoice(hip, buffer, len, (char *) pcm_l, (char *) pcm_r, mp3data,
                                           enc_delay, enc_padding, out, OUTSIZE_CLIPPED,
                                           sizeof(short), decodeMP3);
    }
    return -1;
}


void hip_set_pinfo(hip_t hip, plotting_data* pinfo)
{
    if (hip) {
        hip->pinfo = pinfo;
    }
}



void hip_set_errorf(hip_t hip, lame_report_function func)
{
    if (hip) {
        hip->report_err = func;
    }
}

void hip_set_debugf(hip_t hip, lame_report_function func)
{
    if (hip) {
        hip->report_dbg = func;
    }
}

void hip_set_msgf  (hip_t hip, lame_report_function func)
{
    if (hip) {
        hip->report_msg = func;
    }
}

#endif

/* end of mpglib_interface.c */
