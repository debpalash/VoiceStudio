/*
 *      bit reservoir source file
 *
 *      Copyright (c) 1999-2000 Mark Taylor
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

/* $Id: reservoir.c,v 1.45 2011/05/07 16:05:17 rbrito Exp $ */

#ifdef HAVE_CONFIG_H
# include <config.h>
#endif


#include "lame.h"
#include "machine.h"
#include "encoder.h"
#include "util.h"
#include "reservoir.h"

#include "bitstream.h"
#include "lame-analysis.h"
#include "lame_global_flags.h"


/*
  ResvFrameBegin:
  Called (repeatedly) at the beginning of a frame. Updates the maximum
  size of the reservoir, and checks to make sure main_data_begin
  was set properly by the formatter
*/

/*
 *  Background information:
 *
 *  This is the original text from the ISO standard. Because of
 *  sooo many bugs and irritations correcting comments are added
 *  in brackets []. A '^W' means you should remove the last word.
 *
 *  1) The following rule can be used to calculate the maximum
 *     number of bits used for one granule [^W frame]:
 *     At the highest possible bitrate of Layer III (320 kbps
 *     per stereo signal [^W^W^W], 48 kHz) the frames must be of
 *     [^W^W^W are designed to have] constant length, i.e.
 *     one buffer [^W^W the frame] length is:
 *
 *         320 kbps * 1152/48 kHz = 7680 bit = 960 byte
 *
 *     This value is used as the maximum buffer per channel [^W^W] at
 *     lower bitrates [than 320 kbps]. At 64 kbps mono or 128 kbps
 *     stereo the main granule length is 64 kbps * 576/48 kHz = 768 bit
 *     [per granule and channel] at 48 kHz sampling frequency.
 *     This means that there is a maximum deviation (short time buffer
 *     [= reservoir]) of 7680 - 2*2*768 = 4608 bits is allowed at 64 kbps.
 *     The actual deviation is equal to the number of bytes [with the
 *     meaning of octets] denoted by the main_data_end offset pointer.
 *     The actual maximum deviation is (2^9-1)*8 bit = 4088 bits
 *     [for MPEG-1 and (2^8-1)*8 bit for MPEG-2, both are hard limits].
 *     ... The xchange of buffer bits between the left and right channel
 *     is allowed without restrictions [exception: dual channel].
 *     Because of the [constructed] constraint on the buffer size
 *     main_data_end is always set to 0 in the case of bit_rate_index==14,
 *     i.e. data rate 320 kbps per stereo signal [^W^W^W]. In this case
 *     all data are allocated between adjacent header [^W sync] words
 *     [, i.e. there is no buffering at all].
 */

int
ResvFrameBegin(lame_internal_flags * gfc, int *mean_bits)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    EncStateVar_t *const esv = &gfc->sv_enc;
    int     fullFrameBits;
    int     resvLimit;
    int     maxmp3buf;
    III_side_info_t *const l3_side = &gfc->l3_side;
    int     frameLength;
    int     meanBits;

    frameLength = getframebits(gfc);
    meanBits = (frameLength - cfg->sideinfo_len * 8) / cfg->mode_gr;

/*
 *  Meaning of the variables:
 *      resvLimit: (0, 8, ..., 8*255 (MPEG-2), 8*511 (MPEG-1))
 *          Number of bits can be stored in previous frame(s) due to
 *          counter size constaints
 *      maxmp3buf: ( ??? ... 8*1951 (MPEG-1 and 2), 8*2047 (MPEG-2.5))
 *          Number of bits allowed to encode one frame (you can take 8*511 bit
 *          from the bit reservoir and at most 8*1440 bit from the current
 *          frame (320 kbps, 32 kHz), so 8*1951 bit is the largest possible
 *          value for MPEG-1 and -2)
 *
 *          maximum allowed granule/channel size times 4 = 8*2047 bits.,
 *          so this is the absolute maximum supported by the format.
 *
 *
 *      fullFrameBits:  maximum number of bits available for encoding
 *                      the current frame.
 *
 *      mean_bits:      target number of bits per granule.
 *
 *      frameLength:
 *
 *      gfc->ResvMax:   maximum allowed reservoir
 *
 *      gfc->ResvSize:  current reservoir size
 *
 *      l3_side->resvDrain_pre:
 *         ancillary data to be added to previous frame:
 *         (only usefull in VBR modes if it is possible to have
 *         maxmp3buf < fullFrameBits)).  Currently disabled,
 *         see #define NEW_DRAIN
 *         2010-02-13: RH now enabled, it seems to be needed for CBR too,
 *                     as there exists one example, where the FhG decoder
 *                     can't decode a -b320 CBR file anymore.
 *
 *      l3_side->resvDrain_post:
 *         ancillary data to be added to this frame:
 *
 */

    /* main_data_begin has 9 bits in MPEG-1, 8 bits MPEG-2 */
    resvLimit = (8 * 256) * cfg->mode_gr - 8;

    /* maximum allowed frame size.  dont use more than this number of
       bits, even if the frame has the space for them: */
    maxmp3buf = cfg->buffer_constraint;
    esv->ResvMax = maxmp3buf - frameLength;
    if (esv->ResvMax > resvLimit)
        esv->ResvMax = resvLimit;
    if (esv->ResvMax < 0 || cfg->disable_reservoir)
        esv->ResvMax = 0;
    
    fullFrameBits = meanBits * cfg->mode_gr + Min(esv->ResvSize, esv->ResvMax);

    if (fullFrameBits > maxmp3buf)
        fullFrameBits = maxmp3buf;

    assert(0 == esv->ResvMax % 8);
    assert(esv->ResvMax >= 0);

    l3_side->resvDrain_pre = 0;

    if (gfc->pinfo != NULL) {
        gfc->pinfo->mean_bits = meanBits / 2; /* expected bits per channel per granule [is this also right for mono/stereo, MPEG-1/2 ?] */
        gfc->pinfo->resvsize = esv->ResvSize;
    }
    *mean_bits = meanBits;
    return fullFrameBits;
}


