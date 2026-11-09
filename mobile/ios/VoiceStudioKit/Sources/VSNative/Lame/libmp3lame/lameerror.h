/*
 *  A collection of LAME Error Codes
 *
 *  Please use the constants defined here instead of some arbitrary
 *  values. Currently the values starting at -10 to avoid intersection
 *  with the -1, -2, -3 and -4 used in the current code.
 *
 *  May be this should be a part of the include/lame.h.
 */

typedef enum {
    LAME_OKAY = 0,
    LAME_NOERROR = 0,
    LAME_GENERICERROR = -1,
    LAME_NOMEM = -10,
    LAME_BADBITRATE = -11,
    LAME_BADSAMPFREQ = -12,
    LAME_INTERNALERROR = -13,

    FRONTEND_READERROR = -80,
    FRONTEND_WRITEERROR = -81,
    FRONTEND_FILETOOLARGE = -82,

} lame_errorcodes_t;

/* end of lameerror.h */
