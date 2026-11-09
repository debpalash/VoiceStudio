/* VoiceStudio portable LAME encoder-only configuration (not an upstream file).
 * iOS/macOS and Android 64-bit Clang targets use standard C/POSIX types.
 * SIMD/assembler, decoder, network and frontend features are deliberately absent. */
#ifndef VOICESTUDIO_LAME_CONFIG_H
#define VOICESTUDIO_LAME_CONFIG_H
#include <stdint.h>
#include <float.h>
#define STDC_HEADERS 1
#define HAVE_ERRNO_H 1
#define HAVE_FCNTL_H 1
#define HAVE_INTTYPES_H 1
#define HAVE_LIMITS_H 1
#define HAVE_STDINT_H 1
#define HAVE_STDLIB_H 1
#define HAVE_STRINGS_H 1
#define HAVE_STRING_H 1
#define HAVE_MEMCPY 1
#define HAVE_STRCHR 1
#define HAVE_STRTOL 1
#define HAVE_SYS_STAT_H 1
#define HAVE_SYS_TYPES_H 1
#define HAVE_UNISTD_H 1
#define HAVE_LONG_DOUBLE 1
#if LDBL_MANT_DIG > DBL_MANT_DIG
#define HAVE_LONG_DOUBLE_WIDER 1
#endif
#define SIZEOF_SHORT 2
#define SIZEOF_INT 4
#define SIZEOF_LONG 8
#define SIZEOF_LONG_LONG 8
#define SIZEOF_FLOAT 4
#define SIZEOF_DOUBLE 8
#define PACKAGE "lame"
#define PACKAGE_VERSION "3.100"
#define VERSION "3.100"
typedef float ieee754_float32_t;
typedef double ieee754_float64_t;
typedef long double ieee854_float80_t;
#endif
