/*
 *      quantize_pvt source file
 *
 *      Copyright (c) 1999-2002 Takehiro Tominaga
 *      Copyright (c) 2000-2012 Robert Hegemann
 *      Copyright (c) 2001 Naoki Shibata
 *      Copyright (c) 2002-2005 Gabriel Bouvigne
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

/* $Id: quantize_pvt.c,v 1.175 2017/09/06 15:07:30 robert Exp $ */
#ifdef HAVE_CONFIG_H
# include <config.h>
#endif


#include "lame.h"
#include "machine.h"
#include "encoder.h"
#include "util.h"
#include "quantize_pvt.h"
#include "reservoir.h"
#include "lame-analysis.h"
#include <float.h>


#define NSATHSCALE 100  /* Assuming dynamic range=96dB, this value should be 92 */

/*
  The following table is used to implement the scalefactor
  partitioning for MPEG2 as described in section
  2.4.3.2 of the IS. The indexing corresponds to the
  way the tables are presented in the IS:

  [table_number][row_in_table][column of nr_of_sfb]
*/
const int nr_of_sfb_block[6][3][4] = {
    {
     {6, 5, 5, 5},
     {9, 9, 9, 9},
     {6, 9, 9, 9}
     },
    {
     {6, 5, 7, 3},
     {9, 9, 12, 6},
     {6, 9, 12, 6}
     },
    {
     {11, 10, 0, 0},
     {18, 18, 0, 0},
     {15, 18, 0, 0}
     },
    {
     {7, 7, 7, 0},
     {12, 12, 12, 0},
     {6, 15, 12, 0}
     },
    {
     {6, 6, 6, 3},
     {12, 9, 9, 6},
     {6, 12, 9, 6}
     },
    {
     {8, 8, 5, 0},
     {15, 12, 9, 0},
     {6, 18, 9, 0}
     }
};


/* Table B.6: layer3 preemphasis */
const int pretab[SBMAX_l] = {
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
    1, 1, 1, 1, 2, 2, 3, 3, 3, 2, 0
};

/*
  Here are MPEG1 Table B.8 and MPEG2 Table B.1
  -- Layer III scalefactor bands. 
  Index into this using a method such as:
    idx  = fr_ps->header->sampling_frequency
           + (fr_ps->header->version * 3)
*/


const scalefac_struct sfBandIndex[9] = {
    {                   /* Table B.2.b: 22.05 kHz */
     {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464,
      522, 576},
     {0, 4, 8, 12, 18, 24, 32, 42, 56, 74, 100, 132, 174, 192}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     },
    {                   /* Table B.2.c: 24 kHz */ /* docs: 332. mpg123(broken): 330 */
     {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 114, 136, 162, 194, 232, 278, 332, 394, 464,
      540, 576},
     {0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 136, 180, 192}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     },
    {                   /* Table B.2.a: 16 kHz */
     {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464,
      522, 576},
     {0, 4, 8, 12, 18, 26, 36, 48, 62, 80, 104, 134, 174, 192}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     },
    {                   /* Table B.8.b: 44.1 kHz */
     {0, 4, 8, 12, 16, 20, 24, 30, 36, 44, 52, 62, 74, 90, 110, 134, 162, 196, 238, 288, 342, 418,
      576},
     {0, 4, 8, 12, 16, 22, 30, 40, 52, 66, 84, 106, 136, 192}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     },
    {                   /* Table B.8.c: 48 kHz */
     {0, 4, 8, 12, 16, 20, 24, 30, 36, 42, 50, 60, 72, 88, 106, 128, 156, 190, 230, 276, 330, 384,
      576},
     {0, 4, 8, 12, 16, 22, 28, 38, 50, 64, 80, 100, 126, 192}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     },
    {                   /* Table B.8.a: 32 kHz */
     {0, 4, 8, 12, 16, 20, 24, 30, 36, 44, 54, 66, 82, 102, 126, 156, 194, 240, 296, 364, 448, 550,
      576},
     {0, 4, 8, 12, 16, 22, 30, 42, 58, 78, 104, 138, 180, 192}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     },
    {                   /* MPEG-2.5 11.025 kHz */
     {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464,
      522, 576},
     {0 / 3, 12 / 3, 24 / 3, 36 / 3, 54 / 3, 78 / 3, 108 / 3, 144 / 3, 186 / 3, 240 / 3, 312 / 3,
      402 / 3, 522 / 3, 576 / 3}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     },
    {                   /* MPEG-2.5 12 kHz */
     {0, 6, 12, 18, 24, 30, 36, 44, 54, 66, 80, 96, 116, 140, 168, 200, 238, 284, 336, 396, 464,
      522, 576},
     {0 / 3, 12 / 3, 24 / 3, 36 / 3, 54 / 3, 78 / 3, 108 / 3, 144 / 3, 186 / 3, 240 / 3, 312 / 3,
      402 / 3, 522 / 3, 576 / 3}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     },
    {                   /* MPEG-2.5 8 kHz */
     {0, 12, 24, 36, 48, 60, 72, 88, 108, 132, 160, 192, 232, 280, 336, 400, 476, 566, 568, 570,
      572, 574, 576},
     {0 / 3, 24 / 3, 48 / 3, 72 / 3, 108 / 3, 156 / 3, 216 / 3, 288 / 3, 372 / 3, 480 / 3, 486 / 3,
      492 / 3, 498 / 3, 576 / 3}
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb21 pseudo sub bands */
     , {0, 0, 0, 0, 0, 0, 0} /*  sfb12 pseudo sub bands */
     }
};


