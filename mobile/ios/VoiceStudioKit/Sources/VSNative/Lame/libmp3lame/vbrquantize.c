/*
 *	MP3 quantization
 *
 *	Copyright (c) 1999-2000 Mark Taylor
 *	Copyright (c) 2000-2012 Robert Hegemann
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

/* $Id: vbrquantize.c,v 1.142 2012/02/07 13:36:35 robert Exp $ */

#ifdef HAVE_CONFIG_H
#  include <config.h>
#endif


#include "lame.h"
#include "machine.h"
#include "encoder.h"
#include "util.h"
#include "vbrquantize.h"
#include "quantize_pvt.h"




struct algo_s;
typedef struct algo_s algo_t;

typedef void (*alloc_sf_f) (const algo_t *, const int *, const int *, int);
typedef uint8_t (*find_sf_f) (const FLOAT *, const FLOAT *, FLOAT, unsigned int, uint8_t);

struct algo_s {
    alloc_sf_f alloc;
    find_sf_f  find;
    const FLOAT *xr34orig;
    lame_internal_flags *gfc;
    gr_info *cod_info;
    int     mingain_l;
    int     mingain_s[3];
};



/*  Remarks on optimizing compilers:
 *
 *  the MSVC compiler may get into aliasing problems when accessing
 *  memory through the fi_union. declaring it volatile does the trick here
 *
 *  the calc_sfb_noise_* functions are not inlined because the intel compiler
 *  optimized executeables won't work as expected anymore
 */

#ifdef _MSC_VER
#  if _MSC_VER < 1400
#  define VOLATILE volatile
#  else
#  define VOLATILE
#  endif
#else
#  define VOLATILE
#endif

typedef VOLATILE union {
    float   f;
    int     i;
} fi_union;



#ifdef TAKEHIRO_IEEE754_HACK
#define DOUBLEX double
#else
#define DOUBLEX FLOAT
#endif
 
#define MAGIC_FLOAT_def (65536*(128))
#define MAGIC_INT_def    0x4b000000

#ifdef TAKEHIRO_IEEE754_HACK
#else
/*********************************************************************
 * XRPOW_FTOI is a macro to convert floats to ints.
 * if XRPOW_FTOI(x) = nearest_int(x), then QUANTFAC(x)=adj43asm[x]
 *                                         ROUNDFAC= -0.0946
 *
 * if XRPOW_FTOI(x) = floor(x), then QUANTFAC(x)=asj43[x]
 *                                   ROUNDFAC=0.4054
 *********************************************************************/
#  define QUANTFAC(rx)  adj43[rx]
#  define ROUNDFAC_def 0.4054f
#  define XRPOW_FTOI(src,dest) ((dest) = (int)(src))
#endif

static int const MAGIC_INT = MAGIC_INT_def;
#ifndef TAKEHIRO_IEEE754_HACK
static DOUBLEX const ROUNDFAC = ROUNDFAC_def;
#endif
static DOUBLEX const MAGIC_FLOAT = MAGIC_FLOAT_def;


inline static  float
vec_max_c(const float * xr34, unsigned int bw)
{
    float   xfsf = 0;
    unsigned int i = bw >> 2u;
    unsigned int const remaining = (bw & 0x03u);

    while (i-- > 0) {
        if (xfsf < xr34[0]) {
            xfsf = xr34[0];
        }
        if (xfsf < xr34[1]) {
            xfsf = xr34[1];
        }
        if (xfsf < xr34[2]) {
            xfsf = xr34[2];
        }
        if (xfsf < xr34[3]) {
            xfsf = xr34[3];
        }
        xr34 += 4;
    }
    switch( remaining ) {
    case 3: if (xfsf < xr34[2]) xfsf = xr34[2];
    case 2: if (xfsf < xr34[1]) xfsf = xr34[1];
    case 1: if (xfsf < xr34[0]) xfsf = xr34[0];
    default: break;
    }
    return xfsf;
}


inline static  uint8_t
find_lowest_scalefac(const FLOAT xr34)
{
    uint8_t sf_ok = 255;
    uint8_t sf = 128, delsf = 64;
    uint8_t i;
    FLOAT const ixmax_val = IXMAX_VAL;
    for (i = 0; i < 8; ++i) {
        FLOAT const xfsf = ipow20[sf] * xr34;
        if (xfsf <= ixmax_val) {
            sf_ok = sf;
            sf -= delsf;
        }
        else {
            sf += delsf;
        }
        delsf >>= 1;
    }
    return sf_ok;
}


inline static void
k_34_4(DOUBLEX x[4], int l3[4])
{
#ifdef TAKEHIRO_IEEE754_HACK
    fi_union fi[4];

    assert(x[0] <= IXMAX_VAL && x[1] <= IXMAX_VAL && x[2] <= IXMAX_VAL && x[3] <= IXMAX_VAL);
    x[0] += MAGIC_FLOAT;
    fi[0].f = x[0];
    x[1] += MAGIC_FLOAT;
    fi[1].f = x[1];
    x[2] += MAGIC_FLOAT;
    fi[2].f = x[2];
    x[3] += MAGIC_FLOAT;
    fi[3].f = x[3];
    fi[0].f = x[0] + adj43asm[fi[0].i - MAGIC_INT];
    fi[1].f = x[1] + adj43asm[fi[1].i - MAGIC_INT];
    fi[2].f = x[2] + adj43asm[fi[2].i - MAGIC_INT];
    fi[3].f = x[3] + adj43asm[fi[3].i - MAGIC_INT];
    l3[0] = fi[0].i - MAGIC_INT;
    l3[1] = fi[1].i - MAGIC_INT;
    l3[2] = fi[2].i - MAGIC_INT;
    l3[3] = fi[3].i - MAGIC_INT;
#else
    assert(x[0] <= IXMAX_VAL && x[1] <= IXMAX_VAL && x[2] <= IXMAX_VAL && x[3] <= IXMAX_VAL);
    XRPOW_FTOI(x[0], l3[0]);
    XRPOW_FTOI(x[1], l3[1]);
    XRPOW_FTOI(x[2], l3[2]);
    XRPOW_FTOI(x[3], l3[3]);
    x[0] += QUANTFAC(l3[0]);
    x[1] += QUANTFAC(l3[1]);
    x[2] += QUANTFAC(l3[2]);
    x[3] += QUANTFAC(l3[3]);
    XRPOW_FTOI(x[0], l3[0]);
    XRPOW_FTOI(x[1], l3[1]);
    XRPOW_FTOI(x[2], l3[2]);
    XRPOW_FTOI(x[3], l3[3]);
#endif
}





/*  do call the calc_sfb_noise_* functions only with sf values
 *  for which holds: sfpow34*xr34 <= IXMAX_VAL
 */

