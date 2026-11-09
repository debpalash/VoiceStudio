/*
 *      lame utility library include file
 *
 *      Copyright (c) 1999 Albert L Faber
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

#ifndef LAME_UTIL_H
#define LAME_UTIL_H

#include "l3side.h"
#include "id3tag.h"
#include "lame_global_flags.h"

#ifdef __cplusplus
extern  "C" {
#endif

/***********************************************************************
*
*  Global Definitions
*
***********************************************************************/

#ifndef FALSE
#define         FALSE                   0
#endif

#ifndef TRUE
#define         TRUE                    (!FALSE)
#endif

#ifdef UINT_MAX
# define         MAX_U_32_NUM            UINT_MAX
#else
# define         MAX_U_32_NUM            0xFFFFFFFF
#endif

#ifndef PI
# ifdef M_PI
#  define       PI                      M_PI
# else
#  define       PI                      3.14159265358979323846
# endif
#endif


#ifdef M_LN2
# define        LOG2                    M_LN2
#else
# define        LOG2                    0.69314718055994530942
#endif

#ifdef M_LN10
# define        LOG10                   M_LN10
#else
# define        LOG10                   2.30258509299404568402
#endif


#ifdef M_SQRT2
# define        SQRT2                   M_SQRT2
#else
# define        SQRT2                   1.41421356237309504880
#endif


#define         CRC16_POLYNOMIAL        0x8005

#define MAX_BITS_PER_CHANNEL 4095
#define MAX_BITS_PER_GRANULE 7680

/* "bit_stream.h" Definitions */
#define         BUFFER_SIZE     LAME_MAXMP3BUFFER

#define         Min(A, B)       ((A) < (B) ? (A) : (B))
#define         Max(A, B)       ((A) > (B) ? (A) : (B))

/* log/log10 approximations */
#ifdef USE_FAST_LOG
#define         FAST_LOG10(x)       (fast_log2(x)*(LOG2/LOG10))
#define         FAST_LOG(x)         (fast_log2(x)*LOG2)
#define         FAST_LOG10_X(x,y)   (fast_log2(x)*(LOG2/LOG10*(y)))
#define         FAST_LOG_X(x,y)     (fast_log2(x)*(LOG2*(y)))
#else
#define         FAST_LOG10(x)       log10(x)
#define         FAST_LOG(x)         log(x)
#define         FAST_LOG10_X(x,y)   (log10(x)*(y))
#define         FAST_LOG_X(x,y)     (log(x)*(y))
#endif


    struct replaygain_data;
#ifndef replaygain_data_defined
#define replaygain_data_defined
    typedef struct replaygain_data replaygain_t;
#endif
    struct plotting_data;
#ifndef plotting_data_defined
#define plotting_data_defined
    typedef struct plotting_data plotting_data;
#endif

