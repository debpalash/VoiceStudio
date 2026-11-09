/*
 *	lame utility library source file
 *
 *	Copyright (c) 1999 Albert L Faber
 *	Copyright (c) 2000-2005 Alexander Leidinger
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	 See the GNU
 * Library General Public License for more details.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, write to the
 * Free Software Foundation, Inc., 59 Temple Place - Suite 330,
 * Boston, MA 02111-1307, USA.
 */

/* $Id: util.c,v 1.159 2017/09/06 15:07:30 robert Exp $ */

#ifdef HAVE_CONFIG_H
# include <config.h>
#endif

#include <float.h>
#include "lame.h"
#include "machine.h"
#include "encoder.h"
#include "util.h"
#include "tables.h"

#define PRECOMPUTE
#if defined(__FreeBSD__) && !defined(__alpha__)
# include <machine/floatingpoint.h>
#endif


/***********************************************************************
*
*  Global Function Definitions
*
***********************************************************************/
/*empty and close mallocs in gfc */

void
free_id3tag(lame_internal_flags * const gfc)
{
    gfc->tag_spec.language[0] = 0;
    if (gfc->tag_spec.title != 0) {
        free(gfc->tag_spec.title);
        gfc->tag_spec.title = 0;
    }
    if (gfc->tag_spec.artist != 0) {
        free(gfc->tag_spec.artist);
        gfc->tag_spec.artist = 0;
    }
    if (gfc->tag_spec.album != 0) {
        free(gfc->tag_spec.album);
        gfc->tag_spec.album = 0;
    }
    if (gfc->tag_spec.comment != 0) {
        free(gfc->tag_spec.comment);
        gfc->tag_spec.comment = 0;
    }

    if (gfc->tag_spec.albumart != 0) {
        free(gfc->tag_spec.albumart);
        gfc->tag_spec.albumart = 0;
        gfc->tag_spec.albumart_size = 0;
        gfc->tag_spec.albumart_mimetype = MIMETYPE_NONE;
    }
    if (gfc->tag_spec.v2_head != 0) {
        FrameDataNode *node = gfc->tag_spec.v2_head;
        do {
            void   *p = node->dsc.ptr.b;
            void   *q = node->txt.ptr.b;
            void   *r = node;
            node = node->nxt;
            free(p);
            free(q);
            free(r);
        } while (node != 0);
        gfc->tag_spec.v2_head = 0;
        gfc->tag_spec.v2_tail = 0;
    }
}


static void
free_global_data(lame_internal_flags * gfc)
{
    if (gfc && gfc->cd_psy) {
        if (gfc->cd_psy->l.s3) {
            /* XXX allocated in psymodel_init() */
            free(gfc->cd_psy->l.s3);
        }
        if (gfc->cd_psy->s.s3) {
            /* XXX allocated in psymodel_init() */
            free(gfc->cd_psy->s.s3);
        }
        free(gfc->cd_psy);
        gfc->cd_psy = 0;
    }
}


void
freegfc(lame_internal_flags * const gfc)
{                       /* bit stream structure */
    int     i;

    if (gfc == 0) return;

    for (i = 0; i <= 2 * BPC; i++)
        if (gfc->sv_enc.blackfilt[i] != NULL) {
            free(gfc->sv_enc.blackfilt[i]);
            gfc->sv_enc.blackfilt[i] = NULL;
        }
    if (gfc->sv_enc.inbuf_old[0]) {
        free(gfc->sv_enc.inbuf_old[0]);
        gfc->sv_enc.inbuf_old[0] = NULL;
    }
    if (gfc->sv_enc.inbuf_old[1]) {
        free(gfc->sv_enc.inbuf_old[1]);
        gfc->sv_enc.inbuf_old[1] = NULL;
    }

    if (gfc->bs.buf != NULL) {
        free(gfc->bs.buf);
        gfc->bs.buf = NULL;
    }

    if (gfc->VBR_seek_table.bag) {
        free(gfc->VBR_seek_table.bag);
        gfc->VBR_seek_table.bag = NULL;
        gfc->VBR_seek_table.size = 0;
    }
    if (gfc->ATH) {
        free(gfc->ATH);
    }
    if (gfc->sv_rpg.rgdata) {
        free(gfc->sv_rpg.rgdata);
    }
    if (gfc->sv_enc.in_buffer_0) {
        free(gfc->sv_enc.in_buffer_0);
    }
    if (gfc->sv_enc.in_buffer_1) {
        free(gfc->sv_enc.in_buffer_1);
    }
    free_id3tag(gfc);

#ifdef DECODE_ON_THE_FLY
    if (gfc->hip) {
        hip_decode_exit(gfc->hip);
        gfc->hip = 0;
    }
#endif

    free_global_data(gfc);

    free(gfc);
}

