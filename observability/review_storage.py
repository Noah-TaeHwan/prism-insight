"""Read legacy and bounded compressed BTC review records without changing text."""
import zlib

MAGIC = b'PRISM_REVIEW_GZIP_V1\x00'
MAX_BYTES = 8_000_000


def review_text(value):
    if isinstance(value, str):
        return value
    if not isinstance(value, bytes):
        raise ValueError('review_body_type_invalid')
    if not value.startswith(MAGIC):
        return value.decode('utf-8')
    decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        raw = decoder.decompress(value[len(MAGIC):], MAX_BYTES + 1)
    except zlib.error as exc:
        raise ValueError('review_body_compression_invalid') from exc
    if len(raw) > MAX_BYTES or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
        raise ValueError('review_body_compression_invalid')
    return raw.decode('utf-8')