static  FLOAT
calc_sfb_noise_x34(const FLOAT * xr, const FLOAT * xr34, unsigned int bw, uint8_t sf)
{
    DOUBLEX x[4];
    int     l3[4];
    const FLOAT sfpow = pow20[sf + Q_MAX2]; /*pow(2.0,sf/4.0); */
    const FLOAT sfpow34 = ipow20[sf]; /*pow(sfpow,-3.0/4.0); */

    FLOAT   xfsf = 0;
    unsigned int i = bw >> 2u;
    unsigned int const remaining = (bw & 0x03u);

    while (i-- > 0) {
        x[0] = sfpow34 * xr34[0];
        x[1] = sfpow34 * xr34[1];
        x[2] = sfpow34 * xr34[2];
        x[3] = sfpow34 * xr34[3];

        k_34_4(x, l3);

        x[0] = fabsf(xr[0]) - sfpow * pow43[l3[0]];
        x[1] = fabsf(xr[1]) - sfpow * pow43[l3[1]];
        x[2] = fabsf(xr[2]) - sfpow * pow43[l3[2]];
        x[3] = fabsf(xr[3]) - sfpow * pow43[l3[3]];
        xfsf += (x[0] * x[0] + x[1] * x[1]) + (x[2] * x[2] + x[3] * x[3]);

        xr += 4;
        xr34 += 4;
    }
    if (remaining) {
        x[0] = x[1] = x[2] = x[3] = 0;
        switch( remaining ) {
        case 3: x[2] = sfpow34 * xr34[2];
        case 2: x[1] = sfpow34 * xr34[1];
        case 1: x[0] = sfpow34 * xr34[0];
        }

        k_34_4(x, l3);
        x[0] = x[1] = x[2] = x[3] = 0;

        switch( remaining ) {
        case 3: x[2] = fabsf(xr[2]) - sfpow * pow43[l3[2]];
        case 2: x[1] = fabsf(xr[1]) - sfpow * pow43[l3[1]];
        case 1: x[0] = fabsf(xr[0]) - sfpow * pow43[l3[0]];
        }
        xfsf += (x[0] * x[0] + x[1] * x[1]) + (x[2] * x[2] + x[3] * x[3]);
    }
    return xfsf;
}



struct calc_noise_cache {
    int     valid;
    FLOAT   value;
};

typedef struct calc_noise_cache calc_noise_cache_t;


static  uint8_t
tri_calc_sfb_noise_x34(const FLOAT * xr, const FLOAT * xr34, FLOAT l3_xmin, unsigned int bw,
                       uint8_t sf, calc_noise_cache_t * did_it)
{
    if (did_it[sf].valid == 0) {
        did_it[sf].valid = 1;
        did_it[sf].value = calc_sfb_noise_x34(xr, xr34, bw, sf);
    }
    if (l3_xmin < did_it[sf].value) {
        return 1;
    }
    if (sf < 255) {
        uint8_t const sf_x = sf + 1;
        if (did_it[sf_x].valid == 0) {
            did_it[sf_x].valid = 1;
            did_it[sf_x].value = calc_sfb_noise_x34(xr, xr34, bw, sf_x);
        }
        if (l3_xmin < did_it[sf_x].value) {
            return 1;
        }
    }
    if (sf > 0) {
        uint8_t const sf_x = sf - 1;
        if (did_it[sf_x].valid == 0) {
            did_it[sf_x].valid = 1;
            did_it[sf_x].value = calc_sfb_noise_x34(xr, xr34, bw, sf_x);
        }
        if (l3_xmin < did_it[sf_x].value) {
            return 1;
        }
    }
    return 0;
}


/**
 *  Robert Hegemann 2001-05-01
 *  calculates quantization step size determined by allowed masking
 */
static int
calc_scalefac(FLOAT l3_xmin, int bw)
{
    FLOAT const c = 5.799142446; /* 10 * 10^(2/3) * log10(4/3) */
    return 210 + (int) (c * log10f(l3_xmin / bw) - .5f);
}

static uint8_t
guess_scalefac_x34(const FLOAT * xr, const FLOAT * xr34, FLOAT l3_xmin, unsigned int bw, uint8_t sf_min)
{
    int const guess = calc_scalefac(l3_xmin, bw);
    if (guess < sf_min) return sf_min;
    if (guess >= 255) return 255;
    (void) xr;
    (void) xr34;
    return guess;
}


/* the find_scalefac* routines calculate
 * a quantization step size which would
 * introduce as much noise as is allowed.
 * The larger the step size the more
 * quantization noise we'll get. The
 * scalefactors are there to lower the
 * global step size, allowing limited
 * differences in quantization step sizes
 * per band (shaping the noise).
 */

static  uint8_t
find_scalefac_x34(const FLOAT * xr, const FLOAT * xr34, FLOAT l3_xmin, unsigned int bw,
                  uint8_t sf_min)
{
    calc_noise_cache_t did_it[256];
    uint8_t sf = 128, sf_ok = 255, delsf = 128, seen_good_one = 0, i;
    memset(did_it, 0, sizeof(did_it));
    for (i = 0; i < 8; ++i) {
        delsf >>= 1;
        if (sf <= sf_min) {
            sf += delsf;
        }
        else {
            uint8_t const bad = tri_calc_sfb_noise_x34(xr, xr34, l3_xmin, bw, sf, did_it);
            if (bad) {  /* distortion.  try a smaller scalefactor */
                sf -= delsf;
            }
            else {
                sf_ok = sf;
                sf += delsf;
                seen_good_one = 1;
            }
        }
    }
    /*  returning a scalefac without distortion, if possible
     */
    if (seen_good_one > 0) {
        sf = sf_ok;
    }
    if (sf <= sf_min) {
        sf = sf_min;
    }
    return sf;
}



/***********************************************************************
 *
 *      calc_short_block_vbr_sf()
 *      calc_long_block_vbr_sf()
 *
 *  Mark Taylor 2000-??-??
 *  Robert Hegemann 2000-10-25 made functions of it
 *
 ***********************************************************************/

