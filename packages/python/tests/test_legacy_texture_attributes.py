"""Regression test for legacy textures that carry their own attribute
dictionary (openskp#396).

A texture is an entity in its own right, so right after the material's
"has texture" flag it opens with the standard entity preamble: an
attribute-container reference (null unless an extension stored a
dictionary on the Texture itself - render plugins do) and, from v17 on, the
persistent-id mask. ``_texture_block`` used to skip a fixed 1/2-byte pad
there, which is only right when the reference is null; a real container
left the reader misaligned ("texture object is not a dib").

The writer can't author an attribute container on a texture, so this test
patches the builder to emit one in place of the null reference - the exact
byte shape of a real Rayscaper-tagged SketchUp 2020 texture (container
class-ref, its own null preamble, one named dictionary, end of children,
pid mask), without needing a private model.
"""
from __future__ import annotations

import importlib
import struct
import zlib

from openskp import create, legacy

# ``openskp.create`` the module is shadowed by the ``create`` function the
# package re-exports, so fetch the module explicitly.
create_mod = importlib.import_module("openskp.create")

SQUARE = [(0.0, 0.0, 0.0), (100.0, 0.0, 0.0), (100.0, 100.0, 0.0), (0.0, 100.0, 0.0)]


def _make_test_png(size: int = 4, rgb=(200, 50, 50)) -> bytes:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    raw = b"".join(b"\x00" + bytes(rgb) * size for _ in range(size))
    return b"\x89PNG\r\n\x1a\n" + ihdr + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def _build(tmp_path, with_texture_attrs: bool) -> bytes:
    png_path = tmp_path / "tex.png"
    png_path.write_bytes(_make_test_png())

    original = create_mod._ArchiveWriter._new_of_known_class

    def patched(self, class_name, schema=None):
        if class_name == "CDib" and with_texture_attrs:
            # Drop the null preamble the writer just emitted after the u16
            # texflag: the flag's high byte + the 2-byte pad = null
            # attribute ref (2) + pid mask (1).
            del self.buf[-3:]
            self.buf += struct.pack("<H", 0x8000 | create_mod._ATTR_CONTAINER_SLOT)
            self._alloc()
            self.buf += bytes(3)  # container's own null attrs + mask
            self.write_attribute_dict("Rayscaper_Data", {"image_texture_path": "C:/tex/brick.png"})
            self._null()  # end of the container's children
            self.buf += b"\x00"  # the texture's pid mask
        return original(self, class_name, schema)

    create_mod._ArchiveWriter._new_of_known_class = patched
    try:
        builder = create()
        tex = builder.add_texture_material("Brick", str(png_path))
        builder.add_face(SQUARE, material=tex)
        data = builder.to_bytes()
    finally:
        create_mod._ArchiveWriter._new_of_known_class = original
    return data


class TestTextureWithAttributeDictionary:
    def test_texture_without_attributes_still_parses(self, tmp_path):
        data = _build(tmp_path, with_texture_attrs=False)
        _, _, _, materials = legacy._walk(data)
        assert [v["name"] for _, v in materials if v["name"] == "Brick"] == ["Brick"]

    def test_texture_carrying_an_attribute_dictionary_parses(self, tmp_path):
        data = _build(tmp_path, with_texture_attrs=True)
        _, root, _, materials = legacy._walk(data)
        brick = [v for _, v in materials if v["name"] == "Brick"]
        assert len(brick) == 1
        assert brick[0]["tex_file"].endswith("tex.png")
        # the face using the textured material still resolves after it
        assert [n for (_, n, _) in root if n == "CFace"]