/*
  ResvMaxBits
  returns targ_bits:  target number of bits to use for 1 granule
         extra_bits:  amount extra available from reservoir
  Mark Taylor 4/99
*/
void
ResvMaxBits(lame_internal_flags * gfc, int mean_bits, int *targ_bits, int *extra_bits, int cbr)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    EncStateVar_t *const esv = &gfc->sv_enc;
    int     add_bits, targBits, extraBits;
    int     ResvSize = esv->ResvSize, ResvMax = esv->ResvMax;

    /* conpensate the saved bits used in the 1st granule */
    if (cbr)
        ResvSize += mean_bits;

    if (gfc->sv_qnt.substep_shaping & 1)
        ResvMax *= 0.9;

    targBits = mean_bits;

    /* extra bits if the reservoir is almost full */
    if (ResvSize * 10 > ResvMax * 9) {
        add_bits = ResvSize - (ResvMax * 9) / 10;
        targBits += add_bits;
        gfc->sv_qnt.substep_shaping |= 0x80;
    }
    else {
        add_bits = 0;
        gfc->sv_qnt.substep_shaping &= 0x7f;
        /* build up reservoir.  this builds the reservoir a little slower
         * than FhG.  It could simple be mean_bits/15, but this was rigged
         * to always produce 100 (the old value) at 128kbs */
        /*    *targ_bits -= (int) (mean_bits/15.2); */
        if (!cfg->disable_reservoir && !(gfc->sv_qnt.substep_shaping & 1))
            targBits -= .1 * mean_bits;
    }


    /* amount from the reservoir we are allowed to use. ISO says 6/10 */
    extraBits = (ResvSize < (esv->ResvMax * 6) / 10 ? ResvSize : (esv->ResvMax * 6) / 10);
    extraBits -= add_bits;

    if (extraBits < 0)
        extraBits = 0;

    *targ_bits = targBits;
    *extra_bits = extraBits;
}

/*
  ResvAdjust:
  Called after a granule's bit allocation. Readjusts the size of
  the reservoir to reflect the granule's usage.
*/
void
ResvAdjust(lame_internal_flags * gfc, gr_info const *gi)
{
    gfc->sv_enc.ResvSize -= gi->part2_3_length + gi->part2_length;
}


/*
  ResvFrameEnd:
  Called after all granules in a frame have been allocated. Makes sure
  that the reservoir size is within limits, possibly by adding stuffing
  bits.
*/
void
ResvFrameEnd(lame_internal_flags * gfc, int mean_bits)
{
    SessionConfig_t const *const cfg = &gfc->cfg;
    EncStateVar_t *const esv = &gfc->sv_enc;
    III_side_info_t *const l3_side = &gfc->l3_side;
    int     stuffingBits;
    int     over_bits;

    esv->ResvSize += mean_bits * cfg->mode_gr;
    stuffingBits = 0;
    l3_side->resvDrain_post = 0;
    l3_side->resvDrain_pre = 0;

    /* we must be byte aligned */
    if ((over_bits = esv->ResvSize % 8) != 0)
        stuffingBits += over_bits;


    over_bits = (esv->ResvSize - stuffingBits) - esv->ResvMax;
    if (over_bits > 0) {
        assert(0 == over_bits % 8);
        assert(over_bits >= 0);
        stuffingBits += over_bits;
    }


    /* NOTE: enabling the NEW_DRAIN code fixes some problems with FhG decoder
             shipped with MS Windows operating systems. Using this, it is even
             possible to use Gabriel's lax buffer consideration again, which
             assumes, any decoder should have a buffer large enough
             for a 320 kbps frame at 32 kHz sample rate.

       old drain code:
             lame -b320 BlackBird.wav ---> does not play with GraphEdit.exe using FhG decoder V1.5 Build 50

       new drain code:
             lame -b320 BlackBird.wav ---> plays fine with GraphEdit.exe using FhG decoder V1.5 Build 50

             Robert Hegemann, 2010-02-13.
     */
    /* drain as many bits as possible into previous frame ancillary data
     * In particular, in VBR mode ResvMax may have changed, and we have
     * to make sure main_data_begin does not create a reservoir bigger
     * than ResvMax  mt 4/00*/
    {
        int     mdb_bytes = Min(l3_side->main_data_begin * 8, stuffingBits) / 8;
        l3_side->resvDrain_pre += 8 * mdb_bytes;
        stuffingBits -= 8 * mdb_bytes;
        esv->ResvSize -= 8 * mdb_bytes;
        l3_side->main_data_begin -= mdb_bytes;
    }
    /* drain the rest into this frames ancillary data */
    l3_side->resvDrain_post += stuffingBits;
    esv->ResvSize -= stuffingBits;
}