/* a variation for vbr-mtrh */
static int
block_sf(algo_t * that, const FLOAT l3_xmin[SFBMAX], int vbrsf[SFBMAX], int vbrsfmin[SFBMAX])
{
    FLOAT   max_xr34;
    const FLOAT *const xr = &that->cod_info->xr[0];
    const FLOAT *const xr34_orig = &that->xr34orig[0];
    const int *const width = &that->cod_info->width[0];
    const char *const energy_above_cutoff = &that->cod_info->energy_above_cutoff[0];
    unsigned int const max_nonzero_coeff = (unsigned int) that->cod_info->max_nonzero_coeff;
    uint8_t maxsf = 0;
    int     sfb = 0, m_o = -1;
    unsigned int j = 0, i = 0;
    int const psymax = that->cod_info->psymax;

    assert(that->cod_info->max_nonzero_coeff >= 0);

    that->mingain_l = 0;
    that->mingain_s[0] = 0;
    that->mingain_s[1] = 0;
    that->mingain_s[2] = 0;
    while (j <= max_nonzero_coeff) {
        unsigned int const w = (unsigned int) width[sfb];
        unsigned int const m = (unsigned int) (max_nonzero_coeff - j + 1);
        unsigned int l = w;
        uint8_t m1, m2;
        if (l > m) {
            l = m;
        }
        max_xr34 = vec_max_c(&xr34_orig[j], l);

        m1 = find_lowest_scalefac(max_xr34);
        vbrsfmin[sfb] = m1;
        if (that->mingain_l < m1) {
            that->mingain_l = m1;
        }
        if (that->mingain_s[i] < m1) {
            that->mingain_s[i] = m1;
        }
        if (++i > 2) {
            i = 0;
        }
        if (sfb < psymax && w > 2) { /* mpeg2.5 at 8 kHz doesn't use all scalefactors, unused have width 2 */
            if (energy_above_cutoff[sfb]) {
                m2 = that->find(&xr[j], &xr34_orig[j], l3_xmin[sfb], l, m1);
#if 0
                if (0) {
                    /** Robert Hegemann 2007-09-29:
                     *  It seems here is some more potential for speed improvements.
                     *  Current find method does 11-18 quantization calculations.
                     *  Using a "good guess" may help to reduce this amount.
                     */
                    uint8_t guess = calc_scalefac(l3_xmin[sfb], l);
                    DEBUGF(that->gfc, "sfb=%3d guess=%3d found=%3d diff=%3d\n", sfb, guess, m2,
                           m2 - guess);
                }
#endif
                if (maxsf < m2) {
                    maxsf = m2;
                }
                if (m_o < m2 && m2 < 255) {
                    m_o = m2;
                }
            }
            else {
                m2 = 255;
                maxsf = 255;
            }
        }
        else {
            if (maxsf < m1) {
                maxsf = m1;
            }
            m2 = maxsf;
        }
        vbrsf[sfb] = m2;
        ++sfb;
        j += w;        
    }
    for (; sfb < SFBMAX; ++sfb) {
        vbrsf[sfb] = maxsf;
        vbrsfmin[sfb] = 0;
    }
    if (m_o > -1) {
        maxsf = m_o;
        for (sfb = 0; sfb < SFBMAX; ++sfb) {
            if (vbrsf[sfb] == 255) {
                vbrsf[sfb] = m_o;
            }
        }
    }
    return maxsf;
}



/***********************************************************************
 *
 *  quantize xr34 based on scalefactors
 *
 *  block_xr34
 *
 *  Mark Taylor 2000-??-??
 *  Robert Hegemann 2000-10-20 made functions of them
 *
 ***********************************************************************/

static void
quantize_x34(const algo_t * that)
{
    DOUBLEX x[4];
    const FLOAT *xr34_orig = that->xr34orig;
    gr_info *const cod_info = that->cod_info;
    int const ifqstep = (cod_info->scalefac_scale == 0) ? 2 : 4;
    int    *l3 = cod_info->l3_enc;
    unsigned int j = 0, sfb = 0;
    unsigned int const max_nonzero_coeff = (unsigned int) cod_info->max_nonzero_coeff;

    assert(cod_info->max_nonzero_coeff >= 0);
    assert(cod_info->max_nonzero_coeff < 576);

    while (j <= max_nonzero_coeff) {
        int const s =
            (cod_info->scalefac[sfb] + (cod_info->preflag ? pretab[sfb] : 0)) * ifqstep
            + cod_info->subblock_gain[cod_info->window[sfb]] * 8;
        uint8_t const sfac = (uint8_t) (cod_info->global_gain - s);
        FLOAT const sfpow34 = ipow20[sfac];
        unsigned int const w = (unsigned int) cod_info->width[sfb];
        unsigned int const m = (unsigned int) (max_nonzero_coeff - j + 1);
        unsigned int i, remaining;

        assert((cod_info->global_gain - s) >= 0);
        assert(cod_info->width[sfb] >= 0);
        j += w;
        ++sfb;
        
        i = (w <= m) ? w : m;
        remaining = (i & 0x03u);
        i >>= 2u;

        while (i-- > 0) {
            x[0] = sfpow34 * xr34_orig[0];
            x[1] = sfpow34 * xr34_orig[1];
            x[2] = sfpow34 * xr34_orig[2];
            x[3] = sfpow34 * xr34_orig[3];

            k_34_4(x, l3);

            l3 += 4;
            xr34_orig += 4;
        }
        if (remaining) {
            int tmp_l3[4];
            x[0] = x[1] = x[2] = x[3] = 0;
            switch( remaining ) {
            case 3: x[2] = sfpow34 * xr34_orig[2];
            case 2: x[1] = sfpow34 * xr34_orig[1];
            case 1: x[0] = sfpow34 * xr34_orig[0];
            }

            k_34_4(x, tmp_l3);

            switch( remaining ) {
            case 3: l3[2] = tmp_l3[2];
            case 2: l3[1] = tmp_l3[1];
            case 1: l3[0] = tmp_l3[0];
            }

            l3 += remaining;
            xr34_orig += remaining;
        }
    }
}



static const uint8_t max_range_short[SBMAX_s * 3] = {
    15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15,
    7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7,
    0, 0, 0
};

static const uint8_t max_range_long[SBMAX_l] = {
    15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 15, 7, 7, 7, 7, 7, 7, 7, 7, 7, 7, 0
};

static const uint8_t max_range_long_lsf_pretab[SBMAX_l] = {
    7, 7, 7, 7, 7, 7, 3, 3, 3, 3, 3, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0
};



/*
    sfb=0..5  scalefac < 16
    sfb>5     scalefac < 8

    ifqstep = ( cod_info->scalefac_scale == 0 ) ? 2 : 4;
    ol_sf =  (cod_info->global_gain-210.0);
    ol_sf -= 8*cod_info->subblock_gain[i];
    ol_sf -= ifqstep*scalefac[gr][ch].s[sfb][i];
*/

