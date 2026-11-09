/* -*- mode: C; mode: fold -*- */
/*
 *      LAME MP3 encoding engine
 *
 *      Copyright (c) 1999-2000 Mark Taylor
 *      Copyright (c) 2000-2005 Takehiro Tominaga
 *      Copyright (c) 2000-2017 Robert Hegemann
 *      Copyright (c) 2000-2005 Gabriel Bouvigne
 *      Copyright (c) 2000-2004 Alexander Leidinger
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

/* $Id: lame.c,v 1.377 2017/09/26 12:14:02 robert Exp $ */

#ifdef HAVE_CONFIG_H
# include <config.h>
#endif


#include "lame.h"
#include "machine.h"

#include "encoder.h"
#include "util.h"
#include "lame_global_flags.h"
#include "gain_analysis.h"
#include "bitstream.h"
#include "quantize_pvt.h"
#include "set_get.h"
#include "quantize.h"
#include "psymodel.h"
#include "version.h"
#include "VbrTag.h"
#include "tables.h"


#if defined(__FreeBSD__) && !defined(__alpha__)
#include <floatingpoint.h>
#endif
#ifdef __riscos__
#include "asmstuff.h"
#endif

#ifdef __sun__
/* woraround for SunOS 4.x, it has SEEK_* defined here */
#include <unistd.h>
#endif


#define LAME_DEFAULT_QUALITY 3



int
is_lame_global_flags_valid(const lame_global_flags * gfp)
{
    if (gfp == NULL)
        return 0;
    if (gfp->class_id != LAME_ID)
        return 0;
    return 1;
}


int
is_lame_internal_flags_valid(const lame_internal_flags * gfc)
{
    if (gfc == NULL)
        return 0;
    if (gfc->class_id != LAME_ID)
        return 0;
    if (gfc->lame_init_params_successful <=0)
        return 0;
    return 1;
}



static  FLOAT
filter_coef(FLOAT x)
{
    if (x > 1.0)
        return 0.0;
    if (x <= 0.0)
        return 1.0;

    return cos(PI / 2 * x);
}

static void
lame_init_params_ppflt(lame_internal_flags * gfc)
{
    SessionConfig_t *const cfg = &gfc->cfg;
    
    /***************************************************************/
    /* compute info needed for polyphase filter (filter type==0, default) */
    /***************************************************************/

    int     band, maxband, minband;
    FLOAT   freq;
    int     lowpass_band = 32;
    int     highpass_band = -1;

    if (cfg->lowpass1 > 0) {
        minband = 999;
        for (band = 0; band <= 31; band++) {
            freq = band / 31.0;
            /* this band and above will be zeroed: */
            if (freq >= cfg->lowpass2) {
                lowpass_band = Min(lowpass_band, band);
            }
            if (cfg->lowpass1 < freq && freq < cfg->lowpass2) {
                minband = Min(minband, band);
            }
        }

        /* compute the *actual* transition band implemented by
         * the polyphase filter */
        if (minband == 999) {
            cfg->lowpass1 = (lowpass_band - .75) / 31.0;
        }
        else {
            cfg->lowpass1 = (minband - .75) / 31.0;
        }
        cfg->lowpass2 = lowpass_band / 31.0;
    }

    /* make sure highpass filter is within 90% of what the effective
     * highpass frequency will be */
    if (cfg->highpass2 > 0) {
        if (cfg->highpass2 < .9 * (.75 / 31.0)) {
            cfg->highpass1 = 0;
            cfg->highpass2 = 0;
            MSGF(gfc, "Warning: highpass filter disabled.  " "highpass frequency too small\n");
        }
    }

    if (cfg->highpass2 > 0) {
        maxband = -1;
        for (band = 0; band <= 31; band++) {
            freq = band / 31.0;
            /* this band and below will be zereod */
            if (freq <= cfg->highpass1) {
                highpass_band = Max(highpass_band, band);
            }
            if (cfg->highpass1 < freq && freq < cfg->highpass2) {
                maxband = Max(maxband, band);
            }
        }
        /* compute the *actual* transition band implemented by
         * the polyphase filter */
        cfg->highpass1 = highpass_band / 31.0;
        if (maxband == -1) {
            cfg->highpass2 = (highpass_band + .75) / 31.0;
        }
        else {
            cfg->highpass2 = (maxband + .75) / 31.0;
        }
    }

    for (band = 0; band < 32; band++) {
        FLOAT fc1, fc2;
        freq = band / 31.0f;
        if (cfg->highpass2 > cfg->highpass1) {
            fc1 = filter_coef((cfg->highpass2 - freq) / (cfg->highpass2 - cfg->highpass1 + 1e-20));
        }
        else {
            fc1 = 1.0f;
        }
        if (cfg->lowpass2 > cfg->lowpass1) {
            fc2 = filter_coef((freq - cfg->lowpass1)  / (cfg->lowpass2 - cfg->lowpass1 + 1e-20));
        }
        else {
            fc2 = 1.0f;
        }
        gfc->sv_enc.amp_filter[band] = fc1 * fc2;
    }
}


static void
optimum_bandwidth(double *const lowerlimit, double *const upperlimit, const unsigned bitrate)
{
/*
 *  Input:
 *      bitrate     total bitrate in kbps
 *
 *   Output:
 *      lowerlimit: best lowpass frequency limit for input filter in Hz
 *      upperlimit: best highpass frequency limit for input filter in Hz
 */
    int     table_index;

    typedef struct {
        int     bitrate;     /* only indicative value */
        int     lowpass;
    } band_pass_t;

    const band_pass_t freq_map[] = {
        {8, 2000},
        {16, 3700},
        {24, 3900},
        {32, 5500},
        {40, 7000},
        {48, 7500},
        {56, 10000},
        {64, 11000},
        {80, 13500},
        {96, 15100},
        {112, 15600},
        {128, 17000},
        {160, 17500},
        {192, 18600},
        {224, 19400},
        {256, 19700},
        {320, 20500}
    };


    table_index = nearestBitrateFullIndex(bitrate);

    (void) freq_map[table_index].bitrate;
    *lowerlimit = freq_map[table_index].lowpass;


/*
 *  Now we try to choose a good high pass filtering frequency.
 *  This value is currently not used.
 *    For fu < 16 kHz:  sqrt(fu*fl) = 560 Hz
 *    For fu = 18 kHz:  no high pass filtering
 *  This gives:
 *
 *   2 kHz => 160 Hz
 *   3 kHz => 107 Hz
 *   4 kHz =>  80 Hz
 *   8 kHz =>  40 Hz
 *  16 kHz =>  20 Hz
 *  17 kHz =>  10 Hz
 *  18 kHz =>   0 Hz
 *
 *  These are ad hoc values and these can be optimized if a high pass is available.
 */
/*    if (f_low <= 16000)
        f_high = 16000. * 20. / f_low;
    else if (f_low <= 18000)
        f_high = 180. - 0.01 * f_low;
    else
        f_high = 0.;*/

    /*
     *  When we sometimes have a good highpass filter, we can add the highpass
     *  frequency to the lowpass frequency
     */

    /*if (upperlimit != NULL)
     *upperlimit = f_high;*/
    (void) upperlimit;
}


static int
optimum_samplefreq(int lowpassfreq, int input_samplefreq)
{
/*
 * Rules:
 *  - if possible, sfb21 should NOT be used
 *
 */
    int     suggested_samplefreq = 44100;

    if (input_samplefreq >= 48000)
        suggested_samplefreq = 48000;
    else if (input_samplefreq >= 44100)
        suggested_samplefreq = 44100;
    else if (input_samplefreq >= 32000)
        suggested_samplefreq = 32000;
    else if (input_samplefreq >= 24000)
        suggested_samplefreq = 24000;
    else if (input_samplefreq >= 22050)
        suggested_samplefreq = 22050;
    else if (input_samplefreq >= 16000)
        suggested_samplefreq = 16000;
    else if (input_samplefreq >= 12000)
        suggested_samplefreq = 12000;
    else if (input_samplefreq >= 11025)
        suggested_samplefreq = 11025;
    else if (input_samplefreq >= 8000)
        suggested_samplefreq = 8000;

    if (lowpassfreq == -1)
        return suggested_samplefreq;

    if (lowpassfreq <= 15960)
        suggested_samplefreq = 44100;
    if (lowpassfreq <= 15250)
        suggested_samplefreq = 32000;
    if (lowpassfreq <= 11220)
        suggested_samplefreq = 24000;
    if (lowpassfreq <= 9970)
        suggested_samplefreq = 22050;
    if (lowpassfreq <= 7230)
        suggested_samplefreq = 16000;
    if (lowpassfreq <= 5420)
        suggested_samplefreq = 12000;
    if (lowpassfreq <= 4510)
        suggested_samplefreq = 11025;
    if (lowpassfreq <= 3970)
        suggested_samplefreq = 8000;

    if (input_samplefreq < suggested_samplefreq) {
        /* choose a valid MPEG sample frequency above the input sample frequency
           to avoid SFB21/12 bitrate bloat
           rh 061115
         */
        if (input_samplefreq > 44100) {
            return 48000;
        }
        if (input_samplefreq > 32000) {
            return 44100;
        }
        if (input_samplefreq > 24000) {
            return 32000;
        }
        if (input_samplefreq > 22050) {
            return 24000;
        }
        if (input_samplefreq > 16000) {
            return 22050;
        }
        if (input_samplefreq > 12000) {
            return 16000;
        }
        if (input_samplefreq > 11025) {
            return 12000;
        }
        if (input_samplefreq > 8000) {
            return 11025;
        }
        return 8000;
    }
    return suggested_samplefreq;
}





/* set internal feature flags.  USER should not access these since
 * some combinations will produce strange results */
