"""`_reanchor_on_string_marker`, exercised on synthetic byte buffers.

`_read_definition` reads a 16-byte GUID immediately followed by the name
string's `_STR_MARKER`. Some real files skew that fixed-width assumption in
either direction:

- SketchUp 2020 files can carry two extra bytes ahead of the GUID (the
  marker sits a few bytes AFTER the assumed position).
- A definition imported from DWG in a SketchUp 2018 file has been reported
  (openskp#377) to run the prefix a couple of bytes SHORTER than assumed
  (the marker sits a few bytes BEFORE the assumed position) - the case this
  file locks in with a regression test, since none existed for it before.
"""

from openskp.legacy import _STR_MARKER, _reanchor_on_string_marker


def _record(guid: bytes = bytes(range(16))) -> bytes:
    """16-byte GUID immediately followed by the string marker + a 1-byte length."""
    assert len(guid) == 16
    return guid + _STR_MARKER + bytes([5])


class TestReanchorOnStringMarker:
    def test_leaves_pos_unchanged_when_the_marker_already_follows_the_guid(self):
        data = b"leading junk__" + _record()
        pos = len(b"leading junk__")
        assert _reanchor_on_string_marker(data, pos) == pos

    def test_finds_the_marker_two_bytes_after_pos(self):
        # SketchUp 2020: an extra 2-byte prefix shifts the real GUID start
        # later than `pos`.
        data = b"leading junk__" + b"\x00\x00" + _record()
        pos = len(b"leading junk__")
        assert _reanchor_on_string_marker(data, pos) == pos + 2

    def test_finds_the_marker_two_bytes_before_pos(self):
        # openskp#377: the prefix runs shorter than assumed, so `pos` overshoots
        # into the GUID by 2 bytes.
        record = _record()
        data = b"leading junk__" + record
        real_start = len(b"leading junk__")
        pos = real_start + 2
        assert _reanchor_on_string_marker(data, pos) == real_start

    def test_prefers_the_nearest_offset_when_two_candidates_both_match(self):
        # Two independent, genuinely valid GUID+marker records placed so pos+1
        # and pos+3 both land on one: the nearer (pos+1) must win.
        near_guid = bytes(range(16))
        far_guid = bytes(range(16, 32))
        data = bytearray(b"x")  # pos = 0; pos+1 starts the near record
        data += _record(near_guid)
        data += b"xx"  # pos+3 (2 bytes further) starts the far record
        data += _record(far_guid)
        assert _reanchor_on_string_marker(bytes(data), 0) == 1

    def test_returns_pos_unchanged_when_no_candidate_is_found_within_four_bytes(self):
        data = b"leading junk__" + b"\x00" * 40  # no marker anywhere nearby
        pos = len(b"leading junk__")
        assert _reanchor_on_string_marker(data, pos) == pos

    def test_does_not_wrap_around_to_the_end_of_the_buffer_when_pos_is_near_zero(self):
        # pos=0 with no real record anywhere nearby: the -1/-2/-3/-4
        # candidates all land at a negative "position", which must be
        # skipped outright rather than treated as a valid Python negative
        # slice index (which would silently read from the buffer's own
        # end - exactly the kind of wrong-but-not-crashing result this
        # guard exists to prevent).
        data = bytes(20) + _record()  # a real marker exists, but far away
        assert _reanchor_on_string_marker(data, 0) == 0