/* FIXME: move global variables in some struct */

FLOAT   pow20[Q_MAX + Q_MAX2 + 1];
FLOAT   ipow20[Q_MAX];
FLOAT   pow43[PRECALC_SIZE];
/* initialized in first call to iteration_init */
#ifdef TAKEHIRO_IEEE754_HACK
FLOAT   adj43asm[PRECALC_SIZE];
#else
FLOAT   adj43[PRECALC_SIZE];
#endif

/* 
compute the ATH for each scalefactor band 
cd range:  0..96db

Input:  3.3kHz signal  32767 amplitude  (3.3kHz is where ATH is smallest = -5db)
longblocks:  sfb=12   en0/bw=-11db    max_en0 = 1.3db
shortblocks: sfb=5           -9db              0db

Input:  1 1 1 1 1 1 1 -1 -1 -1 -1 -1 -1 -1 (repeated)
longblocks:  amp=1      sfb=12   en0/bw=-103 db      max_en0 = -92db
            amp=32767   sfb=12           -12 db                 -1.4db 

Input:  1 1 1 1 1 1 1 -1 -1 -1 -1 -1 -1 -1 (repeated)
shortblocks: amp=1      sfb=5   en0/bw= -99                    -86 
            amp=32767   sfb=5           -9  db                  4db 


MAX energy of largest wave at 3.3kHz = 1db
AVE energy of largest wave at 3.3kHz = -11db
Let's take AVE:  -11db = maximum signal in sfb=12.  
Dynamic range of CD: 96db.  Therefor energy of smallest audible wave 
in sfb=12  = -11  - 96 = -107db = ATH at 3.3kHz.  

ATH formula for this wave: -5db.  To adjust to LAME scaling, we need
ATH = ATH_formula  - 103  (db)
ATH = ATH * 2.5e-10      (ener)

*/

static  FLOAT
ATHmdct(SessionConfig_t const *cfg, FLOAT f)
{
    FLOAT   ath;

    ath = ATHformula(cfg, f);

    if (cfg->ATHfixpoint > 0) {
        ath -= cfg->ATHfixpoint;
    }
    else {
        ath -= NSATHSCALE;
    }
    ath += cfg->ATH_offset_db;

    /* modify the MDCT scaling for the ATH and convert to energy */
    ath = powf(10.0f, ath * 0.1f);
    return ath;
}