static void
lame_init_qval(lame_global_flags * gfp)
{
    lame_internal_flags *const gfc = gfp->internal_flags;
    SessionConfig_t *const cfg = &gfc->cfg;

    switch (gfp->quality) {
    default:
    case 9:            /* no psymodel, no noise shaping */
        cfg->noise_shaping = 0;
        cfg->noise_shaping_amp = 0;
        cfg->noise_shaping_stop = 0;
        cfg->use_best_huffman = 0;
        cfg->full_outer_loop = 0;
        break;

    case 8:
        gfp->quality = 7;
        /*lint --fallthrough */
    case 7:            /* use psymodel (for short block and m/s switching), but no noise shapping */
        cfg->noise_shaping = 0;
        cfg->noise_shaping_amp = 0;
        cfg->noise_shaping_stop = 0;
        cfg->use_best_huffman = 0;
        cfg->full_outer_loop = 0;
        if (cfg->vbr == vbr_mt || cfg->vbr == vbr_mtrh) {
            cfg->full_outer_loop  = -1;
        }
        break;

    case 6:
        if (cfg->noise_shaping == 0)
            cfg->noise_shaping = 1;
        cfg->noise_shaping_amp = 0;
        cfg->noise_shaping_stop = 0;
        if (cfg->subblock_gain == -1)
            cfg->subblock_gain = 1;
        cfg->use_best_huffman = 0;
        cfg->full_outer_loop = 0;
        break;

    case 5:
        if (cfg->noise_shaping == 0)
            cfg->noise_shaping = 1;
        cfg->noise_shaping_amp = 0;
        cfg->noise_shaping_stop = 0;
        if (cfg->subblock_gain == -1)
            cfg->subblock_gain = 1;
        cfg->use_best_huffman = 0;
        cfg->full_outer_loop = 0;
        break;

    case 4:
        if (cfg->noise_shaping == 0)
            cfg->noise_shaping = 1;
        cfg->noise_shaping_amp = 0;
        cfg->noise_shaping_stop = 0;
        if (cfg->subblock_gain == -1)
            cfg->subblock_gain = 1;
        cfg->use_best_huffman = 1;
        cfg->full_outer_loop = 0;
        break;

    case 3:
        if (cfg->noise_shaping == 0)
            cfg->noise_shaping = 1;
        cfg->noise_shaping_amp = 1;
        cfg->noise_shaping_stop = 1;
        if (cfg->subblock_gain == -1)
            cfg->subblock_gain = 1;
        cfg->use_best_huffman = 1;
        cfg->full_outer_loop = 0;
        break;

    case 2:
        if (cfg->noise_shaping == 0)
            cfg->noise_shaping = 1;
        if (gfc->sv_qnt.substep_shaping == 0)
            gfc->sv_qnt.substep_shaping = 2;
        cfg->noise_shaping_amp = 1;
        cfg->noise_shaping_stop = 1;
        if (cfg->subblock_gain == -1)
            cfg->subblock_gain = 1;
        cfg->use_best_huffman = 1; /* inner loop */
        cfg->full_outer_loop = 0;
        break;

    case 1:
        if (cfg->noise_shaping == 0)
            cfg->noise_shaping = 1;
        if (gfc->sv_qnt.substep_shaping == 0)
            gfc->sv_qnt.substep_shaping = 2;
        cfg->noise_shaping_amp = 2;
        cfg->noise_shaping_stop = 1;
        if (cfg->subblock_gain == -1)
            cfg->subblock_gain = 1;
        cfg->use_best_huffman = 1;
        cfg->full_outer_loop = 0;
        break;

    case 0:
        if (cfg->noise_shaping == 0)
            cfg->noise_shaping = 1;
        if (gfc->sv_qnt.substep_shaping == 0)
            gfc->sv_qnt.substep_shaping = 2;
        cfg->noise_shaping_amp = 2;
        cfg->noise_shaping_stop = 1;
        if (cfg->subblock_gain == -1)
            cfg->subblock_gain = 1;
        cfg->use_best_huffman = 1; /*type 2 disabled because of it slowness,
                                      in favor of full outer loop search */
        cfg->full_outer_loop = 1;
        break;
    }

}



static double
linear_int(double a, double b, double m)
{
    return a + m * (b - a);
}



/********************************************************************
 *   initialize internal params based on data in gf
 *   (globalflags struct filled in by calling program)
 *
 *  OUTLINE:
 *
 * We first have some complex code to determine bitrate,
 * output samplerate and mode.  It is complicated by the fact
 * that we allow the user to set some or all of these parameters,
 * and need to determine best possible values for the rest of them:
 *
 *  1. set some CPU related flags
 *  2. check if we are mono->mono, stereo->mono or stereo->stereo
 *  3.  compute bitrate and output samplerate:
 *          user may have set compression ratio
 *          user may have set a bitrate
 *          user may have set a output samplerate
 *  4. set some options which depend on output samplerate
 *  5. compute the actual compression ratio
 *  6. set mode based on compression ratio
 *
 *  The remaining code is much simpler - it just sets options
 *  based on the mode & compression ratio:
 *
 *   set allow_diff_short based on mode
 *   select lowpass filter based on compression ratio & mode
 *   set the bitrate index, and min/max bitrates for VBR modes
 *   disable VBR tag if it is not appropriate
 *   initialize the bitstream
 *   initialize scalefac_band data
 *   set sideinfo_len (based on channels, CRC, out_samplerate)
 *   write an id3v2 tag into the bitstream
 *   write VBR tag into the bitstream
 *   set mpeg1/2 flag
 *   estimate the number of frames (based on a lot of data)
 *
 *   now we set more flags:
 *   nspsytune:
 *      see code
 *   VBR modes
 *      see code
 *   CBR/ABR
 *      see code
 *
 *  Finally, we set the algorithm flags based on the gfp->quality value
 *  lame_init_qval(gfp);
 *
 ********************************************************************/