static void
set_subblock_gain(gr_info * cod_info, const int mingain_s[3], int sf[])
{
    const int maxrange1 = 15, maxrange2 = 7;
    const int ifqstepShift = (cod_info->scalefac_scale == 0) ? 1 : 2;
    int    *const sbg = cod_info->subblock_gain;
    unsigned int const psymax = (unsigned int) cod_info->psymax;
    unsigned int psydiv = 18;
    int     sbg0, sbg1, sbg2;
    unsigned int sfb, i;
    int     min_sbg = 7;

    if (psydiv > psymax) {
        psydiv = psymax;
    }
    for (i = 0; i < 3; ++i) {
        int     maxsf1 = 0, maxsf2 = 0, minsf = 1000;
        /* see if we should use subblock gain */
        for (sfb = i; sfb < psydiv; sfb += 3) { /* part 1 */
            int const v = -sf[sfb];
            if (maxsf1 < v) {
                maxsf1 = v;
            }
            if (minsf > v) {
                minsf = v;
            }
        }
        for (; sfb < SFBMAX; sfb += 3) { /* part 2 */
            int const v = -sf[sfb];
            if (maxsf2 < v) {
                maxsf2 = v;
            }
            if (minsf > v) {
                minsf = v;
            }
        }

        /* boost subblock gain as little as possible so we can
         * reach maxsf1 with scalefactors
         * 8*sbg >= maxsf1
         */
        {
            int const m1 = maxsf1 - (maxrange1 << ifqstepShift);
            int const m2 = maxsf2 - (maxrange2 << ifqstepShift);

            maxsf1 = Max(m1, m2);
        }
        if (minsf > 0) {
            sbg[i] = minsf >> 3;
        }
        else {
            sbg[i] = 0;
        }
        if (maxsf1 > 0) {
            int const m1 = sbg[i];
            int const m2 = (maxsf1 + 7) >> 3;
            sbg[i] = Max(m1, m2);
        }
        if (sbg[i] > 0 && mingain_s[i] > (cod_info->global_gain - sbg[i] * 8)) {
            sbg[i] = (cod_info->global_gain - mingain_s[i]) >> 3;
        }
        if (sbg[i] > 7) {
            sbg[i] = 7;
        }
        if (min_sbg > sbg[i]) {
            min_sbg = sbg[i];
        }
    }
    sbg0 = sbg[0] * 8;
    sbg1 = sbg[1] * 8;
    sbg2 = sbg[2] * 8;
    for (sfb = 0; sfb < SFBMAX; sfb += 3) {
        sf[sfb + 0] += sbg0;
        sf[sfb + 1] += sbg1;
        sf[sfb + 2] += sbg2;
    }
    if (min_sbg > 0) {
        for (i = 0; i < 3; ++i) {
            sbg[i] -= min_sbg;
        }
        cod_info->global_gain -= min_sbg * 8;
    }
}



/*
	  ifqstep = ( cod_info->scalefac_scale == 0 ) ? 2 : 4;
	  ol_sf =  (cod_info->global_gain-210.0);
	  ol_sf -= ifqstep*scalefac[gr][ch].l[sfb];
	  if (cod_info->preflag && sfb>=11)
	  ol_sf -= ifqstep*pretab[sfb];
*/
static void
set_scalefacs(gr_info * cod_info, const int *vbrsfmin, int sf[], const uint8_t * max_range)
{
    const int ifqstep = (cod_info->scalefac_scale == 0) ? 2 : 4;
    const int ifqstepShift = (cod_info->scalefac_scale == 0) ? 1 : 2;
    int    *const scalefac = cod_info->scalefac;
    int const sfbmax = cod_info->sfbmax;
    int     sfb;
    int const *const sbg = cod_info->subblock_gain;
    int const *const window = cod_info->window;
    int const preflag = cod_info->preflag;

    if (preflag) {
        for (sfb = 11; sfb < sfbmax; ++sfb) {
            sf[sfb] += pretab[sfb] * ifqstep;
        }
    }
    for (sfb = 0; sfb < sfbmax; ++sfb) {
        int const gain = cod_info->global_gain - (sbg[window[sfb]] * 8)
            - ((preflag ? pretab[sfb] : 0) * ifqstep);

        if (sf[sfb] < 0) {
            int const m = gain - vbrsfmin[sfb];
            /* ifqstep*scalefac >= -sf[sfb], so round UP */
            scalefac[sfb] = (ifqstep - 1 - sf[sfb]) >> ifqstepShift;

            if (scalefac[sfb] > max_range[sfb]) {
                scalefac[sfb] = max_range[sfb];
            }
            if (scalefac[sfb] > 0 && (scalefac[sfb] << ifqstepShift) > m) {
                scalefac[sfb] = m >> ifqstepShift;
            }
        }
        else {
            scalefac[sfb] = 0;
        }
    }
    for (; sfb < SFBMAX; ++sfb) {
        scalefac[sfb] = 0; /* sfb21 */
    }
}


#ifndef NDEBUG
static int
checkScalefactor(const gr_info * cod_info, const int vbrsfmin[SFBMAX])
{
    int const ifqstep = cod_info->scalefac_scale == 0 ? 2 : 4;
    int     sfb;
    for (sfb = 0; sfb < cod_info->psymax; ++sfb) {
        const int s =
            ((cod_info->scalefac[sfb] +
              (cod_info->preflag ? pretab[sfb] : 0)) * ifqstep) +
            cod_info->subblock_gain[cod_info->window[sfb]] * 8;

        if ((cod_info->global_gain - s) < vbrsfmin[sfb]) {
            /*
               fprintf( stdout, "sf %d\n", sfb );
               fprintf( stdout, "min %d\n", vbrsfmin[sfb] );
               fprintf( stdout, "ggain %d\n", cod_info->global_gain );
               fprintf( stdout, "scalefac %d\n", cod_info->scalefac[sfb] );
               fprintf( stdout, "pretab %d\n", (cod_info->preflag ? pretab[sfb] : 0) );
               fprintf( stdout, "scale %d\n", (cod_info->scalefac_scale + 1) );
               fprintf( stdout, "subgain %d\n", cod_info->subblock_gain[cod_info->window[sfb]] * 8 );
               fflush( stdout );
               exit(-1);
             */
            return 0;
        }
    }
    return 1;
}
#endif


/******************************************************************
 *
 *  short block scalefacs
 *
 ******************************************************************/