static void
compute_ath(lame_internal_flags const* gfc)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    FLOAT  *const ATH_l = gfc->ATH->l;
    FLOAT  *const ATH_psfb21 = gfc->ATH->psfb21;
    FLOAT  *const ATH_s = gfc->ATH->s;
    FLOAT  *const ATH_psfb12 = gfc->ATH->psfb12;
    int     sfb, i, start, end;
    FLOAT   ATH_f;
    FLOAT const samp_freq = cfg->samplerate_out;

    for (sfb = 0; sfb < SBMAX_l; sfb++) {
        start = gfc->scalefac_band.l[sfb];
        end = gfc->scalefac_band.l[sfb + 1];
        ATH_l[sfb] = FLOAT_MAX;
        for (i = start; i < end; i++) {
            FLOAT const freq = i * samp_freq / (2 * 576);
            ATH_f = ATHmdct(cfg, freq); /* freq in kHz */
            ATH_l[sfb] = Min(ATH_l[sfb], ATH_f);
        }
    }

    for (sfb = 0; sfb < PSFB21; sfb++) {
        start = gfc->scalefac_band.psfb21[sfb];
        end = gfc->scalefac_band.psfb21[sfb + 1];
        ATH_psfb21[sfb] = FLOAT_MAX;
        for (i = start; i < end; i++) {
            FLOAT const freq = i * samp_freq / (2 * 576);
            ATH_f = ATHmdct(cfg, freq); /* freq in kHz */
            ATH_psfb21[sfb] = Min(ATH_psfb21[sfb], ATH_f);
        }
    }

    for (sfb = 0; sfb < SBMAX_s; sfb++) {
        start = gfc->scalefac_band.s[sfb];
        end = gfc->scalefac_band.s[sfb + 1];
        ATH_s[sfb] = FLOAT_MAX;
        for (i = start; i < end; i++) {
            FLOAT const freq = i * samp_freq / (2 * 192);
            ATH_f = ATHmdct(cfg, freq); /* freq in kHz */
            ATH_s[sfb] = Min(ATH_s[sfb], ATH_f);
        }
        ATH_s[sfb] *= (gfc->scalefac_band.s[sfb + 1] - gfc->scalefac_band.s[sfb]);
    }

    for (sfb = 0; sfb < PSFB12; sfb++) {
        start = gfc->scalefac_band.psfb12[sfb];
        end = gfc->scalefac_band.psfb12[sfb + 1];
        ATH_psfb12[sfb] = FLOAT_MAX;
        for (i = start; i < end; i++) {
            FLOAT const freq = i * samp_freq / (2 * 192);
            ATH_f = ATHmdct(cfg, freq); /* freq in kHz */
            ATH_psfb12[sfb] = Min(ATH_psfb12[sfb], ATH_f);
        }
        /*not sure about the following */
        ATH_psfb12[sfb] *= (gfc->scalefac_band.s[13] - gfc->scalefac_band.s[12]);
    }


    /*  no-ATH mode:
     *  reduce ATH to -200 dB
     */

    if (cfg->noATH) {
        for (sfb = 0; sfb < SBMAX_l; sfb++) {
            ATH_l[sfb] = 1E-20;
        }
        for (sfb = 0; sfb < PSFB21; sfb++) {
            ATH_psfb21[sfb] = 1E-20;
        }
        for (sfb = 0; sfb < SBMAX_s; sfb++) {
            ATH_s[sfb] = 1E-20;
        }
        for (sfb = 0; sfb < PSFB12; sfb++) {
            ATH_psfb12[sfb] = 1E-20;
        }
    }

    /*  work in progress, don't rely on it too much
     */
    gfc->ATH->floor = 10. * log10(ATHmdct(cfg, -1.));

    /*
       {   FLOAT g=10000, t=1e30, x;
       for ( f = 100; f < 10000; f++ ) {
       x = ATHmdct( cfg, f );
       if ( t > x ) t = x, g = f;
       }
       printf("min=%g\n", g);
       } */
}


static float const payload_long[2][4] = 
{ {-0.000f, -0.000f, -0.000f, +0.000f}
, {-0.500f, -0.250f, -0.025f, +0.500f}
};
static float const payload_short[2][4] = 
{ {-0.000f, -0.000f, -0.000f, +0.000f}
, {-2.000f, -1.000f, -0.050f, +0.500f}
};

/************************************************************************/
/*  initialization for iteration_loop */
/************************************************************************/
void
iteration_init(lame_internal_flags * gfc)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    III_side_info_t *const l3_side = &gfc->l3_side;
    FLOAT   adjust, db;
    int     i, sel;

    if (gfc->iteration_init_init == 0) {
        gfc->iteration_init_init = 1;

        l3_side->main_data_begin = 0;
        compute_ath(gfc);

        pow43[0] = 0.0;
        for (i = 1; i < PRECALC_SIZE; i++)
            pow43[i] = pow((FLOAT) i, 4.0 / 3.0);

#ifdef TAKEHIRO_IEEE754_HACK
        adj43asm[0] = 0.0;
        for (i = 1; i < PRECALC_SIZE; i++)
            adj43asm[i] = i - 0.5 - pow(0.5 * (pow43[i - 1] + pow43[i]), 0.75);
#else
        for (i = 0; i < PRECALC_SIZE - 1; i++)
            adj43[i] = (i + 1) - pow(0.5 * (pow43[i] + pow43[i + 1]), 0.75);
        adj43[i] = 0.5;
#endif
        for (i = 0; i < Q_MAX; i++)
            ipow20[i] = pow(2.0, (double) (i - 210) * -0.1875);
        for (i = 0; i <= Q_MAX + Q_MAX2; i++)
            pow20[i] = pow(2.0, (double) (i - 210 - Q_MAX2) * 0.25);

        huffman_init(gfc);
        init_xrpow_core_init(gfc);

        sel = 1;/* RH: all modes like vbr-new (cfg->vbr == vbr_mt || cfg->vbr == vbr_mtrh) ? 1 : 0;*/

        /* long */
        db = cfg->adjust_bass_db + payload_long[sel][0];
        adjust = powf(10.f, db * 0.1f);
        for (i = 0; i <= 6; ++i) {
            gfc->sv_qnt.longfact[i] = adjust;
        }
        db = cfg->adjust_alto_db + payload_long[sel][1];
        adjust = powf(10.f, db * 0.1f);
        for (; i <= 13; ++i) {
            gfc->sv_qnt.longfact[i] = adjust;
        }
        db = cfg->adjust_treble_db + payload_long[sel][2];
        adjust = powf(10.f, db * 0.1f);
        for (; i <= 20; ++i) {
            gfc->sv_qnt.longfact[i] = adjust;
        }
        db = cfg->adjust_sfb21_db + payload_long[sel][3];
        adjust = powf(10.f, db * 0.1f);
        for (; i < SBMAX_l; ++i) {
            gfc->sv_qnt.longfact[i] = adjust;
        }

        /* short */
        db = cfg->adjust_bass_db + payload_short[sel][0];
        adjust = powf(10.f, db * 0.1f);
        for (i = 0; i <= 2; ++i) {
            gfc->sv_qnt.shortfact[i] = adjust;
        }
        db = cfg->adjust_alto_db + payload_short[sel][1];
        adjust = powf(10.f, db * 0.1f);
        for (; i <= 6; ++i) {
            gfc->sv_qnt.shortfact[i] = adjust;
        }
        db = cfg->adjust_treble_db + payload_short[sel][2];
        adjust = powf(10.f, db * 0.1f);
        for (; i <= 11; ++i) {
            gfc->sv_qnt.shortfact[i] = adjust;
        }
        db = cfg->adjust_sfb21_db + payload_short[sel][3];
        adjust = powf(10.f, db * 0.1f);
        for (; i < SBMAX_s; ++i) {
            gfc->sv_qnt.shortfact[i] = adjust;
        }
    }
}





