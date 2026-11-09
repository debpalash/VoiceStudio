/*
 * id3tag.c -- Write ID3 version 1 and 2 tags.
 *
 * Copyright (C) 2000 Don Melton
 * Copyright (C) 2011-2017 Robert Hegemann
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
 * License along with this library; if not, write to the Free Software
 * Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA 02111-1307, USA.
 */

/*
 * HISTORY: This source file is part of LAME (see http://www.mp3dev.org)
 * and was originally adapted by Conrad Sanderson <c.sanderson@me.gu.edu.au>
 * from mp3info by Ricardo Cerqueira <rmc@rccn.net> to write only ID3 version 1
 * tags.  Don Melton <don@blivet.com> COMPLETELY rewrote it to support version
 * 2 tags and be more conformant to other standards while remaining flexible.
 *
 * NOTE: See http://id3.org/ for more information about ID3 tag formats.
 */

/* $Id: id3tag.c,v 1.80 2017/08/28 15:39:51 robert Exp $ */

#ifdef HAVE_CONFIG_H
#include <config.h>
#endif

#ifdef STDC_HEADERS
# include <stddef.h>
# include <stdlib.h>
# include <string.h>
# include <ctype.h>
#else
# ifndef HAVE_STRCHR
#  define strchr index
#  define strrchr rindex
# endif
char   *strchr(), *strrchr();
# ifndef HAVE_MEMCPY
#  define memcpy(d, s, n) bcopy ((s), (d), (n))
# endif
#endif


#include "lame.h"
#include "machine.h"
#include "encoder.h"
#include "id3tag.h"
#include "lame_global_flags.h"
#include "util.h"
#include "bitstream.h"


static const char *const genre_names[] = {
    /*
     * NOTE: The spelling of these genre names is identical to those found in
     * Winamp and mp3info.
     */
    "Blues", "Classic Rock", "Country", "Dance", "Disco", "Funk", "Grunge",
    "Hip-Hop", "Jazz", "Metal", "New Age", "Oldies", "Other", "Pop", "R&B",
    "Rap", "Reggae", "Rock", "Techno", "Industrial", "Alternative", "Ska",
    "Death Metal", "Pranks", "Soundtrack", "Euro-Techno", "Ambient", "Trip-Hop",
    "Vocal", "Jazz+Funk", "Fusion", "Trance", "Classical", "Instrumental",
    "Acid", "House", "Game", "Sound Clip", "Gospel", "Noise", "Alternative Rock",
    "Bass", "Soul", "Punk", "Space", "Meditative", "Instrumental Pop",
    "Instrumental Rock", "Ethnic", "Gothic", "Darkwave", "Techno-Industrial",
    "Electronic", "Pop-Folk", "Eurodance", "Dream", "Southern Rock", "Comedy",
    "Cult", "Gangsta", "Top 40", "Christian Rap", "Pop/Funk", "Jungle",
    "Native US", "Cabaret", "New Wave", "Psychedelic", "Rave",
    "Showtunes", "Trailer", "Lo-Fi", "Tribal", "Acid Punk", "Acid Jazz",
    "Polka", "Retro", "Musical", "Rock & Roll", "Hard Rock", "Folk",
    "Folk-Rock", "National Folk", "Swing", "Fast Fusion", "Bebob", "Latin",
    "Revival", "Celtic", "Bluegrass", "Avantgarde", "Gothic Rock",
    "Progressive Rock", "Psychedelic Rock", "Symphonic Rock", "Slow Rock",
    "Big Band", "Chorus", "Easy Listening", "Acoustic", "Humour", "Speech",
    "Chanson", "Opera", "Chamber Music", "Sonata", "Symphony", "Booty Bass",
    "Primus", "Porn Groove", "Satire", "Slow Jam", "Club", "Tango", "Samba",
    "Folklore", "Ballad", "Power Ballad", "Rhythmic Soul", "Freestyle", "Duet",
    "Punk Rock", "Drum Solo", "A Cappella", "Euro-House", "Dance Hall",
    "Goa", "Drum & Bass", "Club-House", "Hardcore", "Terror", "Indie",
    "BritPop", "Negerpunk", "Polsk Punk", "Beat", "Christian Gangsta",
    "Heavy Metal", "Black Metal", "Crossover", "Contemporary Christian",
    "Christian Rock", "Merengue", "Salsa", "Thrash Metal", "Anime", "JPop",
    "SynthPop"
};

#define GENRE_NAME_COUNT \
    ((int)(sizeof genre_names / sizeof (const char *const)))

static const int genre_alpha_map[] = {
    123, 34, 74, 73, 99, 20, 40, 26, 145, 90, 116, 41, 135, 85, 96, 138, 89, 0,
    107, 132, 65, 88, 104, 102, 97, 136, 61, 141, 32, 1, 112, 128, 57, 140, 2,
    139, 58, 3, 125, 50, 22, 4, 55, 127, 122, 120, 98, 52, 48, 54, 124, 25, 84,
    80, 115, 81, 119, 5, 30, 36, 59, 126, 38, 49, 91, 6, 129, 79, 137, 7, 35,
    100, 131, 19, 33, 46, 47, 8, 29, 146, 63, 86, 71, 45, 142, 9, 77, 82, 64,
    133, 10, 66, 39, 11, 103, 12, 75, 134, 13, 53, 62, 109, 117, 23, 108, 92,
    67, 93, 43, 121, 15, 68, 14, 16, 76, 87, 118, 17, 78, 143, 114, 110, 69, 21,
    111, 95, 105, 42, 37, 24, 56, 44, 101, 83, 94, 106, 147, 113, 18, 51, 130,
    144, 60, 70, 31, 72, 27, 28
};

#define GENRE_ALPHA_COUNT ((int)(sizeof genre_alpha_map / sizeof (int)))

#define GENRE_INDEX_OTHER 12


#define FRAME_ID(a, b, c, d) \
    ( ((unsigned long)(a) << 24) \
    | ((unsigned long)(b) << 16) \
    | ((unsigned long)(c) <<  8) \
    | ((unsigned long)(d) <<  0) )

typedef enum UsualStringIDs { ID_TITLE = FRAME_ID('T', 'I', 'T', '2')
        , ID_ARTIST = FRAME_ID('T', 'P', 'E', '1')
        , ID_ALBUM = FRAME_ID('T', 'A', 'L', 'B')
        , ID_GENRE = FRAME_ID('T', 'C', 'O', 'N')
        , ID_ENCODER = FRAME_ID('T', 'S', 'S', 'E')
        , ID_PLAYLENGTH = FRAME_ID('T', 'L', 'E', 'N')
        , ID_COMMENT = FRAME_ID('C', 'O', 'M', 'M') /* full text string */
} UsualStringIDs;

typedef enum NumericStringIDs { ID_DATE = FRAME_ID('T', 'D', 'A', 'T') /* "ddMM" */
        , ID_TIME = FRAME_ID('T', 'I', 'M', 'E') /* "hhmm" */
        , ID_TPOS = FRAME_ID('T', 'P', 'O', 'S') /* '0'-'9' and '/' allowed */
        , ID_TRACK = FRAME_ID('T', 'R', 'C', 'K') /* '0'-'9' and '/' allowed */
        , ID_YEAR = FRAME_ID('T', 'Y', 'E', 'R') /* "yyyy" */
} NumericStringIDs;

