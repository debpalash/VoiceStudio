/*
 *	quantize_pvt include file
 *
 *	Copyright (c) 1999 Takehiro TOMINAGA
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

#ifndef LAME_QUANTIZE_PVT_H
#define LAME_QUANTIZE_PVT_H

#define IXMAX_VAL 8206  /* ix always <= 8191+15.    see count_bits() */

/* buggy Winamp decoder cannot handle values > 8191 */
/* #define IXMAX_VAL 8191 */

#define PRECALC_SIZE (IXMAX_VAL+2)


extern const int nr_of_sfb_block[6][3][4];
extern const int pretab[SBMAX_l];
extern const int slen1_tab[16];
extern const int slen2_tab[16];

extern const scalefac_struct sfBandIndex[9];

extern FLOAT pow43[PRECALC_SIZE];
#ifdef TAKEHIRO_IEEE754_HACK
extern FLOAT adj43asm[PRECALC_SIZE];
#else
extern FLOAT adj43[PRECALC_SIZE];
#endif

#define Q_MAX (256+1)
#define Q_MAX2 116      /* minimum possible number of
                           -cod_info->global_gain
                           + ((scalefac[] + (cod_info->preflag ? pretab[sfb] : 0))
                           << (cod_info->scalefac_scale + 1))
                           + cod_info->subblock_gain[cod_info->window[sfb]] * 8;

                           for long block, 0+((15+3)<<2) = 18*4 = 72
                           for short block, 0+(15<<2)+7*8 = 15*4+56 = 116
                         */

extern FLOAT pow20[Q_MAX + Q_MAX2 + 1];
extern FLOAT ipow20[Q_MAX];

typedef struct calc_noise_result_t {
    FLOAT   over_noise;      /* sum of quantization noise > masking */
    FLOAT   tot_noise;       /* sum of all quantization noise */
    FLOAT   max_noise;       /* max quantization noise */
    int     over_count;      /* number of quantization noise > masking */
    int     over_SSD;        /* SSD-like cost of distorted bands */
    int     bits;
} calc_noise_result;


/**
* allows re-use of previously
* computed noise values
*/
typedef struct calc_noise_data_t {
    int     global_gain;
    int     sfb_count1;
    int     step[39];
    FLOAT   noise[39];
    FLOAT   noise_log[39];
} calc_noise_data;


int     on_pe(lame_internal_flags * gfc, const FLOAT pe[2][2],
              int targ_bits[2], int mean_bits, int gr, int cbr);

void    reduce_side(int targ_bits[2], FLOAT ms_ener_ratio, int mean_bits, int max_bits);


void    iteration_init(lame_internal_flags * gfc);


int     calc_xmin(lame_internal_flags const *gfc,
                  III_psy_ratio const *const ratio, gr_info * const cod_info, FLOAT * l3_xmin);

int     calc_noise(const gr_info * const cod_info,
                   const FLOAT * l3_xmin,
                   FLOAT * distort, calc_noise_result * const res, calc_noise_data * prev_noise);

void    set_frame_pinfo(lame_internal_flags * gfc, const III_psy_ratio ratio[2][2]);




/* takehiro.c */

int     count_bits(lame_internal_flags const *const gfc, const FLOAT * const xr,
                   gr_info * const cod_info, calc_noise_data * prev_noise);
int     noquant_count_bits(lame_internal_flags const *const gfc,
                           gr_info * const cod_info, calc_noise_data * prev_noise);


void    best_huffman_divide(const lame_internal_flags * const gfc, gr_info * const cod_info);

void    best_scalefac_store(const lame_internal_flags * gfc, const int gr, const int ch,
                            III_side_info_t * const l3_side);

int     scale_bitcount(const lame_internal_flags * gfc, gr_info * cod_info);

void    huffman_init(lame_internal_flags * const gfc);

void    init_xrpow_core_init(lame_internal_flags * const gfc);

FLOAT   athAdjust(FLOAT a, FLOAT x, FLOAT athFloor, float ATHfixpoint);

#define LARGE_BITS 100000

#endif /* LAME_QUANTIZE_PVT_H */