/************************************************************************
 * allocate bits among 2 channels based on PE
 * mt 6/99
 * bugfixes rh 8/01: often allocated more than the allowed 4095 bits
 ************************************************************************/
int
on_pe(lame_internal_flags * gfc, const FLOAT pe[][2], int targ_bits[2], int mean_bits, int gr, int cbr)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    int     extra_bits = 0, tbits, bits;
    int     add_bits[2] = {0, 0};
    int     max_bits;        /* maximum allowed bits for this granule */
    int     ch;

    /* allocate targ_bits for granule */
    ResvMaxBits(gfc, mean_bits, &tbits, &extra_bits, cbr);
    max_bits = tbits + extra_bits;
    if (max_bits > MAX_BITS_PER_GRANULE) /* hard limit per granule */
        max_bits = MAX_BITS_PER_GRANULE;

    for (bits = 0, ch = 0; ch < cfg->channels_out; ++ch) {
        /******************************************************************
         * allocate bits for each channel 
         ******************************************************************/
        targ_bits[ch] = Min(MAX_BITS_PER_CHANNEL, tbits / cfg->channels_out);

        add_bits[ch] = targ_bits[ch] * pe[gr][ch] / 700.0 - targ_bits[ch];

        /* at most increase bits by 1.5*average */
        if (add_bits[ch] > mean_bits * 3 / 4)
            add_bits[ch] = mean_bits * 3 / 4;
        if (add_bits[ch] < 0)
            add_bits[ch] = 0;

        if (add_bits[ch] + targ_bits[ch] > MAX_BITS_PER_CHANNEL)
            add_bits[ch] = Max(0, MAX_BITS_PER_CHANNEL - targ_bits[ch]);

        bits += add_bits[ch];
    }
    if (bits > extra_bits && bits > 0) {
        for (ch = 0; ch < cfg->channels_out; ++ch) {
            add_bits[ch] = extra_bits * add_bits[ch] / bits;
        }
    }

    for (ch = 0; ch < cfg->channels_out; ++ch) {
        targ_bits[ch] += add_bits[ch];
        extra_bits -= add_bits[ch];
    }

    for (bits = 0, ch = 0; ch < cfg->channels_out; ++ch) {
        bits += targ_bits[ch];
    }
    if (bits > MAX_BITS_PER_GRANULE) {
        int     sum = 0;
        for (ch = 0; ch < cfg->channels_out; ++ch) {
            targ_bits[ch] *= MAX_BITS_PER_GRANULE;
            targ_bits[ch] /= bits;
            sum += targ_bits[ch];
        }
        assert(sum <= MAX_BITS_PER_GRANULE);
    }

    return max_bits;
}




void
reduce_side(int targ_bits[2], FLOAT ms_ener_ratio, int mean_bits, int max_bits)
{
    int     move_bits;
    FLOAT   fac;

    assert(max_bits <= MAX_BITS_PER_GRANULE);
    assert(targ_bits[0] + targ_bits[1] <= MAX_BITS_PER_GRANULE);

    /*  ms_ener_ratio = 0:  allocate 66/33  mid/side  fac=.33  
     *  ms_ener_ratio =.5:  allocate 50/50 mid/side   fac= 0 */
    /* 75/25 split is fac=.5 */
    /* float fac = .50*(.5-ms_ener_ratio[gr])/.5; */
    fac = .33 * (.5 - ms_ener_ratio) / .5;
    if (fac < 0)
        fac = 0;
    if (fac > .5)
        fac = .5;

    /* number of bits to move from side channel to mid channel */
    /*    move_bits = fac*targ_bits[1];  */
    move_bits = fac * .5 * (targ_bits[0] + targ_bits[1]);

    if (move_bits > MAX_BITS_PER_CHANNEL - targ_bits[0]) {
        move_bits = MAX_BITS_PER_CHANNEL - targ_bits[0];
    }
    if (move_bits < 0)
        move_bits = 0;

    if (targ_bits[1] >= 125) {
        /* dont reduce side channel below 125 bits */
        if (targ_bits[1] - move_bits > 125) {

            /* if mid channel already has 2x more than average, dont bother */
            /* mean_bits = bits per granule (for both channels) */
            if (targ_bits[0] < mean_bits)
                targ_bits[0] += move_bits;
            targ_bits[1] -= move_bits;
        }
        else {
            targ_bits[0] += targ_bits[1] - 125;
            targ_bits[1] = 125;
        }
    }

    move_bits = targ_bits[0] + targ_bits[1];
    if (move_bits > max_bits) {
        targ_bits[0] = (max_bits * targ_bits[0]) / move_bits;
        targ_bits[1] = (max_bits * targ_bits[1]) / move_bits;
    }
    assert(targ_bits[0] <= MAX_BITS_PER_CHANNEL);
    assert(targ_bits[1] <= MAX_BITS_PER_CHANNEL);
    assert(targ_bits[0] + targ_bits[1] <= MAX_BITS_PER_GRANULE);
}


