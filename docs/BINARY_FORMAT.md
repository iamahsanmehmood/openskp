# SketchUp Binary Format Specification

> **Reverse-engineered by the OpenSKP project.**  
> Covers both the modern VFF container (SketchUp 2021+, Sections 1–7) and the classic MFC `CArchive` container and empty v17 document (SketchUp 2017 / pre-2021, Section 8).

> [!NOTE]
> Sections 1–7 cover the **modern VFF container** (SketchUp 2021+, a header-prefixed ZIP archive whose geometry lives in TLV-encoded `model.dat`).
> Pre-2021 files (SketchUp 3–2020) use a **classic MFC `CArchive` object-graph stream** with a 1-based global store map; see [Section 8](#8-classic-mfc-container--blank-v17-document) below for the store-map wire protocol and the complete field-by-field layout of the empty SketchUp 2017 (`{17.0.1}`) document built by `openskp._blank_document.build_blank_v17()`, as well as each port's `legacy` reader module and [LANGUAGE_PARITY.md § 4](LANGUAGE_PARITY.md#4-real-sketchup-file-version-support).

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

`openskp.create_vff` writes the modern VFF container without patching fixed
byte offsets. By default (`template=None`),
`openskp._blank_document_vff.build_blank_vff()` constructs the empty 2021+
VFF document in code field by field (see [§9](#9-vff-2021-blank-document))
and supplies its header offsets, initial `next_id`, and built-in persistent
IDs and folder names directly to the writer — no `.skp` template or rendered
thumbnail is bundled in the package. Any existing VFF `.skp` file with the
standard sections may also be passed via `template=` to extend it in place.

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

### 7.9 Scenes and the model camera

A scene is a `4871` page in the page items list `616D`, with 17 children
in file order: page base `546F` (entity id plus UTF-8 name `556F`),
property mask `4971` (u32), camera `4A71` (a `BC34` with eye `BD34`,
target `BE34`, up `BF34`, perspective `C234`, ortho height `C334` and FOV
`C434`), empty `4B71`, style `4C71`, rendering `4D71`, shadows `4E71`,
`5B71` (fifteen `0F7B`-`167B` style/mask subrecords, 71 bytes), axes
`4F71`, hidden layer ids `5071`, empty `5971`, empty `5171`, animation
`5271` (one byte), display name `5371`, transition `5471` (one `f64`),
delay `5571` (one `f64`) and thumbnail flag `5771` (one byte). `626D`
selects the first page. The model start camera record is `FA01` and
contains `BC34`; `set_camera` replaces its eye, target, up, perspective,
height and FOV fields in place.

### 7.10 Records the core writer emits

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

---

## 8. Classic MFC Container & Blank v17 Document

Pre-2021 `.skp` files (SketchUp 3 through SketchUp 2020) are not ZIP/TLV containers. After a UTF-16LE file header, the remainder of the file is a single uncompressed **MFC `CArchive` object-graph stream** using a global 1-based store map. Every port's classic writer (`openskp._blank_document.build_blank_v17()` in Python, `buildBlankV17()` in TypeScript and Dart, `BlankDocumentBuilder.BuildBlankV17()` in .NET, `openskp::detail::build_blank_v17()` in C++, and `openskp::create::build_blank_v17()` in Rust) constructs this empty SketchUp 2017 (`{17.0.1}`, `5,796` bytes) document in code field by field and returns the named byte offsets and store-map slots where the writer splices materials, layers, component definitions, root entities, and scenes.

### 8.1 Classic MFC Wire Primitives

- **UTF-16LE string (`utf16`)**: `FF FE FF` followed by a code-unit count (`u8` when `< 0xFF`; `FF` + `u16` when `0xFF .. 0xFFFE`; `FF FF FF` + `u32` when `>= 0xFFFF`), then `2 * count` little-endian UTF-16 bytes.
- **Object / class tags** (1-based store map):
  - `0x0000` (`u16`): null object pointer (consumes no store-map slot).
  - `0xFFFF` (`u16`) + `schema` (`u16`) + `name_len` (`u16`) + ASCII `class_name`: declares a new MFC class (allocates **one** slot for the class) and immediately starts an instance of that class (allocates **a second** slot for the object).
  - `0x8000 | class_slot` (`u16`, for `class_slot < 0x7FFF`): instantiates an already-declared class and allocates **one** object slot.
  - `obj_slot` (`u16`, `1 <= obj_slot < 0x7FFF`): back-reference to an already-serialized object (consumes no slot).
  - `0x7FFF` (`u16`) + `u32`: 32-bit big-tag escape used whenever `slot >= 0x7FFF` (`0x80000000 | class_slot` for a class-ref, `obj_slot` for a back-ref), avoiding collisions with `0x7FFF` and `0xFFFF`.
- **`CEntity` preamble** (SketchUp 2017+, `CEntity` schema 5): an MFC object ref to an optional `CAttributeContainer` (`0x0000` when absent), followed by a variable-length persistent entity ID encoded as a 1-byte bitmask (`bit k` set iff byte `k` of the little-endian `uint64` ID is non-zero) plus only the non-zero bytes (`0x00` alone encodes persistent ID 0).
- **GUID (`uuid_le`)**: 16-byte mixed-endian Windows `GUID` (`Data1: u32 LE`, `Data2: u16 LE`, `Data3: u16 LE`, `Data4: 8B BE`, i.e. Python `uuid.UUID(...).bytes_le`).
- **Colours (`rgba` / ABGR `u32`)**: 4 bytes `[R, G, B, A]` in memory (`0xAABBGGRR` when read as a little-endian `uint32`).

### 8.2 Store-Map Slots and Persistent IDs in the Empty v17 Document

| Store-Map Slot | Kind | Class / Object | Offset | Persistent ID |
|---:|---|---|---:|---:|
| 1, 2 | `class`, `obj` | `CVersionMap` (schema 0) | 76 (`0x004c`) | — |
| 3, 4 | `class`, `obj` | `CAttributeContainer` (schema 0, wraps `ModelProperties`) | 2800 (`0x0af0`) | 0 |
| 5, 6 | `class`, `obj` | `CAttributeNamed` (schema 1, `'ModelProperties'`) | 2828 (`0x0b0c`) | 0 |
| 7, 8 | `class`, `obj` | `CCamera` (schema 5, model camera) | 2974 (`0x0b9e`) | — |
| 9, 10 | `class`, `obj` | `CLayer` (schema 3, `'Layer0'`; embedded `'Layer_Layer0'` material) | 3409 (`0x0d51`) | 4 (layer), 5 (material) |
| 11, 12 | `class`, `obj` | `CSkFont` (schema 1, `'Arial'`, `CDimensionStyle` font) | 3817 (`0x0ee9`) | 2 |
| 13 | `obj` | `CSkFont` (`0x8000 \| 11`, `'Tahoma'`, `CTextStyle` font) | 3924 (`0x0f54`) | 3 |
| 14, 15 | `class`, `obj` | `CSkpStyle` (schema 2, saved `'Style'`) | 4005 (`0x0fa5`) | 1 |
| 16 | `obj` | `CSkpStyle` (`0x8000 \| 14`, working `'Style'` copy) | 4900 (`0x1324`) | 0 |

The model's `next_persistent_id` counter at offset 1987 (`0x07c3`) starts at 18 in a fresh SketchUp blank document; `openskp.create` allocates new entity persistent IDs sequentially from 18 upward across materials, layers, component definitions, root geometry, and scenes, then writes the final next-unused ID back to 1987. When C++ `DocumentOptions::next_persistent_id` overrides the starting counter (`1 <= start <= 250`), the writer also renumbers the five built-in single-byte persistent IDs recorded by `build_blank_v17()` (`Layer0` at `0x0d5f`, `Layer_Layer0` at `0x0d74`, `'Arial'` at `0x0ef8`, `'Tahoma'` at `0x0f58`, and saved `'Style'` at `0x0fb6`) before handing out user entity IDs.

When `openskp.create` inserts new store-map slots before the document tail (`tail_pos = 3515`), six 2-byte references inside the tail shift (widening via `0x7FFF` if a slot reaches `32,767`):
- Tail `+409` (`@3924`): `0x8000 | 11` (`CSkFont` class-ref for `'Tahoma'`)
- Tail `+468` (`@3983`): 13 (`CTextStyle` font back-ref to `'Tahoma'`)
- Tail `+477` (`@3992`): 12 (`CFontManager.fonts[0]` back-ref to `'Arial'`)
- Tail `+479` (`@3994`): 13 (`CFontManager.fonts[1]` back-ref to `'Tahoma'`)
- Tail `+1383` (`@4898`): 15 (`CSkpStyleManager` selected-style back-ref to saved `'Style'`)
- Tail `+1385` (`@4900`): `0x8000 | 14` (`CSkpStyle` class-ref for the working `'Style'` copy)

### 8.3 Complete Field-by-Field Specification (`0 .. 5,796`)

Every byte of the 5,796-byte classic v17 blank document is listed below in file order (`format` = structural field required by the classic `.skp` / MFC format; `default` = default model setting or per-save identifier/timestamp):

| Offset | Size | Kind | Field & Value |
|---:|---:|---|---|
| 0 (`0x0000`) | 32 | format | File magic UTF-16LE string `'SketchUp Model'` (`FF FE FF 0E` + 14 code units) |
| 32 (`0x0020`) | 20 | format | Archive version UTF-16LE string `'{17.0.1}'` (SketchUp 2017; selects `CEntity` PID masks and v17 schemas) |
| 52 (`0x0034`) | 16 | default | Model UUID (`3093d39a-aa69-4e29-8d53-15cbfe3d94b0`, Windows little-endian GUID; arbitrary per-save UUID, overwritten by `set_guid`) |
| 68 (`0x0044`) | 4 | format | Empty header note/build UTF-16LE string `''` (`FF FE FF 00`) |
| 72 (`0x0048`) | 4 | default | Save timestamp (`u32 = 1786620315` = `2026-08-13T11:25:15Z`, Unix `time_t`) |
| 76 (`0x004c`) | 17 | format | `CVersionMap` MFC class declaration (`0xFFFF`, schema 0, ASCII `'CVersionMap'`; allocates class slot 1 and `CVersionMap` object slot 2) |
| 93 (`0x005d`) | 26 | format | `CVersionMap` entry: `'CArcCurve'` -> schema 3 (`utf16` + `u32`) |
| 119 (`0x0077`) | 28 | format | `CVersionMap` entry: `'CAttribute'` -> schema 0 (`utf16` + `u32`) |
| 147 (`0x0093`) | 46 | format | `CVersionMap` entry: `'CAttributeContainer'` -> schema 0 (`utf16` + `u32`) |
| 193 (`0x00c1`) | 38 | format | `CVersionMap` entry: `'CAttributeNamed'` -> schema 1 (`utf16` + `u32`) |
| 231 (`0x00e7`) | 40 | format | `CVersionMap` entry: `'CBackgroundImage'` -> schema 10 (`utf16` + `u32`) |
| 271 (`0x010f`) | 22 | format | `CVersionMap` entry: `'CCamera'` -> schema 5 (`utf16` + `u32`) |
| 293 (`0x0125`) | 28 | format | `CVersionMap` entry: `'CComponent'` -> schema 11 (`utf16` + `u32`) |
| 321 (`0x0141`) | 44 | format | `CVersionMap` entry: `'CComponentBehavior'` -> schema 5 (`utf16` + `u32`) |
| 365 (`0x016d`) | 48 | format | `CVersionMap` entry: `'CComponentDefinition'` -> schema 10 (`utf16` + `u32`) |
| 413 (`0x019d`) | 44 | format | `CVersionMap` entry: `'CComponentInstance'` -> schema 5 (`utf16` + `u32`) |
| 457 (`0x01c9`) | 50 | format | `CVersionMap` entry: `'CConstructionGeometry'` -> schema 0 (`utf16` + `u32`) |
| 507 (`0x01fb`) | 42 | format | `CVersionMap` entry: `'CConstructionLine'` -> schema 1 (`utf16` + `u32`) |
| 549 (`0x0225`) | 44 | format | `CVersionMap` entry: `'CConstructionPoint'` -> schema 0 (`utf16` + `u32`) |
| 593 (`0x0251`) | 20 | format | `CVersionMap` entry: `'CCurve'` -> schema 4 (`utf16` + `u32`) |
| 613 (`0x0265`) | 38 | format | `CVersionMap` entry: `'CDefinitionList'` -> schema 0 (`utf16` + `u32`) |
| 651 (`0x028b`) | 16 | format | `CVersionMap` entry: `'CDib'` -> schema 3 (`utf16` + `u32`) |
| 667 (`0x029b`) | 28 | format | `CVersionMap` entry: `'CDimension'` -> schema 1 (`utf16` + `u32`) |
| 695 (`0x02b7`) | 40 | format | `CVersionMap` entry: `'CDimensionLinear'` -> schema 6 (`utf16` + `u32`) |
| 735 (`0x02df`) | 40 | format | `CVersionMap` entry: `'CDimensionRadial'` -> schema 2 (`utf16` + `u32`) |
| 775 (`0x0307`) | 38 | format | `CVersionMap` entry: `'CDimensionStyle'` -> schema 4 (`utf16` + `u32`) |
| 813 (`0x032d`) | 38 | format | `CVersionMap` entry: `'CDrawingElement'` -> schema 9 (`utf16` + `u32`) |
| 851 (`0x0353`) | 18 | format | `CVersionMap` entry: `'CEdge'` -> schema 2 (`utf16` + `u32`) |
| 869 (`0x0365`) | 24 | format | `CVersionMap` entry: `'CEdgeUse'` -> schema 1 (`utf16` + `u32`) |
| 893 (`0x037d`) | 22 | format | `CVersionMap` entry: `'CEntity'` -> schema 5 (`utf16` + `u32`) |
| 915 (`0x0393`) | 18 | format | `CVersionMap` entry: `'CFace'` -> schema 3 (`utf16` + `u32`) |
| 933 (`0x03a5`) | 44 | format | `CVersionMap` entry: `'CFaceTextureCoords'` -> schema 4 (`utf16` + `u32`) |
| 977 (`0x03d1`) | 32 | format | `CVersionMap` entry: `'CFontManager'` -> schema 0 (`utf16` + `u32`) |
| 1009 (`0x03f1`) | 20 | format | `CVersionMap` entry: `'CGroup'` -> schema 1 (`utf16` + `u32`) |
| 1029 (`0x0405`) | 20 | format | `CVersionMap` entry: `'CImage'` -> schema 1 (`utf16` + `u32`) |
| 1049 (`0x0419`) | 20 | format | `CVersionMap` entry: `'CLayer'` -> schema 2 (`utf16` + `u32`) |
| 1069 (`0x042d`) | 34 | format | `CVersionMap` entry: `'CLayerManager'` -> schema 4 (`utf16` + `u32`) |
| 1103 (`0x044f`) | 18 | format | `CVersionMap` entry: `'CLoop'` -> schema 1 (`utf16` + `u32`) |
| 1121 (`0x0461`) | 26 | format | `CVersionMap` entry: `'CMaterial'` -> schema 12 (`utf16` + `u32`) |
| 1147 (`0x047b`) | 40 | format | `CVersionMap` entry: `'CMaterialManager'` -> schema 4 (`utf16` + `u32`) |
| 1187 (`0x04a3`) | 26 | format | `CVersionMap` entry: `'CPageList'` -> schema 1 (`utf16` + `u32`) |
| 1213 (`0x04bd`) | 30 | format | `CVersionMap` entry: `'CPolyline3d'` -> schema 0 (`utf16` + `u32`) |
| 1243 (`0x04db`) | 34 | format | `CVersionMap` entry: `'CRelationship'` -> schema 0 (`utf16` + `u32`) |
| 1277 (`0x04fd`) | 40 | format | `CVersionMap` entry: `'CRelationshipMap'` -> schema 0 (`utf16` + `u32`) |
| 1317 (`0x0525`) | 42 | format | `CVersionMap` entry: `'CRenderingOptions'` -> schema 36 (`utf16` + `u32`) |
| 1359 (`0x054f`) | 30 | format | `CVersionMap` entry: `'CSchemaFile'` -> schema 1 (`utf16` + `u32`) |
| 1389 (`0x056d`) | 42 | format | `CVersionMap` entry: `'CSchemaFilterFile'` -> schema 0 (`utf16` + `u32`) |
| 1431 (`0x0597`) | 36 | format | `CVersionMap` entry: `'CSchemaZipFile'` -> schema 1 (`utf16` + `u32`) |
| 1467 (`0x05bb`) | 34 | format | `CVersionMap` entry: `'CSectionPlane'` -> schema 2 (`utf16` + `u32`) |
| 1501 (`0x05dd`) | 30 | format | `CVersionMap` entry: `'CShadowInfo'` -> schema 7 (`utf16` + `u32`) |
| 1531 (`0x05fb`) | 22 | format | `CVersionMap` entry: `'CSkFont'` -> schema 1 (`utf16` + `u32`) |
| 1553 (`0x0611`) | 26 | format | `CVersionMap` entry: `'CSketchCS'` -> schema 0 (`utf16` + `u32`) |
| 1579 (`0x062b`) | 36 | format | `CVersionMap` entry: `'CSketchUpModel'` -> schema 26 (`utf16` + `u32`) |
| 1615 (`0x064f`) | 34 | format | `CVersionMap` entry: `'CSketchUpPage'` -> schema 1 (`utf16` + `u32`) |
| 1649 (`0x0671`) | 26 | format | `CVersionMap` entry: `'CSkpStyle'` -> schema 1 (`utf16` + `u32`) |
| 1675 (`0x068b`) | 40 | format | `CVersionMap` entry: `'CSkpStyleManager'` -> schema 2 (`utf16` + `u32`) |
| 1715 (`0x06b3`) | 18 | format | `CVersionMap` entry: `'CText'` -> schema 9 (`utf16` + `u32`) |
| 1733 (`0x06c5`) | 28 | format | `CVersionMap` entry: `'CTextStyle'` -> schema 5 (`utf16` + `u32`) |
| 1761 (`0x06e1`) | 24 | format | `CVersionMap` entry: `'CTexture'` -> schema 6 (`utf16` + `u32`) |
| 1785 (`0x06f9`) | 28 | format | `CVersionMap` entry: `'CThumbnail'` -> schema 1 (`utf16` + `u32`) |
| 1813 (`0x0715`) | 22 | format | `CVersionMap` entry: `'CVertex'` -> schema 0 (`utf16` + `u32`) |
| 1835 (`0x072b`) | 26 | format | `CVersionMap` entry: `'CViewPage'` -> schema 12 (`utf16` + `u32`) |
| 1861 (`0x0745`) | 28 | format | `CVersionMap` entry: `'CWatermark'` -> schema 1 (`utf16` + `u32`) |
| 1889 (`0x0761`) | 42 | format | `CVersionMap` entry: `'CWatermarkManager'` -> schema 2 (`utf16` + `u32`) |
| 1931 (`0x078b`) | 40 | format | `CVersionMap` closing sentinel UTF-16LE string `'End-Of-Version-Map'` |
| 1971 (`0x07b3`) | 4 | format | `CSketchUpModel` header word 0 (`u32 = 0`) |
| 1975 (`0x07b7`) | 4 | format | `CSketchUpModel` header format version/flag (`u32 = 1` in v17–v20) |
| 1979 (`0x07bb`) | 4 | format | String code page (`u32 = 1200` / `0x04B0`, Windows UTF-16LE) |
| 1983 (`0x07bf`) | 4 | format | Model preview thumbnail status (`u32 = 0` when no preview `CDib` is stored) |
| 1987 (`0x07c3`) | 8 | default | `next_persistent_id` counter (`u64 = 18`; SketchUp's initial counter after allocating blank-model entities, updated by writer) |
| 1995 (`0x07cb`) | 2 | format | Model preview thumbnail `CDib` pointer (`u16 = 0`, null MFC object ref) |
| 1997 (`0x07cd`) | 4 | format | Post-thumbnail model header flag (`u32 = 1`) |
| 2001 (`0x07d1`) | 7 | format | Reserved pre-behavior model header bytes (7 zero bytes; unexposed by C API) |
| 2008 (`0x07d8`) | 4 | default | Root `CComponentBehavior.no_scale_mask` (`u32 = 0`; written by C++ and Rust `DocumentOptions`) |
| 2012 (`0x07dc`) | 4 | default | Model description UTF-16LE string `''` (`SUModelGetDescription`) |
| 2016 (`0x07e0`) | 4 | format | `SUOptionsManager` table format version (`u32 = 3`) |
| 2020 (`0x07e4`) | 4 | format | `SUOptionsManager` provider count (`u32 = 4`) |
| 2024 (`0x07e8`) | 28 | format | Options provider 0 name `'NamedOptions'` |
| 2052 (`0x0804`) | 4 | format | `NamedOptions` end-of-entries empty key `''` |
| 2056 (`0x0808`) | 26 | format | Options provider 1 name `'PageOptions'` |
| 2082 (`0x0822`) | 34 | default | `PageOptions['ShowTransition']`: tag `0x07` (`bool`), default `True` (1) |
| 2116 (`0x0844`) | 41 | default | `PageOptions['TransitionTime']`: tag `0x06` (`f64`), default `1.5` s |
| 2157 (`0x086d`) | 4 | format | `PageOptions` end-of-entries empty key `''` |
| 2161 (`0x0871`) | 36 | format | Options provider 2 name `'SlideshowOptions'` |
| 2197 (`0x0895`) | 32 | default | `SlideshowOptions['LoopSlideshow']`: tag `0x07` (`bool`), default `True` (1) |
| 2229 (`0x08b5`) | 31 | default | `SlideshowOptions['SlideTime']`: tag `0x06` (`f64`), default `1.0` s |
| 2260 (`0x08d4`) | 4 | format | `SlideshowOptions` end-of-entries empty key `''` |
| 2264 (`0x08d8`) | 28 | format | Options provider 3 name `'UnitsOptions'` |
| 2292 (`0x08f4`) | 39 | default | `UnitsOptions['LengthPrecision']`: tag `0x04` (`i32`), default 4 (value at `@2327`) |
| 2331 (`0x091b`) | 33 | default | `UnitsOptions['LengthFormat']`: tag `0x04` (`i32`), default 1 = Architectural (value at `@2360`) |
| 2364 (`0x093c`) | 29 | default | `UnitsOptions['LengthUnit']`: tag `0x04` (`i32`), default 0 = Inches (value at `@2389`) |
| 2393 (`0x0959`) | 40 | default | `UnitsOptions['LengthSnapEnabled']`: tag `0x07` (`bool`), default `True` (1) |
| 2433 (`0x0981`) | 45 | default | `UnitsOptions['LengthSnapLength']`: tag `0x06` (`f64`), default `0.0625` in (1/16") |
| 2478 (`0x09ae`) | 37 | default | `UnitsOptions['AnglePrecision']`: tag `0x04` (`i32`), default 1 |
| 2515 (`0x09d3`) | 38 | default | `UnitsOptions['AngleSnapEnabled']`: tag `0x07` (`bool`), default `True` (1) |
| 2553 (`0x09f9`) | 31 | default | `UnitsOptions['SnapAngle']`: tag `0x06` (`f64`), default `15.0` deg |
| 2584 (`0x0a18`) | 46 | default | `UnitsOptions['SuppressUnitsDisplay']`: tag `0x07` (`bool`), default `False` (0) |
| 2630 (`0x0a46`) | 38 | default | `UnitsOptions['ForceInchDisplay']`: tag `0x07` (`bool`), default `False` (0) |
| 2668 (`0x0a6c`) | 25 | default | `UnitsOptions['AreaUnit']`: tag `0x04` (`i32`), default 0 = sq in (value at `@2689`) |
| 2693 (`0x0a85`) | 29 | default | `UnitsOptions['VolumeUnit']`: tag `0x04` (`i32`), default 0 = cu in (value at `@2718`) |
| 2722 (`0x0aa2`) | 35 | default | `UnitsOptions['AreaPrecision']`: tag `0x04` (`i32`), default 2 |
| 2757 (`0x0ac5`) | 39 | default | `UnitsOptions['VolumePrecision']`: tag `0x04` (`i32`), default 2 |
| 2796 (`0x0aec`) | 4 | format | `UnitsOptions` end-of-entries empty key `''` |
| 2800 (`0x0af0`) | 25 | format | `CAttributeContainer` MFC class declaration (`0xFFFF`, schema 0, ASCII `'CAttributeContainer'`; allocates class slot 3 and `ModelProperties container` object slot 4) |
| 2825 (`0x0b09`) | 3 | format | `CAttributeContainer` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 2828 (`0x0b0c`) | 21 | format | `CAttributeNamed` MFC class declaration (`0xFFFF`, schema 1, ASCII `'CAttributeNamed'`; allocates class slot 5 and `ModelProperties dictionary` object slot 6) |
| 2849 (`0x0b21`) | 3 | format | `CAttributeNamed` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 2852 (`0x0b24`) | 4 | format | `CAttributeNamed` header word (`u32 = 0`) |
| 2856 (`0x0b28`) | 34 | format | Internal model-info dictionary name `'ModelProperties'` |
| 2890 (`0x0b4a`) | 30 | default | `ModelProperties['IsClassified']`: tag `0x07` (`bool`), default `False` (0) |
| 2920 (`0x0b68`) | 24 | default | `ModelProperties['IsDynamic']`: tag `0x07` (`bool`), default `False` (0) |
| 2944 (`0x0b80`) | 18 | default | `ModelProperties['IsLive']`: tag `0x07` (`bool`), default `False` (0) |
| 2962 (`0x0b92`) | 4 | format | `ModelProperties` end-of-entries empty key `''` |
| 2966 (`0x0b96`) | 2 | format | `CAttributeContainer` children list null terminator (`u16 = 0`) |
| 2968 (`0x0b98`) | 6 | format | Three null `u16` fields (6 zero bytes) before model `CCamera` |
| 2974 (`0x0b9e`) | 13 | format | `CCamera` MFC class declaration (`0xFFFF`, schema 5, ASCII `'CCamera'`; allocates class slot 7 and `model CCamera` object slot 8) |
| 2987 (`0x0bab`) | 24 | default | Model `CCamera.eye` (`3 x f64 = (0.0, 0.0, 500.0)` in) |
| 3011 (`0x0bc3`) | 24 | default | Model `CCamera.target` (`3 x f64 = (0.0, 0.0, 0.0)` in) |
| 3035 (`0x0bdb`) | 24 | default | Model `CCamera.up` (`3 x f64 = (0.0, 1.0, 0.0)`) |
| 3059 (`0x0bf3`) | 16 | default | Model `CCamera` near/far clip distances (`2 x f64 = (1.0, 1000.0)` in) |
| 3075 (`0x0c03`) | 1 | default | Model `CCamera.perspective` (`u8 = 1`, perspective projection) |
| 3076 (`0x0c04`) | 8 | default | Model `CCamera.fov` (`f64 = 30.0` deg) |
| 3084 (`0x0c0c`) | 8 | default | Model `CCamera.ortho_height` (`f64 = 133.0` in) |
| 3092 (`0x0c14`) | 24 | default | Model `CCamera` frustum/window shift parameters (`3 x f64 = (0.0, 0.0, 0.0)`) |
| 3116 (`0x0c2c`) | 8 | default | Model `CCamera.aspect_ratio` (`f64 = 0.0`, unconstrained) |
| 3124 (`0x0c34`) | 2 | default | Model `CCamera.fov_is_height` (`u8 = 1`) + camera flag (`u8 = 0`) |
| 3126 (`0x0c36`) | 4 | default | Model `CCamera.description` UTF-16LE string `''` |
| 3130 (`0x0c3a`) | 8 | default | Model `CCamera.image_width` (`f64 = 0.0` mm) |
| 3138 (`0x0c42`) | 1 | default | Model `CCamera.is_2d` (`u8 = 0`, 3D camera) |
| 3139 (`0x0c43`) | 8 | default | Model `CCamera.scale_2d` (`f64 = 1.0`) |
| 3147 (`0x0c4b`) | 16 | default | Model `CCamera.center_2d` (`2 x f64 = (0.0, 0.0)`) |
| 3163 (`0x0c5b`) | 3 | format | `CRenderingOptions` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3166 (`0x0c5e`) | 4 | default | `CRenderingOptions.RenderMode` (`u32 = 2`, ShadedWithTextures; style item 2001) |
| 3170 (`0x0c62`) | 1 | default | `CRenderingOptions.ModelTransparency` / X-ray (`u8 = 0`; style item 2004) |
| 3171 (`0x0c63`) | 1 | default | `CRenderingOptions.MaterialTransparency` (`u8 = 1`; style item 2005) |
| 3172 (`0x0c64`) | 1 | default | `CRenderingOptions.JitterEdges` (`u8 = 0`; style item 1012) |
| 3173 (`0x0c65`) | 4 | default | `CRenderingOptions.EdgeDisplayMode` (`u32 = 1`, show edges; style item 1000) |
| 3177 (`0x0c69`) | 4 | default | `CRenderingOptions.BackgroundColor` raw RGBA `(0, 0, 0, 0)` (style item 4000) |
| 3181 (`0x0c6d`) | 4 | default | `CRenderingOptions.ForegroundColor` raw RGBA `(0, 0, 0, 0)` (style item 1014) |
| 3185 (`0x0c71`) | 4 | default | `CRenderingOptions.HighlightColor` RGBA `(255, 255, 0, 255)` (style item 7000) |
| 3189 (`0x0c75`) | 4 | default | `CRenderingOptions.ConstructionColor` RGBA `(128, 128, 128, 255)` (style item 7002) |
| 3193 (`0x0c79`) | 1 | default | `CRenderingOptions` `DisplayShadows` (`u8 = 0`) |
| 3194 (`0x0c7a`) | 1 | default | `CRenderingOptions.DisplayInstanceAxes` (`u8 = 0`) |
| 3195 (`0x0c7b`) | 1 | default | `CRenderingOptions.DisplayColorByLayer` / Color by Tag (`u8 = 0`; style item 7011) |
| 3196 (`0x0c7c`) | 1 | default | `CRenderingOptions.Texture` (`u8 = 1`; style item 2007) |
| 3197 (`0x0c7d`) | 4 | default | `CRenderingOptions.EdgeColorMode` (`u32 = 1`, all same; style item 1002) |
| 3201 (`0x0c81`) | 1 | default | `CRenderingOptions.ExtendLines` (`u8 = 0`; style item 1004) |
| 3202 (`0x0c82`) | 4 | default | `CRenderingOptions.LineExtension` (`u32 = 8` px; style item 1005) |
| 3206 (`0x0c86`) | 1 | default | `CRenderingOptions.DrawSilhouettes` / Profiles (`u8 = 1`; style item 1006) |
| 3207 (`0x0c87`) | 4 | default | `CRenderingOptions.SilhouetteWidth` (`u32 = 3` px; style item 1007) |
| 3211 (`0x0c8b`) | 1 | default | `CRenderingOptions.DrawDepthQue` (`u8 = 0`; style item 1008) |
| 3212 (`0x0c8c`) | 4 | default | `CRenderingOptions.DepthQueWidth` (`u32 = 4` px; style item 1009) |
| 3216 (`0x0c90`) | 1 | default | `CRenderingOptions.DrawLineEnds` / Endpoints (`u8 = 0`; style item 1010) |
| 3217 (`0x0c91`) | 4 | default | `CRenderingOptions.LineEndWidth` (`u32 = 5` px; style item 1011) |
| 3221 (`0x0c95`) | 1 | default | `CRenderingOptions.DrawProfilesOnly` (`u8 = 0`) |
| 3222 (`0x0c96`) | 1 | default | `CRenderingOptions.DrawHidden` (`u8 = 0`; style item 7010) |
| 3223 (`0x0c97`) | 4 | format | `CRenderingOptions` reserved word (`u32 = 0`; unexposed by `SURenderingOptions`) |
| 3227 (`0x0c9b`) | 4 | default | `CRenderingOptions.FaceFrontColor` RGBA `(225, 225, 200, 255)` (style item 2002) |
| 3231 (`0x0c9f`) | 4 | default | `CRenderingOptions.FaceBackColor` RGBA `(128, 128, 200, 255)` (style item 2003) |
| 3235 (`0x0ca3`) | 8 | default | `CRenderingOptions.InactiveFade` (`f64 = 0.75`) |
| 3243 (`0x0cab`) | 8 | default | `CRenderingOptions.InstanceFade` (`f64 = 0.50`) |
| 3251 (`0x0cb3`) | 1 | default | `CRenderingOptions.InactiveHidden` (`u8 = 0`) |
| 3252 (`0x0cb4`) | 1 | default | `CRenderingOptions.InstanceHidden` (`u8 = 0`) |
| 3253 (`0x0cb5`) | 1 | default | `CRenderingOptions.DisplayFog` (`u8 = 0`) |
| 3254 (`0x0cb6`) | 4 | default | `CRenderingOptions.FogColor` raw RGBA `(0, 0, 0, 0)` |
| 3258 (`0x0cba`) | 1 | default | `CRenderingOptions.FogUseBkColor` (`u8 = 1`) |
| 3259 (`0x0cbb`) | 8 | default | `CRenderingOptions.FogStartDist` (`f64 = -1.0`, auto) |
| 3267 (`0x0cc3`) | 8 | default | `CRenderingOptions.FogEndDist` (`f64 = -1.0`, auto) |
| 3275 (`0x0ccb`) | 4 | default | `CRenderingOptions` fog/stroke mode constant (`u32 = 1`; unexposed by `SURenderingOptions`) |
| 3279 (`0x0ccf`) | 4 | default | `CRenderingOptions.EdgeType` (`u32 = 0`, standard strokes; style item 1001) |
| 3283 (`0x0cd3`) | 1 | default | `CRenderingOptions.DisplaySketchAxes` (`u8 = 1`; style item 7008) |
| 3284 (`0x0cd4`) | 1 | default | `CRenderingOptions.DisplayText` (`u8 = 1`) |
| 3285 (`0x0cd5`) | 1 | default | `CRenderingOptions.DisplayDims` (`u8 = 1`) |
| 3286 (`0x0cd6`) | 1 | default | `CRenderingOptions.HideConstructionGeometry` (`u8 = 0`; style item 7012) |
| 3287 (`0x0cd7`) | 4 | default | `CRenderingOptions.SkyColor` RGBA `(140, 165, 255, 255)` (style item 4001) |
| 3291 (`0x0cdb`) | 4 | default | `CRenderingOptions.HorizonColor` RGBA `(0, 0, 0, 0)` (style item 4007) |
| 3295 (`0x0cdf`) | 4 | default | `CRenderingOptions.GroundColor` RGBA `(63, 147, 63, 255)` (style item 4003) |
| 3299 (`0x0ce3`) | 1 | default | `CRenderingOptions.DrawHorizon` (`u8 = 0`; style item 4002) |
| 3300 (`0x0ce4`) | 1 | default | `CRenderingOptions.DrawGround` (`u8 = 0`; style item 4004) |
| 3301 (`0x0ce5`) | 1 | default | `CRenderingOptions.DrawUnderground` (`u8 = 1`; style item 4006) |
| 3302 (`0x0ce6`) | 4 | default | `CRenderingOptions.GroundTransparency` (`u32 = 50`; style item 4005) |
| 3306 (`0x0cea`) | 4 | default | `CRenderingOptions.SectionActiveColor` RGBA `(102, 110, 141, 255)` (style item 7003) |
| 3310 (`0x0cee`) | 4 | default | `CRenderingOptions.SectionInactiveColor` RGBA `(192, 192, 192, 255)` (style item 7004) |
| 3314 (`0x0cf2`) | 4 | default | `CRenderingOptions.SectionDefaultCutColor` RGBA `(255, 0, 0, 255)` (style item 7005) |
| 3318 (`0x0cf6`) | 4 | default | `CRenderingOptions.SectionCutWidth` (`u32 = 4`; style item 7014) |
| 3322 (`0x0cfa`) | 4 | default | `CRenderingOptions` section display bitmask (`u32 = 3` = planes 1 | cuts 2; style item 7013) |
| 3326 (`0x0cfe`) | 4 | default | `CRenderingOptions.TransparencySort` (`u32 = 0`, faster; style item 2006) |
| 3330 (`0x0d02`) | 1 | default | `CRenderingOptions` edge halo flag (`u8 = 1`; style item 1016) |
| 3331 (`0x0d03`) | 8 | default | `CRenderingOptions` crease/halo angle (`f64 = 0.34906585039886595` rad = 20 deg) |
| 3339 (`0x0d0b`) | 1 | default | `CRenderingOptions.ShowViewName` (`u8 = 1`) |
| 3340 (`0x0d0c`) | 4 | default | `CRenderingOptions.LockedColor` RGBA `(255, 0, 0, 255)` (style item 7001) |
| 3344 (`0x0d10`) | 1 | default | `CRenderingOptions.DisplayWatermarks` (`u8 = 1`; style item 5000) |
| 3345 (`0x0d11`) | 8 | default | `CRenderingOptions` transparency factor (`f64 = 0.65`; style item 2008) |
| 3353 (`0x0d19`) | 1 | default | `CRenderingOptions.DrawBackEdges` (`u8 = 0`; style item 1015) |
| 3354 (`0x0d1a`) | 1 | default | `CRenderingOptions` Match Photo background image (`u8 = 1`; style item 8000) |
| 3355 (`0x0d1b`) | 8 | default | `CRenderingOptions` Match Photo background opacity (`f64 = 1.0`; style item 8001) |
| 3363 (`0x0d23`) | 1 | default | `CRenderingOptions` Match Photo foreground photo (`u8 = 1`; style item 8002) |
| 3364 (`0x0d24`) | 8 | default | `CRenderingOptions` Match Photo foreground opacity (`f64 = 0.8`; style item 8003) |
| 3372 (`0x0d2c`) | 8 | format | Post-`CRenderingOptions` model words (`u32 = 0`, `u32 = 1`) |
| 3380 (`0x0d34`) | 3 | format | `CSketchUpModel` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3383 (`0x0d37`) | 5 | format | `CSketchUpModel` `CComponent` base flags (`00 00 00 01 01`, matching `CComponentDefinition` base) |
| 3388 (`0x0d3c`) | 3 | format | `CMaterialManager` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3391 (`0x0d3f`) | 5 | format | `CMaterialManager` header pad (5 zero bytes) |
| 3396 (`0x0d44`) | 4 | format | `CMaterialManager` `material_count` (`u32 = 0`; materials splice after `@3400` starting at slot 9) |
| 3400 (`0x0d48`) | 2 | format | `CMaterialManager` active material pointer (`u16 = 0`, null = default unpainted) |
| 3402 (`0x0d4a`) | 3 | format | `CLayerManager` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3405 (`0x0d4d`) | 4 | format | `CLayerManager` `layer_count` (`u32 = 1`) |
| 3409 (`0x0d51`) | 12 | format | `CLayer` MFC class declaration (`0xFFFF`, schema 3, ASCII `'CLayer'`; allocates class slot 9 and `Layer0` object slot 10) |
| 3421 (`0x0d5d`) | 4 | default | `Layer0` `CEntity` preamble: null attrs (`u16 = 0`) + persistent ID 4 (`01 04`) |
| 3425 (`0x0d61`) | 16 | format | Default untagged layer name `'Layer0'` |
| 3441 (`0x0d71`) | 5 | default | `Layer0` `hidden = 0` (`u8`), flags (`u16 = 0`), embedded material persistent ID 5 (`01 05`) |
| 3446 (`0x0d76`) | 28 | format | `Layer0` internal colour-material name `'Layer_Layer0'` |
| 3474 (`0x0d92`) | 2 | format | `Layer0` embedded material flag (`u16 = 256`) |
| 3476 (`0x0d94`) | 4 | default | `Layer0` default tag colour RGBA `(255, 84, 84, 255)` |
| 3480 (`0x0d98`) | 4 | format | `Layer0` empty texture path `''` |
| 3484 (`0x0d9c`) | 21 | default | `Layer0` 21-byte material tail (`colorize = (0, 0)`, `alpha = 0.5`, `use_opacity = 0`, `trailer = 0`; user layers splice after `@3505`) |
| 3505 (`0x0db1`) | 2 | format | Active-layer object back-ref (`u16 = 10` -> `Layer0`) |
| 3507 (`0x0db3`) | 4 | format | `CDefinitionList` `definition_count` (`u32 = 0`) |
| 3511 (`0x0db7`) | 4 | format | Root entity list `root_entity_count` (`u32 = 0`; document tail starts at `@3515`) |
| 3515 (`0x0dbb`) | 6 | format | Root `CComponent` closing words (`u32 = 0` relationship count + `u16 = 0`) |
| 3521 (`0x0dc1`) | 3 | format | `CShadowInfo` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3524 (`0x0dc4`) | 4 | default | `CShadowInfo.ShadowTime` (`u32 = 1782048600` = `2026-06-21T13:30:00Z`, Unix `time_t`) |
| 3528 (`0x0dc8`) | 1 | default | `CShadowInfo.DaylightSavings` (`u8 = 0`) |
| 3529 (`0x0dc9`) | 28 | default | `CShadowInfo.City` UTF-16LE string `'Boulder (CO)'` |
| 3557 (`0x0de5`) | 10 | default | `CShadowInfo.Country` UTF-16LE string `'USA'` |
| 3567 (`0x0def`) | 8 | default | `CShadowInfo.Longitude` (`f64 = -105.283` deg) |
| 3575 (`0x0df7`) | 8 | default | `CShadowInfo.Latitude` (`f64 = 40.017` deg) |
| 3583 (`0x0dff`) | 8 | default | `CShadowInfo.TZOffset` (`f64 = -7.0` h) |
| 3591 (`0x0e07`) | 24 | default | `CShadowInfo` `NorthAngle` (`0.0`) + north direction `(1.0, 0.0)` (`3 x f64`) |
| 3615 (`0x0e1f`) | 4 | default | `CShadowInfo`: `DisplayNorth=0`, `DisplayOnAllFaces=1`, `DisplayOnGroundPlane=1`, `EdgesCastShadows=0` (`4 x u8`) |
| 3619 (`0x0e23`) | 8 | default | `CShadowInfo.Light` (`u32 = 80`) and `CShadowInfo.Dark` (`u32 = 20`) |
| 3627 (`0x0e2b`) | 1 | default | `CShadowInfo.UseSunForAllShading` (`u8 = 0`) |
| 3628 (`0x0e2c`) | 3 | format | `CPageList` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3631 (`0x0e2f`) | 4 | format | `CPageList` `page_count` (`u32 = 0`) |
| 3635 (`0x0e33`) | 2 | format | `CPageList` `selected_page_ref` (`u16 = 0`, null) |
| 3637 (`0x0e35`) | 3 | format | `CPageList` closing bytes (3 zero bytes) |
| 3640 (`0x0e38`) | 3 | format | `CSketchCS` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3643 (`0x0e3b`) | 7 | format | `CSketchCS` (model axes) flags (`01 01`) + 5 zero header bytes |
| 3650 (`0x0e42`) | 24 | default | `CSketchCS.origin` (`3 x f64 = (0.0, 0.0, 0.0)` in) |
| 3674 (`0x0e5a`) | 24 | default | `CSketchCS.x_axis` (`3 x f64 = (1.0, 0.0, 0.0)`) |
| 3698 (`0x0e72`) | 24 | default | `CSketchCS.y_axis` (`3 x f64 = (0.0, 1.0, 0.0)`) |
| 3722 (`0x0e8a`) | 24 | default | `CSketchCS.z_axis` (`3 x f64 = (0.0, 0.0, 1.0)`) |
| 3746 (`0x0ea2`) | 68 | format | Empty post-axes model sub-tables (68 zero bytes before `CDimensionStyle`) |
| 3814 (`0x0ee6`) | 3 | format | `CDimensionStyle` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3817 (`0x0ee9`) | 13 | format | `CSkFont` MFC class declaration (`0xFFFF`, schema 1, ASCII `'CSkFont'`; allocates class slot 11 and `Arial font` object slot 12) |
| 3830 (`0x0ef6`) | 4 | default | `Arial` `CSkFont` preamble: null attrs (`u16 = 0`) + persistent ID 2 (`01 02`) |
| 3834 (`0x0efa`) | 14 | default | `CDimensionStyle` font face name `'Arial'` |
| 3848 (`0x0f08`) | 15 | default | `Arial` `CSkFont` metrics: `flags = 0` (`u16`), `point_size = 12` (`u32`), `use_world_size = 0` (`u8`), `world_size = 1.0` (`f64`) |
| 3863 (`0x0f17`) | 22 | default | `CDimensionStyle`: `has_3d=0`, `flag=1`, `ext_offset=5`, `ext_overshoot=10`, `line_weight=1`, `arrow_type=3` (closed), `arrow_size=10` |
| 3885 (`0x0f2d`) | 24 | default | `CDimensionStyle`: `hl_nonassoc=0`, `hl_color=(255,0,0,255)`, `show_radial_prefix=1`, `hide_out_of_plane=0` (`0.6`), `hide_small=0` (`10.0`) |
| 3909 (`0x0f45`) | 12 | default | `CDimensionStyle`: `color=(64,64,64,255)`, `use_text_color=0`, `align_flags=0` (3B), `show_units=1`, trailer pad (3B) |
| 3921 (`0x0f51`) | 3 | format | `CTextStyle` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3924 (`0x0f54`) | 2 | format | `CTextStyle` inline `CSkFont` short class-ref (`0x8000 | 11` -> slot 13; tail ref `+409`) |
| 3926 (`0x0f56`) | 4 | default | `Tahoma` `CSkFont` preamble: null attrs (`u16 = 0`) + persistent ID 3 (`01 03`) |
| 3930 (`0x0f5a`) | 16 | default | `CTextStyle` font face name `'Tahoma'` |
| 3946 (`0x0f6a`) | 15 | default | `Tahoma` `CSkFont` metrics: `flags = 0` (`u16`), `point_size = 12` (`u32`), `use_world_size = 0` (`u8`), `world_size = 1.0` (`f64`) |
| 3961 (`0x0f79`) | 22 | default | `CTextStyle`: `leader_type=2` (pushpin), `display_leader=1`, `flag=0`, `arrow_type=3` (closed), `vis=1`, text & leader RGBA `(0,0,0,255)` |
| 3983 (`0x0f8f`) | 2 | format | `CTextStyle` font back-ref (`u16 = 13` -> `Tahoma`; tail ref `+468`) |
| 3985 (`0x0f91`) | 3 | format | `CFontManager` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 3988 (`0x0f94`) | 4 | format | `CFontManager` `font_count` (`u32 = 2`) |
| 3992 (`0x0f98`) | 2 | format | `CFontManager.fonts[0]` back-ref (`u16 = 12` -> `Arial`; tail ref `+477`) |
| 3994 (`0x0f9a`) | 2 | format | `CFontManager.fonts[1]` back-ref (`u16 = 13` -> `Tahoma`; tail ref `+479`) |
| 3996 (`0x0f9c`) | 2 | format | `CFontManager` closing word (`u16 = 0`) |
| 3998 (`0x0f9e`) | 3 | format | `CSkpStyleManager` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 4001 (`0x0fa1`) | 4 | format | `CSkpStyleManager` `saved_style_count` (`u32 = 1`) |
| 4005 (`0x0fa5`) | 15 | format | `CSkpStyle` MFC class declaration (`0xFFFF`, schema 2, ASCII `'CSkpStyle'`; allocates class slot 14 and `saved Style` object slot 15) |
| 4020 (`0x0fb4`) | 4 | default | Saved `CSkpStyle` preamble: null attrs (`u16 = 0`) + persistent ID 1 (`01 01`) |
| 4024 (`0x0fb8`) | 16 | default | Saved `CSkpStyle` UUID (`94fee66b-2dce-4b79-ada7-4be56f9266a2`, Windows LE; tail `+509`) |
| 4040 (`0x0fc8`) | 4 | default | Saved `CSkpStyle` source `.style` path `''` |
| 4044 (`0x0fcc`) | 4 | format | Saved `CSkpStyle` format version (`u32 = 3`) |
| 4048 (`0x0fd0`) | 14 | default | Saved `CSkpStyle` name `'Style'` |
| 4062 (`0x0fde`) | 4 | default | Saved `CSkpStyle` description `''` |
| 4066 (`0x0fe2`) | 4 | format | Saved `CSkpStyle` property count (`u32 = 56`) |
| 4070 (`0x0fe6`) | 16 | default | Saved `CSkpStyle` property 1000: `u32 = 1` (`0x00000001`) |
| 4086 (`0x0ff6`) | 16 | default | Saved `CSkpStyle` property 1001: `u32 = 0` (`0x00000000`) |
| 4102 (`0x1006`) | 16 | default | Saved `CSkpStyle` property 1002: `u32 = 1` (`0x00000001`) |
| 4118 (`0x1016`) | 13 | default | Saved `CSkpStyle` property 1004: `bool = False` |
| 4131 (`0x1023`) | 16 | default | Saved `CSkpStyle` property 1005: `u32 = 8` (`0x00000008`) |
| 4147 (`0x1033`) | 13 | default | Saved `CSkpStyle` property 1006: `bool = True` |
| 4160 (`0x1040`) | 16 | default | Saved `CSkpStyle` property 1007: `u32 = 3` (`0x00000003`) |
| 4176 (`0x1050`) | 13 | default | Saved `CSkpStyle` property 1008: `bool = False` |
| 4189 (`0x105d`) | 16 | default | Saved `CSkpStyle` property 1009: `u32 = 4` (`0x00000004`) |
| 4205 (`0x106d`) | 13 | default | Saved `CSkpStyle` property 1010: `bool = False` |
| 4218 (`0x107a`) | 16 | default | Saved `CSkpStyle` property 1011: `u32 = 5` (`0x00000005`) |
| 4234 (`0x108a`) | 13 | default | Saved `CSkpStyle` property 1012: `bool = False` |
| 4247 (`0x1097`) | 16 | default | Saved `CSkpStyle` property 1014: `u32 = 4278190080` (`0xff000000`) |
| 4263 (`0x10a7`) | 13 | default | Saved `CSkpStyle` property 1015: `bool = False` |
| 4276 (`0x10b4`) | 13 | default | Saved `CSkpStyle` property 1016: `bool = True` |
| 4289 (`0x10c1`) | 16 | default | Saved `CSkpStyle` property 2001: `u32 = 2` (`0x00000002`) |
| 4305 (`0x10d1`) | 16 | default | Saved `CSkpStyle` property 2002: `u32 = 4291355105` (`0xffc8e1e1`) |
| 4321 (`0x10e1`) | 16 | default | Saved `CSkpStyle` property 2003: `u32 = 4291330176` (`0xffc88080`) |
| 4337 (`0x10f1`) | 13 | default | Saved `CSkpStyle` property 2004: `bool = False` |
| 4350 (`0x10fe`) | 13 | default | Saved `CSkpStyle` property 2005: `bool = True` |
| 4363 (`0x110b`) | 16 | default | Saved `CSkpStyle` property 2006: `u32 = 0` (`0x00000000`) |
| 4379 (`0x111b`) | 13 | default | Saved `CSkpStyle` property 2007: `bool = True` |
| 4392 (`0x1128`) | 20 | default | Saved `CSkpStyle` property 2008: `f64 = 0.65` |
| 4412 (`0x113c`) | 16 | default | Saved `CSkpStyle` property 4000: `u32 = 4294967295` (`0xffffffff`) |
| 4428 (`0x114c`) | 16 | default | Saved `CSkpStyle` property 4001: `u32 = 4294944140` (`0xffffa58c`) |
| 4444 (`0x115c`) | 13 | default | Saved `CSkpStyle` property 4002: `bool = False` |
| 4457 (`0x1169`) | 16 | default | Saved `CSkpStyle` property 4003: `u32 = 4282356543` (`0xff3f933f`) |
| 4473 (`0x1179`) | 13 | default | Saved `CSkpStyle` property 4004: `bool = False` |
| 4486 (`0x1186`) | 16 | default | Saved `CSkpStyle` property 4005: `u32 = 50` (`0x00000032`) |
| 4502 (`0x1196`) | 13 | default | Saved `CSkpStyle` property 4006: `bool = True` |
| 4515 (`0x11a3`) | 16 | default | Saved `CSkpStyle` property 4007: `u32 = 0` (`0x00000000`) |
| 4531 (`0x11b3`) | 13 | default | Saved `CSkpStyle` property 5000: `bool = True` |
| 4544 (`0x11c0`) | 8 | default | Saved `CSkpStyle` property 5001: `count = 0` (empty watermark list) |
| 4552 (`0x11c8`) | 16 | default | Saved `CSkpStyle` property 7000: `u32 = 4278255615` (`0xff00ffff`) |
| 4568 (`0x11d8`) | 16 | default | Saved `CSkpStyle` property 7001: `u32 = 4278190335` (`0xff0000ff`) |
| 4584 (`0x11e8`) | 16 | default | Saved `CSkpStyle` property 7002: `u32 = 4286611584` (`0xff808080`) |
| 4600 (`0x11f8`) | 16 | default | Saved `CSkpStyle` property 7003: `u32 = 4287458918` (`0xff8d6e66`) |
| 4616 (`0x1208`) | 16 | default | Saved `CSkpStyle` property 7004: `u32 = 4290822336` (`0xffc0c0c0`) |
| 4632 (`0x1218`) | 16 | default | Saved `CSkpStyle` property 7005: `u32 = 4278190335` (`0xff0000ff`) |
| 4648 (`0x1228`) | 13 | default | Saved `CSkpStyle` property 7008: `bool = True` |
| 4661 (`0x1235`) | 13 | default | Saved `CSkpStyle` property 7010: `bool = False` |
| 4674 (`0x1242`) | 13 | default | Saved `CSkpStyle` property 7011: `bool = False` |
| 4687 (`0x124f`) | 13 | default | Saved `CSkpStyle` property 7012: `bool = False` |
| 4700 (`0x125c`) | 16 | default | Saved `CSkpStyle` property 7013: `u32 = 3` (`0x00000003`) |
| 4716 (`0x126c`) | 16 | default | Saved `CSkpStyle` property 7014: `u32 = 4` (`0x00000004`) |
| 4732 (`0x127c`) | 13 | default | Saved `CSkpStyle` property 7015: `bool = False` |
| 4745 (`0x1289`) | 16 | default | Saved `CSkpStyle` property 7016: `u32 = 4278190080` (`0xff000000`) |
| 4761 (`0x1299`) | 13 | default | Saved `CSkpStyle` property 7017: `bool = False` |
| 4774 (`0x12a6`) | 13 | default | Saved `CSkpStyle` property 7018: `bool = False` |
| 4787 (`0x12b3`) | 13 | default | Saved `CSkpStyle` property 8000: `bool = True` |
| 4800 (`0x12c0`) | 20 | default | Saved `CSkpStyle` property 8001: `f64 = 1.0` |
| 4820 (`0x12d4`) | 13 | default | Saved `CSkpStyle` property 8002: `bool = True` |
| 4833 (`0x12e1`) | 20 | default | Saved `CSkpStyle` property 8003: `f64 = 0.8` |
| 4853 (`0x12f5`) | 13 | default | Saved `CSkpStyle` property 8100: `bool = False` |
| 4866 (`0x1302`) | 16 | default | Saved `CSkpStyle` property 8102: `f32 = 10.0` |
| 4882 (`0x1312`) | 16 | default | Saved `CSkpStyle` property 8103: `f32 = 0.0` |
| 4898 (`0x1322`) | 2 | format | `CSkpStyleManager` selected saved style back-ref (`u16 = 15`; tail ref `+1383`) |
| 4900 (`0x1324`) | 2 | format | Working `CSkpStyle` short class-ref (`0x8000 | 14` -> slot 16; tail ref `+1385`) |
| 4902 (`0x1326`) | 3 | format | `Working CSkpStyle` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 4905 (`0x1329`) | 16 | default | Working `CSkpStyle` UUID (`667aac7a-d15a-409d-9d21-24ffa8045e61`, Windows LE; tail `+1390`) |
| 4921 (`0x1339`) | 4 | default | Working `CSkpStyle` source `.style` path `''` |
| 4925 (`0x133d`) | 4 | format | Working `CSkpStyle` format version (`u32 = 3`) |
| 4929 (`0x1341`) | 14 | default | Working `CSkpStyle` name `'Style'` |
| 4943 (`0x134f`) | 4 | default | Working `CSkpStyle` description `''` |
| 4947 (`0x1353`) | 4 | format | Working `CSkpStyle` property count (`u32 = 56`) |
| 4951 (`0x1357`) | 16 | default | Working `CSkpStyle` property 1000: `u32 = 1` (`0x00000001`) |
| 4967 (`0x1367`) | 16 | default | Working `CSkpStyle` property 1001: `u32 = 0` (`0x00000000`) |
| 4983 (`0x1377`) | 16 | default | Working `CSkpStyle` property 1002: `u32 = 1` (`0x00000001`) |
| 4999 (`0x1387`) | 13 | default | Working `CSkpStyle` property 1004: `bool = False` |
| 5012 (`0x1394`) | 16 | default | Working `CSkpStyle` property 1005: `u32 = 8` (`0x00000008`) |
| 5028 (`0x13a4`) | 13 | default | Working `CSkpStyle` property 1006: `bool = True` |
| 5041 (`0x13b1`) | 16 | default | Working `CSkpStyle` property 1007: `u32 = 3` (`0x00000003`) |
| 5057 (`0x13c1`) | 13 | default | Working `CSkpStyle` property 1008: `bool = False` |
| 5070 (`0x13ce`) | 16 | default | Working `CSkpStyle` property 1009: `u32 = 4` (`0x00000004`) |
| 5086 (`0x13de`) | 13 | default | Working `CSkpStyle` property 1010: `bool = False` |
| 5099 (`0x13eb`) | 16 | default | Working `CSkpStyle` property 1011: `u32 = 5` (`0x00000005`) |
| 5115 (`0x13fb`) | 13 | default | Working `CSkpStyle` property 1012: `bool = False` |
| 5128 (`0x1408`) | 16 | default | Working `CSkpStyle` property 1014: `u32 = 4278190080` (`0xff000000`) |
| 5144 (`0x1418`) | 13 | default | Working `CSkpStyle` property 1015: `bool = False` |
| 5157 (`0x1425`) | 13 | default | Working `CSkpStyle` property 1016: `bool = True` |
| 5170 (`0x1432`) | 16 | default | Working `CSkpStyle` property 2001: `u32 = 2` (`0x00000002`) |
| 5186 (`0x1442`) | 16 | default | Working `CSkpStyle` property 2002: `u32 = 4291355105` (`0xffc8e1e1`) |
| 5202 (`0x1452`) | 16 | default | Working `CSkpStyle` property 2003: `u32 = 4291330176` (`0xffc88080`) |
| 5218 (`0x1462`) | 13 | default | Working `CSkpStyle` property 2004: `bool = False` |
| 5231 (`0x146f`) | 13 | default | Working `CSkpStyle` property 2005: `bool = True` |
| 5244 (`0x147c`) | 16 | default | Working `CSkpStyle` property 2006: `u32 = 0` (`0x00000000`) |
| 5260 (`0x148c`) | 13 | default | Working `CSkpStyle` property 2007: `bool = True` |
| 5273 (`0x1499`) | 20 | default | Working `CSkpStyle` property 2008: `f64 = 0.65` |
| 5293 (`0x14ad`) | 16 | default | Working `CSkpStyle` property 4000: `u32 = 4294967295` (`0xffffffff`) |
| 5309 (`0x14bd`) | 16 | default | Working `CSkpStyle` property 4001: `u32 = 4294944140` (`0xffffa58c`) |
| 5325 (`0x14cd`) | 13 | default | Working `CSkpStyle` property 4002: `bool = False` |
| 5338 (`0x14da`) | 16 | default | Working `CSkpStyle` property 4003: `u32 = 4282356543` (`0xff3f933f`) |
| 5354 (`0x14ea`) | 13 | default | Working `CSkpStyle` property 4004: `bool = False` |
| 5367 (`0x14f7`) | 16 | default | Working `CSkpStyle` property 4005: `u32 = 50` (`0x00000032`) |
| 5383 (`0x1507`) | 13 | default | Working `CSkpStyle` property 4006: `bool = True` |
| 5396 (`0x1514`) | 16 | default | Working `CSkpStyle` property 4007: `u32 = 0` (`0x00000000`) |
| 5412 (`0x1524`) | 13 | default | Working `CSkpStyle` property 5000: `bool = True` |
| 5425 (`0x1531`) | 8 | default | Working `CSkpStyle` property 5001: `count = 0` (empty watermark list) |
| 5433 (`0x1539`) | 16 | default | Working `CSkpStyle` property 7000: `u32 = 4278255615` (`0xff00ffff`) |
| 5449 (`0x1549`) | 16 | default | Working `CSkpStyle` property 7001: `u32 = 4278190335` (`0xff0000ff`) |
| 5465 (`0x1559`) | 16 | default | Working `CSkpStyle` property 7002: `u32 = 4286611584` (`0xff808080`) |
| 5481 (`0x1569`) | 16 | default | Working `CSkpStyle` property 7003: `u32 = 4287458918` (`0xff8d6e66`) |
| 5497 (`0x1579`) | 16 | default | Working `CSkpStyle` property 7004: `u32 = 4290822336` (`0xffc0c0c0`) |
| 5513 (`0x1589`) | 16 | default | Working `CSkpStyle` property 7005: `u32 = 4278190335` (`0xff0000ff`) |
| 5529 (`0x1599`) | 13 | default | Working `CSkpStyle` property 7008: `bool = True` |
| 5542 (`0x15a6`) | 13 | default | Working `CSkpStyle` property 7010: `bool = False` |
| 5555 (`0x15b3`) | 13 | default | Working `CSkpStyle` property 7011: `bool = False` |
| 5568 (`0x15c0`) | 13 | default | Working `CSkpStyle` property 7012: `bool = False` |
| 5581 (`0x15cd`) | 16 | default | Working `CSkpStyle` property 7013: `u32 = 3` (`0x00000003`) |
| 5597 (`0x15dd`) | 16 | default | Working `CSkpStyle` property 7014: `u32 = 4` (`0x00000004`) |
| 5613 (`0x15ed`) | 13 | default | Working `CSkpStyle` property 7015: `bool = False` |
| 5626 (`0x15fa`) | 16 | default | Working `CSkpStyle` property 7016: `u32 = 4278190080` (`0xff000000`) |
| 5642 (`0x160a`) | 13 | default | Working `CSkpStyle` property 7017: `bool = False` |
| 5655 (`0x1617`) | 13 | default | Working `CSkpStyle` property 7018: `bool = False` |
| 5668 (`0x1624`) | 13 | default | Working `CSkpStyle` property 8000: `bool = True` |
| 5681 (`0x1631`) | 20 | default | Working `CSkpStyle` property 8001: `f64 = 1.0` |
| 5701 (`0x1645`) | 13 | default | Working `CSkpStyle` property 8002: `bool = True` |
| 5714 (`0x1652`) | 20 | default | Working `CSkpStyle` property 8003: `f64 = 0.8` |
| 5734 (`0x1666`) | 13 | default | Working `CSkpStyle` property 8100: `bool = False` |
| 5747 (`0x1673`) | 16 | default | Working `CSkpStyle` property 8102: `f32 = 10.0` |
| 5763 (`0x1683`) | 16 | default | Working `CSkpStyle` property 8103: `f32 = 0.0` |
| 5779 (`0x1693`) | 1 | default | `CSkpStyleManager.active_style_changed` (`u8 = 0`) |
| 5780 (`0x1694`) | 3 | format | `CWatermarkManager` `CEntity` preamble: null `CAttributeContainer` ref (`u16 = 0`) + `pid_mask` (`u8 = 0`, no persistent ID) |
| 5783 (`0x1697`) | 4 | format | `CWatermarkManager` `watermark_count` (`u32 = 0`) |
| 5787 (`0x169b`) | 4 | format | `CWatermarkManager` selection/flags (`u32 = 0`) |
| 5791 (`0x169f`) | 1 | default | `CWatermarkManager.display_watermarks` (`u8 = 1`) |
| 5792 (`0x16a0`) | 4 | format | Closing `CSketchUpModel` trailer (`u32 = 0`) |


---

## 9. VFF 2021+ Blank Document

Python's 2021+ VFF writer (`openskp._blank_document_vff.build_blank_vff()`) constructs an empty SketchUp 2025 (`{25.0.571}`, model revision `17`) VFF `.skp` container in code: a `69`-byte header followed by a deterministic `7`-entry ZIP archive holding OpenSKP's own minimal placeholder PNG thumbnails (`meta/model_thumbnail.png` and `meta/preview_thumbnail.png`), `meta/meta.dat` (`366` bytes), `materials/Layer_Layer0/material.xml` (`995` bytes), `styles/[Simple Style]/style.xml` (`5,238` bytes), `styles/[Simple Style]_1/style.xml` (`5,238` bytes), and `model.dat` (`5,282` bytes). Every field below is marked `format` (required by the VFF container or TLV/XML framing) or `default` (an initial model setting, name, UUID, or timestamp).

### 9.1 69-byte VFF File Header (`0 .. 69`)

| Offset | Size | Kind | Field & Value |
|---:|---:|---|---|
| 0 (`0x0000`) | 32 | format | File magic UTF-16LE string `'SketchUp Model'` (`0xFF 0xFE 0xFF 0x0E` + 14 UTF-16LE code units) |
| 32 (`0x0020`) | 24 | default | Version UTF-16LE string `'{25.0.571}'` (`0xFF 0xFE 0xFF 0x0A` + 10 UTF-16LE code units) |
| 56 (`0x0038`) | 3 | format | ASCII trailer signature `'VFF'` (`0x56 0x46 0x46`) |
| 59 (`0x003b`) | 2 | format | Trailer payload byte count (`u16 = 8`: 2B container version + 2B model revision + 4B integrity word) |
| 61 (`0x003d`) | 2 | format | VFF container format version (`u16 = 1`; matches `7300` in `meta/meta.dat`) |
| 63 (`0x003f`) | 2 | format | VFF model schema revision (`u16 = 17`; matches `7400` in `meta/meta.dat`) |
| 65 (`0x0041`) | 4 | format | Archive integrity word (`u32`, computed over the final ZIP entries; see [§7.1](#71-header-and-integrity-word)) |

### 9.2 Deterministic ZIP Container & Empirical SketchUp Requirements

`build_blank_vff()` and `openskp._vff_writer.archive.build_zip()` emit a deterministic ZIP archive with fixed metadata:

1. **Entry order** (`7` entries in the blank document):
   1. `meta/model_thumbnail.png` (`DEFLATE` level `0`, `flag = 0x0000`, `internal_attr = 0`)
   2. `meta/preview_thumbnail.png` (`DEFLATE` level `0`, `flag = 0x0000`, `internal_attr = 0`)
   3. `meta/meta.dat` (`DEFLATE` level `1`, `flag = 0x0006`, `internal_attr = 0`)
   4. `materials/Layer_Layer0/material.xml` (`DEFLATE` level `1`, `flag = 0x0006`, `internal_attr = 1`)
   5. `styles/[Simple Style]/style.xml` (`DEFLATE` level `1`, `flag = 0x0006`, `internal_attr = 1`)
   6. `styles/[Simple Style]_1/style.xml` (`DEFLATE` level `1`, `flag = 0x0006`, `internal_attr = 1`)
   7. `model.dat` (`DEFLATE` level `1`, `flag = 0x0006`, `internal_attr = 0`)
2. **Header fields & timestamps**: local file headers declare `version_needed = 45` with the 20-byte ZIP64 extended-information extra field (`tag = 0x0001`, `length = 16`, two zero `u64`s), no data descriptors, `mod_time = 0x0000`, and `mod_date = 0x0020` (`1980-01-01 00:00:00`); central directory headers declare `version_made_by = 0`, `version_needed = 20`, and `extra_len = 0`.
3. **Empirical verification against `SketchUpAPI.dll` (SketchUp 2025) and desktop integrity checking**:
   - **Compression methods**: `ZIP_STORED` (`method = 0`) and `DEFLATE` (`method = 8`, zlib levels `0`, `1`, `6`, `9`) load cleanly (`SU_ERROR_NONE = 0`, `load_status = 0`, `SUModelFixErrors = 0`). `BZIP2` (`method = 12`) and `LZMA` (`method = 14`) are rejected with `SU_ERROR_SERIALIZATION (7)`.
   - **Required archive entries**: omitting `meta/meta.dat` or `styles/*/style.xml` fails with `SU_ERROR_MODEL_INVALID (12)`; omitting `model.dat` fails. Omitting `meta/model_thumbnail.png` and `meta/preview_thumbnail.png` opens cleanly in headless `SketchUpAPI.dll` (`SU_ERROR_NONE = 0`, `load_status = 0`, `SUModelFixErrors = 0`), and `SUModelSaveToFileWithVersion(..., 14)` itself omits thumbnails on headless saves. However, when thumbnail entries are present in the ZIP, `SketchUpAPI.dll` decodes them through its PNG image decoder on load; furthermore, `meta/meta.dat` (`6700` and `6800`) references `meta/model_thumbnail.png` and `meta/preview_thumbnail.png`, and desktop SketchUp 2025's header integrity word places `meta/meta.dat` at archive index `2` after the two thumbnails (`model.dat` last). `build_blank_vff()` therefore places OpenSKP's own code-generated `1x1` transparent RGBA PNG (`70` bytes, built via `struct` and `zlib.compress`) in both thumbnail slots.
   - **Flags, extra fields, and order**: `SketchUpAPI.dll` reads via the central directory and accepts arbitrary entry order, `flag = 0x0000` / `0x0006` / `0x0800` (UTF-8 bit 11) / `0x0008` (data descriptors), omitting the 20-byte ZIP64 extra field, and non-zero DOS timestamps; desktop SketchUp 2025 additionally checks the 4-byte header integrity word at `0x0041..0x0044`, which binds the exact archive entry order (`meta/meta.dat` at index `2`, `model.dat` last) and entry CRCs.

### 9.3 `meta/meta.dat` (`0 .. 366`)

| Offset | Record Size | Tag (file order) | Kind | Field & Value |
|---:|---:|---|---|---|
| 0 (`0x0000`) | 366 (hdr 6 + 360) | `6400` | format | Outer `meta/meta.dat` wrapper container (payload `360` B, 20 child records) |
| 6 (`0x0006`) | 14 (hdr 6 + 8) | `7500` | default | ↳ Writer application version UTF-8 string `'25.0.571'` (`SUModelGetVersion`) |
| 20 (`0x0014`) | 8 (hdr 6 + 2) | `7600` | default | ↳ Release major/year code (`u16 = 24` for SketchUp 2025) |
| 28 (`0x001c`) | 8 (hdr 6 + 2) | `7700` | default | ↳ Release channel / build kind (`u16 = 2`) |
| 36 (`0x0024`) | 8 (hdr 6 + 2) | `7300` | format | ↳ VFF container format version (`u16 = 1`; matches header `@61`) |
| 44 (`0x002c`) | 8 (hdr 6 + 2) | `7400` | format | ↳ VFF model schema revision (`u16 = 17`; matches header `@63`) |
| 52 (`0x0034`) | 22 (hdr 6 + 16) | `6600` | default | ↳ Model UUID (`ccc9e7ac-a198-46ae-b942-b609057c057e`, Windows LE; updated on write) |
| 74 (`0x004a`) | 52 (hdr 6 + 46) | `6700` | format | ↳ Model thumbnail reference container (`1` child `2823`) |
| 80 (`0x0050`) | 46 (hdr 6 + 40) | `2823` | format | ↳ ↳ Archive file-reference container (payload `40` B) |
| 86 (`0x0056`) | 10 (hdr 6 + 4) | `2923` | format | ↳ ↳ ↳ Thumbnail image format enum (`u32 = 4`, PNG) |
| 96 (`0x0060`) | 30 (hdr 6 + 24) | `2A23` | format | ↳ ↳ ↳ Thumbnail archive path `'meta/model_thumbnail.png'` |
| 126 (`0x007e`) | 54 (hdr 6 + 48) | `6800` | format | ↳ Preview thumbnail reference container (`1` child `2823`) |
| 132 (`0x0084`) | 48 (hdr 6 + 42) | `2823` | format | ↳ ↳ Archive file-reference container (payload `42` B) |
| 138 (`0x008a`) | 10 (hdr 6 + 4) | `2923` | format | ↳ ↳ ↳ Thumbnail image format enum (`u32 = 4`, PNG) |
| 148 (`0x0094`) | 32 (hdr 6 + 26) | `2A23` | format | ↳ ↳ ↳ Thumbnail archive path `'meta/preview_thumbnail.png'` |
| 180 (`0x00b4`) | 7 (hdr 6 + 1) | `6900` | default | ↳ Thumbnail present flag (`u8 = 1`) |
| 187 (`0x00bb`) | 6 (hdr 6 + 0) | `6A00` | default | ↳ Model name UTF-8 string `''` (`SUModelGetName`) |
| 193 (`0x00c1`) | 6 (hdr 6 + 0) | `6B00` | default | ↳ Model description UTF-8 string `''` (`SUModelGetDescription`) |
| 199 (`0x00c7`) | 6 (hdr 6 + 0) | `6C00` | default | ↳ Author / credits UTF-8 string `''` |
| 205 (`0x00cd`) | 6 (hdr 6 + 0) | `6E00` | default | ↳ Source / copyright UTF-8 string `''` |
| 211 (`0x00d3`) | 7 (hdr 6 + 1) | `7100` | default | ↳ Geo-referenced flag (`u8 = 0`, `UsesGeoReferencing`) |
| 218 (`0x00da`) | 7 (hdr 6 + 1) | `7900` | default | ↳ Classified flag (`u8 = 0`, `IsClassified`) |
| 225 (`0x00e1`) | 7 (hdr 6 + 1) | `7200` | default | ↳ Dynamic / live component summary flag (`u8 = 0`) |
| 232 (`0x00e8`) | 12 (hdr 6 + 6) | `6D00` | default | ↳ Display length unit UTF-8 string `'Inches'` (read by `_read_meta_units`) |
| 244 (`0x00f4`) | 7 (hdr 6 + 1) | `7000` | default | ↳ Unit format / display flag (`u8 = 1`) |
| 251 (`0x00fb`) | 27 (hdr 6 + 21) | `6F00` | default | ↳ Last-saved file path UTF-8 string `'C:/Temp/vff/blank.skp'` |
| 278 (`0x0116`) | 88 (hdr 6 + 82) | `7800` | format | ↳ Save-history section container (`1` child `C800`) |
| 284 (`0x011c`) | 82 (hdr 6 + 76) | `C800` | format | ↳ ↳ Save-history table wrapper (`1` child `C900`) |
| 290 (`0x0122`) | 76 (hdr 6 + 70) | `C900` | format | ↳ ↳ ↳ Save-history entry list (`1` child `CA00`) |
| 296 (`0x0128`) | 70 (hdr 6 + 64) | `CA00` | format | ↳ ↳ ↳ ↳ Save-history entry record (`3` child records) |
| 302 (`0x012e`) | 40 (hdr 6 + 34) | `CB00` | default | ↳ ↳ ↳ ↳ ↳ Saving client identifier `'SketchUp Client (Windows) 25.0.571'` |
| 342 (`0x0156`) | 10 (hdr 6 + 4) | `CC00` | default | ↳ ↳ ↳ ↳ ↳ Save timestamp (`u32 = 1795500289` = `2026-11-24T06:04:49Z`, Unix `time_t`; updated on write) |
| 352 (`0x0160`) | 14 (hdr 6 + 8) | `CD00` | default | ↳ ↳ ↳ ↳ ↳ Save session / history token (`u64 = 0x778b43b4c931b848`; final 4 bytes feed the header integrity word) |

### 9.4 Table-Driven XML Entries (`material.xml` and `style.xml`)

- **`materials/Layer_Layer0/material.xml` (`995` bytes)**: UTF-8 XML 1.0 document (`standalone="no"`, LF newlines) with root `<materialDocument>` in namespace `http://sketchup.google.com/schemas/sketchup/1.0/material`. `serialize_material_xml_from_table()` emits `<mat:material>` attributes (`name="Layer_Layer0"`, `type="0"`, `workflow="0"`, `colorRed="255"`, `colorGreen="255"`, `colorBlue="255"`, `colorizeType="0"`, `trans="1"`, `useTrans="0"`, `pbrPromoState="0"`, `hasTexture="0"` — `name`, `type`, `workflow`, `pbrPromoState`, `hasTexture` are `format` for a solid tag-owned material; RGB and opacity settings are `default`) and `<mat:pbrMR xScale="1" yScale="1">` child elements (`enable_metalness=0`, `enable_roughness=0`, `enable_normal=0`, `enable_occlusion=0`, `metallicFactor=1`, `roughnessFactor=1`, `normalMapStyle=1`, `normalScale=1`, `occlusionStrength=1`, `baseColorFactor=4294967295`, all `default`).
- **`styles/[Simple Style]/style.xml` and `styles/[Simple Style]_1/style.xml` (`5,238` bytes each)**: identical UTF-8 XML 1.0 documents (`standalone="no"`, LF newlines) for the saved style and active working copy, with root `<styleDocument>` in namespace `http://sketchup.google.com/schemas/sketchup/1.0/style`. `serialize_style_xml_from_table()` emits `<sty:style name="[Simple Style]" desc="[Default face colors. Blue sky and gray background color.]">` (`default`) followed by `57` `<sty:item id="...">` elements (`id` and `<t:variant type="...">` wrapper are `format`; each setting value is `default`):
  - `type="1"`: boolean (`0` or `1`) — style items 1004, 1006, 1008, 1010, 1012, 1015, 1016, 2004, 2005, 2007, 4002, 4004, 4006, 5000, 7008, 7010, 7011, 7012, 7015, 7017, 7018, 8000, 8002, 8100, and 8101.
  - `type="4"`: signed 32-bit integer or signed 32-bit ABGR colour (`0xAABBGGRR` interpreted as two's-complement `int32`) — style items 1000, 1001, 1002, 1005, 1007, 1009, 1011, 1014, 2001, 2002 (`rgba(255,255,255,255)` = `-1`), 2003 (`rgba(162,183,198,255)` = `-3754078`), 2006, 4000, 4001, 4003, 4005, 4007, 7000, 7001, 7002, 7003, 7004, 7005, 7013, 7014, and 7016.
  - `type="6"`: 32-bit float (`%.9g`) — style items 8102 (`10`) and 8103 (`0`).
  - `type="7"`: 64-bit IEEE-754 double (`%.17g`, `DBL_DECIMAL_DIG`) — style items 2008 (`0.65000000000000002`), 8001 (`1`), and 8003 (`0.80000000000000004`).
  - `type="13"`: watermark list in namespace `http://sketchup.google.com/schemas/sketchup/1.0/wmlist` — style item 5001 (`<n0:wmlist count="0"/>`).

### 9.5 Complete `model.dat` Record-by-Record Specification (`0 .. 5,282`)

Every TLV record in the `5,282`-byte empty `model.dat` is listed below in exact file order (`6`-byte `<tag:u16, length:u32>` header included in `Record Size`; tags shown in two-byte file order):

| Offset | Record Size | Tag (file order) | Kind | Field & Value |
|---:|---:|---|---|---|
| 0 (`0x0000`) | 5282 | `F401` | format | Root VFF `model.dat` document container (`27` top-level child records) |
| 6 (`0x0006`) | 10 | `6300` | format | ↳ Section header preamble word (`u32 = 0`) |
| 16 (`0x0010`) | 19 | `0A02` | format | ↳ Embedded metadata path UTF-8 string `'meta/meta.dat'` |
| 35 (`0x0023`) | 202 | `0902` | format | ↳ Model properties attribute section (`1` child `B136`) |
| 41 (`0x0029`) | 196 | `B136` | format | ↳ ↳ Attribute container wrapper (`1` child `B236`) |
| 47 (`0x002f`) | 190 | `B236` | format | ↳ ↳ ↳ Attribute dictionary set container |
| 53 (`0x0035`) | 184 | `B336` | format | ↳ ↳ ↳ ↳ Named attribute dictionary container (`B036`, `B436`, `B536`, `B736`) |
| 59 (`0x003b`) | 6 | `B036` | format | ↳ ↳ ↳ ↳ ↳ Attribute dictionary empty header marker |
| 65 (`0x0041`) | 21 | `B436` | format | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'ModelProperties'` |
| 86 (`0x0056`) | 141 | `B536` | format | ↳ ↳ ↳ ↳ ↳ Attribute dictionary key/value entry list |
| 92 (`0x005c`) | 17 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'Description'` |
| 109 (`0x006d`) | 12 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 115 (`0x0073`) | 6 | `AD38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'Description'`: UTF-8 string `''` |
| 121 (`0x0079`) | 18 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'IsClassified'` |
| 139 (`0x008b`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 145 (`0x0091`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'IsClassified'`: `bool = False` |
| 152 (`0x0098`) | 15 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'IsDynamic'` |
| 167 (`0x00a7`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 173 (`0x00ad`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'IsDynamic'`: `bool = False` |
| 180 (`0x00b4`) | 12 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'IsLive'` |
| 192 (`0x00c0`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 198 (`0x00c6`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'IsLive'`: `bool = False` |
| 205 (`0x00cd`) | 10 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'Name'` |
| 215 (`0x00d7`) | 12 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 221 (`0x00dd`) | 6 | `AD38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'Name'`: UTF-8 string `''` |
| 227 (`0x00e3`) | 10 | `B736` | format | ↳ ↳ ↳ ↳ ↳ Attribute dictionary trailer word (`u32 = 0`) |
| 237 (`0x00ed`) | 806 | `0002` | format | ↳ `SUOptionsManager` top-level section (`1` child `A861`) |
| 243 (`0x00f3`) | 800 | `A861` | format | ↳ ↳ `SUOptionsManager` container (`1` child `A961`) |
| 249 (`0x00f9`) | 794 | `A961` | format | ↳ ↳ ↳ Options providers list (`5` `AA61` providers) |
| 255 (`0x00ff`) | 525 | `AA61` | format | ↳ ↳ ↳ ↳ Options provider record (`AB61` name + `AC61` entries) |
| 261 (`0x0105`) | 18 | `AB61` | format | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'UnitsOptions'` |
| 279 (`0x0117`) | 501 | `AC61` | format | ↳ ↳ ↳ ↳ ↳ Options provider key/value entries (`AD61` / `A438` pairs) |
| 285 (`0x011d`) | 21 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'LengthPrecision'` |
| 306 (`0x0132`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 312 (`0x0138`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'LengthPrecision'`: `i32 = 4` |
| 322 (`0x0142`) | 18 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'LengthFormat'` |
| 340 (`0x0154`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 346 (`0x015a`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'LengthFormat'`: `i32 = 1` |
| 356 (`0x0164`) | 16 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'LengthUnit'` |
| 372 (`0x0174`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 378 (`0x017a`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'LengthUnit'`: `i32 = 0` |
| 388 (`0x0184`) | 23 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'LengthSnapEnabled'` |
| 411 (`0x019b`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 417 (`0x01a1`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'LengthSnapEnabled'`: `bool = False` |
| 424 (`0x01a8`) | 22 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'LengthSnapLength'` |
| 446 (`0x01be`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 452 (`0x01c4`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'LengthSnapLength'`: `f64 = 0.0625` |
| 466 (`0x01d2`) | 20 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'AnglePrecision'` |
| 486 (`0x01e6`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 492 (`0x01ec`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'AnglePrecision'`: `i32 = 1` |
| 502 (`0x01f6`) | 22 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'AngleSnapEnabled'` |
| 524 (`0x020c`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 530 (`0x0212`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'AngleSnapEnabled'`: `bool = True` |
| 537 (`0x0219`) | 15 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'SnapAngle'` |
| 552 (`0x0228`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 558 (`0x022e`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'SnapAngle'`: `f64 = 15.0` |
| 572 (`0x023c`) | 26 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'SuppressUnitsDisplay'` |
| 598 (`0x0256`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 604 (`0x025c`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'SuppressUnitsDisplay'`: `bool = False` |
| 611 (`0x0263`) | 22 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'ForceInchDisplay'` |
| 633 (`0x0279`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 639 (`0x027f`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'ForceInchDisplay'`: `bool = False` |
| 646 (`0x0286`) | 14 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'AreaUnit'` |
| 660 (`0x0294`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 666 (`0x029a`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'AreaUnit'`: `i32 = 0` |
| 676 (`0x02a4`) | 16 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'VolumeUnit'` |
| 692 (`0x02b4`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 698 (`0x02ba`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'VolumeUnit'`: `i32 = 0` |
| 708 (`0x02c4`) | 19 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'AreaPrecision'` |
| 727 (`0x02d7`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 733 (`0x02dd`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'AreaPrecision'`: `i32 = 2` |
| 743 (`0x02e7`) | 21 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'VolumePrecision'` |
| 764 (`0x02fc`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 770 (`0x0302`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'VolumePrecision'`: `i32 = 2` |
| 780 (`0x030c`) | 102 | `AA61` | format | ↳ ↳ ↳ ↳ Options provider record (`AB61` name + `AC61` entries) |
| 786 (`0x0312`) | 17 | `AB61` | format | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'PageOptions'` |
| 803 (`0x0323`) | 79 | `AC61` | format | ↳ ↳ ↳ ↳ ↳ Options provider key/value entries (`AD61` / `A438` pairs) |
| 809 (`0x0329`) | 20 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'ShowTransition'` |
| 829 (`0x033d`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 835 (`0x0343`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'ShowTransition'`: `bool = True` |
| 842 (`0x034a`) | 20 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'TransitionTime'` |
| 862 (`0x035e`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 868 (`0x0364`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'TransitionTime'`: `f64 = 2.0` |
| 882 (`0x0372`) | 101 | `AA61` | format | ↳ ↳ ↳ ↳ Options provider record (`AB61` name + `AC61` entries) |
| 888 (`0x0378`) | 22 | `AB61` | format | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'SlideshowOptions'` |
| 910 (`0x038e`) | 73 | `AC61` | format | ↳ ↳ ↳ ↳ ↳ Options provider key/value entries (`AD61` / `A438` pairs) |
| 916 (`0x0394`) | 19 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'LoopSlideshow'` |
| 935 (`0x03a7`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 941 (`0x03ad`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'LoopSlideshow'`: `bool = True` |
| 948 (`0x03b4`) | 15 | `AD61` | format | ↳ ↳ ↳ ↳ ↳ ↳ Key `'SlideTime'` |
| 963 (`0x03c3`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 969 (`0x03c9`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'SlideTime'`: `f64 = 1.0` |
| 983 (`0x03d7`) | 30 | `AA61` | format | ↳ ↳ ↳ ↳ Options provider record (`AB61` name + `AC61` entries) |
| 989 (`0x03dd`) | 18 | `AB61` | format | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'NamedOptions'` |
| 1007 (`0x03ef`) | 6 | `AC61` | format | ↳ ↳ ↳ ↳ ↳ Options provider key/value entries (`AD61` / `A438` pairs) |
| 1013 (`0x03f5`) | 30 | `AA61` | format | ↳ ↳ ↳ ↳ Options provider record (`AB61` name + `AC61` entries) |
| 1019 (`0x03fb`) | 18 | `AB61` | format | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'PrintOptions'` |
| 1037 (`0x040d`) | 6 | `AC61` | format | ↳ ↳ ↳ ↳ ↳ Options provider key/value entries (`AD61` / `A438` pairs) |
| 1043 (`0x0413`) | 20 | `F501` | format | ↳ Next persistent entity ID section (`1` child `E803`) |
| 1049 (`0x0419`) | 14 | `E803` | format | ↳ ↳ Next persistent ID inner wrapper (`1` child `E903`) |
| 1055 (`0x041f`) | 8 | `E903` | default | ↳ ↳ ↳ Initial `next_persistent_id` counter (`varint = 41203` / `0xA0F3`; advanced by writer) |
| 1063 (`0x0427`) | 269 | `FA01` | format | ↳ Model start camera section (`1` child `BC34`) |
| 1069 (`0x042d`) | 263 | `BC34` | format | ↳ ↳ `CCamera` container (`18` child records) |
| 1075 (`0x0433`) | 30 | `BD34` | default | ↳ ↳ ↳ Camera `eye` (`3 x f64 = (189.18162001962057, -240.1043324491603, 74.01764083499498)` in) |
| 1105 (`0x0451`) | 30 | `BE34` | default | ↳ ↳ ↳ Camera `target` (`3 x f64 = (-332.7888510540576, 565.0165468333703, -38.71823019085756)` in) |
| 1135 (`0x046f`) | 30 | `BF34` | default | ↳ ↳ ↳ Camera `up` (`3 x f64 = (-0.06347835297529261, 0.09791310082684807, 0.9931684264967428)`) |
| 1165 (`0x048d`) | 14 | `C034` | default | ↳ ↳ ↳ Camera near clip distance (`f64 = 1.0` in) |
| 1179 (`0x049b`) | 14 | `C134` | default | ↳ ↳ ↳ Camera far clip distance (`f64 = 1000.0` in) |
| 1193 (`0x04a9`) | 7 | `C234` | default | ↳ ↳ ↳ Camera `perspective` (`u8 = 1`, perspective projection) |
| 1200 (`0x04b0`) | 14 | `C434` | default | ↳ ↳ ↳ Camera `fov` (`f64 = 35.0` deg) |
| 1214 (`0x04be`) | 14 | `C334` | default | ↳ ↳ ↳ Camera `ortho_height` (`f64 = 133.0` in) |
| 1228 (`0x04cc`) | 14 | `C534` | default | ↳ ↳ ↳ Camera `aspect_ratio` (`f64 = 0.0`, unconstrained; `SUCameraSetAspectRatio`) |
| 1242 (`0x04da`) | 7 | `C634` | default | ↳ ↳ ↳ Camera `fov_is_height` (`u8 = 1`; `SUCameraSetFOVIsHeight`) |
| 1249 (`0x04e1`) | 7 | `C734` | default | ↳ ↳ ↳ Camera 2-point / window shift flag (`u8 = 0`; unexposed by C API) |
| 1256 (`0x04e8`) | 6 | `C834` | default | ↳ ↳ ↳ Camera `description` UTF-8 string `''` (`SUCameraSetDescription`) |
| 1262 (`0x04ee`) | 14 | `C934` | default | ↳ ↳ ↳ Camera `image_width` (`f64 = 0.0` mm; `SUCameraSetImageWidth`) |
| 1276 (`0x04fc`) | 7 | `CA34` | default | ↳ ↳ ↳ Camera `is_2d` (`u8 = 0`; `SUCameraSet2D`) |
| 1283 (`0x0503`) | 14 | `CB34` | default | ↳ ↳ ↳ Camera `scale_2d` (`f64 = 1.0`; `SUCameraSetScale2D`) |
| 1297 (`0x0511`) | 14 | `CC34` | default | ↳ ↳ ↳ Camera `center_2d.x` (`f64 = 0.0`; `SUCameraSetCenter2D`) |
| 1311 (`0x051f`) | 14 | `CD34` | default | ↳ ↳ ↳ Camera `center_2d.y` (`f64 = 0.0`; `SUCameraSetCenter2D`) |
| 1325 (`0x052d`) | 7 | `CE34` | default | ↳ ↳ ↳ Camera trailing flag byte (`u8 = 0`; unexposed by C API) |
| 1332 (`0x0534`) | 662 | `FB01` | format | ↳ `CRenderingOptions` top-level section (`1` child `3C73`) |
| 1338 (`0x053a`) | 656 | `3C73` | format | ↳ ↳ `CRenderingOptions` container (`72` setting records) |
| 1344 (`0x0540`) | 10 | `3D73` | default | ↳ ↳ ↳ `RenderingOptions['RenderMode']` (`u32 = 2`) |
| 1354 (`0x054a`) | 7 | `3E73` | default | ↳ ↳ ↳ `RenderingOptions['ModelTransparency']` (`bool = False`) |
| 1361 (`0x0551`) | 7 | `3F73` | default | ↳ ↳ ↳ `RenderingOptions['MaterialTransparency']` (`bool = True`) |
| 1368 (`0x0558`) | 7 | `4073` | default | ↳ ↳ ↳ `RenderingOptions['Texture']` (`bool = True`) |
| 1375 (`0x055f`) | 10 | `4173` | default | ↳ ↳ ↳ `RenderingOptions['EdgeDisplayMode']` (`u32 = 1`) |
| 1385 (`0x0569`) | 10 | `4273` | default | ↳ ↳ ↳ `RenderingOptions['EdgeType']` (`u32 = 0`) |
| 1395 (`0x0573`) | 7 | `4373` | default | ↳ ↳ ↳ `RenderingOptions['DisplaySketchAxes']` (`bool = True`) |
| 1402 (`0x057a`) | 7 | `4473` | default | ↳ ↳ ↳ `RenderingOptions['DisplayText']` (`bool = True`) |
| 1409 (`0x0581`) | 7 | `4573` | default | ↳ ↳ ↳ `RenderingOptions['DisplayDims']` (`bool = True`) |
| 1416 (`0x0588`) | 7 | `4673` | default | ↳ ↳ ↳ `RenderingOptions['HideConstructionGeometry']` (`bool = False`) |
| 1423 (`0x058f`) | 7 | `4773` | default | ↳ ↳ ↳ `RenderingOptions['DisplayColorByLayer']` (`bool = False`) |
| 1430 (`0x0596`) | 10 | `4873` | default | ↳ ↳ ↳ `RenderingOptions['EdgeColorMode']` (`u32 = 1`) |
| 1440 (`0x05a0`) | 7 | `4973` | default | ↳ ↳ ↳ `RenderingOptions['DisplayInstanceAxes']` (`bool = False`) |
| 1447 (`0x05a7`) | 7 | `8073` | default | ↳ ↳ ↳ `RenderingOptions['DrawHiddenGeometry']` (`bool = False`) |
| 1454 (`0x05ae`) | 7 | `8173` | default | ↳ ↳ ↳ `RenderingOptions['DrawHiddenObjects']` (`bool = False`) |
| 1461 (`0x05b5`) | 7 | `4B73` | default | ↳ ↳ ↳ `RenderingOptions['JitterEdges']` (`bool = False`) |
| 1468 (`0x05bc`) | 7 | `4C73` | default | ↳ ↳ ↳ `RenderingOptions['EdgeHaloFlag']` (`bool = True`) |
| 1475 (`0x05c3`) | 7 | `4D73` | default | ↳ ↳ ↳ `RenderingOptions['ExtendLines']` (`bool = False`) |
| 1482 (`0x05ca`) | 10 | `4E73` | default | ↳ ↳ ↳ `RenderingOptions['LineExtension']` (`u32 = 3`) |
| 1492 (`0x05d4`) | 7 | `4F73` | default | ↳ ↳ ↳ `RenderingOptions['DrawSilhouettes']` (`bool = True`) |
| 1499 (`0x05db`) | 10 | `5073` | default | ↳ ↳ ↳ `RenderingOptions['SilhouetteWidth']` (`u32 = 2`) |
| 1509 (`0x05e5`) | 7 | `5173` | default | ↳ ↳ ↳ `RenderingOptions['DrawDepthQue']` (`bool = False`) |
| 1516 (`0x05ec`) | 10 | `5273` | default | ↳ ↳ ↳ `RenderingOptions['DepthQueWidth']` (`u32 = 4`) |
| 1526 (`0x05f6`) | 7 | `5373` | default | ↳ ↳ ↳ `RenderingOptions['DrawLineEnds']` (`bool = False`) |
| 1533 (`0x05fd`) | 10 | `5473` | default | ↳ ↳ ↳ `RenderingOptions['LineEndWidth']` (`u32 = 9`) |
| 1543 (`0x0607`) | 7 | `5573` | default | ↳ ↳ ↳ `RenderingOptions['DrawProfilesOnly']` (`bool = False`) |
| 1550 (`0x060e`) | 7 | `5673` | default | ↳ ↳ ↳ `RenderingOptions['DrawBackEdges']` (`bool = False`) |
| 1557 (`0x0615`) | 10 | `5773` | default | ↳ ↳ ↳ `RenderingOptions['BackgroundColor']` (`rgba = (191, 191, 198, 255)`) |
| 1567 (`0x061f`) | 10 | `5873` | default | ↳ ↳ ↳ `RenderingOptions['ForegroundColor']` (`rgba = (0, 0, 0, 255)`) |
| 1577 (`0x0629`) | 10 | `5973` | default | ↳ ↳ ↳ `RenderingOptions['HighlightColor']` (`rgba = (0, 1, 255, 255)`) |
| 1587 (`0x0633`) | 10 | `5A73` | default | ↳ ↳ ↳ `RenderingOptions['LockedColor']` (`rgba = (255, 0, 0, 255)`) |
| 1597 (`0x063d`) | 10 | `5B73` | default | ↳ ↳ ↳ `RenderingOptions['ConstructionColor']` (`rgba = (0, 0, 0, 255)`) |
| 1607 (`0x0647`) | 10 | `5C73` | default | ↳ ↳ ↳ `RenderingOptions['FaceFrontColor']` (`rgba = (255, 255, 255, 255)`) |
| 1617 (`0x0651`) | 10 | `5D73` | default | ↳ ↳ ↳ `RenderingOptions['FaceBackColor']` (`rgba = (162, 183, 198, 255)`) |
| 1627 (`0x065b`) | 7 | `5E73` | default | ↳ ↳ ↳ `RenderingOptions['DisplayWatermarks']` (`bool = True`) |
| 1634 (`0x0662`) | 7 | `5F73` | default | ↳ ↳ ↳ `RenderingOptions['DisplayFog']` (`bool = False`) |
| 1641 (`0x0669`) | 10 | `6073` | default | ↳ ↳ ↳ `RenderingOptions['FogColor']` (`rgba = (0, 0, 0, 0)`) |
| 1651 (`0x0673`) | 7 | `6173` | default | ↳ ↳ ↳ `RenderingOptions['FogUseBkColor']` (`bool = True`) |
| 1658 (`0x067a`) | 14 | `6273` | default | ↳ ↳ ↳ `RenderingOptions['FogStartDist']` (`f64 = -1.0`) |
| 1672 (`0x0688`) | 14 | `6373` | default | ↳ ↳ ↳ `RenderingOptions['FogEndDist']` (`f64 = -1.0`) |
| 1686 (`0x0696`) | 10 | `6473` | default | ↳ ↳ ↳ `RenderingOptions['StrokeMode']` (`u32 = 1`) |
| 1696 (`0x06a0`) | 10 | `6573` | default | ↳ ↳ ↳ `RenderingOptions['SkyColor']` (`rgba = (0, 154, 217, 255)`) |
| 1706 (`0x06aa`) | 10 | `6673` | default | ↳ ↳ ↳ `RenderingOptions['HorizonColor']` (`rgba = (0, 0, 0, 0)`) |
| 1716 (`0x06b4`) | 10 | `6773` | default | ↳ ↳ ↳ `RenderingOptions['GroundColor']` (`rgba = (151, 170, 136, 255)`) |
| 1726 (`0x06be`) | 7 | `6873` | default | ↳ ↳ ↳ `RenderingOptions['DrawHorizon']` (`bool = True`) |
| 1733 (`0x06c5`) | 7 | `6973` | default | ↳ ↳ ↳ `RenderingOptions['DrawGround']` (`bool = False`) |
| 1740 (`0x06cc`) | 7 | `6A73` | default | ↳ ↳ ↳ `RenderingOptions['DrawUnderground']` (`bool = True`) |
| 1747 (`0x06d3`) | 10 | `6B73` | default | ↳ ↳ ↳ `RenderingOptions['GroundTransparency']` (`u32 = 50`) |
| 1757 (`0x06dd`) | 14 | `6C73` | default | ↳ ↳ ↳ `RenderingOptions['InactiveFade']` (`f64 = 0.25`) |
| 1771 (`0x06eb`) | 14 | `6D73` | default | ↳ ↳ ↳ `RenderingOptions['InstanceFade']` (`f64 = 0.5`) |
| 1785 (`0x06f9`) | 7 | `6E73` | default | ↳ ↳ ↳ `RenderingOptions['InactiveHidden']` (`bool = False`) |
| 1792 (`0x0700`) | 7 | `6F73` | default | ↳ ↳ ↳ `RenderingOptions['InstanceHidden']` (`bool = False`) |
| 1799 (`0x0707`) | 10 | `7073` | default | ↳ ↳ ↳ `RenderingOptions['SectionActiveColor']` (`rgba = (255, 135, 0, 255)`) |
| 1809 (`0x0711`) | 10 | `7173` | default | ↳ ↳ ↳ `RenderingOptions['SectionInactiveColor']` (`rgba = (112, 105, 97, 255)`) |
| 1819 (`0x071b`) | 10 | `7273` | default | ↳ ↳ ↳ `RenderingOptions['SectionDefaultCutColor']` (`rgba = (0, 0, 0, 255)`) |
| 1829 (`0x0725`) | 10 | `7373` | default | ↳ ↳ ↳ `RenderingOptions['SectionDefaultFillColor']` (`rgba = (63, 63, 63, 255)`) |
| 1839 (`0x072f`) | 10 | `7473` | default | ↳ ↳ ↳ `RenderingOptions['SectionCutWidth']` (`u32 = 3`) |
| 1849 (`0x0739`) | 10 | `7573` | default | ↳ ↳ ↳ `RenderingOptions['SectionDisplayMode']` (`u32 = 2`) |
| 1859 (`0x0743`) | 7 | `7673` | default | ↳ ↳ ↳ `RenderingOptions['SectionCutFilled']` (`bool = True`) |
| 1866 (`0x074a`) | 10 | `7773` | default | ↳ ↳ ↳ `RenderingOptions['TransparencySort']` (`u32 = 0`) |
| 1876 (`0x0754`) | 14 | `7873` | default | ↳ ↳ ↳ `RenderingOptions['TransparencyFactor']` (`f64 = 0.65`) |
| 1890 (`0x0762`) | 7 | `7973` | default | ↳ ↳ ↳ `RenderingOptions['SectionCutDrawEdges']` (`bool = True`) |
| 1897 (`0x0769`) | 14 | `7A73` | default | ↳ ↳ ↳ `RenderingOptions['CreaseAngleRad']` (`f64 = 0.34906585039886595`) |
| 1911 (`0x0777`) | 7 | `7B73` | default | ↳ ↳ ↳ `RenderingOptions['ShowViewName']` (`bool = True`) |
| 1918 (`0x077e`) | 7 | `7C73` | default | ↳ ↳ ↳ `RenderingOptions['MatchPhotoBackgroundEnable']` (`bool = True`) |
| 1925 (`0x0785`) | 14 | `7D73` | default | ↳ ↳ ↳ `RenderingOptions['MatchPhotoBackgroundOpacity']` (`f64 = 1.0`) |
| 1939 (`0x0793`) | 7 | `7E73` | default | ↳ ↳ ↳ `RenderingOptions['MatchPhotoForegroundEnable']` (`bool = True`) |
| 1946 (`0x079a`) | 14 | `7F73` | default | ↳ ↳ ↳ `RenderingOptions['MatchPhotoForegroundOpacity']` (`f64 = 0.8`) |
| 1960 (`0x07a8`) | 7 | `8273` | default | ↳ ↳ ↳ `RenderingOptions['AmbientOcclusionAutoDistanceOff']` (`bool = False`) |
| 1967 (`0x07af`) | 7 | `8373` | default | ↳ ↳ ↳ `RenderingOptions['AmbientOcclusion']` (`bool = False`) |
| 1974 (`0x07b6`) | 10 | `8573` | default | ↳ ↳ ↳ `RenderingOptions['AmbientOcclusionDistance']` (`f32 = 10.0`) |
| 1984 (`0x07c0`) | 10 | `8673` | default | ↳ ↳ ↳ `RenderingOptions['AmbientOcclusionIntensity']` (`f32 = 0.0`) |
| 1994 (`0x07ca`) | 190 | `0402` | format | ↳ `CShadowInfo` top-level section (`1` child `9065`) |
| 2000 (`0x07d0`) | 184 | `9065` | format | ↳ ↳ `CShadowInfo` container (`16` setting records) |
| 2006 (`0x07d6`) | 10 | `9165` | default | ↳ ↳ ↳ `ShadowInfo['ShadowTime']` (`u32 = 1794144600` = `2026-11-08T13:30:00Z`, Unix `time_t`) |
| 2016 (`0x07e0`) | 7 | `9265` | default | ↳ ↳ ↳ `ShadowInfo['DaylightSavings']` (`u8 = 0`) |
| 2023 (`0x07e7`) | 18 | `9365` | default | ↳ ↳ ↳ `ShadowInfo['City']` UTF-8 string `'Boulder (CO)'` |
| 2041 (`0x07f9`) | 9 | `9465` | default | ↳ ↳ ↳ `ShadowInfo['Country']` UTF-8 string `'USA'` |
| 2050 (`0x0802`) | 14 | `9565` | default | ↳ ↳ ↳ `ShadowInfo['Longitude']` (`f64 = -105.242139` deg) |
| 2064 (`0x0810`) | 14 | `9665` | default | ↳ ↳ ↳ `ShadowInfo['Latitude']` (`f64 = 40.018309` deg) |
| 2078 (`0x081e`) | 14 | `9765` | default | ↳ ↳ ↳ `ShadowInfo['TZOffset']` (`f64 = -7.0` h) |
| 2092 (`0x082c`) | 30 | `9865` | default | ↳ ↳ ↳ `ShadowInfo['NorthAngle']` / north direction vector (`3 x f64 = (0.0, 1.0, 0.0)`) |
| 2122 (`0x084a`) | 7 | `9965` | default | ↳ ↳ ↳ `ShadowInfo['DisplayShadows']` (`u8 = 0`) |
| 2129 (`0x0851`) | 7 | `9A65` | default | ↳ ↳ ↳ `ShadowInfo['DisplayNorth']` (`u8 = 0`) |
| 2136 (`0x0858`) | 7 | `9B65` | default | ↳ ↳ ↳ `ShadowInfo['DisplayOnAllFaces']` (`u8 = 1`) |
| 2143 (`0x085f`) | 7 | `9C65` | default | ↳ ↳ ↳ `ShadowInfo['DisplayOnGroundPlane']` (`u8 = 1`) |
| 2150 (`0x0866`) | 7 | `9D65` | default | ↳ ↳ ↳ `ShadowInfo['EdgesCastShadows']` (`u8 = 0`) |
| 2157 (`0x086d`) | 10 | `9E65` | default | ↳ ↳ ↳ `ShadowInfo['Light']` (`u32 = 80`) |
| 2167 (`0x0877`) | 10 | `9F65` | default | ↳ ↳ ↳ `ShadowInfo['Dark']` (`u32 = 45`) |
| 2177 (`0x0881`) | 7 | `A065` | default | ↳ ↳ ↳ `ShadowInfo['UseSunForAllShading']` (`u8 = 0`) |
| 2184 (`0x0888`) | 145 | `FC01` | format | ↳ `CSketchCS` model axes top-level section (`1` child `5046`) |
| 2190 (`0x088e`) | 139 | `5046` | format | ↳ ↳ `CSketchCS` container (`D007` drawing + origin + 3 unit axes) |
| 2196 (`0x0894`) | 13 | `D007` | format | ↳ ↳ ↳ Drawing-element base container |
| 2202 (`0x089a`) | 7 | `D307` | default | ↳ ↳ ↳ ↳ Drawing display flags (`u8 = 0x06`) |
| 2209 (`0x08a1`) | 30 | `5146` | default | ↳ ↳ ↳ Model axes `origin` (`3 x f64 = (0.0, 0.0, 0.0)` in) |
| 2239 (`0x08bf`) | 30 | `5246` | default | ↳ ↳ ↳ Model axes `x_axis` (`3 x f64 = (1.0, 0.0, 0.0)`) |
| 2269 (`0x08dd`) | 30 | `5346` | default | ↳ ↳ ↳ Model axes `y_axis` (`3 x f64 = (0.0, 1.0, 0.0)`) |
| 2299 (`0x08fb`) | 30 | `5446` | default | ↳ ↳ ↳ Model axes `z_axis` (`3 x f64 = (0.0, 0.0, 1.0)`) |
| 2329 (`0x0919`) | 1423 | `0802` | format | ↳ `CLineStyleManager` top-level section (`1` child `7440`) |
| 2335 (`0x091f`) | 1417 | `7440` | format | ↳ ↳ `CLineStyleManager` container (`1` child `7540`) |
| 2341 (`0x0925`) | 1411 | `7540` | format | ↳ ↳ ↳ Line-style table (`12` built-in `7640` line styles) |
| 2347 (`0x092b`) | 92 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 2353 (`0x0931`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 2359 (`0x0937`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13870` / `0x362e`) |
| 2367 (`0x093f`) | 17 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Solid Basic'` |
| 2384 (`0x0950`) | 10 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'16.0'` |
| 2394 (`0x095a`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 2408 (`0x0968`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 2422 (`0x0976`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 2432 (`0x0980`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 2439 (`0x0987`) | 96 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 2445 (`0x098d`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 2451 (`0x0993`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13871` / `0x362f`) |
| 2459 (`0x099b`) | 16 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Short dash'` |
| 2475 (`0x09ab`) | 15 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'6.0, -6.0'` |
| 2490 (`0x09ba`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 2504 (`0x09c8`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 2518 (`0x09d6`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 2528 (`0x09e0`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 2535 (`0x09e7`) | 91 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 2541 (`0x09ed`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 2547 (`0x09f3`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13872` / `0x3630`) |
| 2555 (`0x09fb`) | 10 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Dash'` |
| 2565 (`0x0a05`) | 16 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'12.0, -6.0'` |
| 2581 (`0x0a15`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 2595 (`0x0a23`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 2609 (`0x0a31`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 2619 (`0x0a3b`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 2626 (`0x0a42`) | 89 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 2632 (`0x0a48`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 2638 (`0x0a4e`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13873` / `0x3631`) |
| 2646 (`0x0a56`) | 9 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Dot'` |
| 2655 (`0x0a5f`) | 15 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'1.0, -6.0'` |
| 2670 (`0x0a6e`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 2684 (`0x0a7c`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 2698 (`0x0a8a`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 2708 (`0x0a94`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 2715 (`0x0a9b`) | 106 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 2721 (`0x0aa1`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 2727 (`0x0aa7`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13874` / `0x3632`) |
| 2735 (`0x0aaf`) | 14 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Dash dot'` |
| 2749 (`0x0abd`) | 27 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'12.0, -6.0, 1.0, -6.0'` |
| 2776 (`0x0ad8`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 2790 (`0x0ae6`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 2804 (`0x0af4`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 2814 (`0x0afe`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 2821 (`0x0b05`) | 124 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 2827 (`0x0b0b`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 2833 (`0x0b11`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13875` / `0x3633`) |
| 2841 (`0x0b19`) | 21 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Dash double-dot'` |
| 2862 (`0x0b2e`) | 38 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'12.0, -6.0, 1.0, -6.0, 1.0, -6.0'` |
| 2900 (`0x0b54`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 2914 (`0x0b62`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 2928 (`0x0b70`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 2938 (`0x0b7a`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 2945 (`0x0b81`) | 135 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 2951 (`0x0b87`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 2957 (`0x0b8d`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13876` / `0x3634`) |
| 2965 (`0x0b95`) | 21 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Dash triple-dot'` |
| 2986 (`0x0baa`) | 49 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'12.0, -6.0, 1.0, -6.0, 1.0, -6.0, 1.0, -6.0'` |
| 3035 (`0x0bdb`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 3049 (`0x0be9`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 3063 (`0x0bf7`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 3073 (`0x0c01`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 3080 (`0x0c08`) | 125 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 3086 (`0x0c0e`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 3092 (`0x0c14`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13877` / `0x3635`) |
| 3100 (`0x0c1c`) | 21 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Double-dash dot'` |
| 3121 (`0x0c31`) | 39 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'12.0, -6.0, 12.0, -6.0, 1.0, -6.0'` |
| 3160 (`0x0c58`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 3174 (`0x0c66`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 3188 (`0x0c74`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 3198 (`0x0c7e`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 3205 (`0x0c85`) | 143 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 3211 (`0x0c8b`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 3217 (`0x0c91`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13878` / `0x3636`) |
| 3225 (`0x0c99`) | 28 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Double-dash double-dot'` |
| 3253 (`0x0cb5`) | 50 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'12.0, -6.0, 12.0, -6.0, 1.0, -6.0, 1.0, -6.0'` |
| 3303 (`0x0ce7`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 3317 (`0x0cf5`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 3331 (`0x0d03`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 3341 (`0x0d0d`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 3348 (`0x0d14`) | 154 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 3354 (`0x0d1a`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 3360 (`0x0d20`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13879` / `0x3637`) |
| 3368 (`0x0d28`) | 28 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Double-dash triple-dot'` |
| 3396 (`0x0d44`) | 61 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'12.0, -6.0, 12.0, -6.0, 1.0, -6.0, 1.0, -6.0, 1.0, -6.0'` |
| 3457 (`0x0d81`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 3471 (`0x0d8f`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 3485 (`0x0d9d`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 3495 (`0x0da7`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 3502 (`0x0dae`) | 115 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 3508 (`0x0db4`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 3514 (`0x0dba`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13880` / `0x3638`) |
| 3522 (`0x0dc2`) | 20 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Long-dash dash'` |
| 3542 (`0x0dd6`) | 30 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'36.0, -10.0, 12.0, -10.0'` |
| 3572 (`0x0df4`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 3586 (`0x0e02`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 3600 (`0x0e10`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 3610 (`0x0e1a`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 3617 (`0x0e21`) | 135 | `7640` | format | ↳ ↳ ↳ ↳ `CLineStyle` record (`DC05` ID, `7740` name, `7840` pattern, scales & flags) |
| 3623 (`0x0e27`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 3629 (`0x0e2d`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13881` / `0x3639`) |
| 3637 (`0x0e35`) | 27 | `7740` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Long-dash double-dash'` |
| 3664 (`0x0e50`) | 43 | `7840` | default | ↳ ↳ ↳ ↳ ↳ Line-style stipple pattern `'36.0, -10.0, 12.0, -10.0, 12.0, -10.0'` |
| 3707 (`0x0e7b`) | 14 | `7A40` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` length scale (`f64 = 1.0`) |
| 3721 (`0x0e89`) | 14 | `7940` | default | ↳ ↳ ↳ ↳ ↳ `CLineStyle` width scale (`f64 = 1.0`) |
| 3735 (`0x0e97`) | 10 | `7B40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` built-in kind word (`u32 = 0`) |
| 3745 (`0x0ea1`) | 7 | `7C40` | format | ↳ ↳ ↳ ↳ ↳ `CLineStyle` custom/user-created flag (`u8 = 0` for built-in styles) |
| 3752 (`0x0ea8`) | 18 | `F701` | format | ↳ `CMaterialManager` top-level section (`1` child `D430`) |
| 3758 (`0x0eae`) | 12 | `D430` | format | ↳ ↳ `CMaterialManager` wrapper (`1` child `D530`) |
| 3764 (`0x0eb4`) | 6 | `D530` | format | ↳ ↳ ↳ Model material list (`empty`; user materials append here) |
| 3770 (`0x0eba`) | 176 | `F801` | format | ↳ `CLayerManager` top-level section (`1` child `983A`) |
| 3776 (`0x0ec0`) | 170 | `983A` | format | ↳ ↳ `CLayerManager` container (`993A` list, `9A3A` active ID, `9B3A` folders) |
| 3782 (`0x0ec6`) | 112 | `993A` | format | ↳ ↳ ↳ Layer list (`1` default `8C3C` layer; new tags append here) |
| 3788 (`0x0ecc`) | 106 | `8C3C` | format | ↳ ↳ ↳ ↳ Default `CLayer` record (`'Layer0'`) |
| 3794 (`0x0ed2`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 3800 (`0x0ed8`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 13869` / `0x362d`) |
| 3808 (`0x0ee0`) | 12 | `8D3C` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Layer0'` |
| 3820 (`0x0eec`) | 7 | `8E3C` | default | ↳ ↳ ↳ ↳ ↳ `Layer0` hidden flag (`u8 = 0`, visible) |
| 3827 (`0x0ef3`) | 57 | `8F3C` | format | ↳ ↳ ↳ ↳ ↳ `Layer0` owned tag-material wrapper (`1` child `C832`) |
| 3833 (`0x0ef9`) | 51 | `C832` | format | ↳ ↳ ↳ ↳ ↳ ↳ Embedded `CMaterial` record (`'Layer_Layer0'`) |
| 3839 (`0x0eff`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 3845 (`0x0f05`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 16049` / `0x3eb1`) |
| 3853 (`0x0f0d`) | 18 | `CC32` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Layer_Layer0'` |
| 3871 (`0x0f1f`) | 7 | `CA32` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Material owner kind (`u8 = 1`, owned by parent layer) |
| 3878 (`0x0f26`) | 6 | `CB32` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Material empty texture/extra marker |
| 3884 (`0x0f2c`) | 10 | `903C` | format | ↳ ↳ ↳ ↳ ↳ `CLayer` trailing word (`u32 = 0`) |
| 3894 (`0x0f36`) | 8 | `9A3A` | default | ↳ ↳ ↳ Active layer entity ID (`varint = 13869` -> `'Layer0'`) |
| 3902 (`0x0f3e`) | 44 | `9B3A` | format | ↳ ↳ ↳ Tag-folder hierarchy wrapper (`1` child `803E`) |
| 3908 (`0x0f44`) | 38 | `803E` | format | ↳ ↳ ↳ ↳ Root tag-folder tree container (`empty`) |
| 3914 (`0x0f4a`) | 6 | `813E` | format | ↳ ↳ ↳ ↳ ↳ Root tag-folder name (`''`) |
| 3920 (`0x0f50`) | 6 | `833E` | format | ↳ ↳ ↳ ↳ ↳ Child tag-folders list (`empty`) |
| 3926 (`0x0f56`) | 6 | `843E` | format | ↳ ↳ ↳ ↳ ↳ Folder-assigned tags list (`empty`) |
| 3932 (`0x0f5c`) | 7 | `823E` | default | ↳ ↳ ↳ ↳ ↳ Root tag-folder visibility byte (`u8 = 0`) |
| 3939 (`0x0f63`) | 7 | `853E` | format | ↳ ↳ ↳ ↳ ↳ Root tag-folder trailing byte (`u8 = 0`) |
| 3946 (`0x0f6a`) | 95 | `FD01` | format | ↳ `CFontManager` top-level section (`1` child `204E`) |
| 3952 (`0x0f70`) | 89 | `204E` | format | ↳ ↳ `CFontManager` container (`1` child `214E`) |
| 3958 (`0x0f76`) | 83 | `214E` | format | ↳ ↳ ↳ Font table (`1` default `1450` font) |
| 3964 (`0x0f7c`) | 77 | `1450` | format | ↳ ↳ ↳ ↳ Default `CSkFont` record (`'Tahoma'`, referenced by text & dimension styles) |
| 3970 (`0x0f82`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 3976 (`0x0f88`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 16047` / `0x3eaf`) |
| 3984 (`0x0f90`) | 12 | `1550` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'Tahoma'` |
| 3996 (`0x0f9c`) | 7 | `1650` | default | ↳ ↳ ↳ ↳ ↳ `CSkFont` italic flag (`u8 = 0`) |
| 4003 (`0x0fa3`) | 7 | `1750` | default | ↳ ↳ ↳ ↳ ↳ `CSkFont` bold flag (`u8 = 0`) |
| 4010 (`0x0faa`) | 10 | `1850` | default | ↳ ↳ ↳ ↳ ↳ `CSkFont` point size (`u32 = 12`) |
| 4020 (`0x0fb4`) | 7 | `1950` | default | ↳ ↳ ↳ ↳ ↳ `CSkFont` use-world-height flag (`u8 = 0`) |
| 4027 (`0x0fbb`) | 14 | `1A50` | default | ↳ ↳ ↳ ↳ ↳ `CSkFont` world height (`f64 = 10.0` in) |
| 4041 (`0x0fc9`) | 92 | `FE01` | format | ↳ `CTextStyle` top-level section (`1` child `E457`) |
| 4047 (`0x0fcf`) | 86 | `E457` | format | ↳ ↳ `CTextStyle` container (`9` child records) |
| 4053 (`0x0fd5`) | 8 | `E557` | default | ↳ ↳ ↳ `CTextStyle` primary font ID (`varint = 16047` -> `'Tahoma'`) |
| 4061 (`0x0fdd`) | 8 | `E657` | default | ↳ ↳ ↳ `CTextStyle` leader/secondary font ID (`varint = 16047` -> `'Tahoma'`) |
| 4069 (`0x0fe5`) | 10 | `E757` | default | ↳ ↳ ↳ `CTextStyle` arrow type (`u32 = 3`, closed arrow) |
| 4079 (`0x0fef`) | 10 | `E857` | default | ↳ ↳ ↳ `CTextStyle` alignment / mode word (`u32 = 1`) |
| 4089 (`0x0ff9`) | 7 | `E957` | default | ↳ ↳ ↳ `CTextStyle` flag byte (`u8 = 0`) |
| 4096 (`0x1000`) | 10 | `EA57` | default | ↳ ↳ ↳ `CTextStyle` leader type (`u32 = 2`, pushpin) |
| 4106 (`0x100a`) | 7 | `EB57` | default | ↳ ↳ ↳ `CTextStyle` display-leader flag (`u8 = 1`) |
| 4113 (`0x1011`) | 10 | `EC57` | default | ↳ ↳ ↳ `CTextStyle` text colour RGBA `(63, 63, 63, 255)` |
| 4123 (`0x101b`) | 10 | `ED57` | default | ↳ ↳ ↳ `CTextStyle` leader colour RGBA `(63, 63, 63, 255)` |
| 4133 (`0x1025`) | 180 | `FF01` | format | ↳ `CDimensionStyle` top-level section (`1` child `B45F`) |
| 4139 (`0x102b`) | 174 | `B45F` | format | ↳ ↳ `CDimensionStyle` container (`18` child records) |
| 4145 (`0x1031`) | 8 | `B55F` | default | ↳ ↳ ↳ `CDimensionStyle` font ID (`varint = 16047` -> `'Tahoma'`) |
| 4153 (`0x1039`) | 7 | `B65F` | default | ↳ ↳ ↳ `CDimensionStyle` `has_3d` (`u8 = 0`; `SUDimensionStyleGetHas3D`) |
| 4160 (`0x1040`) | 7 | `B75F` | default | ↳ ↳ ↳ `CDimensionStyle` show-units flag (`u8 = 1`) |
| 4167 (`0x1047`) | 10 | `B85F` | default | ↳ ↳ ↳ `CDimensionStyle` extension-line offset (`u32 = 5`; `SUDimensionStyleGetExtensionLineOffset`) |
| 4177 (`0x1051`) | 10 | `B95F` | default | ↳ ↳ ↳ `CDimensionStyle` extension-line overshoot (`u32 = 10`; `SUDimensionStyleGetExtensionLineOvershoot`) |
| 4187 (`0x105b`) | 10 | `BA5F` | default | ↳ ↳ ↳ `CDimensionStyle` line weight (`u32 = 1`; `SUDimensionStyleGetLineWeight`) |
| 4197 (`0x1065`) | 10 | `BB5F` | default | ↳ ↳ ↳ `CDimensionStyle` arrow type (`u32 = 3`, closed; `SUDimensionStyleGetArrowType`) |
| 4207 (`0x106f`) | 10 | `BC5F` | default | ↳ ↳ ↳ `CDimensionStyle` arrow size (`u32 = 10`; `SUDimensionStyleGetArrowSize`) |
| 4217 (`0x1079`) | 7 | `BD5F` | default | ↳ ↳ ↳ `CDimensionStyle` highlight non-associative (`u8 = 0`) |
| 4224 (`0x1080`) | 10 | `BE5F` | default | ↳ ↳ ↳ `CDimensionStyle` non-associative highlight RGBA `(255, 0, 0, 255)` |
| 4234 (`0x108a`) | 7 | `BF5F` | default | ↳ ↳ ↳ `CDimensionStyle` show radial prefix (`u8 = 1`) |
| 4241 (`0x1091`) | 7 | `C05F` | default | ↳ ↳ ↳ `CDimensionStyle` hide out-of-plane (`u8 = 0`) |
| 4248 (`0x1098`) | 14 | `C15F` | default | ↳ ↳ ↳ `CDimensionStyle` out-of-plane threshold (`f64 = 0.6`) |
| 4262 (`0x10a6`) | 7 | `C25F` | default | ↳ ↳ ↳ `CDimensionStyle` hide small dimensions (`u8 = 0`) |
| 4269 (`0x10ad`) | 14 | `C35F` | default | ↳ ↳ ↳ `CDimensionStyle` small-dimension threshold (`f64 = 10.0`) |
| 4283 (`0x10bb`) | 10 | `C45F` | default | ↳ ↳ ↳ `CDimensionStyle` dimension line RGBA `(63, 63, 63, 255)` |
| 4293 (`0x10c5`) | 10 | `C55F` | default | ↳ ↳ ↳ `CDimensionStyle` custom text RGBA `(0, 0, 0, 255)` (`use_text_color = False`) |
| 4303 (`0x10cf`) | 10 | `C65F` | default | ↳ ↳ ↳ `CDimensionStyle` text alignment mode (`u32 = 3`) |
| 4313 (`0x10d9`) | 6 | `0102` | format | ↳ Legacy embedded preview `CDib` placeholder (`empty` in VFF) |
| 4319 (`0x10df`) | 28 | `0302` | format | ↳ BIM classifications / schema manager section (`1` child `EC2C`) |
| 4325 (`0x10e5`) | 22 | `EC2C` | format | ↳ ↳ Schema manager container (`ED2C` list + `EE2C` flags) |
| 4331 (`0x10eb`) | 6 | `ED2C` | format | ↳ ↳ ↳ Loaded classification schemas (`empty`) |
| 4337 (`0x10f1`) | 10 | `EE2C` | format | ↳ ↳ ↳ Schema manager flags (`u32 = 0`) |
| 4347 (`0x10fb`) | 48 | `F901` | format | ↳ `CDefinitionList` top-level section (`6300` word + `7017` wrapper) |
| 4353 (`0x1101`) | 10 | `6300` | format | ↳ ↳ Section header preamble word (`u32 = 0`) |
| 4363 (`0x110b`) | 32 | `7017` | format | ↳ ↳ Definition-list wrapper (`6300` word + `7117` list) |
| 4369 (`0x1111`) | 10 | `6300` | format | ↳ ↳ ↳ Section header preamble word (`u32 = 0`) |
| 4379 (`0x111b`) | 16 | `7117` | format | ↳ ↳ ↳ Component/group definitions pool (`6300` header word; definitions append here) |
| 4385 (`0x1121`) | 10 | `6300` | format | ↳ ↳ ↳ ↳ Section header preamble word (`u32 = 0`) |
| 4395 (`0x112b`) | 589 | `F601` | format | ↳ Root model entities top-level section (`1` child `8813`) |
| 4401 (`0x1131`) | 583 | `8813` | format | ↳ ↳ Root entity container (`D007` drawing, `9E13`, `9B13` relationships, `9F13` flags) |
| 4407 (`0x1137`) | 546 | `D007` | format | ↳ ↳ ↳ Drawing-element base container |
| 4413 (`0x113d`) | 533 | `DC05` | format | ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 4419 (`0x1143`) | 527 | `DD05` | format | ↳ ↳ ↳ ↳ ↳ Entity attribute-dictionaries block (`DD05`) |
| 4425 (`0x1149`) | 521 | `B136` | format | ↳ ↳ ↳ ↳ ↳ ↳ Attribute container wrapper (`1` child `B236`) |
| 4431 (`0x114f`) | 515 | `B236` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary set container |
| 4437 (`0x1155`) | 324 | `B336` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Named attribute dictionary container (`B036`, `B436`, `B536`, `B736`) |
| 4443 (`0x115b`) | 6 | `B036` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary empty header marker |
| 4449 (`0x1161`) | 18 | `B436` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'GeoReference'` |
| 4467 (`0x1173`) | 284 | `B536` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary key/value entry list |
| 4473 (`0x1179`) | 28 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'GeoReferenceNorthAngle'` |
| 4501 (`0x1195`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4507 (`0x119b`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'GeoReferenceNorthAngle'`: `f64 = 0.1557307335588798` |
| 4521 (`0x11a9`) | 14 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'Latitude'` |
| 4535 (`0x11b7`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4541 (`0x11bd`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'Latitude'`: `f64 = 40.018309` |
| 4555 (`0x11cb`) | 20 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'LocationSource'` |
| 4575 (`0x11df`) | 18 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4581 (`0x11e5`) | 12 | `AD38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'LocationSource'`: UTF-8 string `'Manual'` |
| 4593 (`0x11f1`) | 15 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'Longitude'` |
| 4608 (`0x1200`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4614 (`0x1206`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'Longitude'`: `f64 = -105.242139` |
| 4628 (`0x1214`) | 23 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'ModelTranslationX'` |
| 4651 (`0x122b`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4657 (`0x1231`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'ModelTranslationX'`: `f64 = -18871519.616960514` |
| 4671 (`0x123f`) | 23 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'ModelTranslationY'` |
| 4694 (`0x1256`) | 20 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4700 (`0x125c`) | 14 | `A938` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'ModelTranslationY'`: `f64 = -174402260.58333763` |
| 4714 (`0x126a`) | 24 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'UsesGeoReferencing'` |
| 4738 (`0x1282`) | 13 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4744 (`0x1288`) | 7 | `AA38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'UsesGeoReferencing'`: `bool = False` |
| 4751 (`0x128f`) | 10 | `B736` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary trailer word (`u32 = 0`) |
| 4761 (`0x1299`) | 64 | `B336` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Named attribute dictionary container (`B036`, `B436`, `B536`, `B736`) |
| 4767 (`0x129f`) | 6 | `B036` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary empty header marker |
| 4773 (`0x12a5`) | 10 | `B436` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'temp'` |
| 4783 (`0x12af`) | 32 | `B536` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary key/value entry list |
| 4789 (`0x12b5`) | 10 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'temp'` |
| 4799 (`0x12bf`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4805 (`0x12c5`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'temp'`: `i32 = 0` |
| 4815 (`0x12cf`) | 10 | `B736` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary trailer word (`u32 = 0`) |
| 4825 (`0x12d9`) | 121 | `B336` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Named attribute dictionary container (`B036`, `B436`, `B536`, `B736`) |
| 4831 (`0x12df`) | 6 | `B036` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary empty header marker |
| 4837 (`0x12e5`) | 26 | `B436` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'GSU_ContributorsInfo'` |
| 4863 (`0x12ff`) | 73 | `B536` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary key/value entry list |
| 4869 (`0x1305`) | 23 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'LastModifiedByKey'` |
| 4892 (`0x131c`) | 12 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4898 (`0x1322`) | 6 | `AD38` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'LastModifiedByKey'`: UTF-8 string `''` |
| 4904 (`0x1328`) | 16 | `B636` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Key `'VersionKey'` |
| 4920 (`0x1338`) | 16 | `A438` | format | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Typed value wrapper (`A438`) |
| 4926 (`0x133e`) | 10 | `A738` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Value for `'VersionKey'`: `i32 = 1000` |
| 4936 (`0x1348`) | 10 | `B736` | default | ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ ↳ Attribute dictionary trailer word (`u32 = 1`) |
| 4946 (`0x1352`) | 7 | `D307` | default | ↳ ↳ ↳ ↳ Drawing display flags (`u8 = 0x06`) |
| 4953 (`0x1359`) | 6 | `9E13` | format | ↳ ↳ ↳ Empty root entity pool marker (`9E13`) |
| 4959 (`0x135f`) | 18 | `9B13` | format | ↳ ↳ ↳ Entity relationships container (`1` child `9F63`) |
| 4965 (`0x1365`) | 12 | `9F63` | format | ↳ ↳ ↳ ↳ Relationship map wrapper (`1` child `A063`) |
| 4971 (`0x136b`) | 6 | `A063` | format | ↳ ↳ ↳ ↳ ↳ Relationship list (`empty`) |
| 4977 (`0x1371`) | 7 | `9F13` | format | ↳ ↳ ↳ Entity container state flag (`u8 = 0` on empty root) |
| 4984 (`0x1378`) | 7 | `0C02` | default | ↳ Model post-entities flag (`u8 = 1`) |
| 4991 (`0x137f`) | 10 | `0D02` | default | ↳ Model feature/version mask (`u32 = 1`) |
| 5001 (`0x1389`) | 169 | `0602` | format | ↳ `CSkpStyleManager` top-level section (`1` child `7869`) |
| 5007 (`0x138f`) | 163 | `7869` | format | ↳ ↳ `CSkpStyleManager` container (`7969` saved, `7A69` selected, `7B69` working, `7C69` changed) |
| 5013 (`0x1395`) | 80 | `7969` | format | ↳ ↳ ↳ Saved styles list (`1` `6C6B` style) |
| 5019 (`0x139b`) | 74 | `6C6B` | format | ↳ ↳ ↳ ↳ `CSkpStyle` entry referencing `styles/<folder>/style.xml` |
| 5025 (`0x13a1`) | 14 | `DC05` | format | ↳ ↳ ↳ ↳ ↳ Entity-base container (`DC05`) |
| 5031 (`0x13a7`) | 8 | `DE05` | default | ↳ ↳ ↳ ↳ ↳ ↳ Persistent entity ID (`varint = 16050` / `0x3eb2`) |
| 5039 (`0x13af`) | 22 | `6D6B` | default | ↳ ↳ ↳ ↳ ↳ `CSkpStyle` UUID (`89cb0288-1dbb-432d-a638-fcd642799541`, Windows LE) |
| 5061 (`0x13c5`) | 6 | `6E6B` | default | ↳ ↳ ↳ ↳ ↳ `CSkpStyle` external `.style` source path (`''`) |
| 5067 (`0x13cb`) | 20 | `6F6B` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'[Simple Style]'` |
| 5087 (`0x13df`) | 6 | `706B` | default | ↳ ↳ ↳ ↳ ↳ `CSkpStyle` inline description override (`''`; description lives in `style.xml`) |
| 5093 (`0x13e5`) | 8 | `7A69` | default | ↳ ↳ ↳ Selected saved style entity ID (`varint = 16050` -> `'[Simple Style]'`) |
| 5101 (`0x13ed`) | 62 | `7B69` | format | ↳ ↳ ↳ Active working style wrapper (`1` child `6C6B`) |
| 5107 (`0x13f3`) | 56 | `6C6B` | format | ↳ ↳ ↳ ↳ `CSkpStyle` entry referencing `styles/<folder>/style.xml` |
| 5113 (`0x13f9`) | 22 | `6D6B` | default | ↳ ↳ ↳ ↳ ↳ `CSkpStyle` UUID (`89cb0288-1dbb-432d-a638-fcd642799541`, Windows LE) |
| 5135 (`0x140f`) | 6 | `6E6B` | default | ↳ ↳ ↳ ↳ ↳ `CSkpStyle` external `.style` source path (`''`) |
| 5141 (`0x1415`) | 22 | `6F6B` | default | ↳ ↳ ↳ ↳ ↳ Name / folder UTF-8 string `'[Simple Style]_1'` |
| 5163 (`0x142b`) | 7 | `7C69` | default | ↳ ↳ ↳ `active_style_changed` flag (`u8 = 0`) |
| 5170 (`0x1432`) | 18 | `1002` | format | ↳ `COverlayManager` / environments section (`1` child `1879`) |
| 5176 (`0x1438`) | 12 | `1879` | format | ↳ ↳ Overlay manager wrapper (`1` child `1979`) |
| 5182 (`0x143e`) | 6 | `1979` | format | ↳ ↳ ↳ Overlays list (`empty`) |
| 5188 (`0x1444`) | 18 | `0702` | format | ↳ `CPageList` scenes top-level section (`1` child `606D`) |
| 5194 (`0x144a`) | 12 | `606D` | format | ↳ ↳ Page-list wrapper (`1` child `616D`) |
| 5200 (`0x1450`) | 6 | `616D` | format | ↳ ↳ ↳ Scene items list (`empty`; `add_scene` appends `4871` pages here) |
| 5206 (`0x1456`) | 6 | `0502` | format | ↳ Model watermarks section (`empty`) |
| 5212 (`0x145c`) | 60 | `0E02` | format | ↳ Root `CComponentBehavior` section (`1` child `581B`) |
| 5218 (`0x1462`) | 54 | `581B` | format | ↳ ↳ `CComponentBehavior` container (`6` child records) |
| 5224 (`0x1468`) | 10 | `591B` | default | ↳ ↳ ↳ Root `snap_to` plane mode (`u32 = 0`) |
| 5234 (`0x1472`) | 10 | `5A1B` | default | ↳ ↳ ↳ Root `no_scale_mask` (`u32 = 0`) |
| 5244 (`0x147c`) | 7 | `5B1B` | default | ↳ ↳ ↳ Root `cuts_opening` (`u8 = 0`) |
| 5251 (`0x1483`) | 7 | `5C1B` | default | ↳ ↳ ↳ Root `is_2d` flag (`u8 = 0`) |
| 5258 (`0x148a`) | 7 | `5D1B` | default | ↳ ↳ ↳ Root `always_face_camera` (`u8 = 0`) |
| 5265 (`0x1491`) | 7 | `5E1B` | default | ↳ ↳ ↳ Root `shadows_face_sun` (`u8 = 0`) |
| 5272 (`0x1498`) | 10 | `0F02` | format | ↳ Closing `model.dat` trailer word (`u32 = 0`) |

### 9.6 Fields That Could Not Be Toggled Through `SketchUpAPI.dll`

Every `SURenderingOptions`, `SUShadowInfo`, `SUOptionsProvider`, `SUCamera`, `SUDimensionStyle`, `SUStyles`, `SULayers`, `SULineStyles`, `SUFont`, and `SUModel` getter/setter exposed by `SketchUpAPI.dll` (SketchUp 2025) was queried and toggled against the blank VFF document. The following records could not be toggled directly via `SketchUpAPI.dll` setters (identified by structural comparison across multi-version SketchUp 2021–2026 desktop saves and classic `blank_v17` counterparts):

- **`meta/meta.dat`**: `7600` (`u16 = 24`, release year code), `7700` (`u16 = 2`, release channel), `7200` (`u8 = 0`, dynamic/live summary byte), and `CD00` (`u64` history session token) are written internally by SketchUp on save with no C API setter.
- **`model.dat` preamble & closing words**: `6300` (`u32 = 0` at `@6`, `@4353`, `@4369`, `@4385`), `0102` (empty `CDib` placeholder at `@4313`), `0C02` (`u8 = 1` at `@4984`), `0D02` (`u32 = 1` at `@4991`), and `0F02` (`u32 = 0` at `@5272`).
- **`SUOptionsManager` (`0002`)**: the empty `'PrintOptions'` `AA61` provider (`@1013`) is written by desktop SketchUp on Windows but is not returned by `SUOptionsManagerGetOptionsProviderNames`.
- **`CCamera` (`BC34`)**: `C734` (`u8 = 0` at `@1249`, 2-point window shift flag) and `CE34` (`u8 = 0` at `@1325`, trailing camera byte) have no `SUCameraSet*` setter.
- **`CRenderingOptions` (`3C73`)**: `59` of the `72` child records toggle directly via `SURenderingOptionsSetValue`; the remaining `13` (`4C73` edge halo flag, `6473` fog/stroke mode `1`, `7873` transparency factor `0.65`, `7973` section-cut edge flag, `7A73` `20°` crease angle in radians, `7B73` `ShowViewName` `1`, `7C73`–`7F73` Match Photo background/foreground flags and opacities, `8273` AO auto-distance flag, and `8673` AO intensity `0.0`) are either style-only items (1016, 2008, 8000–8003, 8101, 8103) or read-only in `SURenderingOptionsSetValue`.
- **`CTextStyle` (`FE01 > E457`)**: `SketchUpAPI.dll` exposes `SUDimensionStyle` getters/setters (`FF01 > B45F`), whereas model-level `CTextStyle` (`E457`) has no standalone `SUTextStyle*` C API; its `9` child tags (`E557`–`ED57`) match per-entity `CText` (`F055`) font/arrow/leader/colour fields and classic `CTextStyle`.
- **Root `GeoReference` / `temp` / `GSU_ContributorsInfo` dictionaries (`F601 > ... > B236`)**: written by the desktop SketchUp 2025 template (`'temp'`, `'GSU_ContributorsInfo'` with `B736` trailer `u32 = 1`).
