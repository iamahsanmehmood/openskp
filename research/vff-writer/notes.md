# VFF writer research notes (stage 1)

Everything here was learned from **SketchUp 2025 itself**, running in the
Windows acceptance VM behind `ssh -p 2222 Docker@127.0.0.1` with the community
SketchUp MCP Ruby endpoint (guest `127.0.0.1:9877`, host tunnel
`integration/scripts/mcp_tunnel.sh` → `127.0.0.1:9878`).  The oracle
application reports `Sketchup.version == "25.0.571"`, and writes model revision
17 with the `{25.0.571}` header string.

The tools used to produce the evidence are in `tools/`:

* `gen_oracle.rb` — builds and saves the minimal pairs plus the acceptance
  target through the Ruby API.  It was copied to the guest and evaluated with
  `eval(File.read("C:/Temp/vff/gen_oracle.rb"))`.
* `accept.rb` — reads back the model SketchUp has open and prints faces,
  edges, materials, layers, definitions and instance transforms.
* `mcp.py` — one-shot JSON-line client for the MCP `eval_ruby` tool.
* `vffdump.py` / `sections.py` — dump the ZIP entries and the TLV record tree
  of a `model.dat`.

## Template

`packages/python/src/openskp/_scaffold/blank_vff.skp` is a genuine SketchUp
2025 empty document.  Exact Ruby (one MCP call):

```ruby
m=Sketchup.active_model; m.entities.clear!
m.definitions.purge_unused; m.materials.purge_unused
m.layers.purge_unused; m.save("C:/Temp/vff/blank.skp");
[Sketchup.version, m.entities.length, m.definitions.length,
 m.materials.length, m.layers.map(&:name).inspect].join(" | ")
```

Result: `25.0.571 | 0 | 0 | 0 | ["Layer0"]`.
SHA-256 `795c5cb831a0474d633cf7db2022177678347e767e115bf5a4ee361167eb306c`
(identical to `oracle/blank.skp`).  It has no geometry, no materials, no
definitions and only the default `Layer0` tag (255,255,255).

The fallback (converting `blank_v17.skp` with `skpwright convert`) was **not**
used: the VM route worked.

## Minimal pairs (`oracle/`)

| file | adds | what it isolates |
|---|---|---|
| `blank.skp` | nothing | the empty-document sections, `Layer0` + its `Layer_Layer0` material XML, the two style entries and thumbnails |
| `face.skp` | one 10×10 in square | the smallest entity container: 4 vertices, 4 edges, 1 face, `0x138e` order list |
| `face_material.skp` | + solid red material (220,30,40) on the face | material record `0x32c8`, `materials/SpikeRed/material.xml`, `0x07d1` on the face's drawing element |
| `face_tag.skp` | + tag `SpikeTag` on the face and its edges | layer record `0x3c8c`, its embedded colour material, `Layer_SpikeTag/material.xml`, `0x07d2` on drawing elements |
| `component.skp` | definition `SpikeBox` (a box) + two instances | definition `0x157c`, its own `0x1388` container, instance `0x1964` and the 13-double transform, `0x138c` instance pool |
| `target.skp` | all of the above in one model | the complete acceptance model: box + 4 painted faces + tag + definition + two placed instances |

SHA-256:

```
795c5cb8…b306c  blank.skp
5bbb5d27…521588  face.skp
1b77ff1b…711471  face_material.skp
ff9dc6ea…d1d7e998  face_tag.skp
5386c56e…04594  component.skp
5b00ba9d…425b47  target.skp
```

`tools/gen_oracle.rb` rebuilds all six (the hashes change between saves only
because GUIDs and save times change; the record layouts do not).

## Archive and header

SketchUp 2025 writes:

* two UTF-16 string records (`FF FE FF 0E` + "SketchUp Model", then
  `FF FE FF 0A` + "{25.0.571}"), then 13 bytes
  `56 46 46 08 00 01 00 | u16 revision (17) | u32 integrity word`, then ZIP;
* local headers: version needed 45, method 8, time 0, DOS date `0x0020`,
  a 20-byte ZIP64 extra (`01 00 10 00` + two zero u64s), no data descriptor;
* flags `0x0006` for `.dat`/`.xml`, `0x0000` for PNG/JPEG; XML entries with
  flags 0x0006 have internal attributes 1;
* central directory: version-made-by 0, version-needed 20, no extra field,
  no comment, no external attributes;
* entry order puts `meta/` first, then `materials/`, `styles/`, … and
  `model.dat` last.  `meta/meta.dat` is entry index 2 when the two thumbnail
  entries are present.

`meta/meta.dat` is the same `u16 tag | u32 length | payload` framing with
small tag values: `0x0075` version string, `0x0076/0x0077/0x0073` u16s,
`0x0074` revision, `0x0066` model GUID (fresh on every save), `0x0067/0x0068`
thumbnail image records, `0x006d` `"Inches"`, `0x006f` the save temp path and
`0x0078` the save history (`0x00c8 > 0x00c9 > 0x00ca`, with the last item's
`0x00cc` save time and `0x00cd` u64).  The last four bytes of `meta.dat` are
the high half of that u64.

### Integrity word