static void
short_block_constrain(const algo_t * that, const int vbrsf[SFBMAX],
                      const int vbrsfmin[SFBMAX], int vbrmax)
{
    gr_info *const cod_info = that->cod_info;
    lame_internal_flags const *const gfc = that->gfc;
    SessionConfig_t const *const cfg = &gfc->cfg;
    int const maxminsfb = that->mingain_l;
    int     mover, maxover0 = 0, maxover1 = 0, delta = 0;
    int     v, v0, v1;
    int     sfb;
    int const psymax = cod_info->psymax;

    for (sfb = 0; sfb < psymax; ++sfb) {
        assert(vbrsf[sfb] >= vbrsfmin[sfb]);
        v = vbrmax - vbrsf[sfb];
        if (delta < v) {
            delta = v;
        }
        v0 = v - (4 * 14 + 2 * max_range_short[sfb]);
        v1 = v - (4 * 14 + 4 * max_range_short[sfb]);
        if (maxover0 < v0) {
            maxover0 = v0;
        }
        if (maxover1 < v1) {
            maxover1 = v1;
        }
    }
    if (cfg->noise_shaping == 2) {
        /* allow scalefac_scale=1 */
        mover = Min(maxover0, maxover1);
    }
    else {
        mover = maxover0;
    }
    if (delta > mover) {
        delta = mover;
    }
    vbrmax -= delta;
    maxover0 -= mover;
    maxover1 -= mover;

    if (maxover0 == 0) {
        cod_info->scalefac_scale = 0;
    }
    else if (maxover1 == 0) {
        cod_info->scalefac_scale = 1;
    }
    if (vbrmax < maxminsfb) {
        vbrmax = maxminsfb;
    }
    cod_info->global_gain = vbrmax;

    if (cod_info->global_gain < 0) {
        cod_info->global_gain = 0;
    }
    else if (cod_info->global_gain > 255) {
        cod_info->global_gain = 255;
    }
    {
        int     sf_temp[SFBMAX];
        for (sfb = 0; sfb < SFBMAX; ++sfb) {
            sf_temp[sfb] = vbrsf[sfb] - vbrmax;
        }
        set_subblock_gain(cod_info, &that->mingain_s[0], sf_temp);
        set_scalefacs(cod_info, vbrsfmin, sf_temp, max_range_short);
    }
    assert(checkScalefactor(cod_info, vbrsfmin));
}



/******************************************************************
 *
 *  long block scalefacs
 *
 ******************************************************************/

static void
long_block_constrain(const algo_t * that, const int vbrsf[SFBMAX], const int vbrsfmin[SFBMAX],
                     int vbrmax)
{
    gr_info *const cod_info = that->cod_info;
    lame_internal_flags const *const gfc = that->gfc;
    SessionConfig_t const *const cfg = &gfc->cfg;
    uint8_t const *max_rangep;
    int const maxminsfb = that->mingain_l;
    int     sfb;
    int     maxover0, maxover1, maxover0p, maxover1p, mover, delta = 0;
    int     v, v0, v1, v0p, v1p, vm0p = 1, vm1p = 1;
    int const psymax = cod_info->psymax;

    max_rangep = cfg->mode_gr == 2 ? max_range_long : max_range_long_lsf_pretab;

    maxover0 = 0;
    maxover1 = 0;
    maxover0p = 0;      /* pretab */
    maxover1p = 0;      /* pretab */

    for (sfb = 0; sfb < psymax; ++sfb) {
        assert(vbrsf[sfb] >= vbrsfmin[sfb]);
        v = vbrmax - vbrsf[sfb];
        if (delta < v) {
            delta = v;
        }
        v0 = v - 2 * max_range_long[sfb];
        v1 = v - 4 * max_range_long[sfb];
        v0p = v - 2 * (max_rangep[sfb] + pretab[sfb]);
        v1p = v - 4 * (max_rangep[sfb] + pretab[sfb]);
        if (maxover0 < v0) {
            maxover0 = v0;
        }
        if (maxover1 < v1) {
            maxover1 = v1;
        }
        if (maxover0p < v0p) {
            maxover0p = v0p;
        }
        if (maxover1p < v1p) {
            maxover1p = v1p;
        }
    }
    if (vm0p == 1) {
        int     gain = vbrmax - maxover0p;
        if (gain < maxminsfb) {
            gain = maxminsfb;
        }
        for (sfb = 0; sfb < psymax; ++sfb) {
            int const a = (gain - vbrsfmin[sfb]) - 2 * pretab[sfb];
            if (a <= 0) {
                vm0p = 0;
                vm1p = 0;
                break;
            }
        }
    }
    if (vm1p == 1) {
        int     gain = vbrmax - maxover1p;
        if (gain < maxminsfb) {
            gain = maxminsfb;
        }
        for (sfb = 0; sfb < psymax; ++sfb) {
            int const b = (gain - vbrsfmin[sfb]) - 4 * pretab[sfb];
            if (b <= 0) {
                vm1p = 0;
                break;
            }
        }
    }
    if (vm0p == 0) {
        maxover0p = maxover0;
    }
    if (vm1p == 0) {
        maxover1p = maxover1;
    }
    if (cfg->noise_shaping != 2) {
        maxover1 = maxover0;
        maxover1p = maxover0p;
    }
    mover = Min(maxover0, maxover0p);
    mover = Min(mover, maxover1);
    mover = Min(mover, maxover1p);

    if (delta > mover) {
        delta = mover;
    }
    vbrmax -= delta;
    if (vbrmax < maxminsfb) {
        vbrmax = maxminsfb;
    }
    maxover0 -= mover;
    maxover0p -= mover;
    maxover1 -= mover;
    maxover1p -= mover;

    if (maxover0 == 0) {
        cod_info->scalefac_scale = 0;
        cod_info->preflag = 0;
        max_rangep = max_range_long;
    }
    else if (maxover0p == 0) {
        cod_info->scalefac_scale = 0;
        cod_info->preflag = 1;
    }
    else if (maxover1 == 0) {
        cod_info->scalefac_scale = 1;
        cod_info->preflag = 0;
        max_rangep = max_range_long;
    }
    else if (maxover1p == 0) {
        cod_info->scalefac_scale = 1;
        cod_info->preflag = 1;
    }
    else {
        assert(0);      /* this should not happen */
    }
    cod_info->global_gain = vbrmax;
    if (cod_info->global_gain < 0) {
        cod_info->global_gain = 0;
    }
    else if (cod_info->global_gain > 255) {
        cod_info->global_gain = 255;
    }
    {
        int     sf_temp[SFBMAX];
        for (sfb = 0; sfb < SFBMAX; ++sfb) {
            sf_temp[sfb] = vbrsf[sfb] - vbrmax;
        }
        set_scalefacs(cod_info, vbrsfmin, sf_temp, max_rangep);
    }
    assert(checkScalefactor(cod_info, vbrsfmin));
}



static void
bitcount(const algo_t * that)
{
    int     rc = scale_bitcount(that->gfc, that->cod_info);

    if (rc == 0) {
        return;
    }
    /*  this should not happen due to the way the scalefactors are selected  */
    ERRORF(that->gfc, "INTERNAL ERROR IN VBR NEW CODE (986), please send bug report\n");
    exit(-1);
}



static int
quantizeAndCountBits(const algo_t * that)
{
    quantize_x34(that);
    that->cod_info->part2_3_length = noquant_count_bits(that->gfc, that->cod_info, 0);
    return that->cod_info->part2_3_length;
}





static int
tryGlobalStepsize(const algo_t * that, const int sfwork[SFBMAX],
                  const int vbrsfmin[SFBMAX], int delta)
{
    FLOAT const xrpow_max = that->cod_info->xrpow_max;
    int     sftemp[SFBMAX], i, nbits;
    int     gain, vbrmax = 0;
    for (i = 0; i < SFBMAX; ++i) {
        gain = sfwork[i] + delta;
        if (gain < vbrsfmin[i]) {
            gain = vbrsfmin[i];
        }
        if (gain > 255) {
            gain = 255;
        }
        if (vbrmax < gain) {
            vbrmax = gain;
        }
        sftemp[i] = gain;
    }
    that->alloc(that, sftemp, vbrsfmin, vbrmax);
    bitcount(that);
    nbits = quantizeAndCountBits(that);
    that->cod_info->xrpow_max = xrpow_max;
    return nbits;
}



