# CDimensionLinear (legacy v17/v18) — field notes for the writer

Harvested from real files (quiroz: 28 dims, casa bueno: 16, yanque 661MB)
while building the reader fixes (PR #194). Basis for `add_dimension()`.

## Record layout (after the class tag)

```
preamble                  attrs ref (null) + pid mask/bytes (v17+)
drawbase                  10 bytes (mat u16, hidden, soft, smooth, layer u16)
text     utf16            EMPTY on every auto-computed dimension observed
font     object           CSkFont — new on first use, back-ref after
B37      37 bytes         u8 + u32(=3 v18 / 1 v17?) + 32 bytes zeros
ref1     MFC tag          connection 1 → CVertex back-ref (2 or 6 bytes)
B42      42 bytes         u16 + 40 bytes: u32(=2) + u32(=4) + zeros
ref2     MFC tag          connection 2 → CVertex back-ref
B82      82 bytes         u16 + f64×7 + u32 + f64(OFFSET, inches, signed)
                          + f64(0) + u32(=3)
```

- NO coordinates are cached: geometry comes 100% from the two vertex
  back-refs. Anchored dims in v17 (quiroz) burn NO MapObject indices —
  writing anchored dims is safe for slot accounting.
- The B82 offset f64 (bytes 62..69) is the dimension-line offset from
  the measured edge, inches, signed.
- The u32 at B82+58 varies (1/2/3) — placement/alignment mode?
- The 7 doubles at B82+2..57 are the unsolved part: values are 0/±1
  (plus float-noise) and DO vary with orientation, but the samples are
  too axis-aligned to fix the semantics:
    quiroz  (dir ±Z): (0, 0,  1, 0, -1, 0, 0)   u32=2
    casa#0  (dir +X): (0, 0,  0, -1, -1, 0, 0)  u32=1
    casa#2  (dir +Y): (0, ~0, ~0, -1, -1, ~0, ~0) u32=2
  Candidate readings: two 2D unit attachment points + spare, or a plane
  basis with per-axis sign flags. TO RESOLVE: template-write dims in
  varied orientations and check rendering in real SketchUp (Web) — the
  same controlled-file method the July texture calibration used.

## CSkFont

Small record (~34 bytes) with the face name; one per file re-used by
back-ref. Template from quiroz.

## Writer strategy

Template + patch: emit quiroz's byte template, patching refs (our
vertex slots), offset, font ref, pid bytes; leave the 7 doubles as a
per-orientation hypothesis to be calibrated against SketchUp Web
rendering. Validate every generated file by round-tripping through the
(fixed) legacy reader before human inspection.

## RESOLVED — SDK ground truth (2026-08-20)

A MinGW-built helper (`mkdim`) drove the real SketchUpAPI.dll under
Wine to CREATE dimensions in every orientation and save as SU2017
(`SUModelSaveToFileWithVersion`, version enum: 6={13} … 10={17} 11={18}).
The harvested records settle everything:

- B37 / B42 are the CONNECTION blocks: `[flags][u32 TYPE][u32 4]
  [point3d]` with TYPE **1 = free point stored inline** (object ref is
  null) and **2 = anchored** (point zeroed, back-ref follows) — the same
  1/2 enum as VFF connections.
- The placement head for FREE dimensions is a constant SketchUp
  default: seven doubles `(0,0,0, 1,1,0, 0)`, mode u32 = 0, trailing
  u32 = 1. The offset lives at B82+62 and can be patched in.
- The orientation-specific heads seen in human-drawn corpus files
  (quiroz) apply to ANCHORED dimensions; decoding those fully would be
  the follow-up if anchored writing is wanted (extend mkdim with
  anchored creates).

`add_dimension()` writes byte-exact SDK-shaped free dimensions;
verified by SDK acceptance, reader round-trip, and rendering in real
SketchUp (Web) across X/Y/Z/diagonal orientations.

## CText (leader texts) — same Rosetta method (2026-08-20)

A second SDK helper (`mktext`) settled the text record. Key findings:

- A `SUTextRef` needs a FONT assigned (`SUTextSetFont` with a font from
  `SUModelGetFonts`) or the model fails to serialize (SUResult 7) in
  every version — the failure is the text, not the target version.
- Record layout: preamble + drawbase + font (inline first use /
  back-ref) + two f64 0.5 + the SAME free-connection block dimensions
  use (`[u32 1][u32 4][point3d]`) + a fixed 75-byte tail + string +
  5 zero bytes.
- The reader's "text delimiter" block encodes the ARROW TYPE in its
  u32 (values 0-4 seen); the old predicate hard-required 3 and silently
  dropped any text with a different arrow (root-list tolerance ate the
  error). Now any arrow ≤ 8 matches.
- `_read_skfont` consumed the pid MASK but not the pid bytes it
  declares — fixed; SDK-written fonts also carry a longer tail, which
  the text reader's delimiter scan absorbs.
- Leader VECTORS never persist through the SDK — not even when the
  text is anchored to real geometry via `SUInstancePath` (a second
  harness, `mktext2`, proved it: face- and vertex-anchored records
  save fine but the leader stays zeroed, and face+leader texts are
  silently DROPPED from the file). The SDK can only author SCREEN
  texts: the two doubles after the font are SCREEN FRACTIONS (0.5,
  0.5 = view centre) and SketchUp renders every such text superimposed
  there, detached from the model.
- Human-drawn LEADER texts (casa bueno, quiroz) settle the real
  grammar. Connection types: 1 = free 3D anchor inline, 2 = anchored
  to an entity back-ref (like dimensions), 7 = attached to a FACE
  (two u,v bbox fractions + face ref). The placement tail after the
  connection is `[12B zeros][point3d LABEL world position][16B zeros]
  [f64 1.0][u32 LEADER TYPE (0 none / 2 pushpin)][delimiter]` — the
  leader line joins the label position to the anchor.
- `add_text()` therefore writes the HUMAN shape, not the SDK's:
  screen slot zeroed, free connection with the anchor, label position
  at ``point + leader``, leader type 2, closed arrow (3). The reader
  extracts both points (``TextEntity.point`` / ``label_point``).
