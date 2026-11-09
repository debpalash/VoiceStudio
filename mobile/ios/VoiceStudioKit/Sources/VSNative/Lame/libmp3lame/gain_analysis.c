/*
 *  ReplayGainAnalysis - analyzes input samples and give the recommended dB change
 *  Copyright (C) 2001 David Robinson and Glen Sawyer
 *  Improvements and optimizations added by Frank Klemm, and by Marcel Muller 
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
 *  original coding by Glen Sawyer (mp3gain@hotmail.com)
 *    -- blame him if you think this runs too slowly, or the coding is otherwise flawed
 *
 *  lots of code improvements by Frank Klemm ( http://www.uni-jena.de/~pfk/mpp/ )
 *    -- credit him for all the _good_ programming ;)
 *
 *
 *  For an explanation of the concepts and the basic algorithms involved, go to:
 *    http://www.replaygain.org/
 */

/*
 *  Here's the deal. Call
 *
 *    InitGainAnalysis ( long samplefreq );
 *
 *  to initialize everything. Call
 *
 *    AnalyzeSamples ( const Float_t*  left_samples,
 *                     const Float_t*  right_samples,
 *                     size_t          num_samples,
 *                     int             num_channels );
 *
 *  as many times as you want, with as many or as few samples as you want.
 *  If mono, pass the sample buffer in through left_samples, leave
 *  right_samples NULL, and make sure num_channels = 1.
 *
 *    GetTitleGain()
 *
 *  will return the recommended dB level change for all samples analyzed
 *  SINCE THE LAST TIME you called GetTitleGain() OR InitGainAnalysis().
 *
 *    GetAlbumGain()
 *
 *  will return the recommended dB level change for all samples analyzed
 *  since InitGainAnalysis() was called and finalized with GetTitleGain().
 *
 *  Pseudo-code to process an album:
 *
 *    Float_t       l_samples [4096];
 *    Float_t       r_samples [4096];
 *    size_t        num_samples;
 *    unsigned int  num_songs;
 *    unsigned int  i;
 *
 *    InitGainAnalysis ( 44100 );
 *    for ( i = 1; i <= num_songs; i++ ) {
 *        while ( ( num_samples = getSongSamples ( song[i], left_samples, right_samples ) ) > 0 )
 *            AnalyzeSamples ( left_samples, right_samples, num_samples, 2 );
 *        fprintf ("Recommended dB change for song %2d: %+6.2f dB\n", i, GetTitleGain() );
 *    }
 *    fprintf ("Recommended dB change for whole album: %+6.2f dB\n", GetAlbumGain() );
 */

/*
 *  So here's the main source of potential code confusion:
 *
 *  The filters applied to the incoming samples are IIR filters,
 *  meaning they rely on up to <filter order> number of previous samples
 *  AND up to <filter order> number of previous filtered samples.
 *
 *  I set up the AnalyzeSamples routine to minimize memory usage and interface
 *  complexity. The speed isn't compromised too much (I don't think), but the
 *  internal complexity is higher than it should be for such a relatively
 *  simple routine.
 *
 *  Optimization/clarity suggestions are welcome.
 */

#ifdef HAVE_CONFIG_H
#include <config.h>
#endif

#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "lame.h"
#include "machine.h"
#include "gain_analysis.h"

/* for each filter: */
/* [0] 48 kHz, [1] 44.1 kHz, [2] 32 kHz, [3] 24 kHz, [4] 22050 Hz, [5] 16 kHz, [6] 12 kHz, [7] is 11025 Hz, [8] 8 kHz */

#ifdef WIN32
#pragma warning ( disable : 4305 )
#endif