int
lame_init_params(lame_global_flags * gfp)
{

    int     i;
    int     j;
    lame_internal_flags *gfc;
    SessionConfig_t *cfg;

    if (!is_lame_global_flags_valid(gfp)) 
        return -1;

    gfc = gfp->internal_flags;
    if (gfc == 0) 
        return -1;

    if (is_lame_internal_flags_valid(gfc))
        return -1; /* already initialized */

    /* start updating lame internal flags */
    gfc->class_id = LAME_ID;
    gfc->lame_init_params_successful = 0; /* will be set to one, when we get through until the end */

    if (gfp->samplerate_in < 1)
        return -1; /* input sample rate makes no sense */
    if (gfp->num_channels < 1 || 2 < gfp->num_channels)
        return -1; /* number of input channels makes no sense */
    if (gfp->samplerate_out != 0) {
        int   v=0;
        if (SmpFrqIndex(gfp->samplerate_out, &v) < 0)
            return -1; /* output sample rate makes no sense */
    }

    cfg = &gfc->cfg;

    cfg->enforce_min_bitrate = gfp->VBR_hard_min;
    cfg->analysis = gfp->analysis;
    if (cfg->analysis)
        gfp->write_lame_tag = 0;

    /* some file options not allowed if output is: not specified or stdout */
    if (gfc->pinfo != NULL)
        gfp->write_lame_tag = 0; /* disable Xing VBR tag */

    /* report functions */
    gfc->report_msg = gfp->report.msgf;
    gfc->report_dbg = gfp->report.debugf;
    gfc->report_err = gfp->report.errorf;

    if (gfp->asm_optimizations.amd3dnow)
        gfc->CPU_features.AMD_3DNow = has_3DNow();
    else
        gfc->CPU_features.AMD_3DNow = 0;

    if (gfp->asm_optimizations.mmx)
        gfc->CPU_features.MMX = has_MMX();
    else
        gfc->CPU_features.MMX = 0;

    if (gfp->asm_optimizations.sse) {
        gfc->CPU_features.SSE = has_SSE();
        gfc->CPU_features.SSE2 = has_SSE2();
    }
    else {
        gfc->CPU_features.SSE = 0;
        gfc->CPU_features.SSE2 = 0;
    }


    cfg->vbr = gfp->VBR;
    cfg->error_protection = gfp->error_protection;
    cfg->copyright = gfp->copyright;
    cfg->original = gfp->original;
    cfg->extension = gfp->extension;
    cfg->emphasis = gfp->emphasis;

    cfg->channels_in = gfp->num_channels;
    if (cfg->channels_in == 1)
        gfp->mode = MONO;
    cfg->channels_out = (gfp->mode == MONO) ? 1 : 2;
    if (gfp->mode != JOINT_STEREO)
        gfp->force_ms = 0; /* forced mid/side stereo for j-stereo only */
    cfg->force_ms = gfp->force_ms;

    if (cfg->vbr == vbr_off && gfp->VBR_mean_bitrate_kbps != 128 && gfp->brate == 0)
        gfp->brate = gfp->VBR_mean_bitrate_kbps;

    switch (cfg->vbr) {
    case vbr_off:
    case vbr_mtrh:
    case vbr_mt:
        /* these modes can handle free format condition */
        break;
    default:
        gfp->free_format = 0; /* mode can't be mixed with free format */
        break;
    }

    cfg->free_format = gfp->free_format;

    if (cfg->vbr == vbr_off && gfp->brate == 0) {
        /* no bitrate or compression ratio specified, use 11.025 */
        if (EQ(gfp->compression_ratio, 0))
            gfp->compression_ratio = 11.025; /* rate to compress a CD down to exactly 128000 bps */
    }

    /* find bitrate if user specify a compression ratio */
    if (cfg->vbr == vbr_off && gfp->compression_ratio > 0) {

        if (gfp->samplerate_out == 0)
            gfp->samplerate_out = map2MP3Frequency((int) (0.97 * gfp->samplerate_in)); /* round up with a margin of 3% */

        /* choose a bitrate for the output samplerate which achieves
         * specified compression ratio
         */
        gfp->brate = gfp->samplerate_out * 16 * cfg->channels_out / (1.e3 * gfp->compression_ratio);

        /* we need the version for the bitrate table look up */
        cfg->samplerate_index = SmpFrqIndex(gfp->samplerate_out, &cfg->version);
        assert(cfg->samplerate_index >=0);

        if (!cfg->free_format) /* for non Free Format find the nearest allowed bitrate */
            gfp->brate = FindNearestBitrate(gfp->brate, cfg->version, gfp->samplerate_out);
    }
    if (gfp->samplerate_out) {
        if (gfp->samplerate_out < 16000) {
            gfp->VBR_mean_bitrate_kbps = Max(gfp->VBR_mean_bitrate_kbps, 8);
            gfp->VBR_mean_bitrate_kbps = Min(gfp->VBR_mean_bitrate_kbps, 64);
        }
        else if (gfp->samplerate_out < 32000) {
            gfp->VBR_mean_bitrate_kbps = Max(gfp->VBR_mean_bitrate_kbps, 8);
            gfp->VBR_mean_bitrate_kbps = Min(gfp->VBR_mean_bitrate_kbps, 160);
        }
        else {
            gfp->VBR_mean_bitrate_kbps = Max(gfp->VBR_mean_bitrate_kbps, 32);
            gfp->VBR_mean_bitrate_kbps = Min(gfp->VBR_mean_bitrate_kbps, 320);
        }
    }
    /* WORK IN PROGRESS */
    /* mapping VBR scale to internal VBR quality settings */
    if (gfp->samplerate_out == 0 && (cfg->vbr == vbr_mt || cfg->vbr == vbr_mtrh)) {
        float const qval = gfp->VBR_q + gfp->VBR_q_frac;
        struct q_map { int sr_a; float qa, qb, ta, tb; int lp; };
        struct q_map const m[9]
        = { {48000, 0.0,6.5,  0.0,6.5, 23700}
          , {44100, 0.0,6.5,  0.0,6.5, 21780}
          , {32000, 6.5,8.0,  5.2,6.5, 15800}
          , {24000, 8.0,8.5,  5.2,6.0, 11850}
          , {22050, 8.5,9.01, 5.2,6.5, 10892}
          , {16000, 9.01,9.4, 4.9,6.5,  7903}
          , {12000, 9.4,9.6,  4.5,6.0,  5928}
          , {11025, 9.6,9.9,  5.1,6.5,  5446}
          , { 8000, 9.9,10.,  4.9,6.5,  3952}
        };
        for (i = 2; i < 9; ++i) {
            if (gfp->samplerate_in == m[i].sr_a) {
                if (qval < m[i].qa) {
                    double d = qval / m[i].qa;
                    d = d * m[i].ta;
                    gfp->VBR_q = (int)d;
                    gfp->VBR_q_frac = d - gfp->VBR_q;
                }
            }
            if (gfp->samplerate_in >= m[i].sr_a) {
                if (m[i].qa <= qval && qval < m[i].qb) {
                    float const q_ = m[i].qb-m[i].qa;
                    float const t_ = m[i].tb-m[i].ta;
                    double d = m[i].ta + t_ * (qval-m[i].qa) / q_;
                    gfp->VBR_q = (int)d;
                    gfp->VBR_q_frac = d - gfp->VBR_q;
                    gfp->samplerate_out = m[i].sr_a;
                    if (gfp->lowpassfreq == 0) {
                        gfp->lowpassfreq = -1;
                    }
                    break;
                }
            }
        }
    }

    /****************************************************************/
    /* if a filter has not been enabled, see if we should add one: */
    /****************************************************************/
    if (gfp->lowpassfreq == 0) {
        double  lowpass = 16000;
        double  highpass;

        switch (cfg->vbr) {
        case vbr_off:{
                optimum_bandwidth(&lowpass, &highpass, gfp->brate);
                break;
            }
        case vbr_abr:{
                optimum_bandwidth(&lowpass, &highpass, gfp->VBR_mean_bitrate_kbps);
                break;
            }
        case vbr_rh:{
                int const x[11] = {
                    19500, 19000, 18600, 18000, 17500, 16000, 15600, 14900, 12500, 10000, 3950
                };
                if (0 <= gfp->VBR_q && gfp->VBR_q <= 9) {
                    double  a = x[gfp->VBR_q], b = x[gfp->VBR_q + 1], m = gfp->VBR_q_frac;
                    lowpass = linear_int(a, b, m);
                }
                else {
                    lowpass = 19500;
                }
                break;
            }
        case vbr_mtrh:
        case vbr_mt:{
                int const x[11] = {
                    24000, 19500, 18500, 18000, 17500, 17000, 16500, 15600, 15200, 7230, 3950
                };
                if (0 <= gfp->VBR_q && gfp->VBR_q <= 9) {
                    double  a = x[gfp->VBR_q], b = x[gfp->VBR_q + 1], m = gfp->VBR_q_frac;
                    lowpass = linear_int(a, b, m);
                }
                else {
                    lowpass = 21500;
                }
                break;
            }
        default:{
                int const x[11] = {
                    19500, 19000, 18500, 18000, 17500, 16500, 15500, 14500, 12500, 9500, 3950
                };
                if (0 <= gfp->VBR_q && gfp->VBR_q <= 9) {
                    double  a = x[gfp->VBR_q], b = x[gfp->VBR_q + 1], m = gfp->VBR_q_frac;
                    lowpass = linear_int(a, b, m);
                }
                else {
                    lowpass = 19500;
                }
            }
        }

        if (gfp->mode == MONO && (cfg->vbr == vbr_off || cfg->vbr == vbr_abr))
            lowpass *= 1.5;

        gfp->lowpassfreq = lowpass;
    }

    if (gfp->samplerate_out == 0) {
        if (2 * gfp->lowpassfreq > gfp->samplerate_in) {
            gfp->lowpassfreq = gfp->samplerate_in / 2;
        }
        gfp->samplerate_out = optimum_samplefreq((int) gfp->lowpassfreq, gfp->samplerate_in);
    }
    if (cfg->vbr == vbr_mt || cfg->vbr == vbr_mtrh) {
        gfp->lowpassfreq = Min(24000, gfp->lowpassfreq);
    }
    else {
        gfp->lowpassfreq = Min(20500, gfp->lowpassfreq);
    }
    gfp->lowpassfreq = Min(gfp->samplerate_out / 2, gfp->lowpassfreq);

    if (cfg->vbr == vbr_off) {
        gfp->compression_ratio = gfp->samplerate_out * 16 * cfg->channels_out / (1.e3 * gfp->brate);
    }
    if (cfg->vbr == vbr_abr) {
        gfp->compression_ratio =
            gfp->samplerate_out * 16 * cfg->channels_out / (1.e3 * gfp->VBR_mean_bitrate_kbps);
    }

    cfg->disable_reservoir = gfp->disable_reservoir;
    cfg->lowpassfreq = gfp->lowpassfreq;
    cfg->highpassfreq = gfp->highpassfreq;
    cfg->samplerate_in = gfp->samplerate_in;
    cfg->samplerate_out = gfp->samplerate_out;
    cfg->mode_gr = cfg->samplerate_out <= 24000 ? 1 : 2; /* Number of granules per frame */


    /*
     *  sample freq       bitrate     compression ratio
     *     [kHz]      [kbps/channel]   for 16 bit input
     *     44.1            56               12.6
     *     44.1            64               11.025
     *     44.1            80                8.82
     *     22.05           24               14.7
     *     22.05           32               11.025
     *     22.05           40                8.82
     *     16              16               16.0
     *     16              24               10.667
     *
     */
    /*
     *  For VBR, take a guess at the compression_ratio.
     *  For example:
     *
     *    VBR_q    compression     like
     *     -        4.4         320 kbps/44 kHz
     *   0...1      5.5         256 kbps/44 kHz
     *     2        7.3         192 kbps/44 kHz
     *     4        8.8         160 kbps/44 kHz
     *     6       11           128 kbps/44 kHz
     *     9       14.7          96 kbps
     *
     *  for lower bitrates, downsample with --resample
     */

    switch (cfg->vbr) {
    case vbr_mt:
    case vbr_rh:
    case vbr_mtrh:
        {
            /*numbers are a bit strange, but they determine the lowpass value */
            FLOAT const cmp[] = { 5.7, 6.5, 7.3, 8.2, 10, 11.9, 13, 14, 15, 16.5 };
            gfp->compression_ratio = cmp[gfp->VBR_q];
        }
        break;
    case vbr_abr:
        gfp->compression_ratio =
            cfg->samplerate_out * 16 * cfg->channels_out / (1.e3 * gfp->VBR_mean_bitrate_kbps);
        break;
    default:
        gfp->compression_ratio = cfg->samplerate_out * 16 * cfg->channels_out / (1.e3 * gfp->brate);
        break;
    }


    /* mode = -1 (not set by user) or
     * mode = MONO (because of only 1 input channel).
     * If mode has not been set, then select J-STEREO
     */
    if (gfp->mode == NOT_SET) {
        gfp->mode = JOINT_STEREO;
    }

    cfg->mode = gfp->mode;


    /* apply user driven high pass filter */
    if (cfg->highpassfreq > 0) {
        cfg->highpass1 = 2. * cfg->highpassfreq;

        if (gfp->highpasswidth >= 0)
            cfg->highpass2 = 2. * (cfg->highpassfreq + gfp->highpasswidth);
        else            /* 0% above on default */
            cfg->highpass2 = (1 + 0.00) * 2. * cfg->highpassfreq;

        cfg->highpass1 /= cfg->samplerate_out;
        cfg->highpass2 /= cfg->samplerate_out;
    }
    else {
        cfg->highpass1 = 0;
        cfg->highpass2 = 0;
    }
    /* apply user driven low pass filter */
    cfg->lowpass1 = 0;
    cfg->lowpass2 = 0;
    if (cfg->lowpassfreq > 0 && cfg->lowpassfreq < (cfg->samplerate_out / 2) ) {
        cfg->lowpass2 = 2. * cfg->lowpassfreq;
        if (gfp->lowpasswidth >= 0) {
            cfg->lowpass1 = 2. * (cfg->lowpassfreq - gfp->lowpasswidth);
            if (cfg->lowpass1 < 0) /* has to be >= 0 */
                cfg->lowpass1 = 0;
        }
        else {          /* 0% below on default */
            cfg->lowpass1 = (1 - 0.00) * 2. * cfg->lowpassfreq;
        }
        cfg->lowpass1 /= cfg->samplerate_out;
        cfg->lowpass2 /= cfg->samplerate_out;
    }




  /**********************************************************************/
    /* compute info needed for polyphase filter (filter type==0, default) */
  /**********************************************************************/
    lame_init_params_ppflt(gfc);


  /*******************************************************
   * samplerate and bitrate index
   *******************************************************/
    cfg->samplerate_index = SmpFrqIndex(cfg->samplerate_out, &cfg->version);
    assert(cfg->samplerate_index >= 0);

    if (cfg->vbr == vbr_off) {
        if (cfg->free_format) {
            gfc->ov_enc.bitrate_index = 0;
        }
        else {
            gfp->brate = FindNearestBitrate(gfp->brate, cfg->version, cfg->samplerate_out);
            gfc->ov_enc.bitrate_index = BitrateIndex(gfp->brate, cfg->version, cfg->samplerate_out);
            if (gfc->ov_enc.bitrate_index <= 0) {
                /* This never happens, because of preceding FindNearestBitrate!
                 * But, set a sane value, just in case
                 */
                assert(0);
                gfc->ov_enc.bitrate_index = 8;
            }
        }
    }
    else {
        gfc->ov_enc.bitrate_index = 1;
    }

    init_bit_stream_w(gfc);

    j = cfg->samplerate_index + (3 * cfg->version) + 6 * (cfg->samplerate_out < 16000);
    for (i = 0; i < SBMAX_l + 1; i++)
        gfc->scalefac_band.l[i] = sfBandIndex[j].l[i];

    for (i = 0; i < PSFB21 + 1; i++) {
        int const size = (gfc->scalefac_band.l[22] - gfc->scalefac_band.l[21]) / PSFB21;
        int const start = gfc->scalefac_band.l[21] + i * size;
        gfc->scalefac_band.psfb21[i] = start;
    }
    gfc->scalefac_band.psfb21[PSFB21] = 576;

    for (i = 0; i < SBMAX_s + 1; i++)
        gfc->scalefac_band.s[i] = sfBandIndex[j].s[i];

    for (i = 0; i < PSFB12 + 1; i++) {
        int const size = (gfc->scalefac_band.s[13] - gfc->scalefac_band.s[12]) / PSFB12;
        int const start = gfc->scalefac_band.s[12] + i * size;
        gfc->scalefac_band.psfb12[i] = start;
    }
    gfc->scalefac_band.psfb12[PSFB12] = 192;

    /* determine the mean bitrate for main data */
    if (cfg->mode_gr == 2) /* MPEG 1 */
        cfg->sideinfo_len = (cfg->channels_out == 1) ? 4 + 17 : 4 + 32;
    else                /* MPEG 2 */
        cfg->sideinfo_len = (cfg->channels_out == 1) ? 4 + 9 : 4 + 17;

    if (cfg->error_protection)
        cfg->sideinfo_len += 2;

    {
        int     k;

        for (k = 0; k < 19; k++)
            gfc->sv_enc.pefirbuf[k] = 700 * cfg->mode_gr * cfg->channels_out;

        if (gfp->ATHtype == -1)
            gfp->ATHtype = 4;
    }

    assert(gfp->VBR_q <= 9);
    assert(gfp->VBR_q >= 0);

    switch (cfg->vbr) {

    case vbr_mt:
    case vbr_mtrh:{
            if (gfp->strict_ISO < 0) {
                gfp->strict_ISO = MDB_MAXIMUM;
            }
            if (gfp->useTemporal < 0) {
                gfp->useTemporal = 0; /* off by default for this VBR mode */
            }

            (void) apply_preset(gfp, 500 - (gfp->VBR_q * 10), 0);
            /*  The newer VBR code supports only a limited
               subset of quality levels:
               9-5=5 are the same, uses x^3/4 quantization
               4-0=0 are the same  5 plus best huffman divide code
             */
            if (gfp->quality < 0)
                gfp->quality = LAME_DEFAULT_QUALITY;
            if (gfp->quality < 5)
                gfp->quality = 0;
            if (gfp->quality > 7)
                gfp->quality = 7;

            /*  sfb21 extra only with MPEG-1 at higher sampling rates
             */
            if (gfp->experimentalY)
                gfc->sv_qnt.sfb21_extra = 0;
            else
                gfc->sv_qnt.sfb21_extra = (cfg->samplerate_out > 44000);

            break;

        }
    case vbr_rh:{

            (void) apply_preset(gfp, 500 - (gfp->VBR_q * 10), 0);

            /*  sfb21 extra only with MPEG-1 at higher sampling rates
             */
            if (gfp->experimentalY)
                gfc->sv_qnt.sfb21_extra = 0;
            else
                gfc->sv_qnt.sfb21_extra = (cfg->samplerate_out > 44000);

            /*  VBR needs at least the output of GPSYCHO,
             *  so we have to garantee that by setting a minimum
             *  quality level, actually level 6 does it.
             *  down to level 6
             */
            if (gfp->quality > 6)
                gfp->quality = 6;


            if (gfp->quality < 0)
                gfp->quality = LAME_DEFAULT_QUALITY;

            break;
        }

    default:           /* cbr/abr */  {

            /*  no sfb21 extra with CBR code
             */
            gfc->sv_qnt.sfb21_extra = 0;

            if (gfp->quality < 0)
                gfp->quality = LAME_DEFAULT_QUALITY;


            if (cfg->vbr == vbr_off)
                (void) lame_set_VBR_mean_bitrate_kbps(gfp, gfp->brate);
            /* second, set parameters depending on bitrate */
            (void) apply_preset(gfp, gfp->VBR_mean_bitrate_kbps, 0);
            gfp->VBR = cfg->vbr;

            break;
        }
    }

    /*initialize default values common for all modes */

    gfc->sv_qnt.mask_adjust = gfp->maskingadjust;
    gfc->sv_qnt.mask_adjust_short = gfp->maskingadjust_short;

    /*  just another daily changing developer switch  */
    if (gfp->tune) {
        gfc->sv_qnt.mask_adjust += gfp->tune_value_a;
        gfc->sv_qnt.mask_adjust_short += gfp->tune_value_a;
    }


    if (cfg->vbr != vbr_off) { /* choose a min/max bitrate for VBR */
        /* if the user didn't specify VBR_max_bitrate: */
        cfg->vbr_min_bitrate_index = 1; /* default: allow   8 kbps (MPEG-2) or  32 kbps (MPEG-1) */
        cfg->vbr_max_bitrate_index = 14; /* default: allow 160 kbps (MPEG-2) or 320 kbps (MPEG-1) */
        if (cfg->samplerate_out < 16000)
            cfg->vbr_max_bitrate_index = 8; /* default: allow 64 kbps (MPEG-2.5) */
        if (gfp->VBR_min_bitrate_kbps) {
            gfp->VBR_min_bitrate_kbps =
                FindNearestBitrate(gfp->VBR_min_bitrate_kbps, cfg->version, cfg->samplerate_out);
            cfg->vbr_min_bitrate_index =
                BitrateIndex(gfp->VBR_min_bitrate_kbps, cfg->version, cfg->samplerate_out);
            if (cfg->vbr_min_bitrate_index < 0) {
                /* This never happens, because of preceding FindNearestBitrate!
                 * But, set a sane value, just in case
                 */
                assert(0);
                cfg->vbr_min_bitrate_index = 1;
            }
        }
        if (gfp->VBR_max_bitrate_kbps) {
            gfp->VBR_max_bitrate_kbps =
                FindNearestBitrate(gfp->VBR_max_bitrate_kbps, cfg->version, cfg->samplerate_out);
            cfg->vbr_max_bitrate_index =
                BitrateIndex(gfp->VBR_max_bitrate_kbps, cfg->version, cfg->samplerate_out);
            if (cfg->vbr_max_bitrate_index < 0) {
                /* This never happens, because of preceding FindNearestBitrate!
                 * But, set a sane value, just in case
                 */
                assert(0);
                cfg->vbr_max_bitrate_index = cfg->samplerate_out < 16000 ? 8 : 14;
            }
        }
        gfp->VBR_min_bitrate_kbps = bitrate_table[cfg->version][cfg->vbr_min_bitrate_index];
        gfp->VBR_max_bitrate_kbps = bitrate_table[cfg->version][cfg->vbr_max_bitrate_index];
        gfp->VBR_mean_bitrate_kbps =
            Min(bitrate_table[cfg->version][cfg->vbr_max_bitrate_index],
                gfp->VBR_mean_bitrate_kbps);
        gfp->VBR_mean_bitrate_kbps =
            Max(bitrate_table[cfg->version][cfg->vbr_min_bitrate_index],
                gfp->VBR_mean_bitrate_kbps);
    }

    cfg->preset = gfp->preset;
    cfg->write_lame_tag = gfp->write_lame_tag;
    gfc->sv_qnt.substep_shaping = gfp->substep_shaping;
    cfg->noise_shaping = gfp->noise_shaping;
    cfg->subblock_gain = gfp->subblock_gain;
    cfg->use_best_huffman = gfp->use_best_huffman;
    cfg->avg_bitrate = gfp->brate;
    cfg->vbr_avg_bitrate_kbps = gfp->VBR_mean_bitrate_kbps;
    cfg->compression_ratio = gfp->compression_ratio;

    /* initialize internal qval settings */
    lame_init_qval(gfp);


    /*  automatic ATH adjustment on
     */
    if (gfp->athaa_type < 0)
        gfc->ATH->use_adjust = 3;
    else
        gfc->ATH->use_adjust = gfp->athaa_type;


    /* initialize internal adaptive ATH settings  -jd */
    gfc->ATH->aa_sensitivity_p = pow(10.0, gfp->athaa_sensitivity / -10.0);


    if (gfp->short_blocks == short_block_not_set) {
        gfp->short_blocks = short_block_allowed;
    }

    /*Note Jan/2003: Many hardware decoders cannot handle short blocks in regular
       stereo mode unless they are coupled (same type in both channels)
       it is a rare event (1 frame per min. or so) that LAME would use
       uncoupled short blocks, so lets turn them off until we decide
       how to handle this.  No other encoders allow uncoupled short blocks,
       even though it is in the standard.  */
    /* rh 20040217: coupling makes no sense for mono and dual-mono streams
     */
    if (gfp->short_blocks == short_block_allowed
        && (cfg->mode == JOINT_STEREO || cfg->mode == STEREO)) {
        gfp->short_blocks = short_block_coupled;
    }

    cfg->short_blocks = gfp->short_blocks;


    if (lame_get_quant_comp(gfp) < 0)
        (void) lame_set_quant_comp(gfp, 1);
    if (lame_get_quant_comp_short(gfp) < 0)
        (void) lame_set_quant_comp_short(gfp, 0);

    if (lame_get_msfix(gfp) < 0)
        lame_set_msfix(gfp, 0);

    /* select psychoacoustic model */
    (void) lame_set_exp_nspsytune(gfp, lame_get_exp_nspsytune(gfp) | 1);

    if (gfp->ATHtype < 0)
        gfp->ATHtype = 4;

    if (gfp->ATHcurve < 0)
        gfp->ATHcurve = 4;

    if (gfp->interChRatio < 0)
        gfp->interChRatio = 0;

    if (gfp->useTemporal < 0)
        gfp->useTemporal = 1; /* on by default */


    cfg->interChRatio = gfp->interChRatio;
    cfg->msfix = gfp->msfix;
    cfg->ATH_offset_db = 0-gfp->ATH_lower_db;
    cfg->ATH_offset_factor = powf(10.f, cfg->ATH_offset_db * 0.1f);
    cfg->ATHcurve = gfp->ATHcurve;
    cfg->ATHtype = gfp->ATHtype;
    cfg->ATHonly = gfp->ATHonly;
    cfg->ATHshort = gfp->ATHshort;
    cfg->noATH = gfp->noATH;

    cfg->quant_comp = gfp->quant_comp;
    cfg->quant_comp_short = gfp->quant_comp_short;

    cfg->use_temporal_masking_effect = gfp->useTemporal;
    if (cfg->mode == JOINT_STEREO) {
        cfg->use_safe_joint_stereo = gfp->exp_nspsytune & 2;
    }
    else {
        cfg->use_safe_joint_stereo = 0;
    }
    {
        cfg->adjust_bass_db = (gfp->exp_nspsytune >> 2) & 63;
        if (cfg->adjust_bass_db >= 32.f)
            cfg->adjust_bass_db -= 64.f;
        cfg->adjust_bass_db *= 0.25f;

        cfg->adjust_alto_db = (gfp->exp_nspsytune >> 8) & 63;
        if (cfg->adjust_alto_db >= 32.f)
            cfg->adjust_alto_db -= 64.f;
        cfg->adjust_alto_db *= 0.25f;

        cfg->adjust_treble_db = (gfp->exp_nspsytune >> 14) & 63;
        if (cfg->adjust_treble_db >= 32.f)
            cfg->adjust_treble_db -= 64.f;
        cfg->adjust_treble_db *= 0.25f;

        /*  to be compatible with Naoki's original code, the next 6 bits
         *  define only the amount of changing treble for sfb21 */
        cfg->adjust_sfb21_db = (gfp->exp_nspsytune >> 20) & 63;
        if (cfg->adjust_sfb21_db >= 32.f)
            cfg->adjust_sfb21_db -= 64.f;
        cfg->adjust_sfb21_db *= 0.25f;
        cfg->adjust_sfb21_db += cfg->adjust_treble_db;
    }

    /* Setting up the PCM input data transform matrix, to apply 
     * user defined re-scaling, and or two-to-one channel downmix.
     */
    {
        FLOAT   m[2][2] = { {1.0f, 0.0f}, {0.0f, 1.0f} };

        /* user selected scaling of the samples */
        m[0][0] *= gfp->scale;
        m[0][1] *= gfp->scale;
        m[1][0] *= gfp->scale;
        m[1][1] *= gfp->scale;
        /* user selected scaling of the channel 0 (left) samples */
        m[0][0] *= gfp->scale_left;
        m[0][1] *= gfp->scale_left;
        /* user selected scaling of the channel 1 (right) samples */
        m[1][0] *= gfp->scale_right;
        m[1][1] *= gfp->scale_right;
        /* Downsample to Mono if 2 channels in and 1 channel out */
        if (cfg->channels_in == 2 && cfg->channels_out == 1) {
            m[0][0] = 0.5f * (m[0][0] + m[1][0]);
            m[0][1] = 0.5f * (m[0][1] + m[1][1]);
            m[1][0] = 0;
            m[1][1] = 0;
        }
        cfg->pcm_transform[0][0] = m[0][0];
        cfg->pcm_transform[0][1] = m[0][1];
        cfg->pcm_transform[1][0] = m[1][0];
        cfg->pcm_transform[1][1] = m[1][1];
    }

    /* padding method as described in
     * "MPEG-Layer3 / Bitstream Syntax and Decoding"
     * by Martin Sieler, Ralph Sperschneider
     *
     * note: there is no padding for the very first frame
     *
     * Robert Hegemann 2000-06-22
     */
    gfc->sv_enc.slot_lag = gfc->sv_enc.frac_SpF = 0;
    if (cfg->vbr == vbr_off)
        gfc->sv_enc.slot_lag = gfc->sv_enc.frac_SpF
            = ((cfg->version + 1) * 72000L * cfg->avg_bitrate) % cfg->samplerate_out;

    (void) lame_init_bitstream(gfp);

    iteration_init(gfc);
    (void) psymodel_init(gfp);

    cfg->buffer_constraint = get_max_frame_buffer_size_by_constraint(cfg, gfp->strict_ISO);


    cfg->findReplayGain = gfp->findReplayGain;
    cfg->decode_on_the_fly = gfp->decode_on_the_fly;

    if (cfg->decode_on_the_fly)
        cfg->findPeakSample = 1;

    if (cfg->findReplayGain) {
        if (InitGainAnalysis(gfc->sv_rpg.rgdata, cfg->samplerate_out) == INIT_GAIN_ANALYSIS_ERROR) {
            /* Actually this never happens, our samplerates are the ones RG accepts!
             * But just in case, turn RG off
             */
            assert(0);
            cfg->findReplayGain = 0;
        }
    }

#ifdef DECODE_ON_THE_FLY
    if (cfg->decode_on_the_fly && !gfp->decode_only) {
        if (gfc->hip) {
            hip_decode_exit(gfc->hip);
        }
        gfc->hip = hip_decode_init();
        /* report functions */
        hip_set_errorf(gfc->hip, gfp->report.errorf);
        hip_set_debugf(gfc->hip, gfp->report.debugf);
        hip_set_msgf(gfc->hip, gfp->report.msgf);
    }
#endif
    /* updating lame internal flags finished successful */
    gfc->lame_init_params_successful = 1;
    return 0;
}