typedef enum MiscIDs { ID_TXXX = FRAME_ID('T', 'X', 'X', 'X')
        , ID_WXXX = FRAME_ID('W', 'X', 'X', 'X')
        , ID_SYLT = FRAME_ID('S', 'Y', 'L', 'T')
        , ID_APIC = FRAME_ID('A', 'P', 'I', 'C')
        , ID_GEOB = FRAME_ID('G', 'E', 'O', 'B')
        , ID_PCNT = FRAME_ID('P', 'C', 'N', 'T')
        , ID_AENC = FRAME_ID('A', 'E', 'N', 'C')
        , ID_LINK = FRAME_ID('L', 'I', 'N', 'K')
        , ID_ENCR = FRAME_ID('E', 'N', 'C', 'R')
        , ID_GRID = FRAME_ID('G', 'R', 'I', 'D')
        , ID_PRIV = FRAME_ID('P', 'R', 'I', 'V')
        , ID_USLT = FRAME_ID('U', 'S', 'L', 'T') /* full text string */
        , ID_USER = FRAME_ID('U', 'S', 'E', 'R') /* full text string */
        , ID_PCST = FRAME_ID('P', 'C', 'S', 'T') /* iTunes Podcast indicator, only presence important */
        , ID_WFED = FRAME_ID('W', 'F', 'E', 'D') /* iTunes Podcast URL as TEXT FRAME !!! violates standard */
} MiscIDs;


static int
frame_id_matches(int id, int mask)
{
    int     result = 0, i, window = 0xff;
    for (i = 0; i < 4; ++i, window <<= 8) {
        int const mw = (mask & window);
        int const iw = (id & window);
        if (mw != 0 && mw != iw) {
            result |= iw;
        }
    }
    return result;
}

static int
isFrameIdMatching(int id, int mask)
{
    return frame_id_matches(id, mask) == 0 ? 1 : 0;
}

static int
test_tag_spec_flags(lame_internal_flags const *gfc, unsigned int tst)
{
    return (gfc->tag_spec.flags & tst) != 0u ? 1 : 0;
}

#if 0
static void
debug_tag_spec_flags(lame_internal_flags * gfc, const char* info)
{
    MSGF(gfc, "%s\n", info);
    MSGF(gfc, "CHANGED_FLAG  : %d\n", test_tag_spec_flags(gfc, CHANGED_FLAG )); 
    MSGF(gfc, "ADD_V2_FLAG   : %d\n", test_tag_spec_flags(gfc, ADD_V2_FLAG  )); 
    MSGF(gfc, "V1_ONLY_FLAG  : %d\n", test_tag_spec_flags(gfc, V1_ONLY_FLAG )); 
    MSGF(gfc, "V2_ONLY_FLAG  : %d\n", test_tag_spec_flags(gfc, V2_ONLY_FLAG )); 
    MSGF(gfc, "SPACE_V1_FLAG : %d\n", test_tag_spec_flags(gfc, SPACE_V1_FLAG)); 
    MSGF(gfc, "PAD_V2_FLAG   : %d\n", test_tag_spec_flags(gfc, PAD_V2_FLAG  )); 
}
#endif

static int
is_lame_internal_flags_null(lame_t gfp)
{
    return (gfp && gfp->internal_flags) ? 0 : 1;
}

static int
id3v2_add_ucs2_lng(lame_t gfp, uint32_t frame_id, unsigned short const *desc, unsigned short const *text);
static int
id3v2_add_latin1_lng(lame_t gfp, uint32_t frame_id, char const *desc, char const *text);


static void
copyV1ToV2(lame_t gfp, int frame_id, char const *s)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    if (gfc != 0) {
        unsigned int flags = gfc->tag_spec.flags;
        id3v2_add_latin1_lng(gfp, frame_id, 0, s);
        gfc->tag_spec.flags = flags;
#if 0
        debug_tag_spec_flags(gfc, "copyV1ToV2");
#endif
    }
}


static void
id3v2AddLameVersion(lame_t gfp)
{
    char    buffer[1024];
    const char *b = get_lame_os_bitness();
    const char *v = get_lame_version();
    const char *u = get_lame_url();
    const size_t lenb = strlen(b);

    if (lenb > 0) {
        sprintf(buffer, "LAME %s version %s (%s)", b, v, u);
    }
    else {
        sprintf(buffer, "LAME version %s (%s)", v, u);
    }
    copyV1ToV2(gfp, ID_ENCODER, buffer);
}

static void
id3v2AddAudioDuration(lame_t gfp, double ms)
{
    SessionConfig_t const *const cfg = &gfp->internal_flags->cfg; /* caller checked pointers */
    char    buffer[1024];
    double const max_ulong = MAX_U_32_NUM;
    unsigned long playlength_ms;

    ms *= 1000;
    ms /= cfg->samplerate_in;
    if (ms > max_ulong) {
        playlength_ms = max_ulong;
    }
    else if (ms < 0) {
        playlength_ms = 0;
    }
    else {
        playlength_ms = ms;
    }
    sprintf(buffer, "%lu", playlength_ms);
    copyV1ToV2(gfp, ID_PLAYLENGTH, buffer);
}

void
id3tag_genre_list(void (*handler) (int, const char *, void *), void *cookie)
{
    if (handler) {
        int     i;
        for (i = 0; i < GENRE_NAME_COUNT; ++i) {
            if (i < GENRE_ALPHA_COUNT) {
                int     j = genre_alpha_map[i];
                handler(j, genre_names[j], cookie);
            }
        }
    }
}

#define GENRE_NUM_UNKNOWN 255



void
id3tag_init(lame_t gfp)
{
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return;
    }
    gfc = gfp->internal_flags;
    free_id3tag(gfc);
    memset(&gfc->tag_spec, 0, sizeof gfc->tag_spec);
    gfc->tag_spec.genre_id3v1 = GENRE_NUM_UNKNOWN;
    gfc->tag_spec.padding_size = 128;
    id3v2AddLameVersion(gfp);
}



void
id3tag_add_v2(lame_t gfp)
{
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return;
    }
    gfc = gfp->internal_flags;
    gfc->tag_spec.flags &= ~V1_ONLY_FLAG;
    gfc->tag_spec.flags |= ADD_V2_FLAG;
}

void
id3tag_v1_only(lame_t gfp)
{
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return;
    }
    gfc = gfp->internal_flags;
    gfc->tag_spec.flags &= ~(ADD_V2_FLAG | V2_ONLY_FLAG);
    gfc->tag_spec.flags |= V1_ONLY_FLAG;
}

void
id3tag_v2_only(lame_t gfp)
{
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return;
    }
    gfc = gfp->internal_flags;
    gfc->tag_spec.flags &= ~V1_ONLY_FLAG;
    gfc->tag_spec.flags |= V2_ONLY_FLAG;
}

void
id3tag_space_v1(lame_t gfp)
{
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return;
    }
    gfc = gfp->internal_flags;
    gfc->tag_spec.flags &= ~V2_ONLY_FLAG;
    gfc->tag_spec.flags |= SPACE_V1_FLAG;
}

void
id3tag_pad_v2(lame_t gfp)
{
    id3tag_set_pad(gfp, 128);
}

void
id3tag_set_pad(lame_t gfp, size_t n)
{
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return;
    }
    gfc = gfp->internal_flags;
    gfc->tag_spec.flags &= ~V1_ONLY_FLAG;
    gfc->tag_spec.flags |= PAD_V2_FLAG;
    gfc->tag_spec.flags |= ADD_V2_FLAG;
    gfc->tag_spec.padding_size = (unsigned int)n;
}

static int
hasUcs2ByteOrderMarker(unsigned short bom)
{
    if (bom == 0xFFFEu || bom == 0xFEFFu) {
        return 1;
    }
    return 0;
}


