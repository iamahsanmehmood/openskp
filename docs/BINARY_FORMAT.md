# SketchUp Binary Format Specification (VFF)

> **Reverse-engineered by the OpenSKP project.**  
> Applies to SketchUp 2021+ files using the VFF container format.

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

These tags contain nested child TLV elements:

```
7C15  8813  8913  8A13  8B13  8D13  4C1D  6419
F901  7017  7117  D007  C409  9411  9511  0F01
384A  B80B  9713  2C4C  AC0D  AE0D  F601  F801
983A  993A  8C3C  8D3C
```

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

### 3.2 Topology Tags

| Tag | Name | Payload | Description |
|-----|------|---------|-------------|
| `9411` | **Loop** | Container | Ordered sequence of coedges |
| `A00F` | CoEdge | Inline TLV | Edge reference + orientation |
| `A10F` | Edge Ref | var_int | Referenced edge entity ID |
| `A20F` | Orientation | var_int | `1` = forward, `0` = reversed |

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

| Tag | Name | Payload | Description |
|-----|------|---------|-------------|
| `B636` | Property Key | ASCII | Dynamic attribute name |
| `AD38` | Property Value | ASCII | Dynamic attribute value |

## 4. Coordinate System

| Property | SketchUp | glTF (OpenSKP output) |
|----------|----------|-----------------------|
| Up axis | Z | Y |
| Units | Inches | Millimeters |

**Conversion formula:**

```
x_mm =  x_inches × 25.4
y_mm =  z_inches × 25.4   (Z-up → Y-up)
z_mm = -y_inches × 25.4   (flip Y → -Z)
```

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