void
calloc_aligned(aligned_pointer_t * ptr, unsigned int size, unsigned int bytes)
{
    if (ptr) {
        if (!ptr->pointer) {
            ptr->pointer = malloc(size + bytes);
            if (ptr->pointer != 0) {
                memset(ptr->pointer, 0, size + bytes);
                if (bytes > 0) {
                    ptr->aligned = (void *) ((((size_t) ptr->pointer + bytes - 1) / bytes) * bytes);
                }
                else {
                    ptr->aligned = ptr->pointer;
                }
            }
            else {
                ptr->aligned = 0;
            }
        }
    }
}

void
free_aligned(aligned_pointer_t * ptr)
{
    if (ptr) {
        if (ptr->pointer) {
            free(ptr->pointer);
            ptr->pointer = 0;
            ptr->aligned = 0;
        }
    }
}

/*those ATH formulas are returning
their minimum value for input = -1*/

static  FLOAT
ATHformula_GB(FLOAT f, FLOAT value, FLOAT f_min, FLOAT f_max)
{
    /* from Painter & Spanias
       modified by Gabriel Bouvigne to better fit the reality
       ath =    3.640 * pow(f,-0.8)
       - 6.800 * exp(-0.6*pow(f-3.4,2.0))
       + 6.000 * exp(-0.15*pow(f-8.7,2.0))
       + 0.6* 0.001 * pow(f,4.0);


       In the past LAME was using the Painter &Spanias formula.
       But we had some recurrent problems with HF content.
       We measured real ATH values, and found the older formula
       to be inacurate in the higher part. So we made this new
       formula and this solved most of HF problematic testcases.
       The tradeoff is that in VBR mode it increases a lot the
       bitrate. */


/*this curve can be udjusted according to the VBR scale:
it adjusts from something close to Painter & Spanias
on V9 up to Bouvigne's formula for V0. This way the VBR
bitrate is more balanced according to the -V value.*/

    FLOAT   ath;

    /* the following Hack allows to ask for the lowest value */
    if (f < -.3)
        f = 3410;

    f /= 1000;          /* convert to khz */
    f = Max(f_min, f);
    f = Min(f_max, f);

    ath = 3.640 * pow(f, -0.8)
        - 6.800 * exp(-0.6 * pow(f - 3.4, 2.0))
        + 6.000 * exp(-0.15 * pow(f - 8.7, 2.0))
        + (0.6 + 0.04 * value) * 0.001 * pow(f, 4.0);
    return ath;
}



FLOAT
ATHformula(SessionConfig_t const *cfg, FLOAT f)
{
    FLOAT   ath;
    switch (cfg->ATHtype) {
    case 0:
        ath = ATHformula_GB(f, 9, 0.1f, 24.0f);
        break;
    case 1:
        ath = ATHformula_GB(f, -1, 0.1f, 24.0f); /*over sensitive, should probably be removed */
        break;
    case 2:
        ath = ATHformula_GB(f, 0, 0.1f, 24.0f);
        break;
    case 3:
        ath = ATHformula_GB(f, 1, 0.1f, 24.0f) + 6; /*modification of GB formula by Roel */
        break;
    case 4:
        ath = ATHformula_GB(f, cfg->ATHcurve, 0.1f, 24.0f);
        break;
    case 5:
        ath = ATHformula_GB(f, cfg->ATHcurve, 3.41f, 16.1f);
        break;
    default:
        ath = ATHformula_GB(f, 0, 0.1f, 24.0f);
        break;
    }
    return ath;
}