static unsigned short
swap_bytes(unsigned short w)
{
    return (0xff00u & (w << 8)) | (0x00ffu & (w >> 8));
}


static unsigned short
toLittleEndian(unsigned short bom, unsigned short c)
{
    if (bom == 0xFFFEu) {
        return swap_bytes(c);
    }
    return c;
}

static unsigned short
fromLatin1Char(const unsigned short* s, unsigned short c)
{
    if (s[0] == 0xFFFEu) {
        return swap_bytes(c);
    }
    return c;
}


static  size_t
local_strdup(char **dst, const char *src)
{
    if (dst == 0) {
        return 0;
    }
    free(*dst);
    *dst = 0;
    if (src != 0) {
        size_t  n;
        for (n = 0; src[n] != 0; ++n) { /* calc src string length */
        }
        if (n > 0) {    /* string length without zero termination */
            assert(sizeof(*src) == sizeof(**dst));
            *dst = lame_calloc(char, n + 1);
            if (*dst != 0) {
                memcpy(*dst, src, n * sizeof(**dst));
                (*dst)[n] = 0;
                return n;
            }
        }
    }
    return 0;
}

static  size_t
local_ucs2_strdup(unsigned short **dst, unsigned short const *src)
{
    if (dst == 0) {
        return 0;
    }
    free(*dst);         /* free old string pointer */
    *dst = 0;
    if (src != 0) {
        size_t  n;
        for (n = 0; src[n] != 0; ++n) { /* calc src string length */
        }
        if (n > 0) {    /* string length without zero termination */
            assert(sizeof(*src) >= 2);
            assert(sizeof(*src) == sizeof(**dst));
            *dst = lame_calloc(unsigned short, n + 1);
            if (*dst != 0) {
                memcpy(*dst, src, n * sizeof(**dst));
                (*dst)[n] = 0;
                return n;
            }
        }
    }
    return 0;
}


static  size_t
local_ucs2_strlen(unsigned short const *s)
{
    size_t  n = 0;
    if (s != 0) {
        while (*s++) {
            ++n;
        }
    }
    return n;
}


static size_t
local_ucs2_substr(unsigned short** dst, unsigned short const* src, size_t start, size_t end)
{
    size_t const len = 1 + 1 + ((start < end) ? (end - start) : 0);
    size_t n = 0;
    unsigned short *ptr = lame_calloc(unsigned short, len);
    *dst = ptr;
    if (ptr == 0 || src == 0) {
        return 0;
    }
    if (hasUcs2ByteOrderMarker(src[0])) {
        ptr[n++] = src[0];
        if (start == 0) {
            ++start;
        }
    }
    while (start < end) {
        ptr[n++] = src[start++];
    }
    ptr[n] = 0;
    return n;
}

static int
local_ucs2_pos(unsigned short const* str, unsigned short c)
{
    int     i;
    for (i = 0; str != 0 && str[i] != 0; ++i) {
        if (str[i] == c) {
            return i;
        }
    }
    return -1;
}

static int
local_char_pos(char const* str, char c)
{
    int     i;
    for (i = 0; str != 0 && str[i] != 0; ++i) {
        if (str[i] == c) {
            return i;
        }
    }
    return -1;
}

static int
maybeLatin1(unsigned short const* text)
{
    if (text) {
        unsigned short bom = *text++;
        while (*text) {
            unsigned short c = toLittleEndian(bom, *text++);
            if (c > 0x00fe) return 0;
        }
    }
    return 1;
}

static int searchGenre(char const* genre);
static int sloppySearchGenre(char const* genre);

static int
lookupGenre(char const* genre)
{
    char   *str;
    int     num = strtol(genre, &str, 10);
    /* is the input a string or a valid number? */
    if (*str) {
        num = searchGenre(genre);
        if (num == GENRE_NAME_COUNT) {
            num = sloppySearchGenre(genre);
        }
        if (num == GENRE_NAME_COUNT) {
            return -2; /* no common genre text found */
        }
    }
    else {
        if ((num < 0) || (num >= GENRE_NAME_COUNT)) {
            return -1; /* number unknown */
        }
    }
    return num;
}

static unsigned char *
writeLoBytes(unsigned char *frame, unsigned short const *str, size_t n);

static char*
local_strdup_utf16_to_latin1(unsigned short const* utf16)
{
    size_t  len = local_ucs2_strlen(utf16);
    unsigned char* latin1 = lame_calloc(unsigned char, len+1);
    writeLoBytes(latin1, utf16, len);
    return (char*)latin1;
}


static int
id3tag_set_genre_utf16(lame_t gfp, unsigned short const* text)
{
    lame_internal_flags* gfc = gfp->internal_flags;
    int   ret;
    if (text == 0) {
        return -3;
    }
    if (!hasUcs2ByteOrderMarker(text[0])) {
        return -3;
    }
    if (maybeLatin1(text)) {
        char*   latin1 = local_strdup_utf16_to_latin1(text);
        int     num = lookupGenre(latin1);
        free(latin1);
        if (num == -1) return -1; /* number out of range */
        if (num >= 0) {           /* common genre found  */
            gfc->tag_spec.flags |= CHANGED_FLAG;
            gfc->tag_spec.genre_id3v1 = num;
            copyV1ToV2(gfp, ID_GENRE, genre_names[num]);
            return 0;
        }
    }
    ret = id3v2_add_ucs2_lng(gfp, ID_GENRE, 0, text);
    if (ret == 0) {
        gfc->tag_spec.flags |= CHANGED_FLAG;
        gfc->tag_spec.genre_id3v1 = GENRE_INDEX_OTHER;
    }
    return ret;
}

/*
Some existing options for ID3 tag can be specified by --tv option
as follows.
--tt <value>, --tv TIT2=value
--ta <value>, --tv TPE1=value
--tl <value>, --tv TALB=value
--ty <value>, --tv TYER=value
--tn <value>, --tv TRCK=value
--tg <value>, --tv TCON=value
(although some are not exactly same)*/

int
id3tag_set_albumart(lame_t gfp, const char *image, size_t size)
{
    int     mimetype = MIMETYPE_NONE;
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    gfc = gfp->internal_flags;

    if (image != 0) {
        unsigned char const *data = (unsigned char const *) image;
        /* determine MIME type from the actual image data */
        if (2 < size && data[0] == 0xFF && data[1] == 0xD8) {
            mimetype = MIMETYPE_JPEG;
        }
        else if (4 < size && data[0] == 0x89 && strncmp((const char *) &data[1], "PNG", 3) == 0) {
            mimetype = MIMETYPE_PNG;
        }
        else if (4 < size && strncmp((const char *) data, "GIF8", 4) == 0) {
            mimetype = MIMETYPE_GIF;
        }
        else {
            return -1;
        }
    }
    if (gfc->tag_spec.albumart != 0) {
        free(gfc->tag_spec.albumart);
        gfc->tag_spec.albumart = 0;
        gfc->tag_spec.albumart_size = 0;
        gfc->tag_spec.albumart_mimetype = MIMETYPE_NONE;
    }
    if (size < 1 || mimetype == MIMETYPE_NONE) {
        return 0;
    }
    gfc->tag_spec.albumart = lame_calloc(unsigned char, size);
    if (gfc->tag_spec.albumart != 0) {
        memcpy(gfc->tag_spec.albumart, image, size);
        gfc->tag_spec.albumart_size = (unsigned int)size;
        gfc->tag_spec.albumart_mimetype = mimetype;
        gfc->tag_spec.flags |= CHANGED_FLAG;
        id3tag_add_v2(gfp);
    }
    return 0;
}

