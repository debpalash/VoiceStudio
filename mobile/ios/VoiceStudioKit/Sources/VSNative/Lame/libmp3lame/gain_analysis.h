/*
 *  ReplayGainAnalysis - analyzes input samples and give the recommended dB change
 *  Copyright (C) 2001 David Robinson and Glen Sawyer
 *
 *  This library is free software; you can redistribute it and/or
 *  modify it under the terms of the GNU Lesser General Public
 *  License as published by the Free Software Foundation; either
 *  version 2.1 of the License, or (at your option) any later version.
 *
 *  This library is distributed in the hope that it will be useful,
 *  but WITHOUT ANY WARRANTY; without even the implied warranty of
 *  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 *  Lesser General Public License for more details.
 *
 *  You should have received a copy of the GNU Lesser General Public
 *  License along with this library; if not, write to the Free Software
 *  Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
 *
 *  concept and filter values by David Robinson (David@Robinson.org)
 *    -- blame him if you think the idea is flawed
 *  coding by Glen Sawyer (mp3gain@hotmail.com) 735 W 255 N, Orem, UT 84057-4505 USA
 *    -- blame him if you think this runs too slowly, or the coding is otherwise flawed
 *
 *  For an explanation of the concepts and the basic algorithms involved, go to:
 *    http://www.replaygain.org/
 */

#ifndef GAIN_ANALYSIS_H
#define GAIN_ANALYSIS_H

#ifdef HAVE_INTTYPES_H
# include <inttypes.h>
#else
# ifdef HAVE_STDINT_H
#  include <stdint.h>
# endif
#endif

#ifdef __cplusplus
extern  "C" {
#endif


    typedef sample_t Float_t; /* Type used for filtering */


#define PINK_REF                64.82       /* 298640883795 */ /* calibration value for 89dB */


#define YULE_ORDER         10
#define BUTTER_ORDER        2
#define YULE_FILTER     filterYule
#define BUTTER_FILTER   filterButter
#define RMS_PERCENTILE      0.95 /* percentile which is louder than the proposed level */
#define MAX_SAMP_FREQ   48000L /* maximum allowed sample frequency [Hz] */
#define RMS_WINDOW_TIME_NUMERATOR    1L
#define RMS_WINDOW_TIME_DENOMINATOR 20L /* numerator / denominator = time slice size [s] */
#define STEPS_per_dB      100 /* Table entries per dB */
#define MAX_dB            120 /* Table entries for 0...MAX_dB (normal max. values are 70...80 dB) */

    enum { GAIN_NOT_ENOUGH_SAMPLES = -24601, GAIN_ANALYSIS_ERROR = 0, GAIN_ANALYSIS_OK =
            1, INIT_GAIN_ANALYSIS_ERROR = 0, INIT_GAIN_ANALYSIS_OK = 1
    };

    enum { MAX_ORDER = (BUTTER_ORDER > YULE_ORDER ? BUTTER_ORDER : YULE_ORDER)
            , MAX_SAMPLES_PER_WINDOW = ((MAX_SAMP_FREQ * RMS_WINDOW_TIME_NUMERATOR) / RMS_WINDOW_TIME_DENOMINATOR + 1) /* max. Samples per Time slice */
    };

    struct replaygain_data {
        Float_t linprebuf[MAX_ORDER * 2];
        Float_t *linpre;     /* left input samples, with pre-buffer */
        Float_t lstepbuf[MAX_SAMPLES_PER_WINDOW + MAX_ORDER];
        Float_t *lstep;      /* left "first step" (i.e. post first filter) samples */
        Float_t loutbuf[MAX_SAMPLES_PER_WINDOW + MAX_ORDER];
        Float_t *lout;       /* left "out" (i.e. post second filter) samples */
        Float_t rinprebuf[MAX_ORDER * 2];
        Float_t *rinpre;     /* right input samples ... */
        Float_t rstepbuf[MAX_SAMPLES_PER_WINDOW + MAX_ORDER];
        Float_t *rstep;
        Float_t routbuf[MAX_SAMPLES_PER_WINDOW + MAX_ORDER];
        Float_t *rout;
        long    sampleWindow; /* number of samples required to reach number of milliseconds required for RMS window */
        long    totsamp;
        double  lsum;
        double  rsum;
        int     freqindex;
        int     first;
        uint32_t A[STEPS_per_dB * MAX_dB];
        uint32_t B[STEPS_per_dB * MAX_dB];

    };
#ifndef replaygain_data_defined
#define replaygain_data_defined
    typedef struct replaygain_data replaygain_t;
#endif




    int     InitGainAnalysis(replaygain_t * rgData, long samplefreq);
    int     AnalyzeSamples(replaygain_t * rgData, const Float_t * left_samples,
                           const Float_t * right_samples, size_t num_samples, int num_channels);
    Float_t GetTitleGain(replaygain_t * rgData);


#ifdef __cplusplus
}
#endif
#endif                       /* GAIN_ANALYSIS_H */
