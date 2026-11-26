# SketchUp Binary Format Specification (VFF)

> **Reverse-engineered by the OpenSKP project.**  
> Applies to SketchUp 2021+ files using the VFF container format.

> [!NOTE]
> This document covers the **modern VFF container only** (SketchUp 2021+).
> Pre-2021 files (SketchUp 3–2020) use a completely different container — a
> classic MFC `CArchive` object-graph stream, not a ZIP archive at all, with
> its own store-map/back-reference scheme instead of TLV tags. OpenSKP
> parses SketchUp 4–2020 transparently behind the same `parse()`/`buildScene()`
> calls (see [LANGUAGE_PARITY.md § 4](LANGUAGE_PARITY.md#4-real-sketchup-file-version-support)
> and [DEVELOPER_GUIDE.md](DEVELOPER_GUIDE.md#legacy-format-support-sketchup-42020)),
> but that format isn't documented here — see the extensive docstring at
> the top of each language's `legacy.py`/`legacy.ts`/`Legacy.cs`/`legacy.dart`
> for the reverse-engineered details of the classic container.

## 1. File Container

SKP files are **VFF containers** — a proprietary binary header followed by an embedded ZIP archive.

### 1.1 Header Structure

| Offset | Size | Content | Description |
|--------|------|---------|-------------|
| `0x00` | 4 | `FF FE FF 0E` | VFF magic marker |
| `0x04` | varies | UTF-16LE text | Version metadata |
| varies | 4 | `50 4B 03 04` | ZIP local file header (`PK\x03\x04`) |

**Version Extraction:** The version string is UTF-16LE encoded between two `FF FE FF` markers. It appears in braces, e.g., `{25.0.575}`.

```
Offset  Hex                                           ASCII
0x0000  FF FE FF 0E 00 00 00 00 00 00 FF FE FF 06    ....
0x000E  7B 00 32 00 35 00 2E 00 30 00 2E 00 35 00    {.2.5...0...5.
```

### 1.2 ZIP Contents

| Entry | Description |
|-------|-------------|
| `model.dat` | Binary TLV-encoded geometry and scene data |
| `materials/*/material.xml` | Material definitions (XML, SketchUp namespace) |
| `meta/model_thumbnail.png` | Model preview image |

## 2. TLV Encoding (`model.dat`)

The `model.dat` file uses **Tag-Length-Value** encoding with a 16-byte header.

### 2.1 Element Structure

Each TLV element:

```
┌──────────┬──────────────┬──────────────────────┐
│ Tag (2B) │ Length (4B)   │ Value (Length bytes)  │
│ LE hex   │ uint32 LE    │ payload or children   │
└──────────┴──────────────┴──────────────────────┘
```

- **Tag**: 2 bytes, little-endian. Displayed as uppercase hex (e.g., `C409`).
- **Length**: 4-byte `uint32` LE — payload size in bytes.
- **Value**: Either raw data (leaf) or nested TLV children (container).

### 2.2 Container Tags

These tags contain nested child TLV elements. Real files nest them about a dozen levels deep
(the deepest of the files checked reaches 11); the readers stop at 64 levels and raise a parse
error, so a hostile file cannot exhaust the stack:

```
F401  F701  D430  D530  C832
7C15  8813  8913  8A13  8B13  8C13  8D13  4C1D  6419
F901  7017  7117  D007  C409  9411  9511  0F01
384A  B80B  9713  2C4C  AC0D  AE0D  F601  F801
983A  993A  8C3C  8D3C
9013  401F
9113  9213  6942  6C42  6842
```

`9013`/`401F` are the pair of containers an Image entity's placement is
wrapped in: a placed Image wraps a standard `6419` Instance node inside
`9013` → `401F`. Without treating both as containers, that inner instance
stays buried in an opaque payload and the image looks never placed.

`9113`/`9213` are the per-definition lists of construction/guide lines
(`6942`) and points (`6C42`). `6842` is the drawbase wrapper those
entities share (`D007` / entity id / `D307` flags). `6A42` (8×f64 line
body) and `6D42` (3×f64 point) are leaves, not containers.

A guide's own Hide and tag live in that `6842` → `D007` container, the
same leaves edges, faces and instances use: `D307` display flags (base
`0x06`, `+0x01` = hidden) and `D207` (layer entity id). Large models can
carry tens of thousands of hidden guide points; reading `6D42` alone
draws every one of them.

## 3. Tag Reference

### 3.1 Geometry Tags

| Tag | Name | Payload | Description |
|-----|------|---------|-------------|
| `C409` | **Vertex** | Container | Vertex entity with coordinates |
| `C509` | Vertex Coords | 24B: 3×`float64` | X, Y, Z coordinates (inches) |
| `B80B` | **Edge** | Container | Edge connecting two vertices |
| `B90B` | Edge Start | var_int | Start vertex entity ID |
| `BA0B` | Edge End | var_int | End vertex entity ID |
| `AC0D` | **Face** | Container | Planar face polygon |
| `AD0D` | Face Normal | 24B: 3×`float64` | Normal vector (nx, ny, nz) |
| `AE0D` | Face Loops | Container | Outer boundary + holes |
| `9113` | **CLine list** | Container | Construction/guide lines in this definition |
| `6942` | **CConstructionLine** | Container | Guide line entity |
| `6A42` | Line params | 64B: 8×`float64` | Point, unit direction, start/end params (inches; `|param|≥1e20` unbounded) |
| `9213` | **CPoint list** | Container | Construction/guide points in this definition |
| `6C42` | **CConstructionPoint** | Container | Guide point entity |
| `6D42` | Point position | 24B: 3×`float64` | X, Y, Z (inches) |

### 3.2 Topology Tags

| Tag | Name | Payload | Description |
|-----|------|---------|-------------|
| `9411` | **Loop** | Container | One loop of the face (first = outer, rest = holes) |
| `9511` | Loop CoEdges | Container | Ordered sequence of coedges |
| `A00F` | CoEdge | Inline TLV | Edge reference + orientation |
| `A10F` | Edge Ref | var_int | Referenced edge entity ID |
| `A20F` | Orientation | var_int | `0` = along the edge (start → end), `1` = against it |

A face's coedges sit one level below its loop: `AC0D` → `AE0D` → `9411` →
`9511` → `A00F`. The readers map `A20F` to `CoEdge.orientation` as `0` → `+1`
and `1` → `-1`.

### 3.3 Component Tags

| Tag | Name | Payload | Description |
|-----|------|---------|-------------|
| `7C15` | **Definition** | Container | Component definition |
| `7D15` | Def GUID | 16B | UUID as raw bytes |
| `7E15` | Def Name | ASCII | Component name string |
| `6419` | **Instance** | Container | Component instance (placement) |
| `6519` | Instance Name | ASCII | Instance label |
| `6619` | Transform | 104B: 13×`float64` | 3×4 affine matrix + scale |
| `6719` | Def Reference | var_int | Points to definition entity ID |
| `6819` | Instance GUID | 16B | Instance UUID |

### 3.4 Scene Tags

| Tag | Name | Payload | Description |
|-----|------|---------|-------------|
| `F601` | **Root Geometry** | Container | Top-level loose geometry |
| `D007` | Entity Properties | Container | Layer assignment, attributes |
| `D207` | Layer Reference | var_int | Layer entity ID |
| `993A` | Layer Collection | Container | All layer definitions |
| `8C3C` | Layer Entry | Container | Single layer definition |
| `8D3C` | Layer Name | ASCII | Layer/tag name string |

### 3.5 Entity ID Tags

| Tag | Name | Payload | Description |
|-----|------|---------|-------------|
| `DE05` | Entity ID | var_int | Unique entity identifier |
| `DC05` | Entity Wrapper | Container | Wraps `DE05` inside larger payloads |

### 3.6 Dynamic Property Tags

Inside a `D007` entity-properties record, a `DC05` payload holds the
attribute-dictionary tree. Each dictionary is a `B436` name node immediately
followed by an entries container (typically `B536`), and each entry is a
`B636` key immediately followed by one `A438` value wrapper.

| Tag | Name | Payload | Description |
|-----|------|---------|-------------|
| `B436` | Dictionary Name | UTF-8 | Attribute dictionary name |
| `B636` | Property Key | UTF-8 | Dynamic attribute name |
| `A438` | Property Value | Container | One typed child; empty payload means nil |

An `A438` with no children is `nil`. Otherwise it contains exactly one typed
child record, whose tag selects the value's type:

| Tag | Type | Payload | Description |
|-----|------|---------|-------------|
| `AD38` | String | UTF-8 | Text value |
| `A738` | Integer | 4B: `int32` LE | Signed 32-bit integer |
| `A938` | Float | 8B: `float64` | Plain double |
| `AF38` | Length | 8B: `float64` | Length in inches; same encoding as Float, distinct tag |
| `AA38` | Boolean | 1B | `0` = false, non-zero = true |
| `AC38` | Time | 4B: `uint32` LE | Unix seconds (`time_t`) |
| `B438` | Point3d | 24B: 3×`float64` | x, y, z |
| `B538` | Vector3d | 24B: 3×`float64` | x, y, z |
| `AE38` | Array | Container | Sequence of `A438` values; arrays may nest |

Classic (pre-2021) files store the same kinds of value as typed
`CAttributeNamed` records instead: their boolean type is `0x07` (one-byte
`u8`) and their time type is `0x09` (unsigned 32-bit seconds). Booleans are
rendered as `"1"`/`"0"` so the stringified view matches that classic integer
form.

A type tag the reader does not know, or a known tag whose payload is not the
size listed above, is returned as `nil`/`None` rather than being guessed at.

## 4. Coordinate System

| Property | SketchUp | glTF (OpenSKP output) |
|----------|----------|-----------------------|
| Up axis | Z | Y |
| Units | Inches | Meters |

**Conversion formula (glTF vertex positions & transforms):**

```
x_m =  x_inches × 0.0254
y_m =  z_inches × 0.0254   (Z-up → Y-up)
z_m = -y_inches × 0.0254   (flip Y → -Z)
```

*(Scene metadata fields explicitly suffixed `_mm` / `Mm`, such as `InstanceNode.position_mm` and `MeshMetadata.bounding_box_mm`, convert inches to millimeters via `× 25.4` on SketchUp's Z-up axes.)*

## 5. Transform Matrix

Instance transforms are stored in tag `6619` as **13 consecutive `float64`** values (104 bytes):

```
Index:  [0]  [1]  [2]  [3]  [4]  [5]  [6]  [7]  [8]  [9] [10] [11] [12]
        r00  r01  r02  r10  r11  r12  r20  r21  r22  tx   ty   tz  scale

        ┌─────────────────┐
        │ r00  r01  r02  │  Row 0 (X axis)
        │ r10  r11  r12  │  Row 1 (Y axis)
        │ r20  r21  r22  │  Row 2 (Z axis)
        │ tx   ty   tz   │  Translation
        └─────────────────┘
        scale              Uniform scale factor
```

**Matrix multiplication** for nested instances: `M_world = M_parent × M_child`

## 6. Entity ID Resolution

Entity IDs are variable-length little-endian integers. They appear in two forms:

1. **Direct `DE05`**: Tag `DE05` → length → var_int payload
2. **Wrapped in `DC05`**: Tag `DC05` → payload starts with `DE 05` bytes → inner length → var_int

Resolution algorithm:
```python
def extract_entity_id(node):
    for child in node.children:
        if child.tag == 'DE05':
            return parse_var_int(child.payload, 0, len(child.payload))
        if child.tag == 'DC05':
            if child.payload[:2] == b'\xDE\x05':
                inner_len = read_u32(child.payload, 2)
                return parse_var_int(child.payload, 6, inner_len)
    # Recursive fallback
    for child in node.children:
        result = extract_entity_id(child)
        if result is not None:
            return result
    return None
```

## 7. VFF Writer (Python, 2021+)

`openskp.create_vff` writes the modern container by **editing a template**,
never by patching fixed byte offsets. It parses the template's `model.dat`
into a record tree, appends new records to the model's existing pools,
allocates new ids above the template's own next-id counter, re-encodes the
tree and repacks the archive. The bundled `_scaffold/blank_vff.skp` is an
empty SketchUp 2025 document (one default tag, no geometry); any SketchUp
file with the standard sections can be passed as the template instead.

### 7.1 Header and integrity word

The header is two UTF-16LE marker-prefixed strings followed by a 13-byte
block whose last four bytes are the integrity word, then the embedded ZIP
archive (`PK\x03\x04`). The writer keeps the template's header strings and
its model revision, and patches only the integrity word.

skpwright (https://github.com/hew3d/skpwright, by Kurt Granroth,
GPL-3.0-only) documents the VFF container; the integrity-word algorithm was
implemented from that description and verified against genuine SketchUp
saves. No skpwright code is used.

The integrity word is derived from the final archive: take every entry's
CRC-32 in archive order, subtract 42 from odd-indexed entries, replace the
`meta/meta.dat` slot with a constant derived from `ceil(n/2)` and the last
four bytes of `meta.dat`, lay the slots out evens-first, then CRC-32
`meta.dat` without its last four bytes followed by that table.

### 7.2 ZIP and `model.dat` ordering

New archive entries are emitted as local headers with the 20-byte ZIP64
extended-information field (two zero `u64`s), DEFLATE level 1 (`0x0006`) for
`.dat`/`.xml` and stored DEFLATE level 0 (`0x0000`) for images, no data
descriptor, DOS date `0x0020` and a UTF-8 name. The central directory
repeats the fields without the extra field. Entry order is:

1. `meta/model_thumbnail.png`, `meta/preview_thumbnail.png`
2. `meta/meta.dat`
3. existing `materials/...` entries, then new material folders
4. other existing entries
5. `model.dat` **last**

`meta/meta.dat` is repacked with a fresh model GUID (or the explicit
`set_guid` value) and a fresh save time on the history entry; the template's
own model revision is kept.

### 7.3 Material and tag archive folders

SketchUp derives the folder under `materials/` from the material's display
name; a tag's embedded material uses `Layer_<tag name>` as that name. The
writer reproduces the observed rule:

- Replace the characters `\ / : * ? < > | "`, tab, newline and carriage
  return with `_` (a quote becomes `_` only in the folder; `material.xml`'s
  `name` attribute keeps it, XML-escaped).
- While the name is longer than 80 characters, cut it to `floor(2/3)` of the
  current length; if that lands exactly on 80, cut once more. For example a
  120-character name becomes 53 characters and a 200-character one becomes
  58.
- If the resulting folder is already taken (comparison is case-insensitive,
  as on Windows), strip a leading Windows drive prefix (`a:b` becomes `b`,
  `C:D` becomes `D`), sanitise and truncate that remainder, then append
  `_1`, `_2`, ... . Without a drive prefix this is just the usual `_N`
  suffix, e.g. `a*b` after `a_b` becomes `a_b_1`.
- Material and tag folders share one collision namespace, which is why a
  material named `Layer_Foo` and a tag `Foo` end up at `Layer_Foo` and
  `Layer_Foo_1`.
- Material names are case-insensitive too: a new `wood` after `Wood` becomes
  `wood1` (then `wood2`, ...), as SketchUp's material manager does. Tags are
  case-insensitive rather than renamed: a new `wood` after `Wood` resolves
  to the existing tag.

Names containing characters XML 1.0 cannot hold raise `SkpWriteError`
instead. The `/` and `"` cases therefore never nest or break a ZIP path.

### 7.4 Attribute dictionaries

A drawing element's entity-base record (`DC05`) may carry an attribute block
`DD05` after the entity id. Inside it, `B136` contains one `B236` set whose
children are either `B336` dictionaries or the `1027` texture-placement
record described below.

A dictionary `B336` is laid out in file order as: empty `B036`, UTF-8 name
`B436`, a `B536` entries record, then a `B736` trailer (one `u32`, zero).
Each entry in `B536` is a UTF-8 key `B636` followed by one `A438` value
wrapper. The wrapper's first child gives the type: `A738` signed 32-bit
integer, `A938` little-endian `f64`, `AA38` one bool byte, `AC38` `u32`
Unix time, `AD38` UTF-8 string, `AE38` array of further `A438` wrappers,
`AF38` `f64` Length, `B438` three `f64`s Point3d, `B538` three `f64`s
Vector3d. A null value is an empty `A438`; an empty array is an `AE38` with
no children. The writer rejects non-finite floats, out-of-range integers and
timestamps, and unsupported value types.

### 7.5 Texture materials and UV placement

A texture material is still a `C832` record whose name `CC32` is the folder,
but its `material.xml` has `type="1"` and `hasTexture="1"`. The
`<mat:texture>` element, in attribute order, stores the original
`textureFilename`, the applied tile size as `xScale`/`yScale` in inches and
an `avgColor` packed as `ABGR` (`0xFF` alpha in the top byte, then blue,
green, red). Its
`<mat:image>` names the archive entry as `./<basename>` and `file_name`.
The image bytes sit next to `material.xml` as
`materials/<folder>/<basename>`, stored (`0x0000`) rather than DEFLATEd.
SketchUp keeps the source file's basename verbatim, including spaces, `#`,
`'`, `&` and UTF-8; the writer replaces path separators and `"` with `_`
because Windows cannot represent them, and rejects control characters.
`textureFilename` is XML-escaped independently of the archive name.

A positioned face adds the `1027` record inside its attribute block:
`B036` empty, `1127` front side, `1227` back side. Each side contains a
`u32` flags `1427` (1 when positioned), nine `f64`s matrix `1527`, three
`f64`s offset `1627` (zero) and empty pins `1727`. The stored matrix is the
inverse of the pin map: it maps texture-tile coordinates (multiplied by the
applied size) to face-plane coordinates, which is what `Face.uv_transform`
and the writer's `uv.py` expose. Its inverse maps face-plane coordinates to
texture coordinates; an unpositioned side stores the identity with flags 0.

### 7.6 Curves and arcs

A plain curve is a `384A` record containing the entity base (`DC05`/`DE05`),
a `u32` edge count `394A`, flag byte `3A4A` (zero), and the first `3B4A` and
last `3C4A` edge ids. Each of its edges is an ordinary `B80B` record with an
extra edge-curve reference `BB0B`; the curve record itself sits in the curves
pool `9613`. A polyline writes curves over its chained edges (closing the
last-to-first edge when requested); edges that already exist are skipped and
the remaining new edges are grouped into contiguous runs of at least two,
with a wrap-around run kept as one curve.

An arc or circle wraps that same curve in `2C4C` inside the arc-curves pool
`9713`, followed by a `2D4C` frame of 16 `f64`s in file order: centre x/y/z,
unit normal x/y/z, plane offset, x-axis x/y/z scaled by the radius, y-axis
x/y/z scaled by the radius, radius, start angle, end angle. Circles use a
closed curve with start 0 and end 2π; open arcs use segment count + 1 points
and the requested angles.

### 7.7 Annotations: texts and dimensions

A text is a `F055` record in the texts pool `9813`. In file order it holds
the entity base/drawing (`DC05`/`D007`), UTF-8 string `F155`, empty `FF55`,
two `f64`s `F255`/`F355` for the optional screen position, the anchor
connection `F455` (a `0852` with type `0952`, point `0A52` and two null
refs), the absolute label point as three `f64`s `F555` (not a leader vector;
verified against the genuine SketchUp save), one byte `F655`, up vector
`F755`, zero `F855`, font reference `F955`, arrow type `FA55` (u32),
`FB55` (u32 one), display-leader byte `FC55`, leader type `FD55` (u32) and
screen flag `FE55` (u32).

A linear dimension is a `CC5B` record in the dimensions pool `9913`:
entity drawing head `D859` (with empty `D959`, font `DA59`, flag `DB59`,
arrow `DC59`), start connection `CD5B`, end connection `CE5B`, plane normal
`CF5B` (3 × `f64`), x-axis `D05B` (3 × `f64`), one `D15B` (u32), zero
`D35B` (`f64`), signed offset `D25B` (`f64`) and arrow type `D45B`
(u32). The connection `0852` carries type `0952`, point `0A52`, reference
wrappers `0B52`/`0C52` and the parsed vertex/path refs `FC53`/`FD53`/`FE53`.

### 7.8 Guides and section planes

A construction line is a `6942` record in pool `9113`: its annotation base
`6842` wraps the drawing, then body `6A42` is eight `f64`s (point x/y/z,
unit direction x/y/z, start/end distance; unbounded uses ±1e30) and
stipple `6B42` is `u16 0xFFFF`. A construction point is a `6C42` in pool
`9213`: base `6842`, position `6D42` (3 × `f64`), zero anchor `6E42`
(3 × `f64`) and one tail byte `6F42`.

A section plane is a `5C44` record in pool `9313`: drawing (with the
hidden flag in `D307`), plane `5D44` as four `f64`s `(a, b, c, d)` with
the normal normalized and `d = -(n · point)`, UTF-8 name `5E44` and
UTF-8 short label/symbol `5F44`. The model's active section id is
`9413` in the root container.

### 7.9 Records the core writer emits

Everything lives under the root definition container `8813` inside the
root entity list `F601`, with child pools for vertices (`8913`), edges
(`8A13`), faces (`8B13`), components (`8C13`) and groups (`8D13`). New ids
are allocated from the model's next-id record (`F501`), whose counter the
writer advances.

| Entity | Records |
|---|---|
| Drawing/entity base | `DC05` with entity id `DE05`, then `D007` drawing with optional front material `D107`, layer ref `D207` and flags `D307` |
| Vertex | `C409` wrapping point `C509` (3 × `f64`) |
| Edge | `B80B` with start `B90B`, end `BA0B`; flags carry hidden/soft/smooth bits |
| Face | `AC0D` with plane `AD0D` (normal + distance, 4 × `f64`), loops `AE0D` (a loop `9411` contains edge uses `A00F`, each an edge id `A10F` plus forward bit `A20F`), optional back material `AF0D` |
| Material | `C832` with name `CC32`; the color/opacity lives in `materials/<folder>/material.xml`, and tags own their `Layer_<name>` material |
| Tag/layer | `8C3C` with name `8D3C`, hidden byte `8E3C`, owned material `8F3C` and tail `903C` |
| Definition | `7C15` inside the definitions list (`7017` → `7117`) with GUID `7D15`, name `7E15`, kind `8315` (0 component, 1 group), timestamp and behaviour `581B` (`5D1B` always-faces-camera, `5E1B` shadows-face-sun) |
| Instance/group placement | `6419` with name `6519`, 13 × `f64` transform `6619`, definition reference `6719` and GUID `6819`; a group is the same instance wrapped in `4C1D` in the groups pool |

Faces are deduplicated by exact vertex coordinate and edges by endpoint
pair, so adjacent faces share (and never duplicate) vertices and edges.
Non-coplanar input is rejected unless `auto_triangulate=True`, which
fan-splits it into planar triangles, matching `create.py`'s behavior.