static void
concatSep(char* dest, char const* sep, char const* str)
{
    if (*dest != 0) strcat(dest, sep);
    strcat(dest, str);
}

/*
 *  print_config
 *
 *  Prints some selected information about the coding parameters via
 *  the macro command MSGF(), which is currently mapped to lame_errorf
 *  (reports via a error function?), which is a printf-like function
 *  for <stderr>.
 */

void
lame_print_config(const lame_global_flags * gfp)
{
    lame_internal_flags const *const gfc = gfp->internal_flags;
    SessionConfig_t const *const cfg = &gfc->cfg;
    double const out_samplerate = cfg->samplerate_out;
    double const in_samplerate = cfg->samplerate_in;

    MSGF(gfc, "LAME %s %s (%s)\n", get_lame_version(), get_lame_os_bitness(), get_lame_url());

#if (LAME_ALPHA_VERSION)
    MSGF(gfc, "warning: alpha versions should be used for testing only\n");
#endif
    if (gfc->CPU_features.MMX
        || gfc->CPU_features.AMD_3DNow || gfc->CPU_features.SSE || gfc->CPU_features.SSE2) {
        char    text[256] = { 0 };
        int     fft_asm_used = 0;
#ifdef HAVE_NASM
        if (gfc->CPU_features.AMD_3DNow) {
            fft_asm_used = 1;
        }
        else if (gfc->CPU_features.SSE) {
            fft_asm_used = 2;
        }
#else
# if defined( HAVE_XMMINTRIN_H ) && defined( MIN_ARCH_SSE )
        {
            fft_asm_used = 3;
        }
# endif
#endif
        if (gfc->CPU_features.MMX) {
#ifdef MMX_choose_table
            concatSep(text, ", ", "MMX (ASM used)");
#else
            concatSep(text, ", ", "MMX");
#endif
        }
        if (gfc->CPU_features.AMD_3DNow) {
            concatSep(text, ", ", (fft_asm_used == 1) ? "3DNow! (ASM used)" : "3DNow!");
        }
        if (gfc->CPU_features.SSE) {
#if defined(HAVE_XMMINTRIN_H)
            concatSep(text, ", ", "SSE (ASM used)");
#else
            concatSep(text, ", ", (fft_asm_used == 2) ? "SSE (ASM used)" : "SSE");
#endif
        }
        if (gfc->CPU_features.SSE2) {
            concatSep(text, ", ", (fft_asm_used == 3) ? "SSE2 (ASM used)" : "SSE2");
        }
        MSGF(gfc, "CPU features: %s\n", text);
    }

    if (cfg->channels_in == 2 && cfg->channels_out == 1 /* mono */ ) {
        MSGF(gfc, "Autoconverting from stereo to mono. Setting encoding to mono mode.\n");
    }

    if (isResamplingNecessary(cfg)) {
        MSGF(gfc, "Resampling:  input %g kHz  output %g kHz\n",
             1.e-3 * in_samplerate, 1.e-3 * out_samplerate);
    }

    if (cfg->highpass2 > 0.)
        MSGF(gfc,
             "Using polyphase highpass filter, transition band: %5.0f Hz - %5.0f Hz\n",
             0.5 * cfg->highpass1 * out_samplerate, 0.5 * cfg->highpass2 * out_samplerate);
    if (0. < cfg->lowpass1 || 0. < cfg->lowpass2) {
        MSGF(gfc,
             "Using polyphase lowpass filter, transition band: %5.0f Hz - %5.0f Hz\n",
             0.5 * cfg->lowpass1 * out_samplerate, 0.5 * cfg->lowpass2 * out_samplerate);
    }
    else {
        MSGF(gfc, "polyphase lowpass filter disabled\n");
    }

    if (cfg->free_format) {
        MSGF(gfc, "Warning: many decoders cannot handle free format bitstreams\n");
        if (cfg->avg_bitrate > 320) {
            MSGF(gfc,
                 "Warning: many decoders cannot handle free format bitrates >320 kbps (see documentation)\n");
        }
    }
}