The 2025/2026 rule was implemented from its published description and then
checked against genuine files.  For `n` archive entries in archive order:
CRC-32 each entry, subtract 42 from the odd-indexed ones, replace the entry-2
slot with `V(ceil(n/2)) xor R(last four bytes of meta.dat)` (with
`V(1)=0xFFFFFFFF`, `V(2)=0`, `V(k+1)` the complement of `V(k)` run back
through four zero register steps; `R` the reflected CRC-32 register after the
four tail bytes from zero, without inversions), lay the slots out evens first
then odds, and CRC-32 `meta.dat[:-4] + slots`.  It reproduces the stored word
of `SU_File.skp` (n=7), `Untitled.skp` (n=22), the 2025 minimal pairs
(n=7/9) and of every file this writer produced.  The old SDK-saved 2021
fixtures (`nesting_2021.skp` etc.) store a word from an earlier writer line
that this rule does not reproduce; they are not used for that assertion.

SketchUp 2025 warns on a wrong word, so acceptance was the real check: every
written file below was opened with **no warning dialog**.

## Record tree

Tags below are the 16-bit little-endian values as stored (the reader in
`_core.py` prints them as the two bytes in file order, e.g. tag `0x07d0` is
`"D007"` there).

Common records:

```
0x05dc entity base     : 0x05de <varint id> [0x05dd attribute dictionaries]
0x07d0 drawing element : 0x05dc, 0x07d1 front material, 0x07d2 layer, 0x07d3 flags
```

Flags are `0x06` (casts/receives shadows) on ordinary faces/edges/instances,
`+0x01` hidden.  A root drawing element has no `0x05de` id; a definition's does.

Entity container `0x1388`, children in this order:

```
0x07d0 drawing element (the container's id; absent on the model root)
0x1389 vertex pool   → 0x09c4 { 0x05dc id, 0x09c5 3×f64 position }
0x138a edge pool     → 0x0bb8 { 0x07d0, 0x0bb9 start id, 0x0bba end id }
0x138b face pool     → 0x0dac { 0x07d0, 0x0dad (a,b,c,d), 0x0dae loops,
                                0x0daf back material }
0x138c component pool→ 0x1964 { 0x07d0, 0x1965 name, 0x1966 13×f64 transform,
                                0x1967 definition-container id, 0x1968 GUID }
(0x138d groups / 0x1390 images / … when present)
0x139e empty
0x138e entity order list: repeated <width:u8><id>
0x139b relationships: 0x639f { 0x63a0 } (empty in the spike)
0x139f u8 flags (bit 0: the container's bounds are cached)
0x13a0 bounds (6×f64 min/max) exactly when bit 0 is set
```

Face loops: `0x0dae > 0x1194 (loop) > 0x1195 > 0x0fa0 { 0x0fa1 edge id,
0x0fa2 forward }`.  `0x0fa2` is 0 when the use runs in the edge's stored
start→end direction.  The outer loop's ring order is the face's front winding
(right-hand rule gives the `0x0dad` normal); inner loops (holes) run opposite.
SketchUp refuses a file whose loops are inconsistent ("not a SketchUp model").

Definition `0x157c`: `0x1388` container (its id is what instances reference),
`0x157d` GUID, `0x157e` name, `0x157f` description, `0x1580` source,
`0x1582` u8, `0x1583` u32 kind (0 component), `0x1581` u32 timestamp,
`0x1b58` behaviour (six small records, all zero), `0x1585` thumbnail
(SketchUp writes one; the writer omits it and SketchUp accepts the file).

Transform `0x1966` is 13 f64: row-major 3×3, then translation, then `1.0`.

Materials `0x30d4 > 0x30d5 > 0x32c8 { 0x05dc id, 0x32cc name/folder,
0x32ca owner byte (0 normal, 1 layer colour), 0x32cb empty }`.  Colour and
opacity live only in `materials/<folder>/material.xml` (`<mat:material
name colorRed colorGreen colorBlue ... trans="0.5" useTrans="0">` plus the
constant `<mat:pbrMR>` block).  A tag `0x3c8c` is `{ 0x05dc id, 0x3c8d name,
0x3c8e hidden, 0x3c8f embedded 0x32c8 "Layer_<name>" material, 0x3c90 u32 }`;
its colour lives in `materials/Layer_<name>/material.xml`.  `0x3a9a` is the
current tag id; `0x3a9b > 0x3e80` is the empty folder root.

`0x01f5 > 0x03e8 > 0x03e9` is the next persistent id (minimal width).  New
ids are allocated from it upwards, and it is rewritten past the last one.

## Acceptance

`written_spike.skp` is the writer's output for the spike model (10-inch box,
4 painted faces, tag on all faces/edges, `SpikeBox` definition with its own
box, two instances: one translated +20 in, one translated +30 in and rotated
45° about Z).  `sketchup_resave.skp` is SketchUp 2025's own save of the same
open model.

* Opening `written_spike.skp` with `Sketchup.open_file` returned `true` and
  produced **no warning dialog** (verified by enumerating the guest's windows:
  no window titled "Warning").