/*lint -save -e736 loss of precision */
static const Float_t ABYule[9][multiple_of(4, 2 * YULE_ORDER + 1)] = {
    /* 20                 18                 16                 14                 12                 10                 8                  6                  4                  2                 0                 19                 17                 15                 13                 11                 9                  7                  5                  3                  1              */
    { 0.00288463683916,  0.00012025322027,  0.00306428023191,  0.00594298065125, -0.02074045215285,  0.02161526843274, -0.01655260341619, -0.00009291677959, -0.00123395316851, -0.02160367184185, 0.03857599435200, 0.13919314567432, -0.86984376593551,  2.75465861874613, -5.87257861775999,  9.48293806319790,-12.28759895145294, 13.05504219327545,-11.34170355132042,  7.81501653005538, -3.84664617118067},
    {-0.00187763777362,  0.00674613682247, -0.00240879051584,  0.01624864962975, -0.02596338512915,  0.02245293253339, -0.00834990904936, -0.00851165645469, -0.00848709379851, -0.02911007808948, 0.05418656406430, 0.13149317958808, -0.75104302451432,  2.19611684890774, -4.39470996079559,  6.85401540936998, -8.81498681370155,  9.47693607801280, -8.54751527471874,  6.36317777566148, -3.47845948550071},
    {-0.00881362733839,  0.00651420667831, -0.01390589421898,  0.03174092540049,  0.00222312597743,  0.04781476674921, -0.05588393329856,  0.02163541888798, -0.06247880153653, -0.09331049056315, 0.15457299681924, 0.02347897407020, -0.05032077717131,  0.16378164858596, -0.45953458054983,  1.00595954808547, -1.67148153367602,  2.23697657451713, -2.64577170229825,  2.84868151156327, -2.37898834973084},
    {-0.02950134983287,  0.00205861885564, -0.00000828086748,  0.06276101321749, -0.00584456039913, -0.02364141202522, -0.00915702933434,  0.03282930172664, -0.08587323730772, -0.22613988682123, 0.30296907319327, 0.00302439095741,  0.02005851806501,  0.04500235387352, -0.22138138954925,  0.39120800788284, -0.22638893773906, -0.16276719120440, -0.25656257754070,  1.07977492259970, -1.61273165137247},
    {-0.01760176568150, -0.01635381384540,  0.00832043980773,  0.05724228140351, -0.00589500224440, -0.00469977914380, -0.07834489609479,  0.11921148675203, -0.11828570177555, -0.25572241425570, 0.33642304856132, 0.02977207319925, -0.04237348025746,  0.08333755284107, -0.04067510197014, -0.12453458140019,  0.47854794562326, -0.80774944671438,  0.12205022308084,  0.87350271418188, -1.49858979367799},
    { 0.00541907748707, -0.03193428438915, -0.01863887810927,  0.10478503600251,  0.04097565135648, -0.12398163381748,  0.04078262797139, -0.01419140100551, -0.22784394429749, -0.14351757464547, 0.44915256608450, 0.03222754072173,  0.05784820375801,  0.06747620744683,  0.00613424350682,  0.22199650564824, -0.42029820170918,  0.00213767857124, -0.37256372942400,  0.29661783706366, -0.62820619233671},
    {-0.00588215443421, -0.03788984554840,  0.08647503780351,  0.00647310677246, -0.27562961986224,  0.30931782841830, -0.18901604199609,  0.16744243493672,  0.16242137742230, -0.75464456939302, 0.56619470757641, 0.01807364323573,  0.01639907836189, -0.04784254229033,  0.06739368333110, -0.33032403314006,  0.45054734505008,  0.00819999645858, -0.26806001042947,  0.29156311971249, -1.04800335126349},
    {-0.00749618797172, -0.03721611395801,  0.06920467763959,  0.01628462406333, -0.25344790059353,  0.15558449135573,  0.02377945217615,  0.17520704835522, -0.14289799034253, -0.53174909058578, 0.58100494960553, 0.01818801111503,  0.02442357316099, -0.02505961724053, -0.05246019024463, -0.23313271880868,  0.38952639978999,  0.14728154134330, -0.20256413484477, -0.31863563325245, -0.51035327095184},
    {-0.02217936801134,  0.04788665548180, -0.04060034127000, -0.11202315195388, -0.02459864859345,  0.14590772289388, -0.10214864179676,  0.04267842219415, -0.00275953611929, -0.42163034350696, 0.53648789255105, 0.04704409688120,  0.05477720428674, -0.18823009262115, -0.17556493366449,  0.15113130533216,  0.26408300200955, -0.04678328784242, -0.03424681017675, -0.43193942311114, -0.25049871956020}
};

static const Float_t ABButter[9][multiple_of(4, 2 * BUTTER_ORDER + 1)] = {
    /* 5                4                  3                  2                 1              */
    {0.98621192462708, 0.97261396931306, -1.97242384925416, -1.97223372919527, 0.98621192462708},
    {0.98500175787242, 0.97022847566350, -1.97000351574484, -1.96977855582618, 0.98500175787242},
    {0.97938932735214, 0.95920349965459, -1.95877865470428, -1.95835380975398, 0.97938932735214},
    {0.97531843204928, 0.95124613669835, -1.95063686409857, -1.95002759149878, 0.97531843204928},
    {0.97316523498161, 0.94705070426118, -1.94633046996323, -1.94561023566527, 0.97316523498161},
    {0.96454515552826, 0.93034775234268, -1.92909031105652, -1.92783286977036, 0.96454515552826},
    {0.96009142950541, 0.92177618768381, -1.92018285901082, -1.91858953033784, 0.96009142950541},
    {0.95856916599601, 0.91885558323625, -1.91713833199203, -1.91542108074780, 0.95856916599601},
    {0.94597685600279, 0.89487434461664, -1.89195371200558, -1.88903307939452, 0.94597685600279}
};