/**     rh:
 *      some pretty printing is very welcome at this point!
 *      so, if someone is willing to do so, please do it!
 *      add more, if you see more...
 */
void
lame_print_internals(const lame_global_flags * gfp)
{
    lame_internal_flags const *const gfc = gfp->internal_flags;
    SessionConfig_t const *const cfg = &gfc->cfg;
    const char *pc = "";

    /*  compiler/processor optimizations, operational, etc.
     */
    MSGF(gfc, "\nmisc:\n\n");

    MSGF(gfc, "\tscaling: %g\n", gfp->scale);
    MSGF(gfc, "\tch0 (left) scaling: %g\n", gfp->scale_left);
    MSGF(gfc, "\tch1 (right) scaling: %g\n", gfp->scale_right);
    switch (cfg->use_best_huffman) {
    default:
        pc = "normal";
        break;
    case 1:
        pc = "best (outside loop)";
        break;
    case 2:
        pc = "best (inside loop, slow)";
        break;
    }
    MSGF(gfc, "\thuffman search: %s\n", pc);
    MSGF(gfc, "\texperimental Y=%d\n", gfp->experimentalY);
    MSGF(gfc, "\t...\n");

    /*  everything controlling the stream format
     */
    MSGF(gfc, "\nstream format:\n\n");
    switch (cfg->version) {
    case 0:
        pc = "2.5";
        break;
    case 1:
        pc = "1";
        break;
    case 2:
        pc = "2";
        break;
    default:
        pc = "?";
        break;
    }
    MSGF(gfc, "\tMPEG-%s Layer 3\n", pc);
    switch (cfg->mode) {
    case JOINT_STEREO:
        pc = "joint stereo";
        break;
    case STEREO:
        pc = "stereo";
        break;
    case DUAL_CHANNEL:
        pc = "dual channel";
        break;
    case MONO:
        pc = "mono";
        break;
    case NOT_SET:
        pc = "not set (error)";
        break;
    default:
        pc = "unknown (error)";
        break;
    }
    MSGF(gfc, "\t%d channel - %s\n", cfg->channels_out, pc);

    switch (cfg->vbr) {
    case vbr_off:
        pc = "off";
        break;
    default:
        pc = "all";
        break;
    }
    MSGF(gfc, "\tpadding: %s\n", pc);

    if (vbr_default == cfg->vbr)
        pc = "(default)";
    else if (cfg->free_format)
        pc = "(free format)";
    else
        pc = "";
    switch (cfg->vbr) {
    case vbr_off:
        MSGF(gfc, "\tconstant bitrate - CBR %s\n", pc);
        break;
    case vbr_abr:
        MSGF(gfc, "\tvariable bitrate - ABR %s\n", pc);
        break;
    case vbr_rh:
        MSGF(gfc, "\tvariable bitrate - VBR rh %s\n", pc);
        break;
    case vbr_mt:
        MSGF(gfc, "\tvariable bitrate - VBR mt %s\n", pc);
        break;
    case vbr_mtrh:
        MSGF(gfc, "\tvariable bitrate - VBR mtrh %s\n", pc);
        break;
    default:
        MSGF(gfc, "\t ?? oops, some new one ?? \n");
        break;
    }
    if (cfg->write_lame_tag)
        MSGF(gfc, "\tusing LAME Tag\n");
    MSGF(gfc, "\t...\n");

    /*  everything controlling psychoacoustic settings, like ATH, etc.
     */
    MSGF(gfc, "\npsychoacoustic:\n\n");

    switch (cfg->short_blocks) {
    default:
    case short_block_not_set:
        pc = "?";
        break;
    case short_block_allowed:
        pc = "allowed";
        break;
    case short_block_coupled:
        pc = "channel coupled";
        break;
    case short_block_dispensed:
        pc = "dispensed";
        break;
    case short_block_forced:
        pc = "forced";
        break;
    }
    MSGF(gfc, "\tusing short blocks: %s\n", pc);
    MSGF(gfc, "\tsubblock gain: %d\n", cfg->subblock_gain);
    MSGF(gfc, "\tadjust masking: %g dB\n", gfc->sv_qnt.mask_adjust);
    MSGF(gfc, "\tadjust masking short: %g dB\n", gfc->sv_qnt.mask_adjust_short);
    MSGF(gfc, "\tquantization comparison: %d\n", cfg->quant_comp);
    MSGF(gfc, "\t ^ comparison short blocks: %d\n", cfg->quant_comp_short);
    MSGF(gfc, "\tnoise shaping: %d\n", cfg->noise_shaping);
    MSGF(gfc, "\t ^ amplification: %d\n", cfg->noise_shaping_amp);
    MSGF(gfc, "\t ^ stopping: %d\n", cfg->noise_shaping_stop);

    pc = "using";
    if (cfg->ATHshort)
        pc = "the only masking for short blocks";
    if (cfg->ATHonly)
        pc = "the only masking";
    if (cfg->noATH)
        pc = "not used";
    MSGF(gfc, "\tATH: %s\n", pc);
    MSGF(gfc, "\t ^ type: %d\n", cfg->ATHtype);
    MSGF(gfc, "\t ^ shape: %g%s\n", cfg->ATHcurve, " (only for type 4)");
    MSGF(gfc, "\t ^ level adjustement: %g dB\n", cfg->ATH_offset_db);
    MSGF(gfc, "\t ^ adjust type: %d\n", gfc->ATH->use_adjust);
    MSGF(gfc, "\t ^ adjust sensitivity power: %f\n", gfc->ATH->aa_sensitivity_p);

    MSGF(gfc, "\texperimental psy tunings by Naoki Shibata\n");
    MSGF(gfc, "\t   adjust masking bass=%g dB, alto=%g dB, treble=%g dB, sfb21=%g dB\n",
         10 * log10(gfc->sv_qnt.longfact[0]),
         10 * log10(gfc->sv_qnt.longfact[7]),
         10 * log10(gfc->sv_qnt.longfact[14]), 10 * log10(gfc->sv_qnt.longfact[21]));

    pc = cfg->use_temporal_masking_effect ? "yes" : "no";
    MSGF(gfc, "\tusing temporal masking effect: %s\n", pc);
    MSGF(gfc, "\tinterchannel masking ratio: %g\n", cfg->interChRatio);
    MSGF(gfc, "\t...\n");

    /*  that's all ?
     */
    MSGF(gfc, "\n");
    return;
}


static void
save_gain_values(lame_internal_flags * gfc)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    RpgStateVar_t const *const rsv = &gfc->sv_rpg;
    RpgResult_t *const rov = &gfc->ov_rpg;
    /* save the ReplayGain value */
    if (cfg->findReplayGain) {
        FLOAT const RadioGain = (FLOAT) GetTitleGain(rsv->rgdata);
        if (NEQ(RadioGain, GAIN_NOT_ENOUGH_SAMPLES)) {
            rov->RadioGain = (int) floor(RadioGain * 10.0 + 0.5); /* round to nearest */
        }
        else {
            rov->RadioGain = 0;
        }
    }

    /* find the gain and scale change required for no clipping */
    if (cfg->findPeakSample) {
        rov->noclipGainChange = (int) ceil(log10(rov->PeakSample / 32767.0) * 20.0 * 10.0); /* round up */

        if (rov->noclipGainChange > 0) { /* clipping occurs */
            rov->noclipScale = floor((32767.0f / rov->PeakSample) * 100.0f) / 100.0f; /* round down */
        }
        else            /* no clipping */
            rov->noclipScale = -1.0f;
    }
}