* Ruby reports: `faces=6 edges=12 instances=2 defs=1`,
  `materials=[["SpikeRed", 220, 30, 40, 1.0]]`, `layers=["Layer0",
  "SpikeTag"]`, `face_materials=["SpikeRed"×4, none, none]`,
  `face_layers=["SpikeTag"]`, `edge_layers=["SpikeTag"]`, face normals
  ±X/±Y/±Z with areas 100 in², definition `SpikeBox` with 6 faces/12 edges
  and bounds `(0,0,0)…(10,10,10)`, instance transforms
  `(1,0,0,0,1,0,0,0,1, 20,0,0)` and the 45° rotation at `(0,30,0)`.
* `model.save(path)` wrote `sketchup_resave.skp`; re-parsing both files with
  openskp's public parser gives the same vertices, edges, faces (normals,
  loops, material ids, layer ids), materials, layers, definitions and
  instance matrices.  The re-save adds its own thumbnails, so its size
  differs; revision and integrity word remain valid.
* `skpwright verify` (built from `/home/jason/Repos/skpwright` at
  `0.6.1`, used only as an external verifier) exits 0 on `written_spike.skp`,
  `sketchup_resave.skp` and `blank_vff.skp` ("every record reads and
  reproduces").
* The same build using `nesting_2021.skp` as the template also opens in
  SketchUp with no warning, keeps the template's `Group`/`Shelf`/`Group2`
  definitions and its placed group, and shows the new `SpikeBox` plus both
  placed instances.

### What SketchUp refused, and the fix

The first definition-bearing file issued a **"Warning"** dialog and, after
dismissing it, loaded with no definitions and no instances.  The cause was a
duplicate `0x07d0` drawing element at the end of the definition's entity
container: the container builder both emitted the drawing element first and
re-appended it while copying through the remaining template children.  A
definition container with two drawing elements (and therefore two ids) is
invalid; SketchUp discards it.  After skipping the already-emitted drawing
element when copying existing children, the file opens cleanly and the
definitions survive.  This is the only acceptance failure observed.

## Limits of this stage

* Coordinates are inches; only faces (with optional holes), solid-colour
  materials, tags, component definitions and instances are authored.
* No textures, groups, curves/arcs, scenes, attributes or thumbnails for
  definitions.  Existing template sections are carried through untouched.
* Vertex/edge dedupe is per new container; geometry that already exists in a
  template's root is not deduped against (the two required templates have no
  root geometry or vertices).
* The integrity rule is the 2025/2026 one.  Old SDK-saved 2021 fixtures are
  readable templates, but their own header word comes from an earlier writer
  and is not used to test the rule.

## Committed acceptance artifacts

| file | SHA-256 | size |
|---|---|---|
| `written_spike.skp` | `b9404cf70292063c03d8763959de895d895c9d58b8d9c855231bbec0339fb0ff` | 15,659 B |
| `sketchup_resave.skp` | `055de508ee515faf86a3117fc6a3a2a3eaa0d20c5ae56195b6652b0570f59a51` | 18,238 B |

`written_spike.skp` was produced by the committed writer and opened warning-free
in SketchUp 2025 as described above; `sketchup_resave.skp` is SketchUp's own
save of the same open model.  openskp parses both to the same revision (17),
faces (normals, loops, material ids, layers), vertices, edges, materials,
layers, definitions and instance matrices; `check_integrity` is true for both,
and `skpwright verify` accepts both.  The size difference is SketchUp adding
its own thumbnail/archive entries.

# Stage 2 research notes: textures, groups, cleanup

## Cleanup

* `.gitignore`'s blanket `*.skp` no longer hides this directory: a narrow
  `!research/vff-writer/**/*.skp` keeps the oracle pairs, the stage-1 pair and
  the stage-2 evidence committable (`git check-ignore` exits 1 for them).  The
  tests' genuine entry-count saves under
  `packages/python/tests/fixtures/vff_writer/` get their own narrow negation
  for the same reason.  No other path is widened.