/**
 *  Robert Hegemann 2001-04-27:
 *  this adjusts the ATH, keeping the original noise floor
 *  affects the higher frequencies more than the lower ones
 */

FLOAT
athAdjust(FLOAT a, FLOAT x, FLOAT athFloor, float ATHfixpoint)
{
    /*  work in progress
     */
    FLOAT const o = 90.30873362f;
    FLOAT const p = (ATHfixpoint < 1.f) ? 94.82444863f : ATHfixpoint;
    FLOAT   u = FAST_LOG10_X(x, 10.0f);
    FLOAT const v = a * a;
    FLOAT   w = 0.0f;
    u -= athFloor;      /* undo scaling */
    if (v > 1E-20f)
        w = 1.f + FAST_LOG10_X(v, 10.0f / o);
    if (w < 0)
        w = 0.f;
    u *= w;
    u += athFloor + o - p; /* redo scaling */

    return powf(10.f, 0.1f * u);
}



/*************************************************************************/
/*            calc_xmin                                                  */
/*************************************************************************/

/*
  Calculate the allowed distortion for each scalefactor band,
  as determined by the psychoacoustic model.
  xmin(sb) = ratio(sb) * en(sb) / bw(sb)

  returns number of sfb's with energy > ATH
*/

int
calc_xmin(lame_internal_flags const *gfc,
          III_psy_ratio const *const ratio, gr_info * const cod_info, FLOAT * pxmin)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    int     sfb, gsfb, j = 0, ath_over = 0, k;
    ATH_t const *const ATH = gfc->ATH;
    const FLOAT *const xr = cod_info->xr;
    int     max_nonzero;

    for (gsfb = 0; gsfb < cod_info->psy_lmax; gsfb++) {
        FLOAT   en0, xmin;
        FLOAT   rh1, rh2, rh3;
        int     width, l;

        xmin = athAdjust(ATH->adjust_factor, ATH->l[gsfb], ATH->floor, cfg->ATHfixpoint);
        xmin *= gfc->sv_qnt.longfact[gsfb];

        width = cod_info->width[gsfb];
        rh1 = xmin / width;
#ifdef DBL_EPSILON
        rh2 = DBL_EPSILON;
#else
        rh2 = 2.2204460492503131e-016;
#endif
        en0 = 0.0;
        for (l = 0; l < width; ++l) {
            FLOAT const xa = xr[j++];
            FLOAT const x2 = xa * xa;
            en0 += x2;
            rh2 += (x2 < rh1) ? x2 : rh1;
        }
        if (en0 > xmin)
            ath_over++;

        if (en0 < xmin) {
            rh3 = en0;
        }
        else if (rh2 < xmin) {
            rh3 = xmin;
        }
        else {
            rh3 = rh2;
        }
        xmin = rh3;
        {
            FLOAT const e = ratio->en.l[gsfb];
            if (e > 1e-12f) {
                FLOAT   x;
                x = en0 * ratio->thm.l[gsfb] / e;
                x *= gfc->sv_qnt.longfact[gsfb];
                if (xmin < x)
                    xmin = x;
            }
        }
        xmin = Max(xmin, DBL_EPSILON);
        cod_info->energy_above_cutoff[gsfb] = (en0 > xmin+1e-14f) ? 1 : 0;
        *pxmin++ = xmin;
    }                   /* end of long block loop */




    /*use this function to determine the highest non-zero coeff */
    max_nonzero = 0;
    for (k = 575; k > 0; --k) {
        if (fabs(xr[k]) > 1e-12f) {
            max_nonzero = k;
            break;
        }
    }
    if (cod_info->block_type != SHORT_TYPE) { /* NORM, START or STOP type, but not SHORT */
        max_nonzero |= 1; /* only odd numbers */
    }
    else {
        max_nonzero /= 6; /* 3 short blocks */
        max_nonzero *= 6;
        max_nonzero += 5;
    }

    if (gfc->sv_qnt.sfb21_extra == 0 && cfg->samplerate_out < 44000) {
      int const sfb_l = (cfg->samplerate_out <= 8000) ? 17 : 21;
      int const sfb_s = (cfg->samplerate_out <= 8000) ?  9 : 12;
      int   limit = 575;
      if (cod_info->block_type != SHORT_TYPE) { /* NORM, START or STOP type, but not SHORT */
          limit = gfc->scalefac_band.l[sfb_l]-1;
      }
      else {
          limit = 3*gfc->scalefac_band.s[sfb_s]-1;
      }
      if (max_nonzero > limit) {
          max_nonzero = limit;
      }
    }
    cod_info->max_nonzero_coeff = max_nonzero;



    for (sfb = cod_info->sfb_smin; gsfb < cod_info->psymax; sfb++, gsfb += 3) {
        int     width, b, l;
        FLOAT   tmpATH;

        tmpATH = athAdjust(ATH->adjust_factor, ATH->s[sfb], ATH->floor, cfg->ATHfixpoint);
        tmpATH *= gfc->sv_qnt.shortfact[sfb];
        
        width = cod_info->width[gsfb];
        for (b = 0; b < 3; b++) {
            FLOAT   en0 = 0.0, xmin = tmpATH;
            FLOAT   rh1, rh2, rh3;

            rh1 = tmpATH / width;
#ifdef DBL_EPSILON
            rh2 = DBL_EPSILON;
#else
            rh2 = 2.2204460492503131e-016;
#endif
            for (l = 0; l < width; ++l) {
                FLOAT const xa = xr[j++];
                FLOAT const x2 = xa * xa;
                en0 += x2;
                rh2 += (x2 < rh1) ? x2 : rh1;
            }
            if (en0 > tmpATH)
                ath_over++;
            
            if (en0 < tmpATH) {
                rh3 = en0;
            }
            else if (rh2 < tmpATH) {
                rh3 = tmpATH;
            }
            else {
                rh3 = rh2;
            }
            xmin = rh3;
            {
                FLOAT const e = ratio->en.s[sfb][b];
                if (e > 1e-12f) {
                    FLOAT   x;
                    x = en0 * ratio->thm.s[sfb][b] / e;
                    x *= gfc->sv_qnt.shortfact[sfb];
                    if (xmin < x)
                        xmin = x;
                }
            }
            xmin = Max(xmin, DBL_EPSILON);
            cod_info->energy_above_cutoff[gsfb+b] = (en0 > xmin+1e-14f) ? 1 : 0;
            *pxmin++ = xmin;
        }               /* b */
        if (cfg->use_temporal_masking_effect) {
            if (pxmin[-3] > pxmin[-3 + 1])
                pxmin[-3 + 1] += (pxmin[-3] - pxmin[-3 + 1]) * gfc->cd_psy->decay;
            if (pxmin[-3 + 1] > pxmin[-3 + 2])
                pxmin[-3 + 2] += (pxmin[-3 + 1] - pxmin[-3 + 2]) * gfc->cd_psy->decay;
        }
    }                   /* end of short block sfb loop */

    return ath_over;
}