static unsigned char *
set_4_byte_value(unsigned char *bytes, uint32_t value)
{
    int     i;
    for (i = 3; i >= 0; --i) {
        bytes[i] = value & 0xffUL;
        value >>= 8;
    }
    return bytes + 4;
}

static uint32_t
toID3v2TagId(char const *s)
{
    unsigned int i, x = 0;
    if (s == 0) {
        return 0;
    }
    for (i = 0; i < 4 && s[i] != 0; ++i) {
        char const c = s[i];
        unsigned int const u = 0x0ff & c;
        x <<= 8;
        x |= u;
        if (c < 'A' || 'Z' < c) {
            if (c < '0' || '9' < c) {
                return 0;
            }
        }
    }
    return x;
}

static uint32_t
toID3v2TagId_ucs2(unsigned short const *s)
{
    unsigned int i, x = 0;
    unsigned short bom = 0;
    if (s == 0) {
        return 0;
    }
    bom = s[0];
    if (hasUcs2ByteOrderMarker(bom)) {
        ++s;
    }
    for (i = 0; i < 4 && s[i] != 0; ++i) {
        unsigned short const c = toLittleEndian(bom, s[i]);
        if (c < 'A' || 'Z' < c) {
            if (c < '0' || '9' < c) {
                return 0;
            }
        }
        x <<= 8;
        x |= c;
    }
    return x;
}

#if 0
static int
isNumericString(uint32_t frame_id)
{
    switch (frame_id) {
    case ID_DATE:
    case ID_TIME:
    case ID_TPOS:
    case ID_TRACK:
    case ID_YEAR:
        return 1;
    }
    return 0;
}
#endif

static int
isMultiFrame(uint32_t frame_id)
{
    switch (frame_id) {
    case ID_TXXX:
    case ID_WXXX:
    case ID_COMMENT:
    case ID_SYLT:
    case ID_APIC:
    case ID_GEOB:
    case ID_PCNT:
    case ID_AENC:
    case ID_LINK:
    case ID_ENCR:
    case ID_GRID:
    case ID_PRIV:
        return 1;
    }
    return 0;
}

#if 0
static int
isFullTextString(int frame_id)
{
    switch (frame_id) {
    case ID_VSLT:
    case ID_COMMENT:
        return 1;
    }
    return 0;
}
#endif

static FrameDataNode *
findNode(id3tag_spec const *tag, uint32_t frame_id, FrameDataNode const *last)
{
    FrameDataNode *node = last ? last->nxt : tag->v2_head;
    while (node != 0) {
        if (node->fid == frame_id) {
            return node;
        }
        node = node->nxt;
    }
    return 0;
}

static void
appendNode(id3tag_spec * tag, FrameDataNode * node)
{
    if (tag->v2_tail == 0 || tag->v2_head == 0) {
        tag->v2_head = node;
        tag->v2_tail = node;
    }
    else {
        tag->v2_tail->nxt = node;
        tag->v2_tail = node;
    }
}

static void
setLang(char *dst, char const *src)
{
    int     i;
    if (src == 0 || src[0] == 0) {
        dst[0] = 'e';
        dst[1] = 'n';
        dst[2] = 'g';
    }
    else {
        for (i = 0; i < 3 && src && *src; ++i) {
            dst[i] = src[i];
        }
        for (; i < 3; ++i) {
            dst[i] = ' ';
        }
    }
}

static int
isSameLang(char const *l1, char const *l2)
{
    char    d[3];
    int     i;
    setLang(d, l2);
    for (i = 0; i < 3; ++i) {
        char    a = tolower(l1[i]);
        char    b = tolower(d[i]);
        if (a < ' ')
            a = ' ';
        if (b < ' ')
            b = ' ';
        if (a != b) {
            return 0;
        }
    }
    return 1;
}

static int
isSameDescriptor(FrameDataNode const *node, char const *dsc)
{
    size_t  i;
    if (node->dsc.enc == 1 && node->dsc.dim > 0) {
        return 0;
    }
    for (i = 0; i < node->dsc.dim; ++i) {
        if (!dsc || node->dsc.ptr.l[i] != dsc[i]) {
            return 0;
        }
    }
    return 1;
}

static int
isSameDescriptorUcs2(FrameDataNode const *node, unsigned short const *dsc)
{
    size_t  i;
    if (node->dsc.enc != 1 && node->dsc.dim > 0) {
        return 0;
    }
    for (i = 0; i < node->dsc.dim; ++i) {
        if (!dsc || node->dsc.ptr.u[i] != dsc[i]) {
            return 0;
        }
    }
    return 1;
}

static int
id3v2_add_ucs2(lame_t gfp, uint32_t frame_id, char const *lng, unsigned short const *desc, unsigned short const *text)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    if (gfc != 0) {
        FrameDataNode *node = findNode(&gfc->tag_spec, frame_id, 0);
        char lang[4];
        setLang(lang, lng);
        if (isMultiFrame(frame_id)) {
            while (node) {
                if (isSameLang(node->lng, lang)) {
                    if (isSameDescriptorUcs2(node, desc)) {
                        break;
                    }
                }
                node = findNode(&gfc->tag_spec, frame_id, node);
            }
        }
        if (node == 0) {
            node = lame_calloc(FrameDataNode, 1);
            if (node == 0) {
                return -254; /* memory problem */
            }
            appendNode(&gfc->tag_spec, node);
        }
        node->fid = frame_id;
        setLang(node->lng, lang);
        node->dsc.dim = local_ucs2_strdup(&node->dsc.ptr.u, desc);
        node->dsc.enc = 1;
        node->txt.dim = local_ucs2_strdup(&node->txt.ptr.u, text);
        node->txt.enc = 1;
        gfc->tag_spec.flags |= (CHANGED_FLAG | ADD_V2_FLAG);
        return 0;
    }
    return -255;
}

static int
id3v2_add_latin1(lame_t gfp, uint32_t frame_id, char const *lng, char const *desc, char const *text)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    if (gfc != 0) {
        FrameDataNode *node = findNode(&gfc->tag_spec, frame_id, 0);
        char lang[4];
        setLang(lang, lng);
        if (isMultiFrame(frame_id)) {
            while (node) {
                if (isSameLang(node->lng, lang)) {
                    if (isSameDescriptor(node, desc)) {
                        break;
                    }
                }
                node = findNode(&gfc->tag_spec, frame_id, node);
            }
        }
        if (node == 0) {
            node = lame_calloc(FrameDataNode, 1);
            if (node == 0) {
                return -254; /* memory problem */
            }
            appendNode(&gfc->tag_spec, node);
        }
        node->fid = frame_id;
        setLang(node->lng, lang);
        node->dsc.dim = local_strdup(&node->dsc.ptr.l, desc);
        node->dsc.enc = 0;
        node->txt.dim = local_strdup(&node->txt.ptr.l, text);
        node->txt.enc = 0;
        gfc->tag_spec.flags |= (CHANGED_FLAG | ADD_V2_FLAG);
        return 0;
    }
    return -255;
}

static char const*
id3v2_get_language(lame_t gfp)
{
    lame_internal_flags const* gfc = gfp ? gfp->internal_flags : 0;
    if (gfc) return gfc->tag_spec.language;
    return 0;
}