* Every `research/vff-writer/**/*.skp` is mode 0644.
* The integrity-word constant is now computed with a clean-room formulation:
  the documented step ("complement the register, run it backwards through four
  zero bytes") is inverted one bit at a time from the reflected CRC-32 bit
  step, instead of a tabulated inverse.  The prose in `SKP_FORMAT.md` §16.15
  is the only description used.  The constants it produces are checked
  against values solved out of genuine SketchUp saves with 7, 9, 11, 13 and 15
  archive entries (k = 4, 5, 6, 7, 8):

  | k | V(k) |
  |---|---|
  | 4 | `0x7dc225cd` |
  | 5 | `0xdb9e1da6` |
  | 6 | `0xe339138e` |
  | 7 | `0xc509b474` |
  | 8 | `0x771f0805` |

  The solver in `test_create_vff.py` recovers the constant from a file's own
  stored word by treating the meta slot as 32 unknown bits over GF(2), so the
  expected values are read from SketchUp's files, not from the recurrence.

## Textured materials

Minimal pairs: `stage2/oracle/tex_png.skp`, `tex_jpeg.skp` (default placement)
and `tex_uv.skp` (explicitly positioned), all saved by SketchUp 2025.

* Archive: the image is stored **before** `material.xml` in the material's
  folder (`materials/TexPNG/tex.png`, then `material.xml`).  The image entry
  uses flag `0x0000` (stored DEFLATE), `material.xml` uses `0x0006`.
* Material record `0x32c8` for both PNG and JPEG: `0x05dc` id, `0x32cc`
  folder name, `0x32ca` 0, `0x32cb` empty, **`0x32cd` u32 = `0x5a` (90)**.
  (The field is present for PNG too, so it is written for every texture.)
* `material.xml`: `type="1"`, `colorRed/Green/Blue` = the image's average
  colour (SketchUp's values for the test images are 121/68/114 and
  119/145/84; Pillow's average matches them), `hasTexture="1"`, then

  ```xml
  <mat:texture textureFilename="C:/Temp/vff2/tex.png" xScale="10" yScale="5" avgColor="4285678713">
    <mat:images>
      <mat:image id="1" path="./tex.png" file_name="tex.png"/>
    </mat:images>
  </mat:texture>
  <mat:pbrMR xScale="10" yScale="5"> … constant block … </mat:pbrMR>
  ```

  `avgColor` is `0xAABBGGRR` with the alpha byte `0xFF`.
* Default placement (paint with a texture and nothing else) stores **no UV
  record at all**: SketchUp derives the planar projection from the tile size.
* An explicitly positioned face stores, in its entity base's `0x05dd`:

  ```
  0x36b1 > 0x36b2 > 0x2710
    { 0x36b0 empty,
      0x2711 (front) > 0x2713 { 0x2714 u32 flags (1 = positioned),
                                0x2715 9×f64 matrix,
                                0x2716 3×f64 zeros,
                                0x2717 empty (no pins) },
      0x2712 (back, flags 0, identity matrix, same shape) }
  ```

  The matrix convention was derived from the pair: for face-plane basis
  `(xr, yr)` from the normal and tile size `(tw, th)` in inches,
  `[X,Y,1] @ inv(M) = [u·tw, v·th, 1]` with `(u,v)` tile-space UV (`1.0` is
  one full repetition, the convention `create.py` uses).  Three model
  point/UV pins determine that affine map; the writer inverts it and stores
  `M`.  The derived matrix reproduces `tex_uv.skp`'s stored matrix to 1e-9,
  and SketchUp reports the written mapping back through `uv_tile_at`.

## Groups and nested placements

Minimal pairs: `group_vs_component.skp` (identical box as a group and as a
component), `group_painted.skp` (painted, tagged, named root group),
`group_nested.skp` (group inside a component placed twice).

* A group is a normal component definition with `0x1583` kind = 1.  SketchUp
  names its definition automatically (`Group0#1`); the user-visible name is
  the inner instance's `0x1965` (`RootBox`).  The writer keeps the requested
  definition name and the instance name.
* A root/definition group placement lives in the `0x138d` pool: `0x1d4c`
  wraps exactly one ordinary `0x1964` instance (name, transform, definition
  ref, GUID, and the usual drawing element with optional `0x07d1` material
  and `0x07d2` layer).  The *inner instance id* is the entity listed in
  `0x138e`, not the wrapper (the wrapper has no id of its own).
* Nested groups inside a component use the same `0x138d > 0x1d4c > 0x1964`
  structure inside that definition's `0x1388` container; component instances
  use `0x138c > 0x1964`.
* A nested group's material/tag are stored on its `0x1964` drawing element,
  exactly like a component instance's.

## Stage-2 acceptance

`stage2/written_stage2.skp` (SHA-256
`eb9131f7902de87193482d62584f9c2e66485d2f2b5435fd8adb15b7ae65ee47`) is the
combined model: a default-placed PNG-textured face, a moved/rotated PNG
placement (pins `(0,0)`, `(0.5,0.866)`, `(-0.433,0.25)`), a JPEG-textured face,
a root group painted `GroupRed` and on `GroupTag`, and component `Outer` with
nested group `InnerBox` (painted, on `InnerTag`) placed twice (translated, and
translated + rotated 30° about Z).

* `Sketchup.open_file` returned true with **no warning dialog**.
* Ruby reported: `TexPNG` texture filename `/tmp/vff2_tex.png`, size 10×5 in,
  image 16×8 px; `TexJPEG` 8×4 in, 8×8 px; layers Layer0/GroupTag/InnerTag
  with the requested colours; faces=3, groups=1, component_instances=2;
  face0 default `texture_positioned?` false, face1 true, face2 false;
  `uv_tile_at` on face1 reproduces the three pins exactly (modulo whole-tile
  offsets); group `RootBox` has `definition.group? == true`,
  layer `GroupTag`, material `GroupRed`; both `Outer` instances have nested
  group `InnerBox` (`group? == true`, `InnerTag`, `GroupRed`) and the expected
  transforms; definitions are `RootBox` (true), `InnerBox` (true), `Outer`
  (false).
* SketchUp re-saved it as `stage2/sketchup_resave.skp` (SHA-256
  `99d148599a282fdc7d239ed0c77d8e96f791a95d81b92492e3dae543af5bb43c`).
  openskp parses both with the same revision (17), valid integrity word,
  texture bytes/tile sizes, faces and UV matrices, materials, layers,
  definitions (group-ness), nested instances and transforms — the only
  difference is the extra thumbnail/archive entries of the re-save.
* `skpwright verify` accepts both files ("every record reads and reproduces").

## What SketchUp refused, and the fixes

* **Unused materials make SketchUp 2025 hang while saving** (the file stays
  0 bytes and a dialog appears); painting every material on at least one face
  avoids it.  The entry-count oracle saves therefore paint one tiny face per
  material.  This is a SketchUp quirk, not a file-format issue.
* The first positioned-texture write used the wrong tile scale and assembled
  the affine map transposed; SketchUp still opened the file, but `uv_tile_at`
  showed a 1-inch tile instead of the requested 10×5 (rotated) one.  The
  matrix assembly and the tile-units convention were corrected against the
  `tex_uv.skp` pair, after which the pins round-trip to 1e-9.

## Files

* `stage2/oracle/` — the SketchUp-saved stage-2 pairs, the 7/9/11/13/15-entry
  count variants and `stage2_target.skp` (SketchUp's own combined model).
* `stage2/gen_oracle.rb`, `stage2/accept.rb` — the exact Ruby used.

# Stage 3 research notes: texture path, curves and attribute dictionaries

## Texture `textureFilename` (stage-2 follow-up)

SketchUp itself records the path a texture was loaded from
(`C:/Temp/vff2/tex.png` in `stage2/oracle/tex_png.skp`).  Keeping a caller's
absolute path would leak the host layout, so the writer now stores only the
basename (`tex.png`).  The image is addressed by `<mat:image path="./tex.png">`
inside the material folder anyway, and SketchUp 2025 opens such a file
without a warning: the stage-3 acceptance Ruby dump reports
`["vff2_tex.png", 10.0, 5.0, 16, 8]` for the StageTex material (filename,
tile width/height, pixel width/height).  Test:
`test_texture_filename_is_only_the_basename`.

## Curves: arcs, circles, polylines

Pairs: `stage3/oracle/{arc_open,circle_face,circle_hole,polyline,curve_component}.skp`.

Records (SketchUp 2025):

* Curve pools live in the entity container **between the vertex pool and the
  edge pool**: `0x1396` for plain curves, `0x1397` for arc curves.
* A curve record `0x4a38`: entity base (`0x05dc > 0x05de` id), `0x4a39` u32
  member-edge count, `0x4a3a` u8 0, `0x4a3b` first member edge id, `0x4a3c`
  last member edge id, in start-to-end chain order.
* An arc curve is `0x4c2c > { 0x4a38, 0x4c2d }`; `0x4c2d` is 16 f64:
  centre (3), unit normal (3), plane offset (−normal·centre), radius-scaled x
  axis (3), radius-scaled y axis (3), radius, start angle, end angle.  A full
  circle stores start 0 and end 0 (SketchUp reads it back as 0…2π).
* Every member edge carries `0x0bbb` = its curve's id, after the start/end
  vertex ids.
* The curve ids themselves do **not** appear in the `0x138e` order list;
  only the edges (and a face, if any) do.
* Hole loops run opposite to the outer ring: in `circle_hole.skp` the outer
  loop's 12 edge uses are forward (`0x0fa2` 0) and the inner loop's 8 are
  reversed (`0x0fa2` 1).  The writer reuses the circle's existing edges when a
  `CurveHandle` is passed as `holes=[...]`, which produces exactly that.
* `add_circle` creates the circle edges + ArcCurve and then its face;
  `add_circle_curve` stops after the curve so the handle can be used as a
  hole; `add_arc` creates edges + ArcCurve only; `add_polyline` creates edges
  + a plain Curve (optionally closed).  Segment counts, angles and the
  3…255 range mirror `create.py`.

SketchUp acceptance: `stage3/accept.rb` reports for the written file
`Sketchup::ArcCurve` curves with 6/8/12 edges (centre, radius 10/6/12,
normal, start/end angles, `is_polygon? false`), one `Sketchup::Curve` with 3
edges for the polyline, the holed face's two loops (12 + 8 edges), and the
same curves inside the twice-placed component (8-edge circle, 6-edge arc,
2-edge curve).  SketchUp re-saved the file and openskp parses both with
identical arcs, curve membership, loops, holes, attributes and transforms.

## Attribute dictionaries

Pairs: `stage3/oracle/attributes.skp` and `attrs_texture.skp`.

Layout (all inside the entity's `0x05dc > 0x05dd` block):

```
0x36b1 > 0x36b2
   0x2710                         … texture placement, when present, first
   0x36b3 ×N                      dictionaries, in insertion order
     0x36b0 empty
     0x36b4 dictionary name
     0x36b5 { 0x36b6 key, 0x38a4 value } × N
     0x36b7 u32 0
```

Value records (`0x38a4`, empty wrapper = null):

| Ruby value | tag | payload |
|---|---|---|
| String | `0x38ad` | UTF-8 |
| Integer | `0x38a7` | i32 LE |
| Float | `0x38a9` | f64 |
| true/false | `0x38aa` | 1 byte 0/1 |
| Time | `0x38ac` | u32 unix seconds LE |
| Length | `0x38af` | f64 inches |
| Point3d | `0x38b4` | 3×f64 |
| Vector3d | `0x38b5` | 3×f64 |
| Array | `0x38ae` | its payload is a sequence of `0x38a4` records (nested arrays recurse) |
| nil | empty `0x38a4` | — |

The writer supports `str`, `int`, `float`, `bool`, `None`, `Point3d`,
`Vector3d`, `Length`, `Timestamp` and nested lists/tuples, reusing create.py's
types (`Length` = float inches, `Timestamp` = u32 seconds).  Dictionaries are
written on faces, component definitions (their container's drawing element),
instances, groups (their inner placement) and the model root (appended after
the template's own GeoReference/temp/contributors dictionaries).  The
`attributes`/`attribute_dict_name`/`attribute_dicts` parameters match
create.py, including refusing both shorthand forms at once.

**Reader support:** `_core._decode_vff_attr_value` now decodes `AA38`
(one byte) as a Python bool and `AC38` (u32) as a Python int - the same value
and type the classic reader returns for its `time_t` (legacy type `0x09`,
`r.u32()`).  Unknown tags and wrong payload sizes still return `None`, and an
empty `A438` remains explicit null.  This was added after the SketchUp 2025
Ruby-saved `attributes.skp` provided the evidence; a reader test parses a
copy of that genuine file (`tests/fixtures/vff_writer/genuine_attributes.skp`)
and asserts True/False/1700000000 on the model, definition, instances, group
and face dictionaries.  `_stringify_vff_attr_value` renders bools as "1"/"0"
to stay identical to `legacy._stringify_attr_value`'s int-based rendering
(the classic reader decodes its bool tag as `u8`), so
`extract_dynamic_properties` and the scene/instance paths are unchanged for
classic files.  Every other type (strings, ints, floats, points, vectors,
Length-as-float, arrays/nested arrays, null) was already asserted through
openskp's reader, including face dictionaries via
`_core.extract_attribute_dictionaries`.

## Stage-3 acceptance

`stage3/written_stage3.skp` (SHA-256
`a7676674dec086c5a444f2f492d55dee04d29be4bab924fdfda5c7978fc78113`) is the
combined model: open arc, circle face with circular hole, polyline,
textured face (basename filename), face with every attribute type, component
with circle+arc+polyline placed twice (definition/instance attributes), painted
and tagged group with attributes, and model-level attributes.

* `Sketchup.open_file` returned true with **no warning dialog**.
* Ruby dump: root has 3 `ArcCurve`s (6/8/12 edges, radius 10/6/12, centre,
  normal, start/end angles) and one 3-edge plain `Curve`; the holed face has
  two loops (12 + 8); the component has an 8-edge circle, 6-edge arc and
  2-edge curve; `face.attribute_dictionaries` shows String/Integer/Float/
  TrueClass/FalseClass/Array/Length/Geom::Point3d/Geom::Vector3d/Time/
  NilClass values; definition, instance, group and model dictionaries are all
  present with the expected classes; the GroupRed/GroupTag group is intact.
* SketchUp re-saved it as `stage3/sketchup_resave.skp` (SHA-256
  `2ee97954483bc3df7621ceacc7bc6b7e3294ded390bc11619689a9ab0dfbbd98`).
  openskp parses both with identical revision 17, integrity, root faces,
  curve membership/arc frames, instances, definitions (including curve
  membership), model dictionaries, materials (basename texture) and layers.
* `skpwright verify` accepts both files.

No stage-3 feature was refused by SketchUp; the only notable operational
quirk is the already-documented stage-2 one (SketchUp's save can sit behind a
dialog when a model holds unused materials, so the oracle models paint every
material).

# Stage 4 research notes: text, dimensions, guides, section planes

## Text (CText)

Pairs: `stage4/oracle/text_leader.skp` (two leader texts) and
`text_screen.skp` (a screen text).

A text is `0x55f0` in the `0x1398` pool (pools come after the face pool and
before `0x139e`), and its id is listed in `0x138e`.  Child records, in order:

| tag | contents |
|---|---|
| `0x07d0` | drawing element (entity id, optional material/layer, flags) |
| `0x55f1` | the display string (UTF-8) |
| `0x55ff` | empty |
| `0x55f2`, `0x55f3` | one f64 zero each (unused) |
| `0x55f4` | anchor connection `0x5208 { 0x5209 type 1, 0x520a point,
  0x520b/0x520c -> 0x53fc -> 0x53fe empty }` (free point) |
| `0x55f5` | three f64: the label offset (label at point + vector) |
| `0x55f6` | u8 1 |
| `0x55f7` | three f64 (0,0,1) |
| `0x55f8` | u8 0 |
| `0x55f9` | font id (the template's single `0x5014` font, 0x3eaf in blank_vff) |
| `0x55fa` | u32 arrow type (3) |
| `0x55fb` | u32 1 |
| `0x55fc` | u8: leader displayed (1) / screen text (0) |
| `0x55fd` | u32 leader type (2) |
| `0x55fe` | u32: 0 leader text, 1 screen text |

SketchUp's Ruby `Text#has_leader?` stays true for a screen text created with
`add_text(...); text.display_leader = false`; the acceptance dump shows
`has_leader? true, display_leader? false`.  The API cannot set
`Text#attached_to` directly (it expects an InstancePath), so the writer
anchors leader texts at the free anchor point (the box corner / a point on a
face), which SketchUp reads back exactly.

## Linear dimensions (CDimensionLinear)

Pair: `dim_free.skp` (free points), `dim_vertices.skp` (anchored to two
vertices) and `dim_component.skp` (inside a definition placed twice).

`0x5bcc` in the `0x1399` pool, id in `0x138e`:

```
0x59d8 { 0x07d0, 0x59d9 empty, 0x59da font id, 0x59db u8 0, 0x59dc u32 3 }
0x5bcd / 0x5bce   connection:
    free    0x5208 { 0x5209 u32 1, 0x520a point, 0x520b/0x520c -> 0x53fc -> 0x53fe }
    anchored0x5208 { 0x5209 u32 2, 0x520a zeros,
                     0x520b/0x520c -> 0x53fc { 0x53fd vertex id, 0x53fe empty } }
0x5bcf  3 f64 plane normal
0x5bd0  3 f64 dimension x axis (the measured direction)
0x5bd1  u32 1
0x5bd3  f64 0
0x5bd2  f64 signed offset
0x5bd4  u32 3
```

SketchUp's `DimensionLinear#offset_vector` is `offset * (xaxis × normal)`;
the writer derives a mutually perpendicular normal/xaxis from the requested
offset vector and stores `-|offset|` as the signed offset (matching both
oracle pairs).  An anchored dimension resolves its endpoints to existing
vertex ids in the same container, so a dimension inside a definition stores
the definition-local vertex ids; openskp's global dimension scanner returns
the component dimension's local points (once per definition record) and the
world-space root dimension.

## Guides (ConstructionLine / ConstructionPoint)

Pair: `guides.skp` (bounded line, unbounded line, point).

* Guide lines are `0x4269` in pool `0x1391`.  Their drawing element is
  wrapped in **`0x4268`**, not `0x07d0` (this was the one stage-4 bug: using
  `0x07d0` made SketchUp reject the file with a warning).  Children:
  `0x4268 { 0x07d0 ... }`, `0x426a` eight f64 (point, unit direction,
  start/end distance parameters), `0x426b` u16 stipple mask (`0xffff` =
  solid).
* Bounded guides store params `0` and `length`; unbounded ones store the
  classic `±1e30` sentinel, so SketchUp's `start`/`end` are `nil`.
* Guide points are `0x426c` in pool `0x1392`: `0x4268 { 0x07d0 }`,
  `0x426d` position (3 f64), `0x426e` three zero f64, `0x426f` u8 0.

## Section planes

Pair: `section_planes.skp` (two planes, named/symboled, first active).

`0x445c` in pool `0x1393`, id in `0x138e`:

```
0x07d0 drawing element
0x445d plane 4 f64 (a, b, c, d), normalized normal, d = -normal·point
0x445e name (UTF-8)
0x445f symbol (UTF-8; "A")
```

The active section's id is a `0x1394` record in the entity container, placed
between `0x138e` and `0x139b`.  The writer sets it when
`add_section_plane(..., active=True)` (or `set_active_section_plane`).

## Reader additions

`_core.py` now decodes:

* texts (`0x55f0`): string, anchor point, label point (point + vector),
  hidden flag;
* dimensions (`0x5bcc`) as container-owned `Definition.dimensions` entries
  (text/hidden; the geometric root/world list already came from the
  existing `_parse_dimensions` scanner);
* section planes (`0x445c`): plane, name, symbol (exposed as `label`), hidden;
* the active-section id (`0x1394`) on the internal geometry builder;
* guide lines/points were already decoded; their `0x4268` drawing-element
  wrapper is walked by the existing `_find_tag` logic.

Container tags were extended for the text/dimension/section pools and their
inner records.

## Stage-4 acceptance

`stage4/written_stage4.skp` (SHA-256
`e13adacd8e4ff4ed3f8665053c4b99db315f9ceedd0b17ba5cc774c43e11b94b`) is the
combined model: box, corner + face leader texts, a screen text, an anchored
dimension on the box, a component with an anchored dimension placed twice,
a bounded guide, an unbounded guide, a guide point, and two named/symboled
section planes with the first active.

* `Sketchup.open_file` returned true with **no warning dialog**.
* Ruby dump: `text0` corner label point (0,0,0) + vector (10,10,10),
  `has_leader? true`; `text1` face label; `text2` screen label with
  `display_leader? false`; `dim0` starts/ends attached to the two
  `Sketchup::Vertex` objects with text `10"` and offset vector (0,-8,0);
  `cline0` bounded (start/end 0,0,0 .. 20,10,5), `cline1` unbounded
  (`start`/`end` nil, direction (0,0.707,0.707)); `cpoint0` (5,5,5);
  `section0` plane (0,0,1,-5) name "Section A" symbol "A" active true,
  `section1` plane (1,0,0,-5) name "Section B" symbol "B" active false;
  the component definition has its own dimension.
* SketchUp re-saved it as `stage4/sketchup_resave.skp` (SHA-256
  `dc160ed57ac63abae00db054f5681606e671cea4ba862eeb0e8822d554095f8b`);
  openskp parses both with identical revision, integrity, texts and labels,
  dimensions (points, offset, normal/x axis), guides, guide points, section
  planes/names/symbols and per-definition annotation counts.
* `skpwright verify` accepts both files.

The only refused feature was the pre-fix guide record (drawing element
`0x07d0` instead of `0x4268`), which produced SketchUp's warning dialog;
after the wrapper fix every annotation opened cleanly.  No other stage-4
record was refused.

# Stage 5 research notes: scenes and the model camera

## Scene records

Pairs: `stage5/oracle/scene_perspective.skp` (one scene), `scene_two.skp`
(parallel + perspective), `scene_hidden.skp` (a scene hiding `TagB`) and
`stage5_target.skp` (three scenes with the first selected).

The scene section is top-level `0x0207`: `0x6d60 { 0x6d61 (pages), 0x6d62
(selected page id) }`.  Each page is `0x7148`:

```
0x6f54 { entity base (page id), 0x6f55 name [, 0x6f56 description] }
0x7149 u32 properties saved (1 camera, 2 style/rendering, 4 shadows,
       8 axes, 16 hidden geometry, 32 hidden layers, ...)
0x714a { 0x34bc camera }              (bit 1)
0x714c style id                       (bit 2)
0x714d { 0x733c rendering options }   (bit 2)
0x714e { 0x6590 shadow info }         (bit 4)
0x714f { 0x4650 axes }                (bit 8)
0x7150 hidden-layer id list (u8 width + var-int id per entry) (bit 32)
0x7152 u8 included in animation
0x7153 display name
0x7154 / 0x7155 f64 transition time / delay (-1 = model default)
0x7157 u8 thumbnail flag
```

The camera record `0x34bc` is the same one the model uses (`0x01fa >
0x34bc`): `0x34bd` eye, `0x34be` target, `0x34bf` up (3 f64 each), `0x34c0`
and `0x34c1` constants, `0x34c2` u8 perspective flag (1 perspective, 0
parallel), `0x34c3` visible height (inches; only meaningful in parallel -
SketchUp stores a stale 1.0 in perspective), `0x34c4` field of view degrees,
plus constant fields `0x34c5`-`0x34ce`.  The stored up vector is always
orthogonalized against the view direction (the Ruby camera's `up` value is
not stored verbatim when it is not perpendicular).

The writer emits properties `1|2|4|8|32` (camera, style/rendering, shadows,
axes, hidden layers), copying `0x733c` / `0x6590` / `0x4650` and the style id
from the template's own sections and writes the page's hidden-layer list.
All new pages are appended to any existing page list, and the first page is
the selected one (`0x6d62`).

## Model camera

`0x01fa > 0x34bc` is the camera the file opens with.  The writer sets the
standard Iso view by default - eye `(100, -100, 100)`, target `(0, 0, 0)`,
up Z orthogonalized, parallel projection, ortho height 200 - matching
`create.py`'s Iso default, via `VffBuilder.set_camera(...)` (eye, target, up,
perspective, fov, ortho_height) if the caller wants another view.  The
`34bc` record is rebuilt from the template's own record with the eye / target
/ up / perspective / fov / height leaves replaced, so any constant fields
survive.

## Stage-5 acceptance

`stage5/written_stage5.skp` (SHA-256
`d4d557bbd4b49fc11c6ea88ede3d6ca167b47ad3e1d1d729893cb6c0cc0a38b7`) contains
a box on `TagA` and a box on `TagB` and three scenes: Perspective (fov 45),
Parallel (height 80), and Hide B (hides `TagB`), with the model camera set to
the standard Iso view.

* `Sketchup.open_file` returned true with **no warning dialog** and three
  pages.
* Ruby dump: model camera eye (100,-100,100), target (0,0,0), perspective
  false, fov 35, height 200; page Perspective eye (40,-40,30), fov 45,
  perspective true, height 1; page Parallel eye (0,-60,0), up (0,0,1),
  perspective false, fov 35, height 80; page Hide B carries the hidden-layer
  list.  `selected_page` is "Perspective".
* Switching to each page (with `transition_time = 0` forced for the test,
  since SketchUp animates page changes) applies the stored camera exactly:
  Perspective (40,-40,30) fov 45 perspective, Parallel (0,-60,0) fov 35
  perspective false height 80, Hide B (40,-40,30) with `TagB` hidden and
  `TagA` visible.
* SketchUp re-saved it as `stage5/sketchup_resave.skp` (SHA-256
  `c6a0118426ec339d867dc3807124dd651a764f2b499e06d1b68451eaa52b0cf0`);
  openskp parses both with identical revision, integrity, all three pages
  (names, eye/target/up, perspective/fov/height, hidden tags), the model
  camera record and the layers.  `skpwright verify` accepts both.

No stage-5 record was refused by SketchUp.  The only subtlety is that page
switches animate by default, so the acceptance script forces an instant
transition before reading the applied camera; the stored pages keep the
model's default transition time (-1).