static  FLOAT
calc_noise_core_c(const gr_info * const cod_info, int *startline, int l, FLOAT step)
{
    FLOAT   noise = 0;
    int     j = *startline;
    const int *const ix = cod_info->l3_enc;

    if (j > cod_info->count1) {
        while (l--) {
            FLOAT   temp;
            temp = cod_info->xr[j];
            j++;
            noise += temp * temp;
            temp = cod_info->xr[j];
            j++;
            noise += temp * temp;
        }
    }
    else if (j > cod_info->big_values) {
        FLOAT   ix01[2];
        ix01[0] = 0;
        ix01[1] = step;
        while (l--) {
            FLOAT   temp;
            temp = fabs(cod_info->xr[j]) - ix01[ix[j]];
            j++;
            noise += temp * temp;
            temp = fabs(cod_info->xr[j]) - ix01[ix[j]];
            j++;
            noise += temp * temp;
        }
    }
    else {
        while (l--) {
            FLOAT   temp;
            temp = fabs(cod_info->xr[j]) - pow43[ix[j]] * step;
            j++;
            noise += temp * temp;
            temp = fabs(cod_info->xr[j]) - pow43[ix[j]] * step;
            j++;
            noise += temp * temp;
        }
    }

    *startline = j;
    return noise;
}


/*************************************************************************/
/*            calc_noise                                                 */
/*************************************************************************/

/* -oo dB  =>  -1.00 */
/* - 6 dB  =>  -0.97 */
/* - 3 dB  =>  -0.80 */
/* - 2 dB  =>  -0.64 */
/* - 1 dB  =>  -0.38 */
/*   0 dB  =>   0.00 */
/* + 1 dB  =>  +0.49 */
/* + 2 dB  =>  +1.06 */
/* + 3 dB  =>  +1.68 */
/* + 6 dB  =>  +3.69 */
/* +10 dB  =>  +6.45 */

