#ifndef LAME_GLOBAL_FLAGS_H
#define LAME_GLOBAL_FLAGS_H

#ifndef lame_internal_flags_defined
#define lame_internal_flags_defined
struct lame_internal_flags;
typedef struct lame_internal_flags lame_internal_flags;
#endif


typedef enum short_block_e {
    short_block_not_set = -1, /* allow LAME to decide */
    short_block_allowed = 0, /* LAME may use them, even different block types for L/R */
    short_block_coupled, /* LAME may use them, but always same block types in L/R */
    short_block_dispensed, /* LAME will not use short blocks, long blocks only */
    short_block_forced  /* LAME will not use long blocks, short blocks only */
} short_block_t;

/***********************************************************************
*
*  Control Parameters set by User.  These parameters are here for
*  backwards compatibility with the old, non-shared lib API.
*  Please use the lame_set_variablename() functions below
*
*
***********************************************************************/
struct lame_global_struct {
    unsigned int class_id;

    /* input description */
    unsigned long num_samples; /* number of samples. default=2^32-1           */
    int     num_channels;    /* input number of channels. default=2         */
    int     samplerate_in;   /* input_samp_rate in Hz. default=44.1 kHz     */
    int     samplerate_out;  /* output_samp_rate.
                                default: LAME picks best value
                                at least not used for MP3 decoding:
                                Remember 44.1 kHz MP3s and AC97           */
    float   scale;           /* scale input by this amount before encoding
                                at least not used for MP3 decoding          */
    float   scale_left;      /* scale input of channel 0 (left) by this
                                amount before encoding                      */
    float   scale_right;     /* scale input of channel 1 (right) by this
                                amount before encoding                      */

    /* general control params */
    int     analysis;        /* collect data for a MP3 frame analyzer?      */
    int     write_lame_tag;  /* add Xing VBR tag?                           */
    int     decode_only;     /* use lame/mpglib to convert mp3 to wav       */
    int     quality;         /* quality setting 0=best,  9=worst  default=5 */
    MPEG_mode mode;          /* see enum in lame.h
                                default = LAME picks best value             */
    int     force_ms;        /* force M/S mode.  requires mode=1            */
    int     free_format;     /* use free format? default=0                  */
    int     findReplayGain;  /* find the RG value? default=0       */
    int     decode_on_the_fly; /* decode on the fly? default=0                */
    int     write_id3tag_automatic; /* 1 (default) writes ID3 tags, 0 not */

    int     nogap_total;
    int     nogap_current;

    int     substep_shaping;
    int     noise_shaping;
    int     subblock_gain;   /*  0 = no, 1 = yes */
    int     use_best_huffman; /* 0 = no.  1=outside loop  2=inside loop(slow) */

    /*
     * set either brate>0  or compression_ratio>0, LAME will compute
     * the value of the variable not set.
     * Default is compression_ratio = 11.025
     */
    int     brate;           /* bitrate                                    */
    float   compression_ratio; /* sizeof(wav file)/sizeof(mp3 file)          */


    /* frame params */
    int     copyright;       /* mark as copyright. default=0           */
    int     original;        /* mark as original. default=1            */
    int     extension;       /* the MP3 'private extension' bit.
                                Meaningless                            */
    int     emphasis;        /* Input PCM is emphased PCM (for
                                instance from one of the rarely
                                emphased CDs), it is STRONGLY not
                                recommended to use this, because
                                psycho does not take it into account,
                                and last but not least many decoders
                                don't care about these bits          */
    int     error_protection; /* use 2 bytes per frame for a CRC
                                 checksum. default=0                    */
    int     strict_ISO;      /* enforce ISO spec as much as possible   */

    int     disable_reservoir; /* use bit reservoir?                     */

    /* quantization/noise shaping */
    int     quant_comp;
    int     quant_comp_short;
    int     experimentalY;
    int     experimentalZ;
    int     exp_nspsytune;

    int     preset;

    /* VBR control */
    vbr_mode VBR;
    float   VBR_q_frac;      /* Range [0,...,1[ */
    int     VBR_q;           /* Range [0,...,9] */
    int     VBR_mean_bitrate_kbps;
    int     VBR_min_bitrate_kbps;
    int     VBR_max_bitrate_kbps;
    int     VBR_hard_min;    /* strictly enforce VBR_min_bitrate
                                normaly, it will be violated for analog
                                silence                                 */


    /* resampling and filtering */
    int     lowpassfreq;     /* freq in Hz. 0=lame choses.
                                -1=no filter                          */
    int     highpassfreq;    /* freq in Hz. 0=lame choses.
                                -1=no filter                          */
    int     lowpasswidth;    /* freq width of filter, in Hz
                                (default=15%)                         */
    int     highpasswidth;   /* freq width of filter, in Hz
                                (default=15%)                         */



    /*
     * psycho acoustics and other arguments which you should not change
     * unless you know what you are doing
     */
    float   maskingadjust;
    float   maskingadjust_short;
    int     ATHonly;         /* only use ATH                         */
    int     ATHshort;        /* only use ATH for short blocks        */
    int     noATH;           /* disable ATH                          */
    int     ATHtype;         /* select ATH formula                   */
    float   ATHcurve;        /* change ATH formula 4 shape           */
    float   ATH_lower_db;    /* lower ATH by this many db            */
    int     athaa_type;      /* select ATH auto-adjust scheme        */
    float   athaa_sensitivity; /* dB, tune active region of auto-level */
    short_block_t short_blocks;
    int     useTemporal;     /* use temporal masking effect          */
    float   interChRatio;
    float   msfix;           /* Naoki's adjustment of Mid/Side maskings */

    int     tune;            /* 0 off, 1 on */
    float   tune_value_a;    /* used to pass values for debugging and stuff */

    float   attackthre;      /* attack threshold for L/R/M channel */
    float   attackthre_s;    /* attack threshold for S channel */


    struct {
        void    (*msgf) (const char *format, va_list ap);
        void    (*debugf) (const char *format, va_list ap);
        void    (*errorf) (const char *format, va_list ap);
    } report;

  /************************************************************************/
    /* internal variables, do not set...                                    */
    /* provided because they may be of use to calling application           */
  /************************************************************************/

    int     lame_allocated_gfp; /* is this struct owned by calling
                                   program or lame?                     */



  /**************************************************************************/
    /* more internal variables are stored in this structure:                  */
  /**************************************************************************/
    lame_internal_flags *internal_flags;


    struct {
        int     mmx;
        int     amd3dnow;
        int     sse;

    } asm_optimizations;
};

int     is_lame_global_flags_valid(const lame_global_flags * gfp);

#endif /* LAME_GLOBAL_FLAGS_H */
