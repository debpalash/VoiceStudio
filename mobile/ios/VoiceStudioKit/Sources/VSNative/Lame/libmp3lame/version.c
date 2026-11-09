/*
 *      Version numbering for LAME.
 *
 *      Copyright (c) 1999 A.L. Faber
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

/*!
  \file   version.c
  \brief  Version numbering for LAME.

  Contains functions which describe the version of LAME.

  \author A.L. Faber
  \version \$Id: version.c,v 1.34 2011/11/18 09:51:02 robert Exp $
  \ingroup libmp3lame
*/


#ifdef HAVE_CONFIG_H
# include <config.h>
#endif


#include "lame.h"
#include "machine.h"

#include "version.h"    /* macros of version numbers */





/*! Get the LAME version string. */
/*!
  \param void
  \return a pointer to a string which describes the version of LAME.
*/
const char *
get_lame_version(void)
{                       /* primary to write screen reports */
    /* Here we can also add informations about compile time configurations */

#if   LAME_ALPHA_VERSION
    static /*@observer@ */ const char *const str =
        STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) " "
        "(alpha " STR(LAME_PATCH_VERSION) ", " __DATE__ " " __TIME__ ")";
#elif LAME_BETA_VERSION
    static /*@observer@ */ const char *const str =
        STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) " "
        "(beta " STR(LAME_PATCH_VERSION) ", " __DATE__ ")";
#elif LAME_RELEASE_VERSION && (LAME_PATCH_VERSION > 0)
    static /*@observer@ */ const char *const str =
        STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) "." STR(LAME_PATCH_VERSION);
#else
    static /*@observer@ */ const char *const str =
        STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION);
#endif

    return str;
}


/*! Get the short LAME version string. */
/*!
  It's mainly for inclusion into the MP3 stream.

  \param void   
  \return a pointer to the short version of the LAME version string.
*/
const char *
get_lame_short_version(void)
{
    /* adding date and time to version string makes it harder for output
       validation */

#if   LAME_ALPHA_VERSION
    static /*@observer@ */ const char *const str =
        STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) " (alpha " STR(LAME_PATCH_VERSION) ")";
#elif LAME_BETA_VERSION
    static /*@observer@ */ const char *const str =
        STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) " (beta " STR(LAME_PATCH_VERSION) ")";
#elif LAME_RELEASE_VERSION && (LAME_PATCH_VERSION > 0)
    static /*@observer@ */ const char *const str =
        STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) "." STR(LAME_PATCH_VERSION);
#else
    static /*@observer@ */ const char *const str =
        STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION);
#endif

    return str;
}

/*! Get the _very_ short LAME version string. */
/*!
  It's used in the LAME VBR tag only.

  \param void   
  \return a pointer to the short version of the LAME version string.
*/
const char *
get_lame_very_short_version(void)
{
    /* adding date and time to version string makes it harder for output
       validation */
#if   LAME_ALPHA_VERSION
#define P "a"
#elif LAME_BETA_VERSION
#define P "b"
#elif LAME_RELEASE_VERSION && (LAME_PATCH_VERSION > 0)
#define P "r"
#else
#define P " "
#endif
    static /*@observer@ */ const char *const str =
#if (LAME_PATCH_VERSION > 0)
      "LAME" STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) P STR(LAME_PATCH_VERSION)
#else
      "LAME" STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) P
#endif
      ;
    return str;
}

/*! Get the _very_ short LAME version string. */
/*!
  It's used in the LAME VBR tag only, limited to 9 characters max.
  Due to some 3rd party HW/SW decoders, it has to start with LAME.

  \param void   
  \return a pointer to the short version of the LAME version string.
 */
const char*
get_lame_tag_encoder_short_version(void)
{
    static /*@observer@ */ const char *const str =
            /* FIXME: new scheme / new version counting / drop versioning here ? */
    "LAME" STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) P
    ;
    return str;
}

/*! Get the version string for GPSYCHO. */
/*!
  \param void
  \return a pointer to a string which describes the version of GPSYCHO.
*/
const char *
get_psy_version(void)
{
#if   PSY_ALPHA_VERSION > 0
    static /*@observer@ */ const char *const str =
        STR(PSY_MAJOR_VERSION) "." STR(PSY_MINOR_VERSION)
        " (alpha " STR(PSY_ALPHA_VERSION) ", " __DATE__ " " __TIME__ ")";
#elif PSY_BETA_VERSION > 0
    static /*@observer@ */ const char *const str =
        STR(PSY_MAJOR_VERSION) "." STR(PSY_MINOR_VERSION)
        " (beta " STR(PSY_BETA_VERSION) ", " __DATE__ ")";
#else
    static /*@observer@ */ const char *const str =
        STR(PSY_MAJOR_VERSION) "." STR(PSY_MINOR_VERSION);
#endif

    return str;
}


/*! Get the URL for the LAME website. */
/*!
  \param void
  \return a pointer to a string which is a URL for the LAME website.
*/
const char *
get_lame_url(void)
{
    static /*@observer@ */ const char *const str = LAME_URL;

    return str;
}


/*! Get the numerical representation of the version. */
/*!
  Writes the numerical representation of the version of LAME and
  GPSYCHO into lvp.

  \param lvp    
*/
void
get_lame_version_numerical(lame_version_t * lvp)
{
    static /*@observer@ */ const char *const features = ""; /* obsolete */

    /* generic version */
    lvp->major = LAME_MAJOR_VERSION;
    lvp->minor = LAME_MINOR_VERSION;
#if LAME_ALPHA_VERSION
    lvp->alpha = LAME_PATCH_VERSION;
    lvp->beta = 0;
#elif LAME_BETA_VERSION
    lvp->alpha = 0;
    lvp->beta = LAME_PATCH_VERSION;
#else
    lvp->alpha = 0;
    lvp->beta = 0;
#endif

    /* psy version */
    lvp->psy_major = PSY_MAJOR_VERSION;
    lvp->psy_minor = PSY_MINOR_VERSION;
    lvp->psy_alpha = PSY_ALPHA_VERSION;
    lvp->psy_beta = PSY_BETA_VERSION;

    /* compile time features */
    /*@-mustfree@ */
    lvp->features = features;
    /*@=mustfree@ */
}


const char *
get_lame_os_bitness(void)
{
    static /*@observer@ */ const char *const strXX = "";
    static /*@observer@ */ const char *const str32 = "32bits";
    static /*@observer@ */ const char *const str64 = "64bits";

    switch (sizeof(void *)) {
    case 4:
        return str32;

    case 8:
        return str64;

    default:
        return strXX;
    }
}

/* end of version.c */