int
calc_noise(gr_info const *const cod_info,
           FLOAT const *l3_xmin,
           FLOAT * distort, calc_noise_result * const res, calc_noise_data * prev_noise)
{
    int     sfb, l, over = 0;
    FLOAT   over_noise_db = 0;
    FLOAT   tot_noise_db = 0; /*    0 dB relative to masking */
    FLOAT   max_noise = -20.0; /* -200 dB relative to masking */
    int     j = 0;
    const int *scalefac = cod_info->scalefac;

    res->over_SSD = 0;


    for (sfb = 0; sfb < cod_info->psymax; sfb++) {
        int const s =
            cod_info->global_gain - (((*scalefac++) + (cod_info->preflag ? pretab[sfb] : 0))
                                     << (cod_info->scalefac_scale + 1))
            - cod_info->subblock_gain[cod_info->window[sfb]] * 8;
        FLOAT const r_l3_xmin = 1.f / *l3_xmin++;
        FLOAT   distort_ = 0.0f;
        FLOAT   noise = 0.0f;

        if (prev_noise && (prev_noise->step[sfb] == s)) {

            /* use previously computed values */
            j += cod_info->width[sfb];
            distort_ = r_l3_xmin * prev_noise->noise[sfb];

            noise = prev_noise->noise_log[sfb];

        }
        else {
            FLOAT const step = POW20(s);
            l = cod_info->width[sfb] >> 1;

            if ((j + cod_info->width[sfb]) > cod_info->max_nonzero_coeff) {
                int     usefullsize;
                usefullsize = cod_info->max_nonzero_coeff - j + 1;

                if (usefullsize > 0)
                    l = usefullsize >> 1;
                else
                    l = 0;
            }

            noise = calc_noise_core_c(cod_info, &j, l, step);


            if (prev_noise) {
                /* save noise values */
                prev_noise->step[sfb] = s;
                prev_noise->noise[sfb] = noise;
            }

            distort_ = r_l3_xmin * noise;

            /* multiplying here is adding in dB, but can overflow */
            noise = FAST_LOG10(Max(distort_, 1E-20f));

            if (prev_noise) {
                /* save noise values */
                prev_noise->noise_log[sfb] = noise;
            }
        }
        *distort++ = distort_;

        if (prev_noise) {
            /* save noise values */
            prev_noise->global_gain = cod_info->global_gain;;
        }


        /*tot_noise *= Max(noise, 1E-20); */
        tot_noise_db += noise;

        if (noise > 0.0) {
            int     tmp;

            tmp = Max((int) (noise * 10 + .5), 1);
            res->over_SSD += tmp * tmp;

            over++;
            /* multiplying here is adding in dB -but can overflow */
            /*over_noise *= noise; */
            over_noise_db += noise;
        }
        max_noise = Max(max_noise, noise);

    }

    res->over_count = over;
    res->tot_noise = tot_noise_db;
    res->over_noise = over_noise_db;
    res->max_noise = max_noise;

    return over;
}








/************************************************************************
 *
 *  set_pinfo()
 *
 *  updates plotting data    
 *
 *  Mark Taylor 2000-??-??                
 *
 *  Robert Hegemann: moved noise/distortion calc into it
 *
 ************************************************************************/

