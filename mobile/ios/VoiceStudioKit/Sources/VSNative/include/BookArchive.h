#ifndef VS_BOOK_ARCHIVE_H
#define VS_BOOK_ARCHIVE_H
#include <stdint.h>
#include <stddef.h>
/* Bounded ZIP deflate decoding; rejects partial/trailing streams and CRC mismatches. */
int vs_book_inflate(const uint8_t *source, size_t source_count, uint8_t *output, size_t output_count, uint32_t crc);
uint32_t vs_book_crc32_update(uint32_t prior, const uint8_t *source, size_t count);
uint32_t vs_book_crc32(const uint8_t *source, size_t count);
#endif