static void
searchGlobalStepsizeMax(const algo_t * that, const int sfwork[SFBMAX],
                        const int vbrsfmin[SFBMAX], int target)
{
    gr_info const *const cod_info = that->cod_info;
    const int gain = cod_info->global_gain;
    int     curr = gain;
    int     gain_ok = 1024;
    int     nbits = LARGE_BITS;
    int     l = gain, r = 512;

    assert(gain >= 0);
    while (l <= r) {
        curr = (l + r) >> 1;
        nbits = tryGlobalStepsize(that, sfwork, vbrsfmin, curr - gain);
        if (nbits == 0 || (nbits + cod_info->part2_length) < target) {
            r = curr - 1;
            gain_ok = curr;
        }
        else {
            l = curr + 1;
            if (gain_ok == 1024) {
                gain_ok = curr;
            }
        }
    }
    if (gain_ok != curr) {
        curr = gain_ok;
        nbits = tryGlobalStepsize(that, sfwork, vbrsfmin, curr - gain);
    }
}



static int
sfDepth(const int sfwork[SFBMAX])
{
    int     m = 0;
    unsigned int i, j;
    for (j = SFBMAX, i = 0; j > 0; --j, ++i) {
        int const di = 255 - sfwork[i];
        if (m < di) {
            m = di;
        }
        assert(sfwork[i] >= 0);
        assert(sfwork[i] <= 255);
    }
    assert(m >= 0);
    assert(m <= 255);
    return m;
}


static void
cutDistribution(const int sfwork[SFBMAX], int sf_out[SFBMAX], int cut)
{
    unsigned int i, j;
    for (j = SFBMAX, i = 0; j > 0; --j, ++i) {
        int const x = sfwork[i];
        sf_out[i] = x < cut ? x : cut;
    }
}


static int
flattenDistribution(const int sfwork[SFBMAX], int sf_out[SFBMAX], int dm, int k, int p)
{
    unsigned int i, j;
    int     x, sfmax = 0;
    if (dm > 0) {
        for (j = SFBMAX, i = 0; j > 0; --j, ++i) {
            int const di = p - sfwork[i];
            x = sfwork[i] + (k * di) / dm;
            if (x < 0) {
                x = 0;
            }
            else {
                if (x > 255) {
                    x = 255;
                }
            }
            sf_out[i] = x;
            if (sfmax < x) {
                sfmax = x;
            }
        }
    }
    else {
        for (j = SFBMAX, i = 0; j > 0u; --j, ++i) {
            x = sfwork[i];
            sf_out[i] = x;
            if (sfmax < x) {
                sfmax = x;
            }
        }
    }
    return sfmax;
}


static int
tryThatOne(algo_t const* that, const int sftemp[SFBMAX], const int vbrsfmin[SFBMAX], int vbrmax)
{
    FLOAT const xrpow_max = that->cod_info->xrpow_max;
    int     nbits = LARGE_BITS;
    that->alloc(that, sftemp, vbrsfmin, vbrmax);
    bitcount(that);
    nbits = quantizeAndCountBits(that);
    nbits += that->cod_info->part2_length;
    that->cod_info->xrpow_max = xrpow_max;
    return nbits;
}


static void
outOfBitsStrategy(algo_t const* that, const int sfwork[SFBMAX], const int vbrsfmin[SFBMAX], int target)
{
    int     wrk[SFBMAX];
    int const dm = sfDepth(sfwork);
    int const p = that->cod_info->global_gain;
    int     nbits;

    /* PART 1 */
    {
        int     bi = dm / 2;
        int     bi_ok = -1;
        int     bu = 0;
        int     bo = dm;
        for (;;) {
            int const sfmax = flattenDistribution(sfwork, wrk, dm, bi, p);
            nbits = tryThatOne(that, wrk, vbrsfmin, sfmax);
            if (nbits <= target) {
                bi_ok = bi;
                bo = bi - 1;
            }
            else {
                bu = bi + 1;
            }
            if (bu <= bo) {
                bi = (bu + bo) / 2;
            }
            else {
                break;
            }
        }
        if (bi_ok >= 0) {
            if (bi != bi_ok) {
                int const sfmax = flattenDistribution(sfwork, wrk, dm, bi_ok, p);
                nbits = tryThatOne(that, wrk, vbrsfmin, sfmax);
            }
            return;
        }
    }

    /* PART 2: */
    {
        int     bi = (255 + p) / 2;
        int     bi_ok = -1;
        int     bu = p;
        int     bo = 255;
        for (;;) {
            int const sfmax = flattenDistribution(sfwork, wrk, dm, dm, bi);
            nbits = tryThatOne(that, wrk, vbrsfmin, sfmax);
            if (nbits <= target) {
                bi_ok = bi;
                bo = bi - 1;
            }
            else {
                bu = bi + 1;
            }
            if (bu <= bo) {
                bi = (bu + bo) / 2;
            }
            else {
                break;
            }
        }
        if (bi_ok >= 0) {
            if (bi != bi_ok) {
                int const sfmax = flattenDistribution(sfwork, wrk, dm, dm, bi_ok);
                nbits = tryThatOne(that, wrk, vbrsfmin, sfmax);
            }
            return;
        }
    }

    /* fall back to old code, likely to be never called */
    searchGlobalStepsizeMax(that, wrk, vbrsfmin, target);
}


static int
reduce_bit_usage(lame_internal_flags * gfc, int gr, int ch
#if 0
                 , const FLOAT xr34orig[576], const FLOAT l3_xmin[SFBMAX], int maxbits
#endif
    )
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    gr_info *const cod_info = &gfc->l3_side.tt[gr][ch];
    /*  try some better scalefac storage
     */
    best_scalefac_store(gfc, gr, ch, &gfc->l3_side);

    /*  best huffman_divide may save some bits too
     */
    if (cfg->use_best_huffman == 1)
        best_huffman_divide(gfc, cod_info);
    return cod_info->part2_3_length + cod_info->part2_length;
}




