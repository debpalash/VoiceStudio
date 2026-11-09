/*
 *      Machine dependent defines/includes for LAME.
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

#ifndef LAME_MACHINE_H
#define LAME_MACHINE_H

#include "version.h"

#include <stdio.h>
#include <assert.h>

#ifdef STDC_HEADERS
# include <stdlib.h>
# include <string.h>
#else
# ifndef HAVE_STRCHR
#  define strchr index
#  define strrchr rindex
# endif
char   *strchr(), *strrchr();
# ifndef HAVE_MEMCPY
#  define memcpy(d, s, n) bcopy ((s), (d), (n))
#  define memmove(d, s, n) bcopy ((s), (d), (n))
# endif
#endif

#if  defined(__riscos__)  &&  defined(FPA10)
# include "ymath.h"
#else
# include <math.h>
#endif
#include <limits.h>

#include <ctype.h>

#ifdef HAVE_ERRNO_H
# include <errno.h>
#endif
#ifdef HAVE_FCNTL_H
# include <fcntl.h>
#endif

#if defined(macintosh)
# include <types.h>
# include <stat.h>
#else
# include <sys/types.h>
# include <sys/stat.h>
#endif

#ifdef HAVE_INTTYPES_H
# include <inttypes.h>
#else
# ifdef HAVE_STDINT_H
#  include <stdint.h>
# endif
#endif

#ifdef WITH_DMALLOC
#include <dmalloc.h>
#endif

/*
 * 3 different types of pow() functions:
 *   - table lookup
 *   - pow()
 *   - exp()   on some machines this is claimed to be faster than pow()
 */

#define POW20(x) (assert(0 <= (x+Q_MAX2) && x < Q_MAX), pow20[x+Q_MAX2])
/*#define POW20(x)  pow(2.0,((double)(x)-210)*.25) */
/*#define POW20(x)  exp( ((double)(x)-210)*(.25*LOG2) ) */

#define IPOW20(x)  (assert(0 <= x && x < Q_MAX), ipow20[x])
/*#define IPOW20(x)  exp( -((double)(x)-210)*.1875*LOG2 ) */
/*#define IPOW20(x)  pow(2.0,-((double)(x)-210)*.1875) */

/* in case this is used without configure */
#ifndef inline
# define inline
#endif

#if defined(_MSC_VER)
# undef inline
# define inline _inline
#elif defined(__SASC) || defined(__GNUC__) || defined(__ICC) || defined(__ECC)
/* if __GNUC__ we always want to inline, not only if the user requests it */
# undef inline
# define inline __inline
#endif

#if    defined(_MSC_VER)
# pragma warning( disable : 4244 )
/*# pragma warning( disable : 4305 ) */
#endif

/*
 * FLOAT    for variables which require at least 32 bits
 * FLOAT8   for variables which require at least 64 bits
 *
 * On some machines, 64 bit will be faster than 32 bit.  Also, some math
 * routines require 64 bit float, so setting FLOAT=float will result in a
 * lot of conversions.
 */

#if ( defined(_MSC_VER) || defined(__BORLANDC__) || defined(__MINGW32__) )
# define WIN32_LEAN_AND_MEAN
# include <windows.h>
# include <float.h>
# define FLOAT_MAX FLT_MAX
#else
# ifndef FLOAT
typedef float FLOAT;
#  ifdef FLT_MAX
#   define FLOAT_MAX FLT_MAX
#  else
#   define FLOAT_MAX 1e37 /* approx */
#  endif
# endif
#endif

#ifndef FLOAT8
typedef double FLOAT8;
# ifdef DBL_MAX
#  define FLOAT8_MAX DBL_MAX
# else
#  define FLOAT8_MAX 1e99 /* approx */
# endif
#else
# ifdef FLT_MAX
#  define FLOAT8_MAX FLT_MAX
# else
#  define FLOAT8_MAX 1e37 /* approx */
# endif
#endif

/* sample_t must be floating point, at least 32 bits */
typedef FLOAT sample_t;

#define dimension_of(array) (sizeof(array)/sizeof(array[0]))
#define beyond(array) (array+dimension_of(array))
#define compiletime_assert(expression) enum{static_assert_##FILE##_##LINE = 1/((expression)?1:0)}
#define lame_calloc(TYPE, COUNT) ((TYPE*)calloc(COUNT, sizeof(TYPE)))
#define multiple_of(CHUNK, COUNT) (\
  ( (COUNT) < 1 || (CHUNK) < 1 || (COUNT) % (CHUNK) == 0 ) \
  ? (COUNT) \
  : ((COUNT) + (CHUNK) - (COUNT) % (CHUNK)) \
  )

#if 1
#define EQ(a,b) (\
(fabs(a) > fabs(b)) \
 ? (fabs((a)-(b)) <= (fabs(a) * 1e-6f)) \
 : (fabs((a)-(b)) <= (fabs(b) * 1e-6f)))
#else
#define EQ(a,b) (fabs((a)-(b))<1E-37)
#endif

#define NEQ(a,b) (!EQ(a,b))

#ifdef _MSC_VER
#  if _MSC_VER < 1400
#  define fabsf fabs
#  define powf pow
#  define log10f log10
#  endif
#endif

#endif

/* end of machine.h */