/* see for example "Zwicker: Psychoakustik, 1982; ISBN 3-540-11401-7 */
FLOAT
freq2bark(FLOAT freq)
{
    /* input: freq in hz  output: barks */
    if (freq < 0)
        freq = 0;
    freq = freq * 0.001;
    return 13.0 * atan(.76 * freq) + 3.5 * atan(freq * freq / (7.5 * 7.5));
}

#if 0
extern FLOAT freq2cbw(FLOAT freq);

/* see for example "Zwicker: Psychoakustik, 1982; ISBN 3-540-11401-7 */
FLOAT
freq2cbw(FLOAT freq)
{
    /* input: freq in hz  output: critical band width */
    freq = freq * 0.001;
    return 25 + 75 * pow(1 + 1.4 * (freq * freq), 0.69);
}

#endif




#define ABS(A) (((A)>0) ? (A) : -(A))

int
FindNearestBitrate(int bRate, /* legal rates from 8 to 320 */
                   int version, int samplerate)
{                       /* MPEG-1 or MPEG-2 LSF */
    int     bitrate;
    int     i;

    if (samplerate < 16000)
        version = 2;

    bitrate = bitrate_table[version][1];

    for (i = 2; i <= 14; i++) {
        if (bitrate_table[version][i] > 0) {
            if (ABS(bitrate_table[version][i] - bRate) < ABS(bitrate - bRate))
                bitrate = bitrate_table[version][i];
        }
    }
    return bitrate;
}





#ifndef Min
#define         Min(A, B)       ((A) < (B) ? (A) : (B))
#endif
#ifndef Max
#define         Max(A, B)       ((A) > (B) ? (A) : (B))
#endif


/* Used to find table index when
 * we need bitrate-based values
 * determined using tables
 *
 * bitrate in kbps
 *
 * Gabriel Bouvigne 2002-11-03
 */
int
nearestBitrateFullIndex(uint16_t bitrate)
{
    /* borrowed from DM abr presets */

    const int full_bitrate_table[] =
        { 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320 };


    int     lower_range = 0, lower_range_kbps = 0, upper_range = 0, upper_range_kbps = 0;


    int     b;


    /* We assume specified bitrate will be 320kbps */
    upper_range_kbps = full_bitrate_table[16];
    upper_range = 16;
    lower_range_kbps = full_bitrate_table[16];
    lower_range = 16;

    /* Determine which significant bitrates the value specified falls between,
     * if loop ends without breaking then we were correct above that the value was 320
     */
    for (b = 0; b < 16; b++) {
        if ((Max(bitrate, full_bitrate_table[b + 1])) != bitrate) {
            upper_range_kbps = full_bitrate_table[b + 1];
            upper_range = b + 1;
            lower_range_kbps = full_bitrate_table[b];
            lower_range = (b);
            break;      /* We found upper range */
        }
    }

    /* Determine which range the value specified is closer to */
    if ((upper_range_kbps - bitrate) > (bitrate - lower_range_kbps)) {
        return lower_range;
    }
    return upper_range;
}





/* map frequency to a valid MP3 sample frequency
 *
 * Robert Hegemann 2000-07-01
 */
int
map2MP3Frequency(int freq)
{
    if (freq <= 8000)
        return 8000;
    if (freq <= 11025)
        return 11025;
    if (freq <= 12000)
        return 12000;
    if (freq <= 16000)
        return 16000;
    if (freq <= 22050)
        return 22050;
    if (freq <= 24000)
        return 24000;
    if (freq <= 32000)
        return 32000;
    if (freq <= 44100)
        return 44100;

    return 48000;
}

int
BitrateIndex(int bRate,      /* legal rates from 32 to 448 kbps */
             int version,    /* MPEG-1 or MPEG-2/2.5 LSF */
             int samplerate)
{                       /* convert bitrate in kbps to index */
    int     i;
    if (samplerate < 16000)
        version = 2;
    for (i = 0; i <= 14; i++) {
        if (bitrate_table[version][i] > 0) {
            if (bitrate_table[version][i] == bRate) {
                return i;
            }
        }
    }
    return -1;
}

/* convert samp freq in Hz to index */