static void
set_pinfo(lame_internal_flags const *gfc,
          gr_info * const cod_info, const III_psy_ratio * const ratio, const int gr, const int ch)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    int     sfb, sfb2;
    int     j, i, l, start, end, bw;
    FLOAT   en0, en1;
    FLOAT const ifqstep = (cod_info->scalefac_scale == 0) ? .5 : 1.0;
    int const *const scalefac = cod_info->scalefac;

    FLOAT   l3_xmin[SFBMAX], xfsf[SFBMAX];
    calc_noise_result noise;

    (void) calc_xmin(gfc, ratio, cod_info, l3_xmin);
    (void) calc_noise(cod_info, l3_xmin, xfsf, &noise, 0);

    j = 0;
    sfb2 = cod_info->sfb_lmax;
    if (cod_info->block_type != SHORT_TYPE && !cod_info->mixed_block_flag)
        sfb2 = 22;
    for (sfb = 0; sfb < sfb2; sfb++) {
        start = gfc->scalefac_band.l[sfb];
        end = gfc->scalefac_band.l[sfb + 1];
        bw = end - start;
        for (en0 = 0.0; j < end; j++)
            en0 += cod_info->xr[j] * cod_info->xr[j];
        en0 /= bw;
        /* convert to MDCT units */
        en1 = 1e15;     /* scaling so it shows up on FFT plot */
        gfc->pinfo->en[gr][ch][sfb] = en1 * en0;
        gfc->pinfo->xfsf[gr][ch][sfb] = en1 * l3_xmin[sfb] * xfsf[sfb] / bw;

        if (ratio->en.l[sfb] > 0 && !cfg->ATHonly)
            en0 = en0 / ratio->en.l[sfb];
        else
            en0 = 0.0;

        gfc->pinfo->thr[gr][ch][sfb] = en1 * Max(en0 * ratio->thm.l[sfb], gfc->ATH->l[sfb]);

        /* there is no scalefactor bands >= SBPSY_l */
        gfc->pinfo->LAMEsfb[gr][ch][sfb] = 0;
        if (cod_info->preflag && sfb >= 11)
            gfc->pinfo->LAMEsfb[gr][ch][sfb] = -ifqstep * pretab[sfb];

        if (sfb < SBPSY_l) {
            assert(scalefac[sfb] >= 0); /* scfsi should be decoded by caller side */
            gfc->pinfo->LAMEsfb[gr][ch][sfb] -= ifqstep * scalefac[sfb];
        }
    }                   /* for sfb */

    if (cod_info->block_type == SHORT_TYPE) {
        sfb2 = sfb;
        for (sfb = cod_info->sfb_smin; sfb < SBMAX_s; sfb++) {
            start = gfc->scalefac_band.s[sfb];
            end = gfc->scalefac_band.s[sfb + 1];
            bw = end - start;
            for (i = 0; i < 3; i++) {
                for (en0 = 0.0, l = start; l < end; l++) {
                    en0 += cod_info->xr[j] * cod_info->xr[j];
                    j++;
                }
                en0 = Max(en0 / bw, 1e-20);
                /* convert to MDCT units */
                en1 = 1e15; /* scaling so it shows up on FFT plot */

                gfc->pinfo->en_s[gr][ch][3 * sfb + i] = en1 * en0;
                gfc->pinfo->xfsf_s[gr][ch][3 * sfb + i] = en1 * l3_xmin[sfb2] * xfsf[sfb2] / bw;
                if (ratio->en.s[sfb][i] > 0)
                    en0 = en0 / ratio->en.s[sfb][i];
                else
                    en0 = 0.0;
                if (cfg->ATHonly || cfg->ATHshort)
                    en0 = 0;

                gfc->pinfo->thr_s[gr][ch][3 * sfb + i] =
                    en1 * Max(en0 * ratio->thm.s[sfb][i], gfc->ATH->s[sfb]);

                /* there is no scalefactor bands >= SBPSY_s */
                gfc->pinfo->LAMEsfb_s[gr][ch][3 * sfb + i]
                    = -2.0 * cod_info->subblock_gain[i];
                if (sfb < SBPSY_s) {
                    gfc->pinfo->LAMEsfb_s[gr][ch][3 * sfb + i] -= ifqstep * scalefac[sfb2];
                }
                sfb2++;
            }
        }
    }                   /* block type short */
    gfc->pinfo->LAMEqss[gr][ch] = cod_info->global_gain;
    gfc->pinfo->LAMEmainbits[gr][ch] = cod_info->part2_3_length + cod_info->part2_length;
    gfc->pinfo->LAMEsfbits[gr][ch] = cod_info->part2_length;

    gfc->pinfo->over[gr][ch] = noise.over_count;
    gfc->pinfo->max_noise[gr][ch] = noise.max_noise * 10.0;
    gfc->pinfo->over_noise[gr][ch] = noise.over_noise * 10.0;
    gfc->pinfo->tot_noise[gr][ch] = noise.tot_noise * 10.0;
    gfc->pinfo->over_SSD[gr][ch] = noise.over_SSD;
}


/************************************************************************
 *
 *  set_frame_pinfo()
 *
 *  updates plotting data for a whole frame  
 *
 *  Robert Hegemann 2000-10-21                          
 *
 ************************************************************************/

void
set_frame_pinfo(lame_internal_flags * gfc, const III_psy_ratio ratio[2][2])
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    int     ch;
    int     gr;

    /* for every granule and channel patch l3_enc and set info
     */
    for (gr = 0; gr < cfg->mode_gr; gr++) {
        for (ch = 0; ch < cfg->channels_out; ch++) {
            gr_info *const cod_info = &gfc->l3_side.tt[gr][ch];
            int     scalefac_sav[SFBMAX];
            memcpy(scalefac_sav, cod_info->scalefac, sizeof(scalefac_sav));

            /* reconstruct the scalefactors in case SCFSI was used 
             */
            if (gr == 1) {
                int     sfb;
                for (sfb = 0; sfb < cod_info->sfb_lmax; sfb++) {
                    if (cod_info->scalefac[sfb] < 0) /* scfsi */
                        cod_info->scalefac[sfb] = gfc->l3_side.tt[0][ch].scalefac[sfb];
                }
            }

            set_pinfo(gfc, cod_info, &ratio[gr][ch], gr, ch);
            memcpy(cod_info->scalefac, scalefac_sav, sizeof(scalefac_sav));
        }               /* for ch */
    }                   /* for gr */
}