static int
update_inbuffer_size(lame_internal_flags * gfc, const int nsamples)
{
    EncStateVar_t *const esv = &gfc->sv_enc;
    if (esv->in_buffer_0 == 0 || esv->in_buffer_nsamples < nsamples) {
        if (esv->in_buffer_0) {
            free(esv->in_buffer_0);
        }
        if (esv->in_buffer_1) {
            free(esv->in_buffer_1);
        }
        esv->in_buffer_0 = lame_calloc(sample_t, nsamples);
        esv->in_buffer_1 = lame_calloc(sample_t, nsamples);
        esv->in_buffer_nsamples = nsamples;
    }
    if (esv->in_buffer_0 == NULL || esv->in_buffer_1 == NULL) {
        if (esv->in_buffer_0) {
            free(esv->in_buffer_0);
        }
        if (esv->in_buffer_1) {
            free(esv->in_buffer_1);
        }
        esv->in_buffer_0 = 0;
        esv->in_buffer_1 = 0;
        esv->in_buffer_nsamples = 0;
        ERRORF(gfc, "Error: can't allocate in_buffer buffer\n");
        return -2;
    }
    return 0;
}


static int
calcNeeded(SessionConfig_t const * cfg)
{
    int     mf_needed;
    int     pcm_samples_per_frame = 576 * cfg->mode_gr;

    /* some sanity checks */
#if ENCDELAY < MDCTDELAY
# error ENCDELAY is less than MDCTDELAY, see encoder.h
#endif
#if FFTOFFSET > BLKSIZE
# error FFTOFFSET is greater than BLKSIZE, see encoder.h
#endif

    mf_needed = BLKSIZE + pcm_samples_per_frame - FFTOFFSET; /* amount needed for FFT */
    /*mf_needed = Max(mf_needed, 286 + 576 * (1 + gfc->mode_gr)); */
    mf_needed = Max(mf_needed, 512 + pcm_samples_per_frame - 32);

    assert(MFSIZE >= mf_needed);
    
    return mf_needed;
}


/*
 * THE MAIN LAME ENCODING INTERFACE
 * mt 3/00
 *
 * input pcm data, output (maybe) mp3 frames.
 * This routine handles all buffering, resampling and filtering for you.
 * The required mp3buffer_size can be computed from num_samples,
 * samplerate and encoding rate, but here is a worst case estimate:
 *
 * mp3buffer_size in bytes = 1.25*num_samples + 7200
 *
 * return code = number of bytes output in mp3buffer.  can be 0
 *
 * NOTE: this routine uses LAME's internal PCM data representation,
 * 'sample_t'.  It should not be used by any application.
 * applications should use lame_encode_buffer(),
 *                         lame_encode_buffer_float()
 *                         lame_encode_buffer_int()
 * etc... depending on what type of data they are working with.
*/
static int
lame_encode_buffer_sample_t(lame_internal_flags * gfc,
                            int nsamples, unsigned char *mp3buf, const int mp3buf_size)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    EncStateVar_t *const esv = &gfc->sv_enc;
    int     pcm_samples_per_frame = 576 * cfg->mode_gr;
    int     mp3size = 0, ret, i, ch, mf_needed;
    int     mp3out;
    sample_t *mfbuf[2];
    sample_t *in_buffer[2];

    if (gfc->class_id != LAME_ID)
        return -3;

    if (nsamples == 0)
        return 0;

    /* copy out any tags that may have been written into bitstream */
    {   /* if user specifed buffer size = 0, dont check size */
        int const buf_size = mp3buf_size == 0 ? INT_MAX : mp3buf_size;
        mp3out = copy_buffer(gfc, mp3buf, buf_size, 0);
    }
    if (mp3out < 0)
        return mp3out;  /* not enough buffer space */
    mp3buf += mp3out;
    mp3size += mp3out;

    in_buffer[0] = esv->in_buffer_0;
    in_buffer[1] = esv->in_buffer_1;

    mf_needed = calcNeeded(cfg);

    mfbuf[0] = esv->mfbuf[0];
    mfbuf[1] = esv->mfbuf[1];

    while (nsamples > 0) {
        sample_t const *in_buffer_ptr[2];
        int     n_in = 0;    /* number of input samples processed with fill_buffer */
        int     n_out = 0;   /* number of samples output with fill_buffer */
        /* n_in <> n_out if we are resampling */

        in_buffer_ptr[0] = in_buffer[0];
        in_buffer_ptr[1] = in_buffer[1];
        /* copy in new samples into mfbuf, with resampling */
        fill_buffer(gfc, mfbuf, &in_buffer_ptr[0], nsamples, &n_in, &n_out);

        /* compute ReplayGain of resampled input if requested */
        if (cfg->findReplayGain && !cfg->decode_on_the_fly)
            if (AnalyzeSamples
                (gfc->sv_rpg.rgdata, &mfbuf[0][esv->mf_size], &mfbuf[1][esv->mf_size], n_out,
                 cfg->channels_out) == GAIN_ANALYSIS_ERROR)
                return -6;



        /* update in_buffer counters */
        nsamples -= n_in;
        in_buffer[0] += n_in;
        if (cfg->channels_out == 2)
            in_buffer[1] += n_in;

        /* update mfbuf[] counters */
        esv->mf_size += n_out;
        assert(esv->mf_size <= MFSIZE);
        
        /* lame_encode_flush may have set gfc->mf_sample_to_encode to 0
         * so we have to reinitialize it here when that happened.
         */
        if (esv->mf_samples_to_encode < 1) {
            esv->mf_samples_to_encode = ENCDELAY + POSTDELAY;
        }        
        esv->mf_samples_to_encode += n_out;


        if (esv->mf_size >= mf_needed) {
            /* encode the frame.  */
            /* mp3buf              = pointer to current location in buffer */
            /* mp3buf_size         = size of original mp3 output buffer */
            /*                     = 0 if we should not worry about the */
            /*                       buffer size because calling program is  */
            /*                       to lazy to compute it */
            /* mp3size             = size of data written to buffer so far */
            /* mp3buf_size-mp3size = amount of space avalable  */

            int     buf_size = mp3buf_size - mp3size;
            if (mp3buf_size == 0)
                buf_size = INT_MAX;

            ret = lame_encode_mp3_frame(gfc, mfbuf[0], mfbuf[1], mp3buf, buf_size);

            if (ret < 0)
                return ret;
            mp3buf += ret;
            mp3size += ret;

            /* shift out old samples */
            esv->mf_size -= pcm_samples_per_frame;
            esv->mf_samples_to_encode -= pcm_samples_per_frame;
            for (ch = 0; ch < cfg->channels_out; ch++)
                for (i = 0; i < esv->mf_size; i++)
                    mfbuf[ch][i] = mfbuf[ch][i + pcm_samples_per_frame];
        }
    }
    assert(nsamples == 0);

    return mp3size;
}

enum PCMSampleType 
{   pcm_short_type
,   pcm_int_type
,   pcm_long_type
,   pcm_float_type
,   pcm_double_type
};

static void
lame_copy_inbuffer(lame_internal_flags* gfc, 
                   void const* l, void const* r, int nsamples,
                   enum PCMSampleType pcm_type, int jump, FLOAT s)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    EncStateVar_t *const esv = &gfc->sv_enc;
    sample_t* ib0 = esv->in_buffer_0;
    sample_t* ib1 = esv->in_buffer_1;
    FLOAT   m[2][2];

    /* Apply user defined re-scaling */
    m[0][0] = s * cfg->pcm_transform[0][0];
    m[0][1] = s * cfg->pcm_transform[0][1];
    m[1][0] = s * cfg->pcm_transform[1][0];
    m[1][1] = s * cfg->pcm_transform[1][1];

    /* make a copy of input buffer, changing type to sample_t */
#define COPY_AND_TRANSFORM(T) \
{ \
    T const *bl = l, *br = r; \
    int     i; \
    for (i = 0; i < nsamples; i++) { \
        sample_t const xl = *bl; \
        sample_t const xr = *br; \
        sample_t const u = xl * m[0][0] + xr * m[0][1]; \
        sample_t const v = xl * m[1][0] + xr * m[1][1]; \
        ib0[i] = u; \
        ib1[i] = v; \
        bl += jump; \
        br += jump; \
    } \
}
    switch ( pcm_type ) {
    case pcm_short_type: 
        COPY_AND_TRANSFORM(short int);
        break;
    case pcm_int_type:
        COPY_AND_TRANSFORM(int);
        break;
    case pcm_long_type:
        COPY_AND_TRANSFORM(long int);
        break;
    case pcm_float_type:
        COPY_AND_TRANSFORM(float);
        break;
    case pcm_double_type:
        COPY_AND_TRANSFORM(double);
        break;
    }
}


static int
lame_encode_buffer_template(lame_global_flags * gfp,
                            void const* buffer_l, void const* buffer_r, const int nsamples,
                            unsigned char *mp3buf, const int mp3buf_size, enum PCMSampleType pcm_type, int aa, FLOAT norm)
{
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags *const gfc = gfp->internal_flags;
        if (is_lame_internal_flags_valid(gfc)) {
            SessionConfig_t const *const cfg = &gfc->cfg;

            if (nsamples == 0)
                return 0;

            if (update_inbuffer_size(gfc, nsamples) != 0) {
                return -2;
            }
            /* make a copy of input buffer, changing type to sample_t */
            if (cfg->channels_in > 1) {
                if (buffer_l == 0 || buffer_r == 0) {
                    return 0;
                }
                lame_copy_inbuffer(gfc, buffer_l, buffer_r, nsamples, pcm_type, aa, norm);
            }
            else {
                if (buffer_l == 0) {
                    return 0;
                }
                lame_copy_inbuffer(gfc, buffer_l, buffer_l, nsamples, pcm_type, aa, norm);
            }

            return lame_encode_buffer_sample_t(gfc, nsamples, mp3buf, mp3buf_size);
        }
    }
    return -3;
}

int
lame_encode_buffer(lame_global_flags * gfp,
                   const short int pcm_l[], const short int pcm_r[], const int nsamples,
                   unsigned char *mp3buf, const int mp3buf_size)
{
    return lame_encode_buffer_template(gfp, pcm_l, pcm_r, nsamples, mp3buf, mp3buf_size, pcm_short_type, 1, 1.0);
}


int
lame_encode_buffer_float(lame_global_flags * gfp,
                         const float pcm_l[], const float pcm_r[], const int nsamples,
                         unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- 32768 for full scale */
    return lame_encode_buffer_template(gfp, pcm_l, pcm_r, nsamples, mp3buf, mp3buf_size, pcm_float_type, 1, 1.0);
}


int
lame_encode_buffer_ieee_float(lame_t gfp,
                         const float pcm_l[], const float pcm_r[], const int nsamples,
                         unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- 1.0 for full scale */
    return lame_encode_buffer_template(gfp, pcm_l, pcm_r, nsamples, mp3buf, mp3buf_size, pcm_float_type, 1, 32767.0);
}


int
lame_encode_buffer_interleaved_ieee_float(lame_t gfp,
                         const float pcm[], const int nsamples,
                         unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- 1.0 for full scale */
    return lame_encode_buffer_template(gfp, pcm, pcm+1, nsamples, mp3buf, mp3buf_size, pcm_float_type, 2, 32767.0);
}


int
lame_encode_buffer_ieee_double(lame_t gfp,
                         const double pcm_l[], const double pcm_r[], const int nsamples,
                         unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- 1.0 for full scale */
    return lame_encode_buffer_template(gfp, pcm_l, pcm_r, nsamples, mp3buf, mp3buf_size, pcm_double_type, 1, 32767.0);
}


