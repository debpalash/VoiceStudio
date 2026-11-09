#include "include/BookArchive.h"
#include <zlib.h>
#include <limits.h>
uint32_t vs_book_crc32(const uint8_t *source, size_t count) { return count <= UINT_MAX ? (uint32_t)crc32(0L, source, (uInt)count) : 0; }
int vs_book_inflate(const uint8_t *source, size_t source_count, uint8_t *output, size_t output_count, uint32_t expected) {
    if (source_count > UINT_MAX || output_count > UINT_MAX) return -1;
    z_stream stream = {0}; stream.next_in = (Bytef *)source; stream.avail_in = (uInt)source_count;
    stream.next_out = output; stream.avail_out = (uInt)output_count;
    if (inflateInit2(&stream, -MAX_WBITS) != Z_OK) return -1;
    int status = inflate(&stream, Z_FINISH);
    int ok = status == Z_STREAM_END && stream.total_in == source_count && stream.total_out == output_count;
    inflateEnd(&stream);
    return ok && vs_book_crc32(output, output_count) == expected ? 0 : -1;
}

uint32_t vs_book_crc32_update(uint32_t prior, const uint8_t *source, size_t count) { return count <= UINT_MAX ? (uint32_t)crc32(prior, source, (uInt)count) : prior; }
