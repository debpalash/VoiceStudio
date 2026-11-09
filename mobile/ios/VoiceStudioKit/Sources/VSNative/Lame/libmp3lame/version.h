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

#ifndef LAME_VERSION_H
#define LAME_VERSION_H


/*
 * To make a string from a token, use the # operator:
 */
#ifndef STR
# define __STR(x)  #x
# define STR(x)    __STR(x)
#endif

# define LAME_URL              "http://lame.sf.net"


# define LAME_MAJOR_VERSION      3 /* Major version number */
# define LAME_MINOR_VERSION    100 /* Minor version number */
# define LAME_TYPE_VERSION       2 /* 0:alpha 1:beta 2:release */
# define LAME_PATCH_VERSION      0 /* Patch level */
# define LAME_ALPHA_VERSION     (LAME_TYPE_VERSION==0)
# define LAME_BETA_VERSION      (LAME_TYPE_VERSION==1)
# define LAME_RELEASE_VERSION   (LAME_TYPE_VERSION==2)

# define PSY_MAJOR_VERSION       1 /* Major version number */
# define PSY_MINOR_VERSION       0 /* Minor version number */
# define PSY_ALPHA_VERSION       0 /* Set number if this is an alpha version, otherwise zero */
# define PSY_BETA_VERSION        0 /* Set number if this is a beta version, otherwise zero */

#if LAME_ALPHA_VERSION
#define LAME_PATCH_LEVEL_STRING " alpha " STR(LAME_PATCH_VERSION)
#endif
#if LAME_BETA_VERSION
#define LAME_PATCH_LEVEL_STRING " beta " STR(LAME_PATCH_VERSION)
#endif
#if LAME_RELEASE_VERSION
#if LAME_PATCH_VERSION
#define LAME_PATCH_LEVEL_STRING " release " STR(LAME_PATCH_VERSION)
#else
#define LAME_PATCH_LEVEL_STRING ""
#endif
#endif

# define LAME_VERSION_STRING STR(LAME_MAJOR_VERSION) "." STR(LAME_MINOR_VERSION) LAME_PATCH_LEVEL_STRING

#endif /* LAME_VERSION_H */

/* End of version.h */