int
SmpFrqIndex(int sample_freq, int *const version)
{
    switch (sample_freq) {
    case 44100:
        *version = 1;
        return 0;
    case 48000:
        *version = 1;
        return 1;
    case 32000:
        *version = 1;
        return 2;
    case 22050:
        *version = 0;
        return 0;
    case 24000:
        *version = 0;
        return 1;
    case 16000:
        *version = 0;
        return 2;
    case 11025:
        *version = 0;
        return 0;
    case 12000:
        *version = 0;
        return 1;
    case 8000:
        *version = 0;
        return 2;
    default:
        *version = 0;
        return -1;
    }
}


/*****************************************************************************
*
*  End of bit_stream.c package
*
*****************************************************************************/










/* resampling via FIR filter, blackman window */
inline static FLOAT
blackman(FLOAT x, FLOAT fcn, int l)
{
    /* This algorithm from:
       SIGNAL PROCESSING ALGORITHMS IN FORTRAN AND C
       S.D. Stearns and R.A. David, Prentice-Hall, 1992
     */
    FLOAT   bkwn, x2;
    FLOAT const wcn = (PI * fcn);

    x /= l;
    if (x < 0)
        x = 0;
    if (x > 1)
        x = 1;
    x2 = x - .5;

    bkwn = 0.42 - 0.5 * cos(2 * x * PI) + 0.08 * cos(4 * x * PI);
    if (fabs(x2) < 1e-9)
        return wcn / PI;
    else
        return (bkwn * sin(l * wcn * x2) / (PI * l * x2));


}




/* gcd - greatest common divisor */
/* Joint work of Euclid and M. Hendry */

static int
gcd(int i, int j)
{
    /*    assert ( i > 0  &&  j > 0 ); */
    return j ? gcd(j, i % j) : i;
}



static int
fill_buffer_resample(lame_internal_flags * gfc,
                     sample_t * outbuf,
                     int desired_len, sample_t const *inbuf, int len, int *num_used, int ch)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    EncStateVar_t *esv = &gfc->sv_enc;
    double  resample_ratio = (double)cfg->samplerate_in / (double)cfg->samplerate_out;
    int     BLACKSIZE;
    FLOAT   offset, xvalue;
    int     i, j = 0, k;
    int     filter_l;
    FLOAT   fcn, intratio;
    FLOAT  *inbuf_old;
    int     bpc;             /* number of convolution functions to pre-compute */
    bpc = cfg->samplerate_out / gcd(cfg->samplerate_out, cfg->samplerate_in);
    if (bpc > BPC)
        bpc = BPC;

    intratio = (fabs(resample_ratio - floor(.5 + resample_ratio)) < FLT_EPSILON);
    fcn = 1.00 / resample_ratio;
    if (fcn > 1.00)
        fcn = 1.00;
    filter_l = 31;     /* must be odd */
    filter_l += intratio; /* unless resample_ratio=int, it must be even */


    BLACKSIZE = filter_l + 1; /* size of data needed for FIR */

    if (gfc->fill_buffer_resample_init == 0) {
        esv->inbuf_old[0] = lame_calloc(sample_t, BLACKSIZE);
        esv->inbuf_old[1] = lame_calloc(sample_t, BLACKSIZE);
        for (i = 0; i <= 2 * bpc; ++i)
            esv->blackfilt[i] = lame_calloc(sample_t, BLACKSIZE);

        esv->itime[0] = 0;
        esv->itime[1] = 0;

        /* precompute blackman filter coefficients */
        for (j = 0; j <= 2 * bpc; j++) {
            FLOAT   sum = 0.;
            offset = (j - bpc) / (2. * bpc);
            for (i = 0; i <= filter_l; i++)
                sum += esv->blackfilt[j][i] = blackman(i - offset, fcn, filter_l);
            for (i = 0; i <= filter_l; i++)
                esv->blackfilt[j][i] /= sum;
        }
        gfc->fill_buffer_resample_init = 1;
    }

    inbuf_old = esv->inbuf_old[ch];

    /* time of j'th element in inbuf = itime + j/ifreq; */
    /* time of k'th element in outbuf   =  j/ofreq */
    for (k = 0; k < desired_len; k++) {
        double  time0 = k * resample_ratio; /* time of k'th output sample */
        int     joff;

        j = floor(time0 - esv->itime[ch]);

        /* check if we need more input data */
        if ((filter_l + j - filter_l / 2) >= len)
            break;

        /* blackman filter.  by default, window centered at j+.5(filter_l%2) */
        /* but we want a window centered at time0.   */
        offset = (time0 - esv->itime[ch] - (j + .5 * (filter_l % 2)));
        assert(fabs(offset) <= .501);

        /* find the closest precomputed window for this offset: */
        joff = floor((offset * 2 * bpc) + bpc + .5);

        xvalue = 0.;
        for (i = 0; i <= filter_l; ++i) {
            int const j2 = i + j - filter_l / 2;
            sample_t y;
            assert(j2 < len);
            assert(j2 + BLACKSIZE >= 0);
            y = (j2 < 0) ? inbuf_old[BLACKSIZE + j2] : inbuf[j2];
#ifdef PRECOMPUTE
            xvalue += y * esv->blackfilt[joff][i];
#else
            xvalue += y * blackman(i - offset, fcn, filter_l); /* very slow! */
#endif
        }
        outbuf[k] = xvalue;
    }


    /* k = number of samples added to outbuf */
    /* last k sample used data from [j-filter_l/2,j+filter_l-filter_l/2]  */

    /* how many samples of input data were used:  */
    *num_used = Min(len, filter_l + j - filter_l / 2);

    /* adjust our input time counter.  Incriment by the number of samples used,
     * then normalize so that next output sample is at time 0, next
     * input buffer is at time itime[ch] */
    esv->itime[ch] += *num_used - k * resample_ratio;

    /* save the last BLACKSIZE samples into the inbuf_old buffer */
    if (*num_used >= BLACKSIZE) {
        for (i = 0; i < BLACKSIZE; i++)
            inbuf_old[i] = inbuf[*num_used + i - BLACKSIZE];
    }
    else {
        /* shift in *num_used samples into inbuf_old  */
        int const n_shift = BLACKSIZE - *num_used; /* number of samples to shift */

        /* shift n_shift samples by *num_used, to make room for the
         * num_used new samples */
        for (i = 0; i < n_shift; ++i)
            inbuf_old[i] = inbuf_old[i + *num_used];

        /* shift in the *num_used samples */
        for (j = 0; i < BLACKSIZE; ++i, ++j)
            inbuf_old[i] = inbuf[j];

        assert(j == *num_used);
    }
    return k;           /* return the number samples created at the new samplerate */
}