int
lame_encode_buffer_interleaved_ieee_double(lame_t gfp,
                         const double pcm[], const int nsamples,
                         unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- 1.0 for full scale */
    return lame_encode_buffer_template(gfp, pcm, pcm+1, nsamples, mp3buf, mp3buf_size, pcm_double_type, 2, 32767.0);
}


int
lame_encode_buffer_int(lame_global_flags * gfp,
                       const int pcm_l[], const int pcm_r[], const int nsamples,
                       unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- MAX_INT for full scale */
    FLOAT const norm = (1.0 / (1L << (8 * sizeof(int) - 16)));
    return lame_encode_buffer_template(gfp, pcm_l, pcm_r, nsamples, mp3buf, mp3buf_size, pcm_int_type, 1, norm);
}


int
lame_encode_buffer_long2(lame_global_flags * gfp,
                         const long pcm_l[],  const long pcm_r[], const int nsamples,
                         unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- MAX_LONG for full scale */
    FLOAT const norm = (1.0 / (1L << (8 * sizeof(long) - 16)));
    return lame_encode_buffer_template(gfp, pcm_l, pcm_r, nsamples, mp3buf, mp3buf_size, pcm_long_type, 1, norm);
}


int
lame_encode_buffer_long(lame_global_flags * gfp,
                        const long pcm_l[], const long pcm_r[], const int nsamples,
                        unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- 32768 for full scale */
    return lame_encode_buffer_template(gfp, pcm_l, pcm_r, nsamples, mp3buf, mp3buf_size, pcm_long_type, 1, 1.0);
}



int
lame_encode_buffer_interleaved(lame_global_flags * gfp,
                               short int pcm[], int nsamples,
                               unsigned char *mp3buf, int mp3buf_size)
{
    /* input is assumed to be normalized to +/- MAX_SHORT for full scale */
    return lame_encode_buffer_template(gfp, pcm, pcm+1, nsamples, mp3buf, mp3buf_size, pcm_short_type, 2, 1.0);
}


int
lame_encode_buffer_interleaved_int(lame_t gfp,
                                   const int pcm[], const int nsamples,
                                   unsigned char *mp3buf, const int mp3buf_size)
{
    /* input is assumed to be normalized to +/- MAX(int) for full scale */
    FLOAT const norm = (1.0 / (1L << (8 * sizeof(int)-16)));
    return lame_encode_buffer_template(gfp, pcm, pcm + 1, nsamples, mp3buf, mp3buf_size, pcm_int_type, 2, norm);
}




/*****************************************************************
 Flush mp3 buffer, pad with ancillary data so last frame is complete.
 Reset reservoir size to 0
 but keep all PCM samples and MDCT data in memory
 This option is used to break a large file into several mp3 files
 that when concatenated together will decode with no gaps
 Because we set the reservoir=0, they will also decode seperately
 with no errors.
*********************************************************************/
int
lame_encode_flush_nogap(lame_global_flags * gfp, unsigned char *mp3buffer, int mp3buffer_size)
{
    int     rc = -3;
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags *const gfc = gfp->internal_flags;
        if (is_lame_internal_flags_valid(gfc)) {
            flush_bitstream(gfc);
            /* if user specifed buffer size = 0, dont check size */
            if (mp3buffer_size == 0)
                mp3buffer_size = INT_MAX;
            rc = copy_buffer(gfc, mp3buffer, mp3buffer_size, 1);
            save_gain_values(gfc);
        }
    }
    return rc;
}


/* called by lame_init_params.  You can also call this after flush_nogap
   if you want to write new id3v2 and Xing VBR tags into the bitstream */
int
lame_init_bitstream(lame_global_flags * gfp)
{
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags *const gfc = gfp->internal_flags;
        if (gfc != 0) {
            gfc->ov_enc.frame_number = 0;

            if (gfp->write_id3tag_automatic) {
                (void) id3tag_write_v2(gfp);
            }
            /* initialize histogram data optionally used by frontend */
            memset(gfc->ov_enc.bitrate_channelmode_hist, 0,
                   sizeof(gfc->ov_enc.bitrate_channelmode_hist));
            memset(gfc->ov_enc.bitrate_blocktype_hist, 0,
                   sizeof(gfc->ov_enc.bitrate_blocktype_hist));

            gfc->ov_rpg.PeakSample = 0.0;

            /* Write initial VBR Header to bitstream and init VBR data */
            if (gfc->cfg.write_lame_tag)
                (void) InitVbrTag(gfp);


            return 0;
        }
    }
    return -3;
}


/*****************************************************************/
/* flush internal PCM sample buffers, then mp3 buffers           */
/* then write id3 v1 tags into bitstream.                        */
/*****************************************************************/

int
lame_encode_flush(lame_global_flags * gfp, unsigned char *mp3buffer, int mp3buffer_size)
{
    lame_internal_flags *gfc;
    SessionConfig_t const *cfg;
    EncStateVar_t *esv;
    short int buffer[2][1152];
    int     imp3 = 0, mp3count, mp3buffer_size_remaining;

    /* we always add POSTDELAY=288 padding to make sure granule with real
     * data can be complety decoded (because of 50% overlap with next granule */
    int     end_padding;
    int     frames_left;
    int     samples_to_encode;
    int     pcm_samples_per_frame;
    int     mf_needed;
    int     is_resampling_necessary;
    double  resample_ratio = 1;

    if (!is_lame_global_flags_valid(gfp)) {
        return -3;
    }
    gfc = gfp->internal_flags;
    if (!is_lame_internal_flags_valid(gfc)) {
        return -3;
    }
    cfg = &gfc->cfg;
    esv = &gfc->sv_enc;
    
    /* Was flush already called? */
    if (esv->mf_samples_to_encode < 1) {
        return 0;
    }
    pcm_samples_per_frame = 576 * cfg->mode_gr;
    mf_needed = calcNeeded(cfg);

    samples_to_encode = esv->mf_samples_to_encode - POSTDELAY;

    memset(buffer, 0, sizeof(buffer));
    mp3count = 0;

    is_resampling_necessary = isResamplingNecessary(cfg);
    if (is_resampling_necessary) {
        resample_ratio = (double)cfg->samplerate_in / (double)cfg->samplerate_out;
        /* delay due to resampling; needs to be fixed, if resampling code gets changed */
        samples_to_encode += 16. / resample_ratio;
    }
    end_padding = pcm_samples_per_frame - (samples_to_encode % pcm_samples_per_frame);
    if (end_padding < 576)
        end_padding += pcm_samples_per_frame;
    gfc->ov_enc.encoder_padding = end_padding;
    
    frames_left = (samples_to_encode + end_padding) / pcm_samples_per_frame;
    while (frames_left > 0 && imp3 >= 0) {
        int const frame_num = gfc->ov_enc.frame_number;
        int     bunch = mf_needed - esv->mf_size;

        bunch *= resample_ratio;
        if (bunch > 1152) bunch = 1152;
        if (bunch < 1) bunch = 1;

        mp3buffer_size_remaining = mp3buffer_size - mp3count;

        /* if user specifed buffer size = 0, dont check size */
        if (mp3buffer_size == 0)
            mp3buffer_size_remaining = 0;

        /* send in a frame of 0 padding until all internal sample buffers
         * are flushed
         */
        imp3 = lame_encode_buffer(gfp, buffer[0], buffer[1], bunch,
                                  mp3buffer, mp3buffer_size_remaining);

        mp3buffer += imp3;
        mp3count += imp3;
        {   /* even a single pcm sample can produce several frames!
             * for example: 1 Hz input file resampled to 8 kHz mpeg2.5
             */
            int const new_frames = gfc->ov_enc.frame_number - frame_num;
            if (new_frames > 0)
                frames_left -=  new_frames;
        }
    }
    /* Set esv->mf_samples_to_encode to 0, so we may detect
     * and break loops calling it more than once in a row.
     */
    esv->mf_samples_to_encode = 0;

    if (imp3 < 0) {
        /* some type of fatal error */
        return imp3;
    }

    mp3buffer_size_remaining = mp3buffer_size - mp3count;
    /* if user specifed buffer size = 0, dont check size */
    if (mp3buffer_size == 0)
        mp3buffer_size_remaining = INT_MAX;

    /* mp3 related stuff.  bit buffer might still contain some mp3 data */
    flush_bitstream(gfc);
    imp3 = copy_buffer(gfc, mp3buffer, mp3buffer_size_remaining, 1);
    save_gain_values(gfc);
    if (imp3 < 0) {
        /* some type of fatal error */
        return imp3;
    }
    mp3buffer += imp3;
    mp3count += imp3;
    mp3buffer_size_remaining = mp3buffer_size - mp3count;
    /* if user specifed buffer size = 0, dont check size */
    if (mp3buffer_size == 0)
        mp3buffer_size_remaining = INT_MAX;

    if (gfp->write_id3tag_automatic) {
        /* write a id3 tag to the bitstream */
        (void) id3tag_write_v1(gfp);

        imp3 = copy_buffer(gfc, mp3buffer, mp3buffer_size_remaining, 0);

        if (imp3 < 0) {
            return imp3;
        }
        mp3count += imp3;
    }
#if 0
    {
        int const ed = gfc->ov_enc.encoder_delay;
        int const ep = gfc->ov_enc.encoder_padding;
        int const ns = (gfc->ov_enc.frame_number * pcm_samples_per_frame) - (ed + ep);
        double  duration = ns;
        duration /= cfg->samplerate_out;
        MSGF(gfc, "frames=%d\n", gfc->ov_enc.frame_number);
        MSGF(gfc, "pcm_samples_per_frame=%d\n", pcm_samples_per_frame);
        MSGF(gfc, "encoder delay=%d\n", ed);
        MSGF(gfc, "encoder padding=%d\n", ep);
        MSGF(gfc, "sample count=%d (%g)\n", ns, cfg->samplerate_in * duration);
        MSGF(gfc, "duration=%g sec\n", duration);
    }
#endif
    return mp3count;
}

/***********************************************************************
 *
 *      lame_close ()
 *
 *  frees internal buffers
 *
 ***********************************************************************/

int
lame_close(lame_global_flags * gfp)
{
    int     ret = 0;
    if (gfp && gfp->class_id == LAME_ID) {
        lame_internal_flags *const gfc = gfp->internal_flags;
        gfp->class_id = 0;
        if (NULL == gfc || gfc->class_id != LAME_ID) {
            ret = -3;
        }
        if (NULL != gfc) {
            gfc->lame_init_params_successful = 0;
            gfc->class_id = 0;
            /* this routine will free all malloc'd data in gfc, and then free gfc: */
            freegfc(gfc);
            gfp->internal_flags = NULL;
        }
        if (gfp->lame_allocated_gfp) {
            gfp->lame_allocated_gfp = 0;
            free(gfp);
        }
    }
    return ret;
}

/*****************************************************************/
/* flush internal mp3 buffers, and free internal buffers         */
/*****************************************************************/
#if DEPRECATED_OR_OBSOLETE_CODE_REMOVED
int CDECL
lame_encode_finish(lame_global_flags * gfp, unsigned char *mp3buffer, int mp3buffer_size);
#else
#endif

int
lame_encode_finish(lame_global_flags * gfp, unsigned char *mp3buffer, int mp3buffer_size)
{
    int const ret = lame_encode_flush(gfp, mp3buffer, mp3buffer_size);

    (void) lame_close(gfp);

    return ret;
}