int
VBR_encode_frame(lame_internal_flags * gfc, const FLOAT xr34orig[2][2][576],
                 const FLOAT l3_xmin[2][2][SFBMAX], const int max_bits[2][2])
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    int     sfwork_[2][2][SFBMAX];
    int     vbrsfmin_[2][2][SFBMAX];
    algo_t  that_[2][2];
    int const ngr = cfg->mode_gr;
    int const nch = cfg->channels_out;
    int     max_nbits_ch[2][2] = {{0, 0}, {0 ,0}};
    int     max_nbits_gr[2] = {0, 0};
    int     max_nbits_fr = 0;
    int     use_nbits_ch[2][2] = {{MAX_BITS_PER_CHANNEL+1, MAX_BITS_PER_CHANNEL+1}
                                 ,{MAX_BITS_PER_CHANNEL+1, MAX_BITS_PER_CHANNEL+1}};
    int     use_nbits_gr[2] = { MAX_BITS_PER_GRANULE+1, MAX_BITS_PER_GRANULE+1 };
    int     use_nbits_fr = MAX_BITS_PER_GRANULE+MAX_BITS_PER_GRANULE;
    int     gr, ch;
    int     ok, sum_fr;

    /* set up some encoding parameters
     */
    for (gr = 0; gr < ngr; ++gr) {
        max_nbits_gr[gr] = 0;
        for (ch = 0; ch < nch; ++ch) {
            max_nbits_ch[gr][ch] = max_bits[gr][ch];
            use_nbits_ch[gr][ch] = 0;
            max_nbits_gr[gr] += max_bits[gr][ch];
            max_nbits_fr += max_bits[gr][ch];
            that_[gr][ch].find = (cfg->full_outer_loop < 0) ? guess_scalefac_x34 : find_scalefac_x34;
            that_[gr][ch].gfc = gfc;
            that_[gr][ch].cod_info = &gfc->l3_side.tt[gr][ch];
            that_[gr][ch].xr34orig = xr34orig[gr][ch];
            if (that_[gr][ch].cod_info->block_type == SHORT_TYPE) {
                that_[gr][ch].alloc = short_block_constrain;
            }
            else {
                that_[gr][ch].alloc = long_block_constrain;
            }
        }               /* for ch */
    }
    /* searches scalefactors
     */
    for (gr = 0; gr < ngr; ++gr) {
        for (ch = 0; ch < nch; ++ch) {
            if (max_bits[gr][ch] > 0) {
                algo_t *that = &that_[gr][ch];
                int    *sfwork = sfwork_[gr][ch];
                int    *vbrsfmin = vbrsfmin_[gr][ch];
                int     vbrmax;

                vbrmax = block_sf(that, l3_xmin[gr][ch], sfwork, vbrsfmin);
                that->alloc(that, sfwork, vbrsfmin, vbrmax);
                bitcount(that);
            }
            else {
                /*  xr contains no energy 
                 *  l3_enc, our encoding data, will be quantized to zero
                 *  continue with next channel
                 */
            }
        }               /* for ch */
    }
    /* encode 'as is'
     */
    use_nbits_fr = 0;
    for (gr = 0; gr < ngr; ++gr) {
        use_nbits_gr[gr] = 0;
        for (ch = 0; ch < nch; ++ch) {
            algo_t const *that = &that_[gr][ch];
            if (max_bits[gr][ch] > 0) {
                memset(&that->cod_info->l3_enc[0], 0, sizeof(that->cod_info->l3_enc));
                (void) quantizeAndCountBits(that);
            }
            else {
                /*  xr contains no energy 
                 *  l3_enc, our encoding data, will be quantized to zero
                 *  continue with next channel
                 */
            }
            use_nbits_ch[gr][ch] = reduce_bit_usage(gfc, gr, ch);
            use_nbits_gr[gr] += use_nbits_ch[gr][ch];
        }               /* for ch */
        use_nbits_fr += use_nbits_gr[gr];
    }

    /* check bit constrains
     */
    if (use_nbits_fr <= max_nbits_fr) {
        ok = 1;
        for (gr = 0; gr < ngr; ++gr) {
            if (use_nbits_gr[gr] > MAX_BITS_PER_GRANULE) {
                /* violates the rule that every granule has to use no more
                 * bits than MAX_BITS_PER_GRANULE
                 */
                ok = 0;
            }
            for (ch = 0; ch < nch; ++ch) {
                if (use_nbits_ch[gr][ch] > MAX_BITS_PER_CHANNEL) {
                    /* violates the rule that every gr_ch has to use no more
                     * bits than MAX_BITS_PER_CHANNEL
                     *
                     * This isn't explicitly stated in the ISO docs, but the
                     * part2_3_length field has only 12 bits, that makes it
                     * up to a maximum size of 4095 bits!!!
                     */
                    ok = 0;
                }
            }
        }
        if (ok) {
            return use_nbits_fr;
        }
    }
    
    /* OK, we are in trouble and have to define how many bits are
     * to be used for each granule
     */
    {
        ok = 1;
        sum_fr = 0;

        for (gr = 0; gr < ngr; ++gr) {
            max_nbits_gr[gr] = 0;
            for (ch = 0; ch < nch; ++ch) {
                if (use_nbits_ch[gr][ch] > MAX_BITS_PER_CHANNEL) {
                    max_nbits_ch[gr][ch] = MAX_BITS_PER_CHANNEL;
                }
                else {
                    max_nbits_ch[gr][ch] = use_nbits_ch[gr][ch];
                }
                max_nbits_gr[gr] += max_nbits_ch[gr][ch];
            }
            if (max_nbits_gr[gr] > MAX_BITS_PER_GRANULE) {
                float   f[2] = {0.0f, 0.0f}, s = 0.0f;
                for (ch = 0; ch < nch; ++ch) {
                    if (max_nbits_ch[gr][ch] > 0) {
                        f[ch] = sqrt(sqrt(max_nbits_ch[gr][ch]));
                        s += f[ch];
                    }
                    else {
                        f[ch] = 0;
                    }
                }
                for (ch = 0; ch < nch; ++ch) {
                    if (s > 0) {
                        max_nbits_ch[gr][ch] = MAX_BITS_PER_GRANULE * f[ch] / s;
                    }
                    else {
                        max_nbits_ch[gr][ch] = 0;
                    }
                }
                if (nch > 1) {
                    if (max_nbits_ch[gr][0] > use_nbits_ch[gr][0] + 32) {
                        max_nbits_ch[gr][1] += max_nbits_ch[gr][0];
                        max_nbits_ch[gr][1] -= use_nbits_ch[gr][0] + 32;
                        max_nbits_ch[gr][0] = use_nbits_ch[gr][0] + 32;
                    }
                    if (max_nbits_ch[gr][1] > use_nbits_ch[gr][1] + 32) {
                        max_nbits_ch[gr][0] += max_nbits_ch[gr][1];
                        max_nbits_ch[gr][0] -= use_nbits_ch[gr][1] + 32;
                        max_nbits_ch[gr][1] = use_nbits_ch[gr][1] + 32;
                    }
                    if (max_nbits_ch[gr][0] > MAX_BITS_PER_CHANNEL) {
                        max_nbits_ch[gr][0] = MAX_BITS_PER_CHANNEL;
                    }
                    if (max_nbits_ch[gr][1] > MAX_BITS_PER_CHANNEL) {
                        max_nbits_ch[gr][1] = MAX_BITS_PER_CHANNEL;
                    }
                }
                max_nbits_gr[gr] = 0;
                for (ch = 0; ch < nch; ++ch) {
                    max_nbits_gr[gr] += max_nbits_ch[gr][ch];
                }
            }
            sum_fr += max_nbits_gr[gr];
        }
        if (sum_fr > max_nbits_fr) {
            {
                float   f[2] = {0.0f, 0.0f}, s = 0.0f;
                for (gr = 0; gr < ngr; ++gr) {
                    if (max_nbits_gr[gr] > 0) {
                        f[gr] = sqrt(max_nbits_gr[gr]);
                        s += f[gr];
                    }
                    else {
                        f[gr] = 0;
                    }
                }
                for (gr = 0; gr < ngr; ++gr) {
                    if (s > 0) {
                        max_nbits_gr[gr] = max_nbits_fr * f[gr] / s;
                    }
                    else {
                        max_nbits_gr[gr] = 0;
                    }
                }
            }
            if (ngr > 1) {
                if (max_nbits_gr[0] > use_nbits_gr[0] + 125) {
                    max_nbits_gr[1] += max_nbits_gr[0];
                    max_nbits_gr[1] -= use_nbits_gr[0] + 125;
                    max_nbits_gr[0] = use_nbits_gr[0] + 125;
                }
                if (max_nbits_gr[1] > use_nbits_gr[1] + 125) {
                    max_nbits_gr[0] += max_nbits_gr[1];
                    max_nbits_gr[0] -= use_nbits_gr[1] + 125;
                    max_nbits_gr[1] = use_nbits_gr[1] + 125;
                }
                for (gr = 0; gr < ngr; ++gr) {
                    if (max_nbits_gr[gr] > MAX_BITS_PER_GRANULE) {
                        max_nbits_gr[gr] = MAX_BITS_PER_GRANULE;
                    }
                }
            }
            for (gr = 0; gr < ngr; ++gr) {
                float   f[2] = {0.0f, 0.0f}, s = 0.0f;
                for (ch = 0; ch < nch; ++ch) {
                    if (max_nbits_ch[gr][ch] > 0) {
                        f[ch] = sqrt(max_nbits_ch[gr][ch]);
                        s += f[ch];
                    }
                    else {
                        f[ch] = 0;
                    }
                }
                for (ch = 0; ch < nch; ++ch) {
                    if (s > 0) {
                        max_nbits_ch[gr][ch] = max_nbits_gr[gr] * f[ch] / s;
                    }
                    else {
                        max_nbits_ch[gr][ch] = 0;
                    }
                }
                if (nch > 1) {
                    if (max_nbits_ch[gr][0] > use_nbits_ch[gr][0] + 32) {
                        max_nbits_ch[gr][1] += max_nbits_ch[gr][0];
                        max_nbits_ch[gr][1] -= use_nbits_ch[gr][0] + 32;
                        max_nbits_ch[gr][0] = use_nbits_ch[gr][0] + 32;
                    }
                    if (max_nbits_ch[gr][1] > use_nbits_ch[gr][1] + 32) {
                        max_nbits_ch[gr][0] += max_nbits_ch[gr][1];
                        max_nbits_ch[gr][0] -= use_nbits_ch[gr][1] + 32;
                        max_nbits_ch[gr][1] = use_nbits_ch[gr][1] + 32;
                    }
                    for (ch = 0; ch < nch; ++ch) {
                        if (max_nbits_ch[gr][ch] > MAX_BITS_PER_CHANNEL) {
                            max_nbits_ch[gr][ch] = MAX_BITS_PER_CHANNEL;
                        }
                    }
                }
            }
        }
        /* sanity check */
        sum_fr = 0;
        for (gr = 0; gr < ngr; ++gr) {
            int     sum_gr = 0;
            for (ch = 0; ch < nch; ++ch) {
                sum_gr += max_nbits_ch[gr][ch];
                if (max_nbits_ch[gr][ch] > MAX_BITS_PER_CHANNEL) {
                    ok = 0;
                }
            }
            sum_fr += sum_gr;
            if (sum_gr > MAX_BITS_PER_GRANULE) {
                ok = 0;
            }
        }
        if (sum_fr > max_nbits_fr) {
            ok = 0;
        }
        if (!ok) {
            /* we must have done something wrong, fallback to 'on_pe' based constrain */
            for (gr = 0; gr < ngr; ++gr) {
                for (ch = 0; ch < nch; ++ch) {
                    max_nbits_ch[gr][ch] = max_bits[gr][ch];
                }
            }
        }
    }

    /* we already called the 'best_scalefac_store' function, so we need to reset some
     * variables before we can do it again.
     */
    for (ch = 0; ch < nch; ++ch) {
        gfc->l3_side.scfsi[ch][0] = 0;
        gfc->l3_side.scfsi[ch][1] = 0;
        gfc->l3_side.scfsi[ch][2] = 0;
        gfc->l3_side.scfsi[ch][3] = 0;
    }
    for (gr = 0; gr < ngr; ++gr) {
        for (ch = 0; ch < nch; ++ch) {
            gfc->l3_side.tt[gr][ch].scalefac_compress = 0;
        }
    }

    /* alter our encoded data, until it fits into the target bitrate
     */
    use_nbits_fr = 0;
    for (gr = 0; gr < ngr; ++gr) {
        use_nbits_gr[gr] = 0;
        for (ch = 0; ch < nch; ++ch) {
            algo_t const *that = &that_[gr][ch];
            use_nbits_ch[gr][ch] = 0;
            if (max_bits[gr][ch] > 0) {
                int    *sfwork = sfwork_[gr][ch];
                int const *vbrsfmin = vbrsfmin_[gr][ch];
                cutDistribution(sfwork, sfwork, that->cod_info->global_gain);
                outOfBitsStrategy(that, sfwork, vbrsfmin, max_nbits_ch[gr][ch]);
            }
            use_nbits_ch[gr][ch] = reduce_bit_usage(gfc, gr, ch);
            assert(use_nbits_ch[gr][ch] <= max_nbits_ch[gr][ch]);
            use_nbits_gr[gr] += use_nbits_ch[gr][ch];
        }               /* for ch */
        use_nbits_fr += use_nbits_gr[gr];
    }

    /* check bit constrains, but it should always be ok, iff there are no bugs ;-)
     */
    if (use_nbits_fr <= max_nbits_fr) {
        return use_nbits_fr;
    }

    ERRORF(gfc, "INTERNAL ERROR IN VBR NEW CODE (1313), please send bug report\n"
           "maxbits=%d usedbits=%d\n", max_nbits_fr, use_nbits_fr);
    exit(-1);
}