- Every connection type is followed, as in a dimension, by the
  anchored entity's ref and its instance paths: `[entity ref][entity
  ref][u32 n][n path refs][u32 n][n refs]` — the placement tail's 12
  zero bytes for types 0 and 1. Anchored labels carry
  real refs there (types 2, 4, 5 and 7 in the 25 SketchUp 2015 models
  of the BRAS room-acoustics benchmark, 2026-10-28), and a path
  instance not written yet comes IN FULL, class declaration included
  when it is the file's first instance. Every port's reader reads the block
  for types other than 0 and 1; skipped as bytes, it shifted every later
  store-map slot. The entity list's declared count holds such an
  instance even where SketchUp never lists it again (no back-ref), so
  the list keeps it as its own entity.

## Legacy C++ reader: inline vertices and plane offsets

A connection reference can be a complete `CVertex` object, including a new
class declaration or a short/escaped class reference. It is not always a
back-reference. Reading it into the archive preserves the slot numbering for
later dimensions and edges. Plain forward references are resolved after the
archive walk. Free connections store their xyz directly after type 1 and the
constant 4. Connections with instance paths still consume those variable-length
paths, but leave endpoints unset until those paths can be resolved in owner
coordinates.

After the second connection's path lists, the remaining placement block is
72 bytes (68 before version 6): three doubles for the plane normal, three for
the X axis, a u32 constrained axis, the signed offset double, a reserved double,
and the version-dependent trailing u32. SDK comparisons on axis-constrained
SU2018 dimensions identify mode 1 as offset along `normal × X` and mode 2 as
offset along `-X`. Multiplying by the stored signed distance preserves the
line's side even when measured endpoints are reversed. Other modes retain their
frame and scalar offset without inventing an explicit offset vector.

`LegacyDimensionGeometry` tests use generated records with synthetic vertices,
short and escaped references, forward references, variable-length paths and
plane frames. No user model is required.

## C++ writer: vector offsets

`SkpBuilder::add_dimension(p1, p2, offset_vector)` and the matching component
builder overload preserve a three-dimensional line offset. The X axis follows
the measured segment; its cross product with the offset determines the plane
normal and perpendicular distance. Mode 1 encodes the side in the normal, so
vertical and reversed segments retain their placement. Any component parallel
to the segment is discarded. Zero perpendicular offset uses a stable plane.
The scalar overload retains its existing default frame and mode 0.

Vector inputs are validated before any dimension record bytes are appended.
Tests cover root and nested dimensions, reversed endpoints, oblique planes,
zero/parallel offsets and invalid inputs followed by valid records. Independent
SketchUp SDK reads of an SU2018 accessory and its re-export agree on all three
dimensions' endpoints and bounding boxes.

## VFF reader: constrained plane offsets

Modern SKP dimensions store the plane normal in `5BCF`, the plane X axis in
`5BD0`, the constraint mode in `5BD1`, and the signed distance in `5BD2`.
Modes 1 and 2 use the same `normal × X` and `-X` offset directions as legacy
records. In particular, a vertical measurement with reversed endpoints must
not reverse the side of its offset. Ignoring `5BD1` let downstream geometric
heuristics flip an inward height annotation to the outside of the component.

The C++ reader now exposes `offset_vector` for these VFF modes as well, both
through raw dimensions and public model dimensions. The historical `plane_x`
and `normal` fields retain their original VFF tag assignments for compatibility;
use `offset_vector` for resolved placement. Missing, malformed, or unsupported
constraint modes keep the existing scalar/frame fallback.

Synthetic tests exercise both constraint modes, positive and negative distances,
reversed endpoints, public-model conversion, and invalid/missing mode records.
A downstream modern SKP reproduction additionally tests definition-local
annotations on direct load and re-export; SDK bounds put all three annotations
inside an 800 × 560 × 645 mm box.


## C++ annotation drawing properties and ownership

Dimensions and text now expose their own `layer` and `hidden` drawing properties.
Legacy annotations retain the drawing base already consumed by the archive walker.
VFF dimensions read `59D8 > 07D0 > 07D2/07D3`; text reads its own `07D0` base.
A referenced vertex or other connection target must not override these properties.
Numeric layer references are resolved to names alongside guide and instance layers.

VFF records inside a `7C15` definition populate that definition's annotations in
local coordinates. Root annotations populate `root()`. The existing world-space
`SkpModel::dimensions` view remains available for compatibility. Definition spans
are read only from the actual definition catalog, and candidate annotation records
must contain a complete, valid TLV field list before their payloads are copied.

Text retains free anchors or resolvable vertex attachments, label positions,
leader/arrow types and screen fractions. Legacy nested/inline text attachments
remain unresolved when their owner-local point cannot be established. This does
not add font/style or attachment-relationship preservation to the writer.

`DimensionOptions` and `TextOptions` allow root and component writers to preserve
tags and hidden state. Existing dimension overloads and the root three-argument
`add_text` overload remain available. A screen position overrides the text leader
type; invalid positions and layer handles are rejected before writing a record.

`annotation_layers_legacy.skp` and `annotation_layers_vff.skp` are SDK-generated
fixtures saved as 2019 and current VFF formats. They extend the repository's public
`legacy_text_arrows_2017.skp` fixture with root/nested dimensions, guide lines/points,
and screen text on three named tags, including hidden elements. They contain no
user model data. Tests verify each owner's tags, hidden states and text placement,
as well as writer round trips and invalid-options recovery.


### Text leader visibility

Native leader type 0 denotes screen text, even when a free 3D connection is present.
`TextOptions::leader_type == 0` without `screen_position` is therefore written as
model text (type 2) with `display_leader=false`. This canonicalization preserves
both the world anchor and label position without moving the note to the screen.

The independent display switch is the last byte of the legacy arrow delimiter
(immediately before the text string), and VFF field `55FC`. It must be accepted
as either 0 or 1 by the legacy reader. The SDK-generated annotation fixtures now
hide the first root text's leader while retaining its model placement. Tests cover
visible/hidden View and Model leaders, leaderless model text, root/component
ownership and unchanged screen fractions.