/*lint -restore */

#ifdef WIN32
#pragma warning ( default : 4305 )
#endif

/* When calling this procedure, make sure that ip[-order] and op[-order] point to real data! */

static void
filterYule(const Float_t * input, Float_t * output, size_t nSamples, const Float_t * const kernel)
{
    while (nSamples--) {
        Float_t y0 =  input[-10] * kernel[ 0];
        Float_t y2 =  input[ -9] * kernel[ 1];
        Float_t y4 =  input[ -8] * kernel[ 2];
        Float_t y6 =  input[ -7] * kernel[ 3];
        Float_t s00 = y0 + y2 + y4 + y6;
        Float_t y8 =  input[ -6] * kernel[ 4];
        Float_t yA =  input[ -5] * kernel[ 5];
        Float_t yC =  input[ -4] * kernel[ 6];
        Float_t yE =  input[ -3] * kernel[ 7];
        Float_t s01 = y8 + yA + yC + yE;
        Float_t yG =  input[ -2] * kernel[ 8] + input[ -1] * kernel[ 9];
        Float_t yK =  input[  0] * kernel[10];

        Float_t s1 = s00 + s01 + yG + yK;

        Float_t x1 = output[-10] * kernel[11] + output[ -9] * kernel[12];
        Float_t x5 = output[ -8] * kernel[13] + output[ -7] * kernel[14];
        Float_t x9 = output[ -6] * kernel[15] + output[ -5] * kernel[16];
        Float_t xD = output[ -4] * kernel[17] + output[ -3] * kernel[18];
        Float_t xH = output[ -2] * kernel[19] + output[ -1] * kernel[20];

        Float_t s2 = x1 + x5 + x9 + xD + xH;

        output[0] = (Float_t)(s1 - s2);

        ++output;
        ++input;
    }
}

static void
filterButter(const Float_t * input, Float_t * output, size_t nSamples, const Float_t * const kernel)
{
    while (nSamples--) {
        Float_t s1 =  input[-2] * kernel[0] +  input[-1] * kernel[2] +  input[ 0] * kernel[4];
        Float_t s2 = output[-2] * kernel[1] + output[-1] * kernel[3];
        output[0] = (Float_t)(s1 - s2);
        ++output;
        ++input;
    }
}



static int ResetSampleFrequency(replaygain_t * rgData, long samplefreq);

/* returns a INIT_GAIN_ANALYSIS_OK if successful, INIT_GAIN_ANALYSIS_ERROR if not */

int
ResetSampleFrequency(replaygain_t * rgData, long samplefreq)
{
    /* zero out initial values, only first MAX_ORDER values */
    memset(rgData->linprebuf, 0, MAX_ORDER * sizeof(*rgData->linprebuf));
    memset(rgData->rinprebuf, 0, MAX_ORDER * sizeof(*rgData->rinprebuf));
    memset(rgData->lstepbuf,  0, MAX_ORDER * sizeof(*rgData->lstepbuf));
    memset(rgData->rstepbuf,  0, MAX_ORDER * sizeof(*rgData->rstepbuf));
    memset(rgData->loutbuf,   0, MAX_ORDER * sizeof(*rgData->loutbuf));
    memset(rgData->routbuf,   0, MAX_ORDER * sizeof(*rgData->routbuf));

    switch ((int) (samplefreq)) {
    case 48000:
        rgData->freqindex = 0;
        break;
    case 44100:
        rgData->freqindex = 1;
        break;
    case 32000:
        rgData->freqindex = 2;
        break;
    case 24000:
        rgData->freqindex = 3;
        break;
    case 22050:
        rgData->freqindex = 4;
        break;
    case 16000:
        rgData->freqindex = 5;
        break;
    case 12000:
        rgData->freqindex = 6;
        break;
    case 11025:
        rgData->freqindex = 7;
        break;
    case 8000:
        rgData->freqindex = 8;
        break;
    default:
        return INIT_GAIN_ANALYSIS_ERROR;
    }

    rgData->sampleWindow =
        (samplefreq * RMS_WINDOW_TIME_NUMERATOR + RMS_WINDOW_TIME_DENOMINATOR -
         1) / RMS_WINDOW_TIME_DENOMINATOR;

    rgData->lsum = 0.;
    rgData->rsum = 0.;
    rgData->totsamp = 0;

    memset(rgData->A, 0, sizeof(rgData->A));

    return INIT_GAIN_ANALYSIS_OK;
}