static int
id3v2_add_ucs2_lng(lame_t gfp, uint32_t frame_id, unsigned short const *desc, unsigned short const *text)
{
    char const* lang = id3v2_get_language(gfp);
    return id3v2_add_ucs2(gfp, frame_id, lang, desc, text);
}

static int
id3v2_add_latin1_lng(lame_t gfp, uint32_t frame_id, char const *desc, char const *text)
{
    char const* lang = id3v2_get_language(gfp);
    return id3v2_add_latin1(gfp, frame_id, lang, desc, text);
}

static int
id3tag_set_userinfo_latin1(lame_t gfp, uint32_t id, char const *fieldvalue)
{
    char const separator = '=';
    int     rc = -7;
    int     a = local_char_pos(fieldvalue, separator);
    if (a >= 0) {
        char*   dup = 0;
        local_strdup(&dup, fieldvalue);
        dup[a] = 0;
        rc = id3v2_add_latin1_lng(gfp, id, dup, dup+a+1);
        free(dup);
    }
    return rc;
}

static int
id3tag_set_userinfo_ucs2(lame_t gfp, uint32_t id, unsigned short const *fieldvalue)
{
    unsigned short const separator = fromLatin1Char(fieldvalue,'=');
    int     rc = -7;
    size_t  b = local_ucs2_strlen(fieldvalue);
    int     a = local_ucs2_pos(fieldvalue, separator);
    if (a >= 0) {
        unsigned short* dsc = 0, *val = 0;
        local_ucs2_substr(&dsc, fieldvalue, 0, a);
        local_ucs2_substr(&val, fieldvalue, a+1, b);
        rc = id3v2_add_ucs2_lng(gfp, id, dsc, val);
        free(dsc);
        free(val);
    }
    return rc;
}

int
id3tag_set_textinfo_utf16(lame_t gfp, char const *id, unsigned short const *text)
{
    uint32_t const frame_id = toID3v2TagId(id);
    if (frame_id == 0) {
        return -1;
    }
    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    if (text == 0) {
        return 0;
    }
    if (!hasUcs2ByteOrderMarker(text[0])) {
        return -3;  /* BOM missing */
    }
    if (frame_id == ID_TXXX || frame_id == ID_WXXX || frame_id == ID_COMMENT) {
        return id3tag_set_userinfo_ucs2(gfp, frame_id, text);
    }
    if (frame_id == ID_GENRE) {
        return id3tag_set_genre_utf16(gfp, text);
    }
    if (frame_id == ID_PCST) {
        return id3v2_add_ucs2_lng(gfp, frame_id, 0, text);
    }
    if (frame_id == ID_USER) {
        return id3v2_add_ucs2_lng(gfp, frame_id, text, 0);
    }
    if (frame_id == ID_WFED) {
        return id3v2_add_ucs2_lng(gfp, frame_id, text, 0); /* iTunes expects WFED to be a text frame */
    }
    if (isFrameIdMatching(frame_id, FRAME_ID('T', 0, 0, 0))
      ||isFrameIdMatching(frame_id, FRAME_ID('W', 0, 0, 0))) {
#if 0
        if (isNumericString(frame_id)) {
            return -2;  /* must be Latin-1 encoded */
        }
#endif
        return id3v2_add_ucs2_lng(gfp, frame_id, 0, text);
    }
    return -255;        /* not supported by now */
}

extern int
id3tag_set_textinfo_ucs2(lame_t gfp, char const *id, unsigned short const *text);

int
id3tag_set_textinfo_ucs2(lame_t gfp, char const *id, unsigned short const *text)
{
    return id3tag_set_textinfo_utf16(gfp, id, text);
}

int
id3tag_set_textinfo_latin1(lame_t gfp, char const *id, char const *text)
{
    uint32_t const frame_id = toID3v2TagId(id);
    if (frame_id == 0) {
        return -1;
    }
    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    if (text == 0) {
        return 0;
    }
    if (frame_id == ID_TXXX || frame_id == ID_WXXX || frame_id == ID_COMMENT) {
        return id3tag_set_userinfo_latin1(gfp, frame_id, text);
    }
    if (frame_id == ID_GENRE) {
        return id3tag_set_genre(gfp, text);
    }
    if (frame_id == ID_PCST) {
        return id3v2_add_latin1_lng(gfp, frame_id, 0, text);
    }
    if (frame_id == ID_USER) {
        return id3v2_add_latin1_lng(gfp, frame_id, text, 0);
    }
    if (frame_id == ID_WFED) {
        return id3v2_add_latin1_lng(gfp, frame_id, text, 0); /* iTunes expects WFED to be a text frame */
    }
    if (isFrameIdMatching(frame_id, FRAME_ID('T', 0, 0, 0))
      ||isFrameIdMatching(frame_id, FRAME_ID('W', 0, 0, 0))) {
        return id3v2_add_latin1_lng(gfp, frame_id, 0, text);
    }
    return -255;        /* not supported by now */
}


int
id3tag_set_comment_latin1(lame_t gfp, char const *lang, char const *desc, char const *text)
{
    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    return id3v2_add_latin1(gfp, ID_COMMENT, lang, desc, text);
}


int
id3tag_set_comment_utf16(lame_t gfp, char const *lang, unsigned short const *desc, unsigned short const *text)
{
    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    return id3v2_add_ucs2(gfp, ID_COMMENT, lang, desc, text);
}

extern int
id3tag_set_comment_ucs2(lame_t gfp, char const *lang, unsigned short const *desc, unsigned short const *text);


int
id3tag_set_comment_ucs2(lame_t gfp, char const *lang, unsigned short const *desc, unsigned short const *text)
{
    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    return id3tag_set_comment_utf16(gfp, lang, desc, text);
}


void
id3tag_set_title(lame_t gfp, const char *title)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    if (gfc && title && *title) {
        local_strdup(&gfc->tag_spec.title, title);
        gfc->tag_spec.flags |= CHANGED_FLAG;
        copyV1ToV2(gfp, ID_TITLE, title);
    }
}

void
id3tag_set_artist(lame_t gfp, const char *artist)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    if (gfc && artist && *artist) {
        local_strdup(&gfc->tag_spec.artist, artist);
        gfc->tag_spec.flags |= CHANGED_FLAG;
        copyV1ToV2(gfp, ID_ARTIST, artist);
    }
}

void
id3tag_set_album(lame_t gfp, const char *album)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    if (gfc && album && *album) {
        local_strdup(&gfc->tag_spec.album, album);
        gfc->tag_spec.flags |= CHANGED_FLAG;
        copyV1ToV2(gfp, ID_ALBUM, album);
    }
}

void
id3tag_set_year(lame_t gfp, const char *year)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    if (gfc && year && *year) {
        int     num = atoi(year);
        if (num < 0) {
            num = 0;
        }
        /* limit a year to 4 digits so it fits in a version 1 tag */
        if (num > 9999) {
            num = 9999;
        }
        if (num) {
            gfc->tag_spec.year = num;
            gfc->tag_spec.flags |= CHANGED_FLAG;
        }
        copyV1ToV2(gfp, ID_YEAR, year);
    }
}

void
id3tag_set_comment(lame_t gfp, const char *comment)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    if (gfc && comment && *comment) {
        local_strdup(&gfc->tag_spec.comment, comment);
        gfc->tag_spec.flags |= CHANGED_FLAG;
        {
            uint32_t const flags = gfc->tag_spec.flags;
            id3v2_add_latin1_lng(gfp, ID_COMMENT, "", comment);
            gfc->tag_spec.flags = flags;
        }
    }
}