/***********************************************************************
*
*  Global Type Definitions
*
***********************************************************************/

    typedef struct {
        void   *aligned;     /* pointer to ie. 128 bit aligned memory */
        void   *pointer;     /* to use with malloc/free */
    } aligned_pointer_t;

    void    calloc_aligned(aligned_pointer_t * ptr, unsigned int size, unsigned int bytes);
    void    free_aligned(aligned_pointer_t * ptr);


    /* "bit_stream.h" Type Definitions */

    typedef struct bit_stream_struc {
        unsigned char *buf;  /* bit stream buffer */
        int     buf_size;    /* size of buffer (in number of bytes) */
        int     totbit;      /* bit counter of bit stream */
        int     buf_byte_idx; /* pointer to top byte in buffer */
        int     buf_bit_idx; /* pointer to top bit of top byte in buffer */

        /* format of file in rd mode (BINARY/ASCII) */
    } Bit_stream_struc;



    typedef struct {
        int     sum;         /* what we have seen so far */
        int     seen;        /* how many frames we have seen in this chunk */
        int     want;        /* how many frames we want to collect into one chunk */
        int     pos;         /* actual position in our bag */
        int     size;        /* size of our bag */
        int    *bag;         /* pointer to our bag */
        unsigned int nVbrNumFrames;
        unsigned long nBytesWritten;
        /* VBR tag data */
        unsigned int TotalFrameSize;
    } VBR_seek_info_t;


    /**
     *  ATH related stuff, if something new ATH related has to be added,
     *  please plugg it here into the ATH_t struct
     */
    typedef struct {
        int     use_adjust;  /* method for the auto adjustment  */
        FLOAT   aa_sensitivity_p; /* factor for tuning the (sample power)
                                     point below which adaptive threshold
                                     of hearing adjustment occurs */
        FLOAT   adjust_factor; /* lowering based on peak volume, 1 = no lowering */
        FLOAT   adjust_limit; /* limit for dynamic ATH adjust */
        FLOAT   decay;       /* determined to lower x dB each second */
        FLOAT   floor;       /* lowest ATH value */
        FLOAT   l[SBMAX_l];  /* ATH for sfbs in long blocks */
        FLOAT   s[SBMAX_s];  /* ATH for sfbs in short blocks */
        FLOAT   psfb21[PSFB21]; /* ATH for partitionned sfb21 in long blocks */
        FLOAT   psfb12[PSFB12]; /* ATH for partitionned sfb12 in short blocks */
        FLOAT   cb_l[CBANDS]; /* ATH for long block convolution bands */
        FLOAT   cb_s[CBANDS]; /* ATH for short block convolution bands */
        FLOAT   eql_w[BLKSIZE / 2]; /* equal loudness weights (based on ATH) */
    } ATH_t;

    /**
     *  PSY Model related stuff
     */

    typedef struct {
        FLOAT   masking_lower[CBANDS];
        FLOAT   minval[CBANDS];
        FLOAT   rnumlines[CBANDS];
        FLOAT   mld_cb[CBANDS];
        FLOAT   mld[Max(SBMAX_l,SBMAX_s)];
        FLOAT   bo_weight[Max(SBMAX_l,SBMAX_s)]; /* band weight long scalefactor bands, at transition */
        FLOAT   attack_threshold; /* short block tuning */
        int     s3ind[CBANDS][2];
        int     numlines[CBANDS];
        int     bm[Max(SBMAX_l,SBMAX_s)];
        int     bo[Max(SBMAX_l,SBMAX_s)];
        int     npart;
        int     n_sb; /* SBMAX_l or SBMAX_s */
        FLOAT  *s3;
    } PsyConst_CB2SB_t;


    /**
     *  global data constants
     */
    typedef struct {
        FLOAT window[BLKSIZE], window_s[BLKSIZE_s / 2];
        PsyConst_CB2SB_t l;
        PsyConst_CB2SB_t s;
        PsyConst_CB2SB_t l_to_s;
        FLOAT   attack_threshold[4];
        FLOAT   decay;
        int     force_short_block_calc;
    } PsyConst_t;


    typedef struct {

        FLOAT   nb_l1[4][CBANDS], nb_l2[4][CBANDS];
        FLOAT   nb_s1[4][CBANDS], nb_s2[4][CBANDS];

        III_psy_xmin thm[4];
        III_psy_xmin en[4];

        /* loudness calculation (for adaptive threshold of hearing) */
        FLOAT   loudness_sq_save[2]; /* account for granule delay of L3psycho_anal */

        FLOAT   tot_ener[4];

        FLOAT   last_en_subshort[4][9];
        int     last_attacks[4];

        int     blocktype_old[2];
    } PsyStateVar_t;


    typedef struct {
        /* loudness calculation (for adaptive threshold of hearing) */
        FLOAT   loudness_sq[2][2]; /* loudness^2 approx. per granule and channel */
    } PsyResult_t;


    /* variables used by encoder.c */
    typedef struct {
        /* variables for newmdct.c */
        FLOAT   sb_sample[2][2][18][SBLIMIT];
        FLOAT   amp_filter[32];

        /* variables used by util.c */
        /* BPC = maximum number of filter convolution windows to precompute */
#define BPC 320
        double  itime[2]; /* float precision seems to be not enough */
        sample_t *inbuf_old[2];
        sample_t *blackfilt[2 * BPC + 1];

        FLOAT   pefirbuf[19];
        
        /* used for padding */
        int     frac_SpF;
        int     slot_lag;

        /* variables for bitstream.c */
        /* mpeg1: buffer=511 bytes  smallest frame: 96-38(sideinfo)=58
         * max number of frames in reservoir:  8
         * mpeg2: buffer=255 bytes.  smallest frame: 24-23bytes=1
         * with VBR, if you are encoding all silence, it is possible to
         * have 8kbs/24khz frames with 1byte of data each, which means we need
         * to buffer up to 255 headers! */
        /* also, max_header_buf has to be a power of two */
#define MAX_HEADER_BUF 256
#define MAX_HEADER_LEN 40    /* max size of header is 38 */
        struct {
            int     write_timing;
            int     ptr;
            char    buf[MAX_HEADER_LEN];
        } header[MAX_HEADER_BUF];

        int     h_ptr;
        int     w_ptr;
        int     ancillary_flag;

        /* variables for reservoir.c */
        int     ResvSize;    /* in bits */
        int     ResvMax;     /* in bits */

        int     in_buffer_nsamples;
        sample_t *in_buffer_0;
        sample_t *in_buffer_1;

#ifndef  MFSIZE
# define MFSIZE  ( 3*1152 + ENCDELAY - MDCTDELAY )
#endif
        sample_t mfbuf[2][MFSIZE];

        int     mf_samples_to_encode;
        int     mf_size;

    } EncStateVar_t;


    typedef struct {
        /* simple statistics */
        int     bitrate_channelmode_hist[16][4 + 1];
        int     bitrate_blocktype_hist[16][4 + 1 + 1]; /*norm/start/short/stop/mixed(short)/sum */

        int     bitrate_index;
        int     frame_number; /* number of frames encoded             */
        int     padding;     /* padding for the current frame? */
        int     mode_ext;
        int     encoder_delay;
        int     encoder_padding; /* number of samples of padding appended to input */
    } EncResult_t;


    /* variables used by quantize.c */
    typedef struct {
        /* variables for nspsytune */
        FLOAT   longfact[SBMAX_l];
        FLOAT   shortfact[SBMAX_s];
        FLOAT   masking_lower;
        FLOAT   mask_adjust; /* the dbQ stuff */
        FLOAT   mask_adjust_short; /* the dbQ stuff */
        int     OldValue[2];
        int     CurrentStep[2];
        int     pseudohalf[SFBMAX];
        int     sfb21_extra; /* will be set in lame_init_params */
        int     substep_shaping; /* 0 = no substep
                                    1 = use substep shaping at last step(VBR only)
                                    (not implemented yet)
                                    2 = use substep inside loop
                                    3 = use substep inside loop and last step
                                  */


        char    bv_scf[576];
    } QntStateVar_t;


    typedef struct {
        replaygain_t *rgdata;
        /* ReplayGain */
    } RpgStateVar_t;


    typedef struct {
        FLOAT   noclipScale; /* user-specified scale factor required for preventing clipping */
        sample_t PeakSample;
        int     RadioGain;
        int     noclipGainChange; /* gain change required for preventing clipping */
    } RpgResult_t;


    typedef struct {
        int     version;     /* 0=MPEG-2/2.5  1=MPEG-1               */
        int     samplerate_index;
        int     sideinfo_len;

        int     noise_shaping; /* 0 = none
                                  1 = ISO AAC model
                                  2 = allow scalefac_select=1
                                */

        int     subblock_gain; /*  0 = no, 1 = yes */
        int     use_best_huffman; /* 0 = no.  1=outside loop  2=inside loop(slow) */
        int     noise_shaping_amp; /*  0 = ISO model: amplify all distorted bands
                                      1 = amplify within 50% of max (on db scale)
                                      2 = amplify only most distorted band
                                      3 = method 1 and refine with method 2
                                    */

        int     noise_shaping_stop; /* 0 = stop at over=0, all scalefacs amplified or
                                       a scalefac has reached max value
                                       1 = stop when all scalefacs amplified or
                                       a scalefac has reached max value
                                       2 = stop when all scalefacs amplified
                                     */


        int     full_outer_loop; /* 0 = stop early after 0 distortion found. 1 = full search */

        int     lowpassfreq;
        int     highpassfreq;
        int     samplerate_in; /* input_samp_rate in Hz. default=44.1 kHz     */
        int     samplerate_out; /* output_samp_rate. */
        int     channels_in; /* number of channels in the input data stream (PCM or decoded PCM) */
        int     channels_out; /* number of channels in the output data stream (not used for decoding) */
        int     mode_gr;     /* granules per frame */
        int     force_ms;    /* force M/S mode.  requires mode=1            */

        int     quant_comp;
        int     quant_comp_short;

        int     use_temporal_masking_effect;
        int     use_safe_joint_stereo;

        int     preset;

        vbr_mode vbr;
        int     vbr_avg_bitrate_kbps;
        int     vbr_min_bitrate_index; /* min bitrate index */
        int     vbr_max_bitrate_index; /* max bitrate index */
        int     avg_bitrate;
        int     enforce_min_bitrate; /* strictly enforce VBR_min_bitrate normaly, it will be violated for analog silence */

        int     findReplayGain; /* find the RG value? default=0       */
        int     findPeakSample;
        int     decode_on_the_fly; /* decode on the fly? default=0                */
        int     analysis;
        int     disable_reservoir;
        int     buffer_constraint;  /* enforce ISO spec as much as possible   */
        int     free_format;
        int     write_lame_tag; /* add Xing VBR tag?                           */

        int     error_protection; /* use 2 bytes per frame for a CRC checksum. default=0 */
        int     copyright;   /* mark as copyright. default=0           */
        int     original;    /* mark as original. default=1            */
        int     extension;   /* the MP3 'private extension' bit. Meaningless */
        int     emphasis;    /* Input PCM is emphased PCM (for
                                instance from one of the rarely
                                emphased CDs), it is STRONGLY not
                                recommended to use this, because
                                psycho does not take it into account,
                                and last but not least many decoders
                                don't care about these bits          */


        MPEG_mode mode;
        short_block_t short_blocks;

        float   interChRatio;
        float   msfix;       /* Naoki's adjustment of Mid/Side maskings */
        float   ATH_offset_db;/* add to ATH this many db            */
        float   ATH_offset_factor;/* change ATH by this factor, derived from ATH_offset_db */
        float   ATHcurve;    /* change ATH formula 4 shape           */
        int     ATHtype;
        int     ATHonly;     /* only use ATH                         */
        int     ATHshort;    /* only use ATH for short blocks        */
        int     noATH;       /* disable ATH                          */
        
        float   ATHfixpoint;

        float   adjust_alto_db;
        float   adjust_bass_db;
        float   adjust_treble_db;
        float   adjust_sfb21_db;

        float   compression_ratio; /* sizeof(wav file)/sizeof(mp3 file)          */

        /* lowpass and highpass filter control */
        FLOAT   lowpass1, lowpass2; /* normalized frequency bounds of passband */
        FLOAT   highpass1, highpass2; /* normalized frequency bounds of passband */

        /* scale input by this amount before encoding at least not used for MP3 decoding */
        FLOAT   pcm_transform[2][2];

        FLOAT   minval;
    } SessionConfig_t;


    struct lame_internal_flags {

  /********************************************************************
   * internal variables NOT set by calling program, and should not be *
   * modified by the calling program                                  *
   ********************************************************************/

        /*
         * Some remarks to the Class_ID field:
         * The Class ID is an Identifier for a pointer to this struct.
         * It is very unlikely that a pointer to lame_global_flags has the same 32 bits
         * in it's structure (large and other special properties, for instance prime).
         *
         * To test that the structure is right and initialized, use:
         *     if ( gfc -> Class_ID == LAME_ID ) ...
         * Other remark:
         *     If you set a flag to 0 for uninit data and 1 for init data, the right test
         *     should be "if (flag == 1)" and NOT "if (flag)". Unintended modification
         *     of this element will be otherwise misinterpreted as an init.
         */
#  define  LAME_ID   0xFFF88E3B
        unsigned long class_id;

        int     lame_init_params_successful;
        int     lame_encode_frame_init;
        int     iteration_init_init;
        int     fill_buffer_resample_init;

        SessionConfig_t cfg;

        /* variables used by lame.c */
        Bit_stream_struc bs;
        III_side_info_t l3_side;

        scalefac_struct scalefac_band;

        PsyStateVar_t sv_psy; /* DATA FROM PSYMODEL.C */
        PsyResult_t ov_psy;
        EncStateVar_t sv_enc; /* DATA FROM ENCODER.C */
        EncResult_t ov_enc;
        QntStateVar_t sv_qnt; /* DATA FROM QUANTIZE.C */

        RpgStateVar_t sv_rpg;
        RpgResult_t ov_rpg;

        /* optional ID3 tags, used in id3tag.c  */
        struct id3tag_spec tag_spec;
        uint16_t nMusicCRC;

        uint16_t _unused;

        /* CPU features */
        struct {
            unsigned int MMX:1; /* Pentium MMX, Pentium II...IV, K6, K6-2,
                                   K6-III, Athlon */
            unsigned int AMD_3DNow:1; /* K6-2, K6-III, Athlon      */
            unsigned int SSE:1; /* Pentium III, Pentium 4    */
            unsigned int SSE2:1; /* Pentium 4, K8             */
            unsigned int _unused:28;
        } CPU_features;


        VBR_seek_info_t VBR_seek_table; /* used for Xing VBR header */

        ATH_t  *ATH;         /* all ATH related stuff */

        PsyConst_t *cd_psy;

        /* used by the frame analyzer */
        plotting_data *pinfo;
        hip_t hip;

        /* functions to replace with CPU feature optimized versions in takehiro.c */
        int     (*choose_table) (const int *ix, const int *const end, int *const s);
        void    (*fft_fht) (FLOAT *, int);
        void    (*init_xrpow_core) (gr_info * const cod_info, FLOAT xrpow[576], int upper,
                                    FLOAT * sum);

        lame_report_function report_msg;
        lame_report_function report_dbg;
        lame_report_function report_err;
    };