int
isResamplingNecessary(SessionConfig_t const* cfg)
{
    int const l = cfg->samplerate_out * 0.9995f;
    int const h = cfg->samplerate_out * 1.0005f;
    return (cfg->samplerate_in < l) || (h < cfg->samplerate_in) ? 1 : 0;
}

/* copy in new samples from in_buffer into mfbuf, with resampling
   if necessary.  n_in = number of samples from the input buffer that
   were used.  n_out = number of samples copied into mfbuf  */

void
fill_buffer(lame_internal_flags * gfc,
            sample_t * const mfbuf[2], sample_t const * const in_buffer[2], int nsamples, int *n_in, int *n_out)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    int     mf_size = gfc->sv_enc.mf_size;
    int     framesize = 576 * cfg->mode_gr;
    int     nout, ch = 0;
    int     nch = cfg->channels_out;

    /* copy in new samples into mfbuf, with resampling if necessary */
    if (isResamplingNecessary(cfg)) {
        do {
            nout =
                fill_buffer_resample(gfc, &mfbuf[ch][mf_size],
                                     framesize, in_buffer[ch], nsamples, n_in, ch);
        } while (++ch < nch);
        *n_out = nout;
    }
    else {
        nout = Min(framesize, nsamples);
        do {
            memcpy(&mfbuf[ch][mf_size], &in_buffer[ch][0], nout * sizeof(mfbuf[0][0]));
        } while (++ch < nch);
        *n_out = nout;
        *n_in = nout;
    }
}







/***********************************************************************
*
*  Message Output
*
***********************************************************************/

void
lame_report_def(const char *format, va_list args)
{
    (void) vfprintf(stderr, format, args);
    fflush(stderr); /* an debug function should flush immediately */
}