int
id3tag_set_track(lame_t gfp, const char *track)
{
    char const *trackcount;
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    int     ret = 0;

    if (gfc && track && *track) {
        int     num = atoi(track);
        /* check for valid ID3v1 track number range */
        if (num < 1 || num > 255) {
            num = 0;
            ret = -1;   /* track number out of ID3v1 range, ignored for ID3v1 */
            gfc->tag_spec.flags |= (CHANGED_FLAG | ADD_V2_FLAG);
        }
        if (num) {
            gfc->tag_spec.track_id3v1 = num;
            gfc->tag_spec.flags |= CHANGED_FLAG;
        }
        /* Look for the total track count after a "/", same restrictions */
        trackcount = strchr(track, '/');
        if (trackcount && *trackcount) {
            gfc->tag_spec.flags |= (CHANGED_FLAG | ADD_V2_FLAG);
        }
        copyV1ToV2(gfp, ID_TRACK, track);
    }
    return ret;
}

/* would use real "strcasecmp" but it isn't portable */
static int
local_strcasecmp(const char *s1, const char *s2)
{
    unsigned char c1;
    unsigned char c2;
    do {
        c1 = tolower(*s1);
        c2 = tolower(*s2);
        if (!c1) {
            break;
        }
        ++s1;
        ++s2;
    } while (c1 == c2);
    return c1 - c2;
}


static 
const char* nextUpperAlpha(const char* p, char x)
{
    char c;
    for(c = toupper(*p); *p != 0; c = toupper(*++p)) {
        if ('A' <= c && c <= 'Z') {
            if (c != x) {
                return p;
            }
        }
    }
    return p;
}


static int
sloppyCompared(const char* p, const char* q)
{
    char cp, cq;
    p = nextUpperAlpha(p, 0);
    q = nextUpperAlpha(q, 0);
    cp = toupper(*p);
    cq = toupper(*q);
    while (cp == cq) {
        if (cp == 0) {
            return 1;
        }
        if (p[1] == '.') { /* some abbrevation */
            while (*q && *q++ != ' ') {
            }
        }
        p = nextUpperAlpha(p, cp);
        q = nextUpperAlpha(q, cq);
        cp = toupper(*p);
        cq = toupper(*q);
    }
    return 0;
}


static int 
sloppySearchGenre(const char *genre)
{
    int i;
    for (i = 0; i < GENRE_NAME_COUNT; ++i) {
        if (sloppyCompared(genre, genre_names[i])) {
            return i;
        }
    }
    return GENRE_NAME_COUNT;
}


static int
searchGenre(const char* genre)
{
    int i;
    for (i = 0; i < GENRE_NAME_COUNT; ++i) {
        if (!local_strcasecmp(genre, genre_names[i])) {
            return i;
        }
    }
    return GENRE_NAME_COUNT;
}


int
id3tag_set_genre(lame_t gfp, const char *genre)
{
    lame_internal_flags *gfc = gfp != 0 ? gfp->internal_flags : 0;
    int     ret = 0;
    if (gfc && genre && *genre) {
        int const num = lookupGenre(genre);
        if (num == -1) return num;
        gfc->tag_spec.flags |= CHANGED_FLAG;
        if (num >= 0) {
            gfc->tag_spec.genre_id3v1 = num;
            genre = genre_names[num];
        }
        else {
            gfc->tag_spec.genre_id3v1 = GENRE_INDEX_OTHER;
            gfc->tag_spec.flags |= ADD_V2_FLAG;
        }
        copyV1ToV2(gfp, ID_GENRE, genre);
    }
    return ret;
}


static  size_t
sizeOfNode(FrameDataNode const *node)
{
    size_t  n = 0;
    if (node) {
        n = 10;         /* header size */
        n += 1;         /* text encoding flag */
        switch (node->txt.enc) {
        default:
        case 0:
            if (node->dsc.dim > 0) {
                n += node->dsc.dim + 1;
            }
            n += node->txt.dim;
            break;
        case 1:
            if (node->dsc.dim > 0) {
                n += (node->dsc.dim+1) * 2;
            }
            n += node->txt.dim * 2;
            break;
        }
    }
    return n;
}

static  size_t
sizeOfCommentNode(FrameDataNode const *node)
{
    size_t  n = 0;
    if (node) {
        n = 10;         /* header size */
        n += 1;         /* text encoding flag */
        n += 3;         /* language */
        switch (node->dsc.enc) {
        default:
        case 0:
            n += 1 + node->dsc.dim;
            break;
        case 1:
            n += 2 + node->dsc.dim * 2;
            break;
        }
        switch (node->txt.enc) {
        default:
        case 0:
            n += node->txt.dim;
            break;
        case 1:
            n += node->txt.dim * 2;
            break;
        }
    }
    return n;
}

static size_t
sizeOfWxxxNode(FrameDataNode const *node)
{
    size_t  n = 0;
    if (node) {
        n = 10;         /* header size */
        if (node->dsc.dim > 0) {
            n += 1;         /* text encoding flag */
            switch (node->dsc.enc) {
            default:
            case 0:
                n += 1 + node->dsc.dim;
                break;
            case 1:
                n += 2 + node->dsc.dim * 2;
                break;
            }
        }
        if (node->txt.dim > 0) {
            switch (node->txt.enc) {
            default:
            case 0:
                n += node->txt.dim;
                break;
            case 1:
                n += node->txt.dim - 1; /* UCS2 -> Latin1, skip BOM */
                break;
            }
        }
    }
    return n;
}

static unsigned char *
writeChars(unsigned char *frame, char const *str, size_t n)
{
    while (n--) {
        *frame++ = *str++;
    }
    return frame;
}

static unsigned char *
writeUcs2s(unsigned char *frame, unsigned short const *str, size_t n)
{
    if (n > 0) {
        unsigned short const bom = *str;
        while (n--) {
            unsigned short const c = toLittleEndian(bom, *str++);
            *frame++ = 0x00ffu & c;
            *frame++ = 0x00ffu & (c >> 8);
        }
    }
    return frame;
}

static unsigned char *
writeLoBytes(unsigned char *frame, unsigned short const *str, size_t n)
{
    if (n > 0) {
        unsigned short const bom = *str;
        if (hasUcs2ByteOrderMarker(bom)) {
            str++; n--; /* skip BOM */
        }
        while (n--) {
            unsigned short const c = toLittleEndian(bom, *str++);
            if (c < 0x0020u || 0x00ffu < c) {
                *frame++ = 0x0020; /* blank */
            }
            else {
                *frame++ = c;
            }
        }
    }
    return frame;
}

static unsigned char *
set_frame_comment(unsigned char *frame, FrameDataNode const *node)
{
    size_t const n = sizeOfCommentNode(node);
    if (n > 10) {
        frame = set_4_byte_value(frame, node->fid);
        frame = set_4_byte_value(frame, (uint32_t) (n - 10));
        /* clear 2-byte header flags */
        *frame++ = 0;
        *frame++ = 0;
        /* encoding descriptor byte */
        *frame++ = node->txt.enc == 1 ? 1 : 0;
        /* 3 bytes language */
        *frame++ = node->lng[0];
        *frame++ = node->lng[1];
        *frame++ = node->lng[2];
        /* descriptor with zero byte(s) separator */
        if (node->dsc.enc != 1) {
            frame = writeChars(frame, node->dsc.ptr.l, node->dsc.dim);
            *frame++ = 0;
        }
        else {
            frame = writeUcs2s(frame, node->dsc.ptr.u, node->dsc.dim);
            *frame++ = 0;
            *frame++ = 0;
        }
        /* comment full text */
        if (node->txt.enc != 1) {
            frame = writeChars(frame, node->txt.ptr.l, node->txt.dim);
        }
        else {
            frame = writeUcs2s(frame, node->txt.ptr.u, node->txt.dim);
        }
    }
    return frame;
}