#ifndef lame_internal_flags_defined
#define lame_internal_flags_defined
    typedef struct lame_internal_flags lame_internal_flags;
#endif


/***********************************************************************
*
*  Global Function Prototype Declarations
*
***********************************************************************/
    void    freegfc(lame_internal_flags * const gfc);
    void    free_id3tag(lame_internal_flags * const gfc);
    extern int BitrateIndex(int, int, int);
    extern int FindNearestBitrate(int, int, int);
    extern int map2MP3Frequency(int freq);
    extern int SmpFrqIndex(int, int *const);
    extern int nearestBitrateFullIndex(uint16_t brate);
    extern FLOAT ATHformula(SessionConfig_t const *cfg, FLOAT freq);
    extern FLOAT freq2bark(FLOAT freq);
    void    disable_FPE(void);

/* log/log10 approximations */
    extern void init_log_table(void);
    extern ieee754_float32_t fast_log2(ieee754_float32_t x);

    int     isResamplingNecessary(SessionConfig_t const* cfg);

    void    fill_buffer(lame_internal_flags * gfc,
                        sample_t *const mfbuf[2],
                        sample_t const *const in_buffer[2], int nsamples, int *n_in, int *n_out);

/* same as lame_decode1 (look in lame.h), but returns
   unclipped raw floating-point samples. It is declared
   here, not in lame.h, because it returns LAME's
   internal type sample_t. No more than 1152 samples
   per channel are allowed. */
    int     hip_decode1_unclipped(hip_t hip, unsigned char *mp3buf,
                                   size_t len, sample_t pcm_l[], sample_t pcm_r[]);


    extern int has_MMX(void);
    extern int has_3DNow(void);
    extern int has_SSE(void);
    extern int has_SSE2(void);



/***********************************************************************
*
*  Macros about Message Printing and Exit
*
***********************************************************************/

    extern void lame_report_def(const char* format, va_list args);
    extern void lame_report_fnc(lame_report_function print_f, const char *, ...);
    extern void lame_errorf(const lame_internal_flags * gfc, const char *, ...);
    extern void lame_debugf(const lame_internal_flags * gfc, const char *, ...);
    extern void lame_msgf(const lame_internal_flags * gfc, const char *, ...);
#define DEBUGF  lame_debugf
#define ERRORF  lame_errorf
#define MSGF    lame_msgf

    int     is_lame_internal_flags_valid(const lame_internal_flags * gfp);
    
    extern void hip_set_pinfo(hip_t hip, plotting_data* pinfo);

#ifdef __cplusplus
}
#endif
#endif                       /* LAME_UTIL_H */