void 
lame_report_fnc(lame_report_function print_f, const char *format, ...)
{
    if (print_f) {
        va_list args;
        va_start(args, format);
        print_f(format, args);
        va_end(args);
    }
}


void
lame_debugf(const lame_internal_flags* gfc, const char *format, ...)
{
    if (gfc && gfc->report_dbg) {
        va_list args;
        va_start(args, format);
        gfc->report_dbg(format, args);
        va_end(args);
    }
}


void
lame_msgf(const lame_internal_flags* gfc, const char *format, ...)
{
    if (gfc && gfc->report_msg) {
        va_list args;
        va_start(args, format);
        gfc->report_msg(format, args);
        va_end(args);
    }
}


void
lame_errorf(const lame_internal_flags* gfc, const char *format, ...)
{
    if (gfc && gfc->report_err) {
        va_list args;
        va_start(args, format);
        gfc->report_err(format, args);
        va_end(args);
    }
}



/***********************************************************************
 *
 *      routines to detect CPU specific features like 3DNow, MMX, SSE
 *
 *  donated by Frank Klemm
 *  added Robert Hegemann 2000-10-10
 *
 ***********************************************************************/

#ifdef HAVE_NASM
extern int has_MMX_nasm(void);
extern int has_3DNow_nasm(void);
extern int has_SSE_nasm(void);
extern int has_SSE2_nasm(void);
#endif

int
has_MMX(void)
{
#ifdef HAVE_NASM
    return has_MMX_nasm();
#else
    return 0;           /* don't know, assume not */
#endif
}

int
has_3DNow(void)
{
#ifdef HAVE_NASM
    return has_3DNow_nasm();
#else
    return 0;           /* don't know, assume not */
#endif
}

int
has_SSE(void)
{
#ifdef HAVE_NASM
    return has_SSE_nasm();
#else
#if defined( _M_X64 ) || defined( MIN_ARCH_SSE )
    return 1;
#else
    return 0;           /* don't know, assume not */
#endif
#endif
}

int
has_SSE2(void)
{
#ifdef HAVE_NASM
    return has_SSE2_nasm();
#else
#if defined( _M_X64 ) || defined( MIN_ARCH_SSE )
    return 1;
#else
    return 0;           /* don't know, assume not */
#endif
#endif
}

