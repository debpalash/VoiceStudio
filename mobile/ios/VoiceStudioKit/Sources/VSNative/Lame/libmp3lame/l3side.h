/*
 *	Layer 3 side include file
 *
 *	Copyright (c) 1999 Mark Taylor
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

#ifndef LAME_L3SIDE_H
#define LAME_L3SIDE_H

/* max scalefactor band, max(SBMAX_l, SBMAX_s*3, (SBMAX_s-3)*3+8) */
#define SFBMAX (SBMAX_s*3)

/* Layer III side information. */
typedef struct {
    int     l[1 + SBMAX_l];
    int     s[1 + SBMAX_s];
    int     psfb21[1 + PSFB21];
    int     psfb12[1 + PSFB12];
} scalefac_struct;


typedef struct {
    FLOAT   l[SBMAX_l];
    FLOAT   s[SBMAX_s][3];
} III_psy_xmin;

typedef struct {
    III_psy_xmin thm;
    III_psy_xmin en;
} III_psy_ratio;

typedef struct {
    FLOAT   xr[576];
    int     l3_enc[576];
    int     scalefac[SFBMAX];
    FLOAT   xrpow_max;

    int     part2_3_length;
    int     big_values;
    int     count1;
    int     global_gain;
    int     scalefac_compress;
    int     block_type;
    int     mixed_block_flag;
    int     table_select[3];
    int     subblock_gain[3 + 1];
    int     region0_count;
    int     region1_count;
    int     preflag;
    int     scalefac_scale;
    int     count1table_select;

    int     part2_length;
    int     sfb_lmax;
    int     sfb_smin;
    int     psy_lmax;
    int     sfbmax;
    int     psymax;
    int     sfbdivide;
    int     width[SFBMAX];
    int     window[SFBMAX];
    int     count1bits;
    /* added for LSF */
    const int *sfb_partition_table;
    int     slen[4];

    int     max_nonzero_coeff;
    char    energy_above_cutoff[SFBMAX];
} gr_info;

typedef struct {
    gr_info tt[2][2];
    int     main_data_begin;
    int     private_bits;
    int     resvDrain_pre;
    int     resvDrain_post;
    int     scfsi[2][4];
} III_side_info_t;

#endif