int
InitGainAnalysis(replaygain_t * rgData, long samplefreq)
{
    if (ResetSampleFrequency(rgData, samplefreq) != INIT_GAIN_ANALYSIS_OK) {
        return INIT_GAIN_ANALYSIS_ERROR;
    }

    rgData->linpre = rgData->linprebuf + MAX_ORDER;
    rgData->rinpre = rgData->rinprebuf + MAX_ORDER;
    rgData->lstep = rgData->lstepbuf + MAX_ORDER;
    rgData->rstep = rgData->rstepbuf + MAX_ORDER;
    rgData->lout = rgData->loutbuf + MAX_ORDER;
    rgData->rout = rgData->routbuf + MAX_ORDER;

    memset(rgData->B, 0, sizeof(rgData->B));

    return INIT_GAIN_ANALYSIS_OK;
}

/* returns GAIN_ANALYSIS_OK if successful, GAIN_ANALYSIS_ERROR if not */

int
AnalyzeSamples(replaygain_t * rgData, const Float_t * left_samples, const Float_t * right_samples,
               size_t num_samples, int num_channels)
{
    const Float_t *curleft;
    const Float_t *curright;
    long    batchsamples;
    long    cursamples;
    long    cursamplepos;
    int     i;
    Float_t sum_l, sum_r;

    if (num_samples == 0)
        return GAIN_ANALYSIS_OK;

    cursamplepos = 0;
    batchsamples = (long) num_samples;

    switch (num_channels) {
    case 1:
        right_samples = left_samples;
        break;
    case 2:
        break;
    default:
        return GAIN_ANALYSIS_ERROR;
    }

    if (num_samples < MAX_ORDER) {
        memcpy(rgData->linprebuf + MAX_ORDER, left_samples, num_samples * sizeof(Float_t));
        memcpy(rgData->rinprebuf + MAX_ORDER, right_samples, num_samples * sizeof(Float_t));
    }
    else {
        memcpy(rgData->linprebuf + MAX_ORDER, left_samples, MAX_ORDER * sizeof(Float_t));
        memcpy(rgData->rinprebuf + MAX_ORDER, right_samples, MAX_ORDER * sizeof(Float_t));
    }

    while (batchsamples > 0) {
        cursamples = batchsamples > rgData->sampleWindow - rgData->totsamp ?
            rgData->sampleWindow - rgData->totsamp : batchsamples;
        if (cursamplepos < MAX_ORDER) {
            curleft = rgData->linpre + cursamplepos;
            curright = rgData->rinpre + cursamplepos;
            if (cursamples > MAX_ORDER - cursamplepos)
                cursamples = MAX_ORDER - cursamplepos;
        }
        else {
            curleft = left_samples + cursamplepos;
            curright = right_samples + cursamplepos;
        }

        YULE_FILTER(curleft, rgData->lstep + rgData->totsamp, cursamples,
                    ABYule[rgData->freqindex]);
        YULE_FILTER(curright, rgData->rstep + rgData->totsamp, cursamples,
                    ABYule[rgData->freqindex]);

        BUTTER_FILTER(rgData->lstep + rgData->totsamp, rgData->lout + rgData->totsamp, cursamples,
                      ABButter[rgData->freqindex]);
        BUTTER_FILTER(rgData->rstep + rgData->totsamp, rgData->rout + rgData->totsamp, cursamples,
                      ABButter[rgData->freqindex]);

        curleft = rgData->lout + rgData->totsamp; /* Get the squared values */
        curright = rgData->rout + rgData->totsamp;

        sum_l = 0;
        sum_r = 0;
        i = cursamples & 0x03;
        while (i--) {
            Float_t const l = *curleft++;
            Float_t const r = *curright++;
            sum_l += l * l;
            sum_r += r * r;
        }
        i = cursamples / 4;
        while (i--) {
            Float_t l0 = curleft[0] * curleft[0];
            Float_t l1 = curleft[1] * curleft[1];
            Float_t l2 = curleft[2] * curleft[2];
            Float_t l3 = curleft[3] * curleft[3];
            Float_t sl = l0 + l1 + l2 + l3;
            Float_t r0 = curright[0] * curright[0];
            Float_t r1 = curright[1] * curright[1];
            Float_t r2 = curright[2] * curright[2];
            Float_t r3 = curright[3] * curright[3];
            Float_t sr = r0 + r1 + r2 + r3;
            sum_l += sl;
            curleft += 4;
            sum_r += sr;
            curright += 4;
        }
        rgData->lsum += sum_l;
        rgData->rsum += sum_r;

        batchsamples -= cursamples;
        cursamplepos += cursamples;
        rgData->totsamp += cursamples;
        if (rgData->totsamp == rgData->sampleWindow) { /* Get the Root Mean Square (RMS) for this set of samples */
            double const val =
                STEPS_per_dB * 10. * log10((rgData->lsum + rgData->rsum) / rgData->totsamp * 0.5 +
                                           1.e-37);
            size_t  ival = (val <= 0) ? 0 : (size_t) val;
            if (ival >= sizeof(rgData->A) / sizeof(*(rgData->A)))
                ival = sizeof(rgData->A) / sizeof(*(rgData->A)) - 1;
            rgData->A[ival]++;
            rgData->lsum = rgData->rsum = 0.;
            memmove(rgData->loutbuf, rgData->loutbuf + rgData->totsamp,
                    MAX_ORDER * sizeof(Float_t));
            memmove(rgData->routbuf, rgData->routbuf + rgData->totsamp,
                    MAX_ORDER * sizeof(Float_t));
            memmove(rgData->lstepbuf, rgData->lstepbuf + rgData->totsamp,
                    MAX_ORDER * sizeof(Float_t));
            memmove(rgData->rstepbuf, rgData->rstepbuf + rgData->totsamp,
                    MAX_ORDER * sizeof(Float_t));
            rgData->totsamp = 0;
        }
        if (rgData->totsamp > rgData->sampleWindow) /* somehow I really screwed up: Error in programming! Contact author about totsamp > sampleWindow */
            return GAIN_ANALYSIS_ERROR;
    }
    if (num_samples < MAX_ORDER) {
        memmove(rgData->linprebuf, rgData->linprebuf + num_samples,
                (MAX_ORDER - num_samples) * sizeof(Float_t));
        memmove(rgData->rinprebuf, rgData->rinprebuf + num_samples,
                (MAX_ORDER - num_samples) * sizeof(Float_t));
        memcpy(rgData->linprebuf + MAX_ORDER - num_samples, left_samples,
               num_samples * sizeof(Float_t));
        memcpy(rgData->rinprebuf + MAX_ORDER - num_samples, right_samples,
               num_samples * sizeof(Float_t));
    }
    else {
        memcpy(rgData->linprebuf, left_samples + num_samples - MAX_ORDER,
               MAX_ORDER * sizeof(Float_t));
        memcpy(rgData->rinprebuf, right_samples + num_samples - MAX_ORDER,
               MAX_ORDER * sizeof(Float_t));
    }

    return GAIN_ANALYSIS_OK;
}