static unsigned char *
set_frame_custom2(unsigned char *frame, FrameDataNode const *node)
{
    size_t const n = sizeOfNode(node);
    if (n > 10) {
        frame = set_4_byte_value(frame, node->fid);
        frame = set_4_byte_value(frame, (unsigned long) (n - 10));
        /* clear 2-byte header flags */
        *frame++ = 0;
        *frame++ = 0;
        /* clear 1 encoding descriptor byte to indicate ISO-8859-1 format */
        *frame++ = node->txt.enc == 1 ? 1 : 0;
        if (node->dsc.dim > 0) {
            if (node->dsc.enc != 1) {
                frame = writeChars(frame, node->dsc.ptr.l, node->dsc.dim);
                *frame++ = 0;
            }
            else {
                frame = writeUcs2s(frame, node->dsc.ptr.u, node->dsc.dim);
                *frame++ = 0;
                *frame++ = 0;
            }
        }
        if (node->txt.enc != 1) {
            frame = writeChars(frame, node->txt.ptr.l, node->txt.dim);
        }
        else {
            frame = writeUcs2s(frame, node->txt.ptr.u, node->txt.dim);
        }
    }
    return frame;
}

static unsigned char *
set_frame_wxxx(unsigned char *frame, FrameDataNode const *node)
{
    size_t const n = sizeOfWxxxNode(node);
    if (n > 10) {
        frame = set_4_byte_value(frame, node->fid);
        frame = set_4_byte_value(frame, (unsigned long) (n - 10));
        /* clear 2-byte header flags */
        *frame++ = 0;
        *frame++ = 0;
        if (node->dsc.dim > 0) {
            /* clear 1 encoding descriptor byte to indicate ISO-8859-1 format */
            *frame++ = node->dsc.enc == 1 ? 1 : 0;
            if (node->dsc.enc != 1) {
                frame = writeChars(frame, node->dsc.ptr.l, node->dsc.dim);
                *frame++ = 0;
            }
            else {
                frame = writeUcs2s(frame, node->dsc.ptr.u, node->dsc.dim);
                *frame++ = 0;
                *frame++ = 0;
            }
        }
        if (node->txt.enc != 1) {
            frame = writeChars(frame, node->txt.ptr.l, node->txt.dim);
        }
        else {
            frame = writeLoBytes(frame, node->txt.ptr.u, node->txt.dim);
        }
    }
    return frame;
}

static unsigned char *
set_frame_apic(unsigned char *frame, const char *mimetype, const unsigned char *data, size_t size)
{
    /* ID3v2.3 standard APIC frame:
     *     <Header for 'Attached picture', ID: "APIC">
     *     Text encoding    $xx
     *     MIME type        <text string> $00
     *     Picture type     $xx
     *     Description      <text string according to encoding> $00 (00)
     *     Picture data     <binary data>
     */
    if (mimetype && data && size) {
        frame = set_4_byte_value(frame, FRAME_ID('A', 'P', 'I', 'C'));
        frame = set_4_byte_value(frame, (unsigned long) (4 + strlen(mimetype) + size));
        /* clear 2-byte header flags */
        *frame++ = 0;
        *frame++ = 0;
        /* clear 1 encoding descriptor byte to indicate ISO-8859-1 format */
        *frame++ = 0;
        /* copy mime_type */
        while (*mimetype) {
            *frame++ = *mimetype++;
        }
        *frame++ = 0;
        /* set picture type to 0 */
        *frame++ = 0;
        /* empty description field */
        *frame++ = 0;
        /* copy the image data */
        while (size--) {
            *frame++ = *data++;
        }
    }
    return frame;
}

int
id3tag_set_fieldvalue(lame_t gfp, const char *fieldvalue)
{
    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    if (fieldvalue && *fieldvalue) {
        if (strlen(fieldvalue) < 5 || fieldvalue[4] != '=') {
            return -1;
        }
        return id3tag_set_textinfo_latin1(gfp, fieldvalue, &fieldvalue[5]);
    }
    return 0;
}

int
id3tag_set_fieldvalue_utf16(lame_t gfp, const unsigned short *fieldvalue)
{
    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    if (fieldvalue && *fieldvalue) {
        size_t dx = hasUcs2ByteOrderMarker(fieldvalue[0]);
        unsigned short const separator = fromLatin1Char(fieldvalue, '=');
        char fid[5] = {0,0,0,0,0};
        uint32_t const frame_id = toID3v2TagId_ucs2(fieldvalue);
        if (local_ucs2_strlen(fieldvalue) < (5+dx) || fieldvalue[4+dx] != separator) {
            return -1;
        }
        fid[0] = (frame_id >> 24) & 0x0ff;
        fid[1] = (frame_id >> 16) & 0x0ff;
        fid[2] = (frame_id >> 8) & 0x0ff;
        fid[3] = frame_id & 0x0ff;
        if (frame_id != 0) {
            unsigned short* txt = 0;
            int     rc;
            local_ucs2_substr(&txt, fieldvalue, dx+5, local_ucs2_strlen(fieldvalue));
            rc = id3tag_set_textinfo_utf16(gfp, fid, txt);
            free(txt);
            return rc;
        }
    }
    return -1;
}

extern int
id3tag_set_fieldvalue_ucs2(lame_t gfp, const unsigned short *fieldvalue);

int
id3tag_set_fieldvalue_ucs2(lame_t gfp, const unsigned short *fieldvalue)
{
    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    return id3tag_set_fieldvalue_utf16(gfp, fieldvalue);
}

