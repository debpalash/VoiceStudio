## $Id: Makefile.am,v 1.41 2016/01/29 21:06:26 aleidinger Exp $

include $(top_srcdir)/Makefile.am.global

SUBDIRS = i386 vector

lib_LTLIBRARIES = libmp3lame.la

if HAVE_NASM
cpu_ldadd = $(top_builddir)/libmp3lame/@CPUTYPE@/liblameasmroutines.la
endif
if WITH_VECTOR
vector_ldadd = $(top_builddir)/libmp3lame/vector/liblamevectorroutines.la
endif

if LIB_WITH_DECODER
decoder_ldadd = $(top_builddir)/mpglib/libmpgdecoder.la
else
decoder_ldadd =
endif

libmp3lame_la_LIBADD =	$(cpu_ldadd) $(vector_ldadd) $(decoder_ldadd) \
			$(CONFIG_MATH_LIB)
libmp3lame_la_LDFLAGS = -version-info @LIB_MAJOR_VERSION@:@LIB_MINOR_VERSION@ \
			-export-symbols $(top_srcdir)/include/libmp3lame.sym \
			-no-undefined

INCLUDES = @INCLUDES@ -I$(top_srcdir)/mpglib -I$(top_builddir)

DEFS = @DEFS@ @CONFIG_DEFS@

EXTRA_DIST = \
	lame.rc \
	vbrquantize.h \
	logoe.ico

libmp3lame_la_SOURCES = \
        VbrTag.c \
	bitstream.c \
	encoder.c \
	fft.c \
	gain_analysis.c \
        id3tag.c \
        lame.c \
        newmdct.c \
	presets.c \
	psymodel.c \
	quantize.c \
	quantize_pvt.c \
	reservoir.c \
	set_get.c \
	tables.c \
	takehiro.c \
	util.c \
	vbrquantize.c \
	version.c \
	mpglib_interface.c

noinst_HEADERS= \
	VbrTag.h \
	bitstream.h \
	encoder.h \
	fft.h \
	gain_analysis.h \
	id3tag.h \
	l3side.h \
	lame-analysis.h \
	lame_global_flags.h \
	lameerror.h \
	machine.h \
	newmdct.h \
	psymodel.h \
	quantize.h  \
	quantize_pvt.h \
	reservoir.h \
	set_get.h \
	tables.h \
	util.h \
	vbrquantize.h \
	version.h

CLEANFILES = lclint.txt

LCLINTFLAGS= \
	+posixlib \
	+showsummary \
	+showalluses \
	+whichlib \
	+forcehints \
	-fixedformalarray \
	+matchanyintegral \
	-Dlint

lclint.txt: ${libmp3lame_la_SOURCES} ${noinst_HEADERS}
	@lclint ${LCLINTFLAGS} ${INCLUDES} ${DEFS} ${libmp3lame_la_SOURCES} 2>&1 >lclint.txt || true

lclint: lclint.txt
	more lclint.txt

#$(OBJECTS): libtool
#libtool: $(LIBTOOL_DEPS)
#	$(SHELL) $(top_builddir)/config.status --recheck