void
disable_FPE(void)
{
/* extremly system dependent stuff, move to a lib to make the code readable */
/*==========================================================================*/



    /*
     *  Disable floating point exceptions
     */




#if defined(__FreeBSD__) && !defined(__alpha__)
    {
        /* seet floating point mask to the Linux default */
        fp_except_t mask;
        mask = fpgetmask();
        /* if bit is set, we get SIGFPE on that error! */
        fpsetmask(mask & ~(FP_X_INV | FP_X_DZ));
        /*  DEBUGF("FreeBSD mask is 0x%x\n",mask); */
    }
#endif

#if defined(__riscos__) && !defined(ABORTFP)
    /* Disable FPE's under RISC OS */
    /* if bit is set, we disable trapping that error! */
    /*   _FPE_IVO : invalid operation */
    /*   _FPE_DVZ : divide by zero */
    /*   _FPE_OFL : overflow */
    /*   _FPE_UFL : underflow */
    /*   _FPE_INX : inexact */
    DisableFPETraps(_FPE_IVO | _FPE_DVZ | _FPE_OFL);
#endif

    /*
     *  Debugging stuff
     *  The default is to ignore FPE's, unless compiled with -DABORTFP
     *  so add code below to ENABLE FPE's.
     */

#if defined(ABORTFP)
#if defined(_MSC_VER)
    {
#if 0
        /* rh 061207
           the following fix seems to be a workaround for a problem in the
           parent process calling LAME. It would be better to fix the broken
           application => code disabled.
         */

        /* set affinity to a single CPU.  Fix for EAC/lame on SMP systems from
           "Todd Richmond" <todd.richmond@openwave.com> */
        SYSTEM_INFO si;
        GetSystemInfo(&si);
        SetProcessAffinityMask(GetCurrentProcess(), si.dwActiveProcessorMask);
#endif
#include <float.h>
        unsigned int mask;
        mask = _controlfp(0, 0);
        mask &= ~(_EM_OVERFLOW | _EM_UNDERFLOW | _EM_ZERODIVIDE | _EM_INVALID);
        mask = _controlfp(mask, _MCW_EM);
    }
#elif defined(__CYGWIN__)
#  define _FPU_GETCW(cw) __asm__ ("fnstcw %0" : "=m" (*&cw))
#  define _FPU_SETCW(cw) __asm__ ("fldcw %0" : : "m" (*&cw))

#  define _EM_INEXACT     0x00000020 /* inexact (precision) */
#  define _EM_UNDERFLOW   0x00000010 /* underflow */
#  define _EM_OVERFLOW    0x00000008 /* overflow */
#  define _EM_ZERODIVIDE  0x00000004 /* zero divide */
#  define _EM_INVALID     0x00000001 /* invalid */
    {
        unsigned int mask;
        _FPU_GETCW(mask);
        /* Set the FPU control word to abort on most FPEs */
        mask &= ~(_EM_OVERFLOW | _EM_ZERODIVIDE | _EM_INVALID);
        _FPU_SETCW(mask);
    }
# elif defined(__linux__)
    {

#  include <fpu_control.h>
#  ifndef _FPU_GETCW
#  define _FPU_GETCW(cw) __asm__ ("fnstcw %0" : "=m" (*&cw))
#  endif
#  ifndef _FPU_SETCW
#  define _FPU_SETCW(cw) __asm__ ("fldcw %0" : : "m" (*&cw))
#  endif

        /* 
         * Set the Linux mask to abort on most FPE's
         * if bit is set, we _mask_ SIGFPE on that error!
         *  mask &= ~( _FPU_MASK_IM | _FPU_MASK_ZM | _FPU_MASK_OM | _FPU_MASK_UM );
         */

        unsigned int mask;
        _FPU_GETCW(mask);
        mask &= ~(_FPU_MASK_IM | _FPU_MASK_ZM | _FPU_MASK_OM);
        _FPU_SETCW(mask);
    }
#endif
#endif /* ABORTFP */
}





#ifdef USE_FAST_LOG
/***********************************************************************
 *
 * Fast Log Approximation for log2, used to approximate every other log
 * (log10 and log)
 * maximum absolute error for log10 is around 10-6
 * maximum *relative* error can be high when x is almost 1 because error/log10(x) tends toward x/e
 *
 * use it if typical RESULT values are > 1e-5 (for example if x>1.00001 or x<0.99999)
 * or if the relative precision in the domain around 1 is not important (result in 1 is exact and 0)
 *
 ***********************************************************************/


#define LOG2_SIZE       (512)
#define LOG2_SIZE_L2    (9)

static ieee754_float32_t log_table[LOG2_SIZE + 1];



void
init_log_table(void)
{
    int     j;
    static int init = 0;

    /* Range for log2(x) over [1,2[ is [0,1[ */
    assert((1 << LOG2_SIZE_L2) == LOG2_SIZE);

    if (!init) {
        for (j = 0; j < LOG2_SIZE + 1; j++)
            log_table[j] = log(1.0f + j / (ieee754_float32_t) LOG2_SIZE) / log(2.0f);
    }
    init = 1;
}



ieee754_float32_t
fast_log2(ieee754_float32_t x)
{
    ieee754_float32_t log2val, partial;
    union {
        ieee754_float32_t f;
        int     i;
    } fi;
    int     mantisse;
    fi.f = x;
    mantisse = fi.i & 0x7fffff;
    log2val = ((fi.i >> 23) & 0xFF) - 0x7f;
    partial = (mantisse & ((1 << (23 - LOG2_SIZE_L2)) - 1));
    partial *= 1.0f / ((1 << (23 - LOG2_SIZE_L2)));


    mantisse >>= (23 - LOG2_SIZE_L2);

    /* log2val += log_table[mantisse];  without interpolation the results are not good */
    log2val += log_table[mantisse] * (1.0f - partial) + log_table[mantisse + 1] * partial;

    return log2val;
}

#else /* Don't use FAST_LOG */


void
init_log_table(void)
{
}


#endif

/* end of util.c */
