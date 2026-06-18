# Reverse Engineering Methodology

## Overview

The SketchUp binary format was reverse-engineered through systematic binary analysis of `.skp` files produced by SketchUp 2021–2025. No decompilation or disassembly of SketchUp software was performed.

## Process

### Phase 1 — Container Discovery

1. Opened SKP files in a hex editor
2. Identified the `FF FE FF 0E` magic header
3. Discovered the `PK\x03\x04` ZIP signature embedded within
4. Extracted ZIP contents → found `model.dat`, material XMLs, thumbnails

### Phase 2 — TLV Structure Discovery

1. Observed that `model.dat` has a 16-byte header followed by repeating patterns
2. Identified the **Tag-Length-Value** encoding: 2-byte tag + 4-byte uint32 length + payload
3. Built a recursive parser to enumerate all unique tags
4. Identified container vs. leaf tags by checking if payloads contained valid nested TLV

### Phase 3 — Geometry Mapping

1. Created reference models with known geometry (single cube, simple building)
2. Searched binary data for known coordinate values (float64 patterns)
3. Mapped tag `C409` → Vertex, `C509` → XYZ coordinates (3×float64)
4. Mapped tag `B80B` → Edge, `B90B`/`BA0B` → vertex references
5. Mapped tag `AC0D` → Face, `AD0D` → normal, `AE0D` → boundary loops

### Phase 4 — Component Hierarchy

1. Placed multiple copies of a named component at known positions
2. Found `7C15` → Component Definition with GUID (`7D15`) and name (`7E15`)
3. Found `6419` → Component Instance with transform matrix (`6619`)
4. Identified the 13-element affine matrix layout: 3×3 rotation + translation + scale

### Phase 5 — Metadata Extraction

1. Created models with named layers and varied layer assignments
2. Found layer definitions in `993A`/`8C3C` containers
3. Mapped `D207` in entity properties → layer ID reference
4. Discovered dynamic component properties in nested `B636`/`AD38` key-value pairs

## Tools Used

- **Hex editor** — HxD for initial binary inspection
- **Python** — Custom scripts for systematic TLV enumeration
- **NumPy** — Float64 pattern matching in binary data
- **SketchUp Free** — Creating reference models with known geometry

## Challenges

1. **False-positive tags in string payloads** — ASCII text could contain byte sequences that look like TLV headers. Solved by only descending into known container tags.
2. **Variable-length entity IDs** — IDs can be 1–4 bytes depending on the model complexity. Required a variable-length integer decoder.
3. **Coordinate system conversion** — SketchUp uses Z-up inches; glTF expects Y-up. Required careful axis swapping and unit conversion.
4. **Nested DC05/DE05 wrappers** — Entity IDs sometimes appear directly as `DE05` tags and sometimes wrapped inside `DC05` containers.

## Validation

Tested against 10+ reference SKP models ranging from simple cubes to 131MB architectural models with hundreds of components.