size_t
lame_get_id3v2_tag(lame_t gfp, unsigned char *buffer, size_t size)
{
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    gfc = gfp->internal_flags;
    if (test_tag_spec_flags(gfc, V1_ONLY_FLAG)) {
        return 0;
    }
#if 0
    debug_tag_spec_flags(gfc, "lame_get_id3v2_tag");
#endif
    {
        int usev2 = test_tag_spec_flags(gfc, ADD_V2_FLAG | V2_ONLY_FLAG);
        /* calculate length of four fields which may not fit in verion 1 tag */
        size_t  title_length = gfc->tag_spec.title ? strlen(gfc->tag_spec.title) : 0;
        size_t  artist_length = gfc->tag_spec.artist ? strlen(gfc->tag_spec.artist) : 0;
        size_t  album_length = gfc->tag_spec.album ? strlen(gfc->tag_spec.album) : 0;
        size_t  comment_length = gfc->tag_spec.comment ? strlen(gfc->tag_spec.comment) : 0;
        /* write tag if explicitly requested or if fields overflow */
        if ((title_length > 30)
            || (artist_length > 30)
            || (album_length > 30)
            || (comment_length > 30)
            || (gfc->tag_spec.track_id3v1 && (comment_length > 28))) {
            usev2 = 1;
        }
        if (usev2) {
            size_t  tag_size;
            unsigned char *p;
            size_t  adjusted_tag_size;
            const char *albumart_mime = NULL;
            static const char *mime_jpeg = "image/jpeg";
            static const char *mime_png = "image/png";
            static const char *mime_gif = "image/gif";

            if (gfp->num_samples != MAX_U_32_NUM) {
                id3v2AddAudioDuration(gfp, gfp->num_samples);
            }

            /* calulate size of tag starting with 10-byte tag header */
            tag_size = 10;
            if (gfc->tag_spec.albumart && gfc->tag_spec.albumart_size) {
                switch (gfc->tag_spec.albumart_mimetype) {
                case MIMETYPE_JPEG:
                    albumart_mime = mime_jpeg;
                    break;
                case MIMETYPE_PNG:
                    albumart_mime = mime_png;
                    break;
                case MIMETYPE_GIF:
                    albumart_mime = mime_gif;
                    break;
                }
                if (albumart_mime) {
                    tag_size += 10 + 4 + strlen(albumart_mime) + gfc->tag_spec.albumart_size;
                }
            }
            {
                id3tag_spec *tag = &gfc->tag_spec;
                if (tag->v2_head != 0) {
                    FrameDataNode *node;
                    for (node = tag->v2_head; node != 0; node = node->nxt) {
                        if (node->fid == ID_COMMENT || node->fid == ID_USER) {
                            tag_size += sizeOfCommentNode(node);
                        }
                        else if (isFrameIdMatching(node->fid, FRAME_ID('W',0,0,0))) {
                            tag_size += sizeOfWxxxNode(node);
                        }
                        else {
                            tag_size += sizeOfNode(node);
                        }
                    }
                }
            }
            if (test_tag_spec_flags(gfc, PAD_V2_FLAG)) {
                /* add some bytes of padding */
                tag_size += gfc->tag_spec.padding_size;
            }
            if (size < tag_size) {
                return tag_size;
            }
            if (buffer == 0) {
                return 0;
            }
            p = buffer;
            /* set tag header starting with file identifier */
            *p++ = 'I';
            *p++ = 'D';
            *p++ = '3';
            /* set version number word */
            *p++ = 3;
            *p++ = 0;
            /* clear flags byte */
            *p++ = 0;
            /* calculate and set tag size = total size - header size */
            adjusted_tag_size = tag_size - 10;
            /* encode adjusted size into four bytes where most significant 
             * bit is clear in each byte, for 28-bit total */
            *p++ = (unsigned char) ((adjusted_tag_size >> 21) & 0x7fu);
            *p++ = (unsigned char) ((adjusted_tag_size >> 14) & 0x7fu);
            *p++ = (unsigned char) ((adjusted_tag_size >> 7) & 0x7fu);
            *p++ = (unsigned char) (adjusted_tag_size & 0x7fu);

            /*
             * NOTE: The remainder of the tag (frames and padding, if any)
             * are not "unsynchronized" to prevent false MPEG audio headers
             * from appearing in the bitstream.  Why?  Well, most players
             * and utilities know how to skip the ID3 version 2 tag by now
             * even if they don't read its contents, and it's actually
             * very unlikely that such a false "sync" pattern would occur
             * in just the simple text frames added here.
             */

            /* set each frame in tag */
            {
                id3tag_spec *tag = &gfc->tag_spec;
                if (tag->v2_head != 0) {
                    FrameDataNode *node;
                    for (node = tag->v2_head; node != 0; node = node->nxt) {
                        if (node->fid == ID_COMMENT || node->fid == ID_USER) {
                            p = set_frame_comment(p, node);
                        }
                        else if (isFrameIdMatching(node->fid,FRAME_ID('W',0,0,0))) {
                            p = set_frame_wxxx(p, node);
                        }
                        else {
                            p = set_frame_custom2(p, node);
                        }
                    }
                }
            }
            if (albumart_mime) {
                p = set_frame_apic(p, albumart_mime, gfc->tag_spec.albumart,
                                   gfc->tag_spec.albumart_size);
            }
            /* clear any padding bytes */
            memset(p, 0, tag_size - (p - buffer));
            return tag_size;
        }
    }
    return 0;
}

int
id3tag_write_v2(lame_t gfp)
{
    lame_internal_flags *gfc = 0;

    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    gfc = gfp->internal_flags;
#if 0
    debug_tag_spec_flags(gfc, "write v2");
#endif
    if (test_tag_spec_flags(gfc, V1_ONLY_FLAG)) {
        return 0;
    }
    if (test_tag_spec_flags(gfc, CHANGED_FLAG)) {
        unsigned char *tag = 0;
        size_t  tag_size, n;

        n = lame_get_id3v2_tag(gfp, 0, 0);
        tag = lame_calloc(unsigned char, n);
        if (tag == 0) {
            return -1;
        }
        tag_size = lame_get_id3v2_tag(gfp, tag, n);
        if (tag_size > n) {
            free(tag);
            return -1;
        }
        else {
            size_t  i;
            /* write tag directly into bitstream at current position */
            for (i = 0; i < tag_size; ++i) {
                add_dummy_byte(gfc, tag[i], 1);
            }
        }
        free(tag);
        return (int) tag_size; /* ok, tag should not exceed 2GB */
    }
    return 0;
}

static unsigned char *
set_text_field(unsigned char *field, const char *text, size_t size, int pad)
{
    while (size--) {
        if (text && *text) {
            *field++ = *text++;
        }
        else {
            *field++ = pad;
        }
    }
    return field;
}

size_t
lame_get_id3v1_tag(lame_t gfp, unsigned char *buffer, size_t size)
{
    size_t const tag_size = 128;
    lame_internal_flags *gfc;

    if (gfp == 0) {
        return 0;
    }
    if (size < tag_size) {
        return tag_size;
    }
    gfc = gfp->internal_flags;
    if (gfc == 0) {
        return 0;
    }
    if (buffer == 0) {
        return 0;
    }
    if (test_tag_spec_flags(gfc, V2_ONLY_FLAG)) {
        return 0;
    }
    if (test_tag_spec_flags(gfc, CHANGED_FLAG)) {
        unsigned char *p = buffer;
        int     pad = test_tag_spec_flags(gfc, SPACE_V1_FLAG) ? ' ' : 0;
        char    year[5];

        /* set tag identifier */
        *p++ = 'T';
        *p++ = 'A';
        *p++ = 'G';
        /* set each field in tag */
        p = set_text_field(p, gfc->tag_spec.title, 30, pad);
        p = set_text_field(p, gfc->tag_spec.artist, 30, pad);
        p = set_text_field(p, gfc->tag_spec.album, 30, pad);
        sprintf(year, "%d", gfc->tag_spec.year);
        p = set_text_field(p, gfc->tag_spec.year ? year : NULL, 4, pad);
        /* limit comment field to 28 bytes if a track is specified */
        p = set_text_field(p, gfc->tag_spec.comment, gfc->tag_spec.track_id3v1 ? 28 : 30, pad);
        if (gfc->tag_spec.track_id3v1) {
            /* clear the next byte to indicate a version 1.1 tag */
            *p++ = 0;
            *p++ = gfc->tag_spec.track_id3v1;
        }
        *p++ = gfc->tag_spec.genre_id3v1;
        return tag_size;
    }
    return 0;
}

int
id3tag_write_v1(lame_t gfp)
{
    lame_internal_flags* gfc = 0;
    size_t  i, n, m;
    unsigned char tag[128];

    if (is_lame_internal_flags_null(gfp)) {
        return 0;
    }
    gfc = gfp->internal_flags;

    m = sizeof(tag);
    n = lame_get_id3v1_tag(gfp, tag, m);
    if (n > m) {
        return 0;
    }
    /* write tag directly into bitstream at current position */
    for (i = 0; i < n; ++i) {
        add_dummy_byte(gfc, tag[i], 1);
    }
    return (int) n;     /* ok, tag has fixed size of 128 bytes, well below 2GB */
}
