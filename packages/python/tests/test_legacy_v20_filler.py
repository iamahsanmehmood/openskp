"""The v20 filler probe, exercised on synthetic records.

Ported to TypeScript, .NET, Dart and C++ as openskp#192 (following the
review on openskp#155): a byte-at-a-time "walk to the first non-zero byte"
scan cannot represent a count which is an exact multiple of 256 - its low
byte IS 0x00, so the scan walks straight into the count and misaligns every
read after it. This project's Python implementation independently avoids
that failure mode already (it backs up to a handful of candidate positions
before the first non-zero byte rather than trusting that byte alone, then
validates each candidate structurally via ``_plausible_list_tag``) - this
file exists to lock that in with a real regression test, since none existed
here despite the other four ports having one.

Layout (see ``_retry_count_after_v20_filler``, legacy.py):
    <ff fe ff> <u8 0>   empty UTF-16 string
    <zero padding>      length varies per call site, always pad % 4 == 1
    <u32 count>
"""

import struct

from openskp.legacy import _R, _retry_count_after_v20_filler


def _filler(count: int, pad: int) -> bytes:
    """Builds a filler record followed by *count*, then a class-record header."""
    b = bytearray([0xff, 0xfe, 0xff, 0x00])
    b += bytes(pad)
    b += struct.pack('<I', count)
    b += bytes([0xff, 0xff, 0x0b, 0x00])  # whatever record comes next
    return bytes(b)


def _retry(data: bytes) -> int | None:
    return _retry_count_after_v20_filler(_R(data, 0), 0, ar=None)


class TestRetryCountAfterV20Filler:
    def test_reads_the_counts_and_paddings_seen_in_real_v20_files(self):
        # both padding lengths observed in gondola_v20.skp and a second v20 model
        assert _retry(_filler(20, 9)) == 20
        assert _retry(_filler(5425, 13)) == 5425

    def test_reads_a_count_that_is_an_exact_multiple_of_256(self):
        # the regression this test exists for: a 0x00 low byte is
        # indistinguishable from padding to a byte-at-a-time scan
        for count in (256, 512, 1024, 65536, 16_777_216 // 16):
            for pad in (9, 13):
                assert _retry(_filler(count, pad)) == count, f'count={count} pad={pad}'

    def test_ignores_a_non_empty_string_record(self):
        # a real string here is genuine data; moving the cursor past it
        # would corrupt the parse
        data = bytes([0xff, 0xfe, 0xff, 0x05, 0, 0, 0, 0, 0, 0, 0, 0, 20, 0, 0, 0])
        assert _retry(data) is None

    def test_returns_none_when_there_is_no_marker_ahead(self):
        data = bytes(32)  # all zeros, no ff fe ff
        assert _retry(data) is None

    def test_does_not_run_past_the_end_of_the_buffer(self):
        data = bytes([0xff, 0xfe, 0xff, 0x00, 0, 0])
        assert _retry(data) is None