static  Float_t
analyzeResult(uint32_t const *Array, size_t len)
{
    uint32_t elems;
    uint32_t upper;
    uint32_t sum;
    size_t  i;

    elems = 0;
    for (i = 0; i < len; i++)
        elems += Array[i];
    if (elems == 0)
        return GAIN_NOT_ENOUGH_SAMPLES;

    upper = (uint32_t) ceil(elems * (1. - RMS_PERCENTILE));
    sum = 0;
    for (i = len; i-- > 0;) {
        sum += Array[i];
        if (sum >= upper) {
            break;
        }
    }

    return (Float_t) ((Float_t) PINK_REF - (Float_t) i / (Float_t) STEPS_per_dB);
}


Float_t
GetTitleGain(replaygain_t * rgData)
{
    Float_t retval;
    unsigned int i;

    retval = analyzeResult(rgData->A, sizeof(rgData->A) / sizeof(*(rgData->A)));

    for (i = 0; i < sizeof(rgData->A) / sizeof(*(rgData->A)); i++) {
        rgData->B[i] += rgData->A[i];
        rgData->A[i] = 0;
    }

    for (i = 0; i < MAX_ORDER; i++)
        rgData->linprebuf[i] = rgData->lstepbuf[i]
            = rgData->loutbuf[i]
            = rgData->rinprebuf[i]
            = rgData->rstepbuf[i]
            = rgData->routbuf[i] = 0.f;

    rgData->totsamp = 0;
    rgData->lsum = rgData->rsum = 0.;
    return retval;
}

#if 0
static Float_t GetAlbumGain(replaygain_t const* rgData);

Float_t
GetAlbumGain(replaygain_t const* rgData)
{
    return analyzeResult(rgData->B, sizeof(rgData->B) / sizeof(*(rgData->B)));
}
#endif

/* end of gain_analysis.c */