/*****************************************************************/
/* write VBR Xing header, and ID3 version 1 tag, if asked for    */
/*****************************************************************/
void    lame_mp3_tags_fid(lame_global_flags * gfp, FILE * fpStream);

void
lame_mp3_tags_fid(lame_global_flags * gfp, FILE * fpStream)
{
    lame_internal_flags *gfc;
    SessionConfig_t const *cfg;
    if (!is_lame_global_flags_valid(gfp)) {
        return;
    }
    gfc = gfp->internal_flags;
    if (!is_lame_internal_flags_valid(gfc)) {
        return;
    }
    cfg = &gfc->cfg;
    if (!cfg->write_lame_tag) {
        return;
    }
    /* Write Xing header again */
    if (fpStream && !fseek(fpStream, 0, SEEK_SET)) {
        int     rc = PutVbrTag(gfp, fpStream);
        switch (rc) {
        default:
            /* OK */
            break;

        case -1:
            ERRORF(gfc, "Error: could not update LAME tag.\n");
            break;

        case -2:
            ERRORF(gfc, "Error: could not update LAME tag, file not seekable.\n");
            break;

        case -3:
            ERRORF(gfc, "Error: could not update LAME tag, file not readable.\n");
            break;
        }
    }
}


static int
lame_init_internal_flags(lame_internal_flags* gfc)
{
    if (NULL == gfc)
        return -1;

    gfc->cfg.vbr_min_bitrate_index = 1; /* not  0 ????? */
    gfc->cfg.vbr_max_bitrate_index = 13; /* not 14 ????? */
    gfc->cfg.decode_on_the_fly = 0;
    gfc->cfg.findReplayGain = 0;
    gfc->cfg.findPeakSample = 0;

    gfc->sv_qnt.OldValue[0] = 180;
    gfc->sv_qnt.OldValue[1] = 180;
    gfc->sv_qnt.CurrentStep[0] = 4;
    gfc->sv_qnt.CurrentStep[1] = 4;
    gfc->sv_qnt.masking_lower = 1;

    /* The reason for
     *       int mf_samples_to_encode = ENCDELAY + POSTDELAY;
     * ENCDELAY = internal encoder delay.  And then we have to add POSTDELAY=288
     * because of the 50% MDCT overlap.  A 576 MDCT granule decodes to
     * 1152 samples.  To synthesize the 576 samples centered under this granule
     * we need the previous granule for the first 288 samples (no problem), and
     * the next granule for the next 288 samples (not possible if this is last
     * granule).  So we need to pad with 288 samples to make sure we can
     * encode the 576 samples we are interested in.
     */
    gfc->sv_enc.mf_samples_to_encode = ENCDELAY + POSTDELAY;
    gfc->sv_enc.mf_size = ENCDELAY - MDCTDELAY; /* we pad input with this many 0's */
    gfc->ov_enc.encoder_padding = 0;
    gfc->ov_enc.encoder_delay = ENCDELAY;

    gfc->ov_rpg.RadioGain = 0;
    gfc->ov_rpg.noclipGainChange = 0;
    gfc->ov_rpg.noclipScale = -1.0;

    gfc->ATH = lame_calloc(ATH_t, 1);
    if (NULL == gfc->ATH)
        return -2;      /* maybe error codes should be enumerated in lame.h ?? */

    gfc->sv_rpg.rgdata = lame_calloc(replaygain_t, 1);
    if (NULL == gfc->sv_rpg.rgdata) {
        return -2;
    }
    return 0;
}

/* initialize mp3 encoder */
#if DEPRECATED_OR_OBSOLETE_CODE_REMOVED
static
#else
#endif
int
lame_init_old(lame_global_flags * gfp)
{
    disable_FPE();      /* disable floating point exceptions */

    memset(gfp, 0, sizeof(lame_global_flags));

    gfp->class_id = LAME_ID;

    /* Global flags.  set defaults here for non-zero values */
    /* see lame.h for description */
    /* set integer values to -1 to mean that LAME will compute the
     * best value, UNLESS the calling program as set it
     * (and the value is no longer -1)
     */
    gfp->strict_ISO = MDB_MAXIMUM;

    gfp->mode = NOT_SET;
    gfp->original = 1;
    gfp->samplerate_in = 44100;
    gfp->num_channels = 2;
    gfp->num_samples = MAX_U_32_NUM;

    gfp->write_lame_tag = 1;
    gfp->quality = -1;
    gfp->short_blocks = short_block_not_set;
    gfp->subblock_gain = -1;

    gfp->lowpassfreq = 0;
    gfp->highpassfreq = 0;
    gfp->lowpasswidth = -1;
    gfp->highpasswidth = -1;

    gfp->VBR = vbr_off;
    gfp->VBR_q = 4;
    gfp->VBR_mean_bitrate_kbps = 128;
    gfp->VBR_min_bitrate_kbps = 0;
    gfp->VBR_max_bitrate_kbps = 0;
    gfp->VBR_hard_min = 0;

    gfp->quant_comp = -1;
    gfp->quant_comp_short = -1;

    gfp->msfix = -1;

    gfp->attackthre = -1;
    gfp->attackthre_s = -1;

    gfp->scale = 1;
    gfp->scale_left = 1;
    gfp->scale_right = 1;

    gfp->ATHcurve = -1;
    gfp->ATHtype = -1;  /* default = -1 = set in lame_init_params */
    /* 2 = equal loudness curve */
    gfp->athaa_sensitivity = 0.0; /* no offset */
    gfp->athaa_type = -1;
    gfp->useTemporal = -1;
    gfp->interChRatio = -1;

    gfp->findReplayGain = 0;
    gfp->decode_on_the_fly = 0;

    gfp->asm_optimizations.mmx = 1;
    gfp->asm_optimizations.amd3dnow = 1;
    gfp->asm_optimizations.sse = 1;

    gfp->preset = 0;

    gfp->write_id3tag_automatic = 1;

    gfp->report.debugf = &lame_report_def;
    gfp->report.errorf = &lame_report_def;
    gfp->report.msgf = &lame_report_def;

    gfp->internal_flags = lame_calloc(lame_internal_flags, 1);

    if (lame_init_internal_flags(gfp->internal_flags) < 0) {
        freegfc(gfp->internal_flags);
        gfp->internal_flags = 0;
        return -1;
    }
    return 0;
}


lame_global_flags *
lame_init(void)
{
    lame_global_flags *gfp;
    int     ret;

    init_log_table();

    gfp = lame_calloc(lame_global_flags, 1);
    if (gfp == NULL)
        return NULL;

    ret = lame_init_old(gfp);
    if (ret != 0) {
        free(gfp);
        return NULL;
    }

    gfp->lame_allocated_gfp = 1;
    return gfp;
}


/***********************************************************************
 *
 *  some simple statistics
 *
 *  Robert Hegemann 2000-10-11
 *
 ***********************************************************************/

/*  histogram of used bitrate indexes:
 *  One has to weight them to calculate the average bitrate in kbps
 *
 *  bitrate indices:
 *  there are 14 possible bitrate indices, 0 has the special meaning
 *  "free format" which is not possible to mix with VBR and 15 is forbidden
 *  anyway.
 *
 *  stereo modes:
 *  0: LR   number of left-right encoded frames
 *  1: LR-I number of left-right and intensity encoded frames
 *  2: MS   number of mid-side encoded frames
 *  3: MS-I number of mid-side and intensity encoded frames
 *
 *  4: number of encoded frames
 *
 */

void
lame_bitrate_kbps(const lame_global_flags * gfp, int bitrate_kbps[14])
{
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags const *const gfc = gfp->internal_flags;
        if (is_lame_internal_flags_valid(gfc)) {
            SessionConfig_t const *const cfg = &gfc->cfg;
            int     i;
            if (cfg->free_format) {
                for (i = 0; i < 14; i++)
                    bitrate_kbps[i] = -1;
                bitrate_kbps[0] = cfg->avg_bitrate;
            }
            else {
                for (i = 0; i < 14; i++)
                    bitrate_kbps[i] = bitrate_table[cfg->version][i + 1];
            }
        }
    }
}


void
lame_bitrate_hist(const lame_global_flags * gfp, int bitrate_count[14])
{
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags const *const gfc = gfp->internal_flags;
        if (is_lame_internal_flags_valid(gfc)) {
            SessionConfig_t const *const cfg = &gfc->cfg;
            EncResult_t const *const eov = &gfc->ov_enc;
            int     i;

            if (cfg->free_format) {
                for (i = 0; i < 14; i++) {
                    bitrate_count[i] = 0;
                }
                bitrate_count[0] = eov->bitrate_channelmode_hist[0][4];
            }
            else {
                for (i = 0; i < 14; i++) {
                    bitrate_count[i] = eov->bitrate_channelmode_hist[i + 1][4];
                }
            }
        }
    }
}


void
lame_stereo_mode_hist(const lame_global_flags * gfp, int stmode_count[4])
{
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags const *const gfc = gfp->internal_flags;
        if (is_lame_internal_flags_valid(gfc)) {
            EncResult_t const *const eov = &gfc->ov_enc;
            int     i;

            for (i = 0; i < 4; i++) {
                stmode_count[i] = eov->bitrate_channelmode_hist[15][i];
            }
        }
    }
}



void
lame_bitrate_stereo_mode_hist(const lame_global_flags * gfp, int bitrate_stmode_count[14][4])
{
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags const *const gfc = gfp->internal_flags;
        if (is_lame_internal_flags_valid(gfc)) {
            SessionConfig_t const *const cfg = &gfc->cfg;
            EncResult_t const *const eov = &gfc->ov_enc;
            int     i;
            int     j;

            if (cfg->free_format) {
                for (j = 0; j < 14; j++)
                    for (i = 0; i < 4; i++) {
                        bitrate_stmode_count[j][i] = 0;
                    }
                for (i = 0; i < 4; i++) {
                    bitrate_stmode_count[0][i] = eov->bitrate_channelmode_hist[0][i];
                }
            }
            else {
                for (j = 0; j < 14; j++) {
                    for (i = 0; i < 4; i++) {
                        bitrate_stmode_count[j][i] = eov->bitrate_channelmode_hist[j + 1][i];
                    }
                }
            }
        }
    }
}


void
lame_block_type_hist(const lame_global_flags * gfp, int btype_count[6])
{
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags const *const gfc = gfp->internal_flags;
        if (is_lame_internal_flags_valid(gfc)) {
            EncResult_t const *const eov = &gfc->ov_enc;
            int     i;

            for (i = 0; i < 6; ++i) {
                btype_count[i] = eov->bitrate_blocktype_hist[15][i];
            }
        }
    }
}



void
lame_bitrate_block_type_hist(const lame_global_flags * gfp, int bitrate_btype_count[14][6])
{
    if (is_lame_global_flags_valid(gfp)) {
        lame_internal_flags const *const gfc = gfp->internal_flags;
        if (is_lame_internal_flags_valid(gfc)) {
            SessionConfig_t const *const cfg = &gfc->cfg;
            EncResult_t const *const eov = &gfc->ov_enc;
            int     i, j;

            if (cfg->free_format) {
                for (j = 0; j < 14; ++j) {
                    for (i = 0; i < 6; ++i) {
                        bitrate_btype_count[j][i] = 0;
                    }
                }
                for (i = 0; i < 6; ++i) {
                    bitrate_btype_count[0][i] = eov->bitrate_blocktype_hist[0][i];
                }
            }
            else {
                for (j = 0; j < 14; ++j) {
                    for (i = 0; i < 6; ++i) {
                        bitrate_btype_count[j][i] = eov->bitrate_blocktype_hist[j + 1][i];
                    }
                }
            }
        }
    }
}

/* end of lame.c */
