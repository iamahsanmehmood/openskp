//! AutoCAD R2000 (AC1015) 3D ASCII DXF export (`dxf_export.hpp` /
//! `dxf_export.cpp`).
//!
//! The output is byte-identical to the C++ port's, which was verified to open
//! in desktop AutoCAD: same static scaffold, same handle numbering, same
//! `\r\n` line endings and the same `%.6f` coordinate formatting.

use std::collections::BTreeMap;
use std::io;
use std::path::Path;

use crate::scene::{GlbPrimitive, Scene};

/// Characters AutoCAD rejects in a symbol-table (LAYER) name.
const ILLEGAL_LAYER_CHARS: &[char] = &[
    '<', '>', '/', '\\', '"', '~', ':', ';', '?', '*', '=', '`', '|',
];

fn is_trim_char(c: char) -> bool {
    matches!(c, ' ' | '\t' | '\r' | '\n')
}

/// 64-bit FNV-1a: what MSVC's `std::hash<std::string>` computes, so a
/// truncated layer name gets the same suffix the C++ port's Windows build
/// gives it (libstdc++/libc++ use other, equally implementation-defined
/// hashes; no one value can match every C++ build).
fn fnv1a64(bytes: &[u8]) -> u64 {
    let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
    for &b in bytes {
        hash ^= u64::from(b);
        hash = hash.wrapping_mul(0x0000_0100_0000_01b3);
    }
    hash
}

fn sanitize_layer_name(name: &str) -> String {
    if name.is_empty() {
        return "0".to_string();
    }
    let clean: String = name
        .chars()
        .map(|c| {
            if ILLEGAL_LAYER_CHARS.contains(&c) {
                '_'
            } else {
                c
            }
        })
        .collect();
    let clean = clean.trim_matches(is_trim_char);
    if clean.is_empty() {
        return "0".to_string();
    }
    // R2000 extended symbol names (enabled by $EXTNAMES=1 in the header
    // scaffold) support up to 255 characters, but AutoCAD desktop has been
    // reported to reject long table entry names in practice - cap
    // defensively and append a short content hash on truncation so two
    // different long names sharing a common prefix don't collide.
    const MAX_LEN: usize = 255;
    if clean.len() > MAX_LEN {
        let suffix = format!("{:08x}", fnv1a64(clean.as_bytes()) & 0xFFFF_FFFF);
        // The C++ cuts at a byte offset; a Rust string has to stay valid
        // UTF-8, so the cut backs off to the previous character boundary
        // (only ever different for a non-ASCII name).
        let mut cut = MAX_LEN - suffix.len() - 1;
        while !clean.is_char_boundary(cut) {
            cut -= 1;
        }
        return format!("{}_{}", &clean[..cut], suffix);
    }
    clean.to_string()
}

fn rgb_to_aci(r: i32, g: i32, b: i32) -> i32 {
    const STANDARD_ACI: [(i32, i32, i32, i32); 9] = [
        (255, 0, 0, 1),
        (255, 255, 0, 2),
        (0, 255, 0, 3),
        (0, 255, 255, 4),
        (0, 0, 255, 5),
        (255, 0, 255, 6),
        (255, 255, 255, 7),
        (128, 128, 128, 8),
        (192, 192, 192, 9),
    ];
    let mut best_aci = 7;
    let mut min_dist = 1e18;
    for (sr, sg, sb, aci) in STANDARD_ACI {
        let d = f64::from((r - sr) * (r - sr) + (g - sg) * (g - sg) + (b - sb) * (b - sb));
        if d < min_dist {
            min_dist = d;
            best_aci = aci;
        }
    }
    best_aci
}

/// A float channel as C++'s `static_cast<int>(std::round(v * 255.0))`.
fn channel_to_int(v: f64) -> i32 {
    (v * 255.0).round() as i32
}

fn get_prim_rgb(scene: &Scene, prim: &GlbPrimitive) -> (i32, i32, i32) {
    let (mut r, mut g, mut b) = (200, 200, 200);
    if let Some(mat) = scene.gltf_materials.get(prim.material_index) {
        let factor = &mat.pbr_metallic_roughness.base_color_factor;
        r = channel_to_int(factor[0]);
        g = channel_to_int(factor[1]);
        b = channel_to_int(factor[2]);
    }
    (r.clamp(0, 255), g.clamp(0, 255), b.clamp(0, 255))
}

/// `std::fixed << std::setprecision(6)`: Rust's `{:.6}` matches it digit for
/// digit (exact decimal expansion, `-0.000000` for negative zero, `inf`);
/// only NaN is spelled differently, so it is spelled the glibc way here.
fn fmt6(v: f64) -> String {
    if v.is_nan() {
        return if v.is_sign_negative() { "-nan" } else { "nan" }.to_string();
    }
    format!("{v:.6}")
}

/// The next entity handle, as upper-case hex.
fn next_handle(handle_id: &mut u64) -> String {
    let h = format!("{:X}", *handle_id);
    *handle_id += 1;
    h
}

/// The DXF text being assembled: one group code or value per line, each
/// terminated with `\r\n`.
struct Lines(String);

impl Lines {
    fn push(&mut self, line: &str) {
        self.0.push_str(line);
        self.0.push_str("\r\n");
    }

    fn extend(&mut self, lines: &[&str]) {
        for line in lines {
            self.push(line);
        }
    }
}

/// Serializes a baked [`Scene`] into AutoCAD R2000 (AC1015) 3D ASCII DXF text.
///
/// `mode` `"polyface"` (the C++ default) writes one Polyface Mesh (POLYLINE
/// 70=64) per primitive; any other value writes one 3DFACE per triangle.
/// Every primitive goes on a layer named after its `geom_name`, colored by
/// its material's nearest ACI color. `scale` multiplies every vertex
/// coordinate; the C++ default is [`METRES_TO_INCHES`](crate::METRES_TO_INCHES).
pub fn to_dxf(scene: &Scene, scale: f64, mode: &str) -> String {
    let mut layer_colors: BTreeMap<String, (i32, i32, i32)> = BTreeMap::new();
    for prim in &scene.glb_primitives {
        layer_colors
            .entry(sanitize_layer_name(&prim.geom_name))
            .or_insert_with(|| get_prim_rgb(scene, prim));
    }
    if layer_colors.is_empty() {
        layer_colors.insert("0".to_string(), (200, 200, 200));
    }

    // Every static handle in HEADER_STATIC_*/OBJECTS_STATIC is well below
    // 0x691 - starting dynamic handles there (matching the reference file's
    // own first entity handle) keeps every handle in the file globally
    // unique.
    let mut handle_id: u64 = 0x691;
    let layer_handles: BTreeMap<&str, String> = layer_colors
        .keys()
        .map(|name| (name.as_str(), next_handle(&mut handle_id)))
        .collect();

    let mut lines = Lines(String::new());
    lines.extend(HEADER_STATIC_A);
    // LAYER table record count: built-in "0" + "Defpoints" + this scene's layers.
    lines.push(&(layer_colors.len() + 2).to_string());
    lines.extend(HEADER_STATIC_B);

    for (l_name, &(lr, lg, lb)) in &layer_colors {
        let aci = rgb_to_aci(lr, lg, lb);
        // Group 420 (24-bit true color) is an R2004+ addition - real ezdxf
        // never emits it for an R2000/AC1015 document, so it's dropped here
        // too; ACI (62) is the only color mechanism R2000 actually supports.
        // 370/390 (lineweight, plot style handle) are required on every LAYER
        // record once the drawing uses a Named plot style table - AutoCAD
        // desktop rejects the whole TABLES section ("Did not receive
        // PlotStyleName") if any layer omits them.
        lines.push("  0");
        lines.push("LAYER");
        lines.push("  5");
        lines.push(&layer_handles[l_name.as_str()]);
        lines.push("330");
        lines.push("1");
        lines.push("100");
        lines.push("AcDbSymbolTableRecord");
        lines.push("100");
        lines.push("AcDbLayerTableRecord");
        lines.push("  2");
        lines.push(l_name);
        lines.push(" 70");
        lines.push("0");
        lines.push(" 62");
        lines.push(&aci.to_string());
        lines.push("  6");
        lines.push("Continuous");
        lines.push("370");
        lines.push("-3");
        lines.push("390");
        lines.push("13");
    }

    lines.extend(HEADER_STATIC_C);
    lines.push("  0");
    lines.push("SECTION");
    lines.push("  2");
    lines.push("ENTITIES");

    for prim in &scene.glb_primitives {
        let l_name = sanitize_layer_name(&prim.geom_name);
        let tri_count = prim.indices.len() / 3;
        if tri_count == 0 {
            continue;
        }

        let (pr, pg, pb) = get_prim_rgb(scene, prim);
        let aci = rgb_to_aci(pr, pg, pb).to_string();

        if mode == "polyface" {
            // Structure verified against real ezdxf's own render_polyface()
            // output byte-for-byte, not guessed: face-record VERTEX entries
            // carry a color (62) and dummy 0/0/0 coordinates but NOT the
            // AcDbVertex subclass marker that point-vertex entries have, and
            // SEQEND's owner (330) is the POLYLINE's own handle, not
            // Model_Space's - both were wrong here before and are exactly why
            // AutoCAD desktop rejected polyface-mode output while accepting
            // 3dface-mode output using the same scaffold.
            let vert_count = prim.positions.len() / 3;
            let polyline_handle = next_handle(&mut handle_id);

            lines.push("  0");
            lines.push("POLYLINE");
            lines.push("  5");
            lines.push(&polyline_handle);
            lines.push("330");
            lines.push("17");
            lines.push("100");
            lines.push("AcDbEntity");
            lines.push("  8");
            lines.push(&l_name);
            lines.push(" 62");
            lines.push(&aci);
            lines.push("100");
            lines.push("AcDbPolyFaceMesh");
            lines.push(" 66");
            lines.push("1");
            lines.push(" 10");
            lines.push("0.0");
            lines.push(" 20");
            lines.push("0.0");
            lines.push(" 30");
            lines.push("0.0");
            lines.push(" 70");
            lines.push("64");
            lines.push(" 71");
            lines.push(&vert_count.to_string());
            lines.push(" 72");
            lines.push(&tri_count.to_string());

            for v in prim.positions.as_chunks::<3>().0 {
                lines.push("  0");
                lines.push("VERTEX");
                lines.push("  5");
                lines.push(&next_handle(&mut handle_id));
                lines.push("330");
                lines.push("17");
                lines.push("100");
                lines.push("AcDbEntity");
                lines.push("  8");
                lines.push(&l_name);
                lines.push("100");
                lines.push("AcDbVertex");
                lines.push("100");
                lines.push("AcDbPolyFaceMeshVertex");
                lines.push(" 10");
                lines.push(&fmt6(f64::from(v[0]) * scale));
                lines.push(" 20");
                lines.push(&fmt6(f64::from(v[1]) * scale));
                lines.push(" 30");
                lines.push(&fmt6(f64::from(v[2]) * scale));
                lines.push(" 70");
                lines.push("192");
            }

            for tri in prim.indices.as_chunks::<3>().0 {
                lines.push("  0");
                lines.push("VERTEX");
                lines.push("  5");
                lines.push(&next_handle(&mut handle_id));
                lines.push("330");
                lines.push("17");
                lines.push("100");
                lines.push("AcDbEntity");
                lines.push("  8");
                lines.push(&l_name);
                lines.push(" 62");
                lines.push(&aci);
                lines.push("100");
                lines.push("AcDbFaceRecord");
                lines.push(" 10");
                lines.push("0.0");
                lines.push(" 20");
                lines.push("0.0");
                lines.push(" 30");
                lines.push("0.0");
                lines.push(" 70");
                lines.push("128");
                lines.push(" 71");
                lines.push(&tri[0].wrapping_add(1).to_string());
                lines.push(" 72");
                lines.push(&tri[1].wrapping_add(1).to_string());
                lines.push(" 73");
                lines.push(&tri[2].wrapping_add(1).to_string());
            }

            lines.push("  0");
            lines.push("SEQEND");
            lines.push("  5");
            lines.push(&next_handle(&mut handle_id));
            lines.push("330");
            lines.push(&polyline_handle);
            lines.push("100");
            lines.push("AcDbEntity");
            lines.push("  8");
            lines.push(&l_name);
            continue;
        }

        // One coordinate of vertex `index`, scaled. An index past the end of
        // `positions` (undefined behavior in the C++) reads as 0 here.
        let coord = |index: u32, axis: usize| -> String {
            let v = (index as usize)
                .checked_mul(3)
                .and_then(|i| prim.positions.get(i + axis))
                .copied();
            fmt6(f64::from(v.unwrap_or(0.0)) * scale)
        };

        for tri in prim.indices.as_chunks::<3>().0 {
            let (i0, i1, i2) = (tri[0], tri[1], tri[2]);
            let (v0x, v0y, v0z) = (coord(i0, 0), coord(i0, 1), coord(i0, 2));
            let (v1x, v1y, v1z) = (coord(i1, 0), coord(i1, 1), coord(i1, 2));
            let (v2x, v2y, v2z) = (coord(i2, 0), coord(i2, 1), coord(i2, 2));

            lines.push("  0");
            lines.push("3DFACE");
            lines.push("  5");
            lines.push(&next_handle(&mut handle_id));
            lines.push("330");
            lines.push("17");
            lines.push("100");
            lines.push("AcDbEntity");
            lines.push("  8");
            lines.push(&l_name);
            lines.push(" 62");
            lines.push(&aci);
            lines.push("100");
            lines.push("AcDbFace");
            lines.push(" 10");
            lines.push(&v0x);
            lines.push(" 20");
            lines.push(&v0y);
            lines.push(" 30");
            lines.push(&v0z);
            lines.push(" 11");
            lines.push(&v1x);
            lines.push(" 21");
            lines.push(&v1y);
            lines.push(" 31");
            lines.push(&v1z);
            lines.push(" 12");
            lines.push(&v2x);
            lines.push(" 22");
            lines.push(&v2y);
            lines.push(" 32");
            lines.push(&v2z);
            lines.push(" 13");
            lines.push(&v2x);
            lines.push(" 23");
            lines.push(&v2y);
            lines.push(" 33");
            lines.push(&v2z);
        }
    }

    lines.push("  0");
    lines.push("ENDSEC");
    lines.extend(OBJECTS_STATIC);

    let mut text = lines.0;
    let handseed = format!("{:X}", handle_id + 0x10);
    if let Some(pos) = text.find(HANDSEED_PLACEHOLDER) {
        text.replace_range(pos..pos + HANDSEED_PLACEHOLDER.len(), &handseed);
    }
    text
}

/// Exports a baked [`Scene`] directly to an AutoCAD R2000 3D DXF file (see
/// [`to_dxf`]), creating the parent directory if needed.
pub fn export_dxf(scene: &Scene, path: impl AsRef<Path>, scale: f64, mode: &str) -> io::Result<()> {
    let path = path.as_ref();
    if let Some(parent) = path.parent().filter(|p| !p.as_os_str().is_empty()) {
        std::fs::create_dir_all(parent)?;
    }
    let content = to_dxf(scene, scale, mode);
    std::fs::write(path, content.as_bytes()).map_err(|e| {
        io::Error::new(
            e.kind(),
            format!("Failed to open output file: {}: {e}", path.display()),
        )
    })
}

/// The `$HANDSEED` value in [`HEADER_STATIC_A`], filled in once every handle
/// has been handed out.
const HANDSEED_PLACEHOLDER: &str = "__HANDSEED__";

// Static AutoCAD R2000 (AC1015) boilerplate, extracted byte-for-byte from a
// real ezdxf-generated file independently confirmed to open cleanly in
// desktop AutoCAD - not just lenient third-party readers. Desktop AutoCAD
// validates far more strictly than ezdxf.readfile() (or ezdxf's own
// .audit(), which also reports zero errors on incorrect output) or web
// viewers do: it needs the full CLASSES table, a real *Active VPORT
// record, and a complete OBJECTS dictionary tree (root dictionary plus
// LAYOUT records for Model/Layout1), or it silently refuses to open the
// file. Split into three header pieces so the LAYER table's per-scene
// entries can be spliced between the built-in "0"/"Defpoints" records
// (HEADER_STATIC_B) and the rest of TABLES/BLOCKS (HEADER_STATIC_C);
// OBJECTS_STATIC needs no splicing - it carries no per-scene data at all.
// Every handle in this scaffold is a small hex literal well below 0x691,
// where dynamic (layer/entity) handles start - matching the exact starting
// handle the reference file itself uses for its own first entity, so every
// handle in an exported file stays globally unique ("monomorphic").
//
// Generated mechanically from dxf_export.cpp's kHeaderStaticA/B/C and
// kObjectsStatic (one string literal per DXF line); do not edit by hand.

#[rustfmt::skip]
static HEADER_STATIC_A: &[&str] = &[
    "  0", "SECTION", "  2", "HEADER", "  9", "$ACADVER", "  1", "AC1015", "  9", "$ACADMAINTVER", " 70", "6", "  9",
    "$DWGCODEPAGE", "  3", "ANSI_1252", "  9", "$INSBASE", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9", "$EXTMIN",
    " 10", "1e+20", " 20", "1e+20", " 30", "1e+20", "  9", "$EXTMAX", " 10", "-1e+20", " 20", "-1e+20", " 30",
    "-1e+20", "  9", "$LIMMIN", " 10", "0.0", " 20", "0.0", "  9", "$LIMMAX", " 10", "420.0", " 20", "297.0", "  9",
    "$ORTHOMODE", " 70", "0", "  9", "$REGENMODE", " 70", "1", "  9", "$FILLMODE", " 70", "1", "  9", "$QTEXTMODE",
    " 70", "0", "  9", "$MIRRTEXT", " 70", "1", "  9", "$LTSCALE", " 40", "1.0", "  9", "$ATTMODE", " 70", "1", "  9",
    "$TEXTSIZE", " 40", "2.5", "  9", "$TRACEWID", " 40", "1.0", "  9", "$TEXTSTYLE", "  7", "Standard", "  9",
    "$CLAYER", "  8", "0", "  9", "$CELTYPE", "  6", "ByLayer", "  9", "$CECOLOR", " 62", "256", "  9", "$CELTSCALE",
    " 40", "1.0", "  9", "$DISPSILH", " 70", "0", "  9", "$DIMSCALE", " 40", "1.0", "  9", "$DIMASZ", " 40", "2.5",
    "  9", "$DIMEXO", " 40", "0.625", "  9", "$DIMDLI", " 40", "3.75", "  9", "$DIMRND", " 40", "0.0", "  9",
    "$DIMDLE", " 40", "0.0", "  9", "$DIMEXE", " 40", "1.25", "  9", "$DIMTP", " 40", "0.0", "  9", "$DIMTM", " 40",
    "0.0", "  9", "$DIMTXT", " 40", "2.5", "  9", "$DIMCEN", " 40", "2.5", "  9", "$DIMTSZ", " 40", "0.0", "  9",
    "$DIMTOL", " 70", "0", "  9", "$DIMLIM", " 70", "0", "  9", "$DIMTIH", " 70", "0", "  9", "$DIMTOH", " 70", "0",
    "  9", "$DIMSE1", " 70", "0", "  9", "$DIMSE2", " 70", "0", "  9", "$DIMTAD", " 70", "1", "  9", "$DIMZIN", " 70",
    "8", "  9", "$DIMBLK", "  1", "", "  9", "$DIMASO", " 70", "1", "  9", "$DIMSHO", " 70", "1", "  9", "$DIMPOST",
    "  1", "", "  9", "$DIMAPOST", "  1", "", "  9", "$DIMALT", " 70", "0", "  9", "$DIMALTD", " 70", "3", "  9",
    "$DIMALTF", " 40", "0.03937007874", "  9", "$DIMLFAC", " 40", "1.0", "  9", "$DIMTOFL", " 70", "1", "  9",
    "$DIMTVP", " 40", "0.0", "  9", "$DIMTIX", " 70", "0", "  9", "$DIMSOXD", " 70", "0", "  9", "$DIMSAH", " 70",
    "0", "  9", "$DIMBLK1", "  1", "", "  9", "$DIMBLK2", "  1", "", "  9", "$DIMSTYLE", "  2", "ISO-25", "  9",
    "$DIMCLRD", " 70", "0", "  9", "$DIMCLRE", " 70", "0", "  9", "$DIMCLRT", " 70", "0", "  9", "$DIMTFAC", " 40",
    "1.0", "  9", "$DIMGAP", " 40", "0.625", "  9", "$DIMJUST", " 70", "0", "  9", "$DIMSD1", " 70", "0", "  9",
    "$DIMSD2", " 70", "0", "  9", "$DIMTOLJ", " 70", "0", "  9", "$DIMTZIN", " 70", "8", "  9", "$DIMALTZ", " 70",
    "0", "  9", "$DIMALTTZ", " 70", "0", "  9", "$DIMUPT", " 70", "0", "  9", "$DIMDEC", " 70", "2", "  9",
    "$DIMTDEC", " 70", "2", "  9", "$DIMALTU", " 70", "2", "  9", "$DIMALTTD", " 70", "3", "  9", "$DIMTXSTY", "  7",
    "Standard", "  9", "$DIMAUNIT", " 70", "0", "  9", "$DIMADEC", " 70", "0", "  9", "$DIMALTRND", " 40", "0.0",
    "  9", "$DIMAZIN", " 70", "0", "  9", "$DIMDSEP", " 70", "44", "  9", "$DIMATFIT", " 70", "3", "  9", "$DIMFRAC",
    " 70", "0", "  9", "$DIMLDRBLK", "  1", "", "  9", "$DIMLUNIT", " 70", "2", "  9", "$DIMLWD", " 70", "-2", "  9",
    "$DIMLWE", " 70", "-2", "  9", "$DIMTMOVE", " 70", "0", "  9", "$LUNITS", " 70", "2", "  9", "$LUPREC", " 70",
    "4", "  9", "$SKETCHINC", " 40", "1.0", "  9", "$FILLETRAD", " 40", "10.0", "  9", "$AUNITS", " 70", "0", "  9",
    "$AUPREC", " 70", "2", "  9", "$MENU", "  1", ".", "  9", "$ELEVATION", " 40", "0.0", "  9", "$PELEVATION", " 40",
    "0.0", "  9", "$THICKNESS", " 40", "0.0", "  9", "$LIMCHECK", " 70", "0", "  9", "$CHAMFERA", " 40", "0.0", "  9",
    "$CHAMFERB", " 40", "0.0", "  9", "$CHAMFERC", " 40", "0.0", "  9", "$CHAMFERD", " 40", "0.0", "  9", "$SKPOLY",
    " 70", "0", "  9", "$TDCREATE", " 40", "2461265.626689815", "  9", "$TDUCREATE", " 40", "2458532.153996898",
    "  9", "$TDUPDATE", " 40", "2461265.626689815", "  9", "$TDUUPDATE", " 40", "2458532.1544311", "  9", "$TDINDWG",
    " 40", "0.0", "  9", "$TDUSRTIMER", " 40", "0.0", "  9", "$USRTIMER", " 70", "1", "  9", "$ANGBASE", " 50", "0.0",
    "  9", "$ANGDIR", " 70", "0", "  9", "$PDMODE", " 70", "0", "  9", "$PDSIZE", " 40", "0.0", "  9", "$PLINEWID",
    " 40", "0.0", "  9", "$SPLFRAME", " 70", "0", "  9", "$SPLINETYPE", " 70", "6", "  9", "$SPLINESEGS", " 70", "8",
    "  9", "$HANDSEED", "  5", "__HANDSEED__", "  9", "$SURFTAB1", " 70", "6", "  9", "$SURFTAB2", " 70", "6", "  9",
    "$SURFTYPE", " 70", "6", "  9", "$SURFU", " 70", "6", "  9", "$SURFV", " 70", "6", "  9", "$UCSBASE", "  2", "",
    "  9", "$UCSNAME", "  2", "", "  9", "$UCSORG", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9", "$UCSXDIR",
    " 10", "1.0", " 20", "0.0", " 30", "0.0", "  9", "$UCSYDIR", " 10", "0.0", " 20", "1.0", " 30", "0.0", "  9",
    "$UCSORTHOREF", "  2", "", "  9", "$UCSORTHOVIEW", " 70", "0", "  9", "$UCSORGTOP", " 10", "0.0", " 20", "0.0",
    " 30", "0.0", "  9", "$UCSORGBOTTOM", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9", "$UCSORGLEFT", " 10",
    "0.0", " 20", "0.0", " 30", "0.0", "  9", "$UCSORGRIGHT", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9",
    "$UCSORGFRONT", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9", "$UCSORGBACK", " 10", "0.0", " 20", "0.0", " 30",
    "0.0", "  9", "$PUCSBASE", "  2", "", "  9", "$PUCSNAME", "  2", "", "  9", "$PUCSORG", " 10", "0.0", " 20",
    "0.0", " 30", "0.0", "  9", "$PUCSXDIR", " 10", "1.0", " 20", "0.0", " 30", "0.0", "  9", "$PUCSYDIR", " 10",
    "0.0", " 20", "1.0", " 30", "0.0", "  9", "$PUCSORTHOREF", "  2", "", "  9", "$PUCSORTHOVIEW", " 70", "0", "  9",
    "$PUCSORGTOP", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9", "$PUCSORGBOTTOM", " 10", "0.0", " 20", "0.0",
    " 30", "0.0", "  9", "$PUCSORGLEFT", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9", "$PUCSORGRIGHT", " 10",
    "0.0", " 20", "0.0", " 30", "0.0", "  9", "$PUCSORGFRONT", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9",
    "$PUCSORGBACK", " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9", "$USERI1", " 70", "0", "  9", "$USERI2", " 70",
    "0", "  9", "$USERI3", " 70", "0", "  9", "$USERI4", " 70", "0", "  9", "$USERI5", " 70", "0", "  9", "$USERR1",
    " 40", "0.0", "  9", "$USERR2", " 40", "0.0", "  9", "$USERR3", " 40", "0.0", "  9", "$USERR4", " 40", "0.0",
    "  9", "$USERR5", " 40", "0.0", "  9", "$WORLDVIEW", " 70", "1", "  9", "$SHADEDGE", " 70", "3", "  9",
    "$SHADEDIF", " 70", "70", "  9", "$TILEMODE", " 70", "1", "  9", "$MAXACTVP", " 70", "64", "  9", "$PINSBASE",
    " 10", "0.0", " 20", "0.0", " 30", "0.0", "  9", "$PLIMCHECK", " 70", "0", "  9", "$PEXTMIN", " 10", "1e+20",
    " 20", "1e+20", " 30", "1e+20", "  9", "$PEXTMAX", " 10", "-1e+20", " 20", "-1e+20", " 30", "-1e+20", "  9",
    "$PLIMMIN", " 10", "0.0", " 20", "0.0", "  9", "$PLIMMAX", " 10", "420.0", " 20", "297.0", "  9", "$UNITMODE",
    " 70", "0", "  9", "$VISRETAIN", " 70", "1", "  9", "$PLINEGEN", " 70", "0", "  9", "$PSLTSCALE", " 70", "1",
    "  9", "$TREEDEPTH", " 70", "3020", "  9", "$CMLSTYLE", "  2", "Standard", "  9", "$CMLJUST", " 70", "0", "  9",
    "$CMLSCALE", " 40", "20.0", "  9", "$PROXYGRAPHICS", " 70", "1", "  9", "$MEASUREMENT", " 70", "1", "  9",
    "$CELWEIGHT", "370", "-1", "  9", "$ENDCAPS", "280", "0", "  9", "$JOINSTYLE", "280", "0", "  9", "$LWDISPLAY",
    "290", "0", "  9", "$INSUNITS", " 70", "1", "  9", "$HYPERLINKBASE", "  1", "", "  9", "$STYLESHEET", "  1", "",
    "  9", "$XEDIT", "290", "1", "  9", "$CEPSNTYPE", "380", "0", "  9", "$PSTYLEMODE", "290", "1", "  9",
    "$FINGERPRINTGUID", "  2", "{901E6446-C8CA-4381-B5FE-8494D931A798}", "  9", "$VERSIONGUID", "  2",
    "{4B5A3BC4-57FB-4960-955B-D909E47DC28A}", "  9", "$EXTNAMES", "290", "1", "  9", "$PSVPSCALE", " 40", "0.0",
    "  9", "$OLESTARTUP", "290", "0", "  0", "ENDSEC", "  0", "SECTION", "  2", "CLASSES", "  0", "CLASS", "  1",
    "ACDBDICTIONARYWDFLT", "  2", "AcDbDictionaryWithDefault", "  3", "ObjectDBX Classes", " 90", "0", "280", "0",
    "281", "0", "  0", "CLASS", "  1", "SUN", "  2", "AcDbSun", "  3", "SCENEOE", " 90", "1153", "280", "0", "281",
    "0", "  0", "CLASS", "  1", "VISUALSTYLE", "  2", "AcDbVisualStyle", "  3", "ObjectDBX Classes", " 90", "4095",
    "280", "0", "281", "0", "  0", "CLASS", "  1", "MATERIAL", "  2", "AcDbMaterial", "  3", "ObjectDBX Classes",
    " 90", "1153", "280", "0", "281", "0", "  0", "CLASS", "  1", "SCALE", "  2", "AcDbScale", "  3",
    "ObjectDBX Classes", " 90", "1153", "280", "0", "281", "0", "  0", "CLASS", "  1", "TABLESTYLE", "  2",
    "AcDbTableStyle", "  3", "ObjectDBX Classes", " 90", "4095", "280", "0", "281", "0", "  0", "CLASS", "  1",
    "MLEADERSTYLE", "  2", "AcDbMLeaderStyle", "  3", "ACDB_MLEADERSTYLE_CLASS", " 90", "4095", "280", "0", "281",
    "0", "  0", "CLASS", "  1", "DICTIONARYVAR", "  2", "AcDbDictionaryVar", "  3", "ObjectDBX Classes", " 90", "0",
    "280", "0", "281", "0", "  0", "CLASS", "  1", "CELLSTYLEMAP", "  2", "AcDbCellStyleMap", "  3",
    "ObjectDBX Classes", " 90", "1152", "280", "0", "281", "0", "  0", "CLASS", "  1", "MENTALRAYRENDERSETTINGS",
    "  2", "AcDbMentalRayRenderSettings", "  3", "SCENEOE", " 90", "1024", "280", "0", "281", "0", "  0", "CLASS",
    "  1", "ACDBDETAILVIEWSTYLE", "  2", "AcDbDetailViewStyle", "  3", "ObjectDBX Classes", " 90", "1025", "280", "0",
    "281", "0", "  0", "CLASS", "  1", "ACDBSECTIONVIEWSTYLE", "  2", "AcDbSectionViewStyle", "  3",
    "ObjectDBX Classes", " 90", "1025", "280", "0", "281", "0", "  0", "CLASS", "  1", "RASTERVARIABLES", "  2",
    "AcDbRasterVariables", "  3", "ISM", " 90", "0", "280", "0", "281", "0", "  0", "CLASS", "  1", "ACDBPLACEHOLDER",
    "  2", "AcDbPlaceHolder", "  3", "ObjectDBX Classes", " 90", "0", "280", "0", "281", "0", "  0", "CLASS", "  1",
    "LAYOUT", "  2", "AcDbLayout", "  3", "ObjectDBX Classes", " 90", "0", "280", "0", "281", "0", "  0", "ENDSEC",
    "  0", "SECTION", "  2", "TABLES", "  0", "TABLE", "  2", "VPORT", "  5", "8", "330", "0", "100",
    "AcDbSymbolTable", " 70", "1", "  0", "VPORT", "  5", "23", "330", "8", "100", "AcDbSymbolTableRecord", "100",
    "AcDbViewportTableRecord", "  2", "*Active", " 70", "0", " 10", "0.0", " 20", "0.0", " 11", "1.0", " 21", "1.0",
    " 12", "0.0", " 22", "0.0", " 13", "0.0", " 23", "0.0", " 14", "0.5", " 24", "0.5", " 15", "0.5", " 25", "0.5",
    " 16", "0.0", " 26", "0.0", " 36", "1.0", " 17", "0.0", " 27", "0.0", " 37", "0.0", " 40", "1000.0", " 41",
    "1.34", " 42", "50.0", " 43", "0.0", " 44", "0.0", " 50", "0.0", " 51", "0.0", " 71", "0", " 72", "1000", " 73",
    "1", " 74", "3", " 75", "0", " 76", "0", " 77", "0", " 78", "0", "281", "0", " 65", "0", "146", "0.0", "  0",
    "ENDTAB", "  0", "TABLE", "  2", "LTYPE", "  5", "2", "330", "0", "100", "AcDbSymbolTable", " 70", "3", "  0",
    "LTYPE", "  5", "24", "330", "2", "100", "AcDbSymbolTableRecord", "100", "AcDbLinetypeTableRecord", "  2",
    "ByBlock", " 70", "0", "  3", "", " 72", "65", " 73", "0", " 40", "0.0", "  0", "LTYPE", "  5", "25", "330", "2",
    "100", "AcDbSymbolTableRecord", "100", "AcDbLinetypeTableRecord", "  2", "ByLayer", " 70", "0", "  3", "", " 72",
    "65", " 73", "0", " 40", "0.0", "  0", "LTYPE", "  5", "26", "330", "2", "100", "AcDbSymbolTableRecord", "100",
    "AcDbLinetypeTableRecord", "  2", "Continuous", " 70", "0", "  3", "", " 72", "65", " 73", "0", " 40", "0.0",
    "  0", "ENDTAB", "  0", "TABLE", "  2", "LAYER", "  5", "1", "330", "0", "100", "AcDbSymbolTable", " 70",
];

#[rustfmt::skip]
static HEADER_STATIC_B: &[&str] = &[
    "  0", "LAYER", "  5", "27", "330", "1", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord", "  2",
    "0", " 70", "0", " 62", "7", "  6", "Continuous", "370", "-3", "390", "13", "  0", "LAYER", "  5", "28", "330",
    "1", "100", "AcDbSymbolTableRecord", "100", "AcDbLayerTableRecord", "  2", "Defpoints", " 70", "0", " 62", "7",
    "  6", "Continuous", "290", "0", "370", "-3", "390", "13",
];

#[rustfmt::skip]
static HEADER_STATIC_C: &[&str] = &[
    "  0", "ENDTAB", "  0", "TABLE", "  2", "STYLE", "  5", "5", "330", "0", "100", "AcDbSymbolTable", " 70", "1",
    "  0", "STYLE", "  5", "29", "330", "5", "100", "AcDbSymbolTableRecord", "100", "AcDbTextStyleTableRecord", "  2",
    "Standard", " 70", "0", " 40", "0.0", " 41", "1.0", " 50", "0.0", " 71", "0", " 42", "2.5", "  3", "txt", "  4",
    "", "  0", "ENDTAB", "  0", "TABLE", "  2", "VIEW", "  5", "7", "330", "0", "100", "AcDbSymbolTable", " 70", "0",
    "  0", "ENDTAB", "  0", "TABLE", "  2", "UCS", "  5", "6", "330", "0", "100", "AcDbSymbolTable", " 70", "0",
    "  0", "ENDTAB", "  0", "TABLE", "  2", "APPID", "  5", "3", "330", "0", "100", "AcDbSymbolTable", " 70", "3",
    "  0", "APPID", "  5", "2A", "330", "3", "100", "AcDbSymbolTableRecord", "100", "AcDbRegAppTableRecord", "  2",
    "ACAD", " 70", "0", "  0", "APPID", "  5", "2F", "330", "3", "100", "AcDbSymbolTableRecord", "100",
    "AcDbRegAppTableRecord", "  2", "HATCHBACKGROUNDCOLOR", " 70", "0", "  0", "APPID", "  5", "30", "330", "3",
    "100", "AcDbSymbolTableRecord", "100", "AcDbRegAppTableRecord", "  2", "EZDXF", " 70", "0", "  0", "ENDTAB",
    "  0", "TABLE", "  2", "DIMSTYLE", "  5", "4", "330", "0", "100", "AcDbSymbolTable", " 70", "1", "100",
    "AcDbDimStyleTable", "  0", "DIMSTYLE", "105", "2B", "330", "4", "100", "AcDbSymbolTableRecord", "100",
    "AcDbDimStyleTableRecord", "  2", "Standard", " 70", "0", "  3", "", "  4", "", " 40", "1.0", " 41", "2.5", " 42",
    "0.625", " 43", "3.75", " 44", "1.25", " 45", "0.0", " 46", "0.0", " 47", "0.0", " 48", "0.0", "140", "2.5",
    "141", "2.5", "142", "0.0", "143", "0.03937007874", "144", "1.0", "145", "0.0", "146", "1.0", "147", "0.625",
    "148", "0.0", " 71", "0", " 72", "0", " 73", "0", " 74", "0", " 75", "0", " 76", "0", " 77", "1", " 78", "8",
    " 79", "3", "170", "0", "171", "3", "172", "1", "173", "0", "174", "0", "175", "0", "176", "0", "177", "0", "178",
    "0", "179", "2", "271", "2", "272", "2", "273", "2", "274", "3", "275", "0", "276", "0", "277", "2", "278", "44",
    "279", "0", "280", "0", "281", "0", "282", "0", "283", "0", "284", "8", "285", "0", "286", "0", "288", "0", "289",
    "3", "371", "-2", "372", "-2", "  0", "ENDTAB", "  0", "TABLE", "  2", "BLOCK_RECORD", "  5", "9", "330", "0",
    "100", "AcDbSymbolTable", " 70", "2", "  0", "BLOCK_RECORD", "  5", "17", "330", "9", "100",
    "AcDbSymbolTableRecord", "100", "AcDbBlockTableRecord", "  2", "*Model_Space", "340", "1A", "  0", "BLOCK_RECORD",
    "  5", "1B", "330", "9", "100", "AcDbSymbolTableRecord", "100", "AcDbBlockTableRecord", "  2", "*Paper_Space",
    "340", "1E", "  0", "ENDTAB", "  0", "ENDSEC", "  0", "SECTION", "  2", "BLOCKS", "  0", "BLOCK", "  5", "18",
    "330", "17", "100", "AcDbEntity", "  8", "0", "100", "AcDbBlockBegin", "  2", "*Model_Space", " 70", "0", " 10",
    "0.0", " 20", "0.0", " 30", "0.0", "  3", "*Model_Space", "  1", "", "  0", "ENDBLK", "  5", "19", "330", "17",
    "100", "AcDbEntity", "  8", "0", "100", "AcDbBlockEnd", "  0", "BLOCK", "  5", "1C", "330", "1B", "100",
    "AcDbEntity", "  8", "0", "100", "AcDbBlockBegin", "  2", "*Paper_Space", " 70", "0", " 10", "0.0", " 20", "0.0",
    " 30", "0.0", "  3", "*Paper_Space", "  1", "", "  0", "ENDBLK", "  5", "1D", "330", "1B", "100", "AcDbEntity",
    "  8", "0", "100", "AcDbBlockEnd", "  0", "ENDSEC",
];

#[rustfmt::skip]
static OBJECTS_STATIC: &[&str] = &[
    "  0", "SECTION", "  2", "OBJECTS", "  0", "DICTIONARY", "  5", "A", "330", "0", "100", "AcDbDictionary", "281",
    "1", "  3", "ACAD_COLOR", "350", "B", "  3", "ACAD_GROUP", "350", "C", "  3", "ACAD_LAYOUT", "350", "D", "  3",
    "ACAD_MATERIAL", "350", "E", "  3", "ACAD_MLEADERSTYLE", "350", "F", "  3", "ACAD_MLINESTYLE", "350", "10", "  3",
    "ACAD_PLOTSETTINGS", "350", "11", "  3", "ACAD_PLOTSTYLENAME", "350", "12", "  3", "ACAD_SCALELIST", "350", "14",
    "  3", "ACAD_TABLESTYLE", "350", "15", "  3", "ACAD_VISUALSTYLE", "350", "16", "  3", "EZDXF_META", "350", "2D",
    "  0", "DICTIONARY", "  5", "B", "330", "A", "100", "AcDbDictionary", "281", "1", "  0", "DICTIONARY", "  5", "C",
    "330", "A", "100", "AcDbDictionary", "281", "1", "  0", "DICTIONARY", "  5", "D", "330", "A", "100",
    "AcDbDictionary", "281", "1", "  3", "Model", "350", "1A", "  3", "Layout1", "350", "1E", "  0", "DICTIONARY",
    "  5", "E", "330", "A", "100", "AcDbDictionary", "281", "1", "  3", "ByBlock", "350", "1F", "  3", "ByLayer",
    "350", "20", "  3", "Global", "350", "21", "  0", "DICTIONARY", "  5", "F", "330", "A", "100", "AcDbDictionary",
    "281", "1", "  3", "Standard", "350", "2C", "  0", "DICTIONARY", "  5", "10", "330", "A", "100", "AcDbDictionary",
    "281", "1", "  3", "Standard", "350", "22", "  0", "DICTIONARY", "  5", "11", "330", "A", "100", "AcDbDictionary",
    "281", "1", "  0", "ACDBDICTIONARYWDFLT", "  5", "12", "330", "A", "100", "AcDbDictionary", "281", "1", "  3",
    "Normal", "350", "13", "100", "AcDbDictionaryWithDefault", "340", "13", "  0", "ACDBPLACEHOLDER", "  5", "13",
    "330", "12", "  0", "DICTIONARY", "  5", "14", "330", "A", "100", "AcDbDictionary", "281", "1", "  0",
    "DICTIONARY", "  5", "15", "330", "A", "100", "AcDbDictionary", "281", "1", "  0", "DICTIONARY", "  5", "16",
    "330", "A", "100", "AcDbDictionary", "281", "1", "  0", "LAYOUT", "  5", "1A", "330", "D", "100",
    "AcDbPlotSettings", "  1", "", "  4", "A3", "  6", "", " 40", "7.5", " 41", "20.0", " 42", "7.5", " 43", "20.0",
    " 44", "420.0", " 45", "297.0", " 46", "0.0", " 47", "0.0", " 48", "0.0", " 49", "0.0", "140", "0.0", "141",
    "0.0", "142", "1.0", "143", "1.0", " 70", "1024", " 72", "1", " 73", "0", " 74", "5", "  7", "", " 75", "16",
    " 76", "0", " 77", "2", " 78", "300", "147", "1.0", "148", "0.0", "149", "0.0", "100", "AcDbLayout", "  1",
    "Model", " 70", "1", " 71", "0", " 10", "0.0", " 20", "0.0", " 11", "420.0", " 21", "297.0", " 12", "0.0", " 22",
    "0.0", " 32", "0.0", " 14", "1e+20", " 24", "1e+20", " 34", "1e+20", " 15", "-1e+20", " 25", "-1e+20", " 35",
    "-1e+20", "146", "0.0", " 13", "0.0", " 23", "0.0", " 33", "0.0", " 16", "1.0", " 26", "0.0", " 36", "0.0", " 17",
    "0.0", " 27", "1.0", " 37", "0.0", " 76", "1", "330", "17", "  0", "LAYOUT", "  5", "1E", "330", "D", "100",
    "AcDbPlotSettings", "  1", "", "  4", "A3", "  6", "", " 40", "7.5", " 41", "20.0", " 42", "7.5", " 43", "20.0",
    " 44", "420.0", " 45", "297.0", " 46", "0.0", " 47", "0.0", " 48", "0.0", " 49", "0.0", "140", "0.0", "141",
    "0.0", "142", "1.0", "143", "1.0", " 70", "0", " 72", "1", " 73", "0", " 74", "5", "  7", "", " 75", "16", " 76",
    "0", " 77", "2", " 78", "300", "147", "1.0", "148", "0.0", "149", "0.0", "100", "AcDbLayout", "  1", "Layout1",
    " 70", "1", " 71", "1", " 10", "0.0", " 20", "0.0", " 11", "420.0", " 21", "297.0", " 12", "0.0", " 22", "0.0",
    " 32", "0.0", " 14", "1e+20", " 24", "1e+20", " 34", "1e+20", " 15", "-1e+20", " 25", "-1e+20", " 35", "-1e+20",
    "146", "0.0", " 13", "0.0", " 23", "0.0", " 33", "0.0", " 16", "1.0", " 26", "0.0", " 36", "0.0", " 17", "0.0",
    " 27", "1.0", " 37", "0.0", " 76", "1", "330", "1B", "  0", "MATERIAL", "  5", "1F", "102", "{ACAD_REACTORS",
    "330", "E", "102", "}", "330", "E", "100", "AcDbMaterial", "  1", "ByBlock", "  2", "", " 70", "0", " 40", "1.0",
    " 71", "1", " 41", "1.0", " 91", "-1023410177", " 42", "1.0", " 72", "1", "  3", "", " 73", "1", " 74", "1",
    " 75", "1", " 44", "0.5", " 73", "0", " 45", "1.0", " 46", "1.0", " 77", "1", "  4", "", " 78", "1", " 79", "1",
    "170", "1", " 48", "1.0", "171", "1", "  6", "", "172", "1", "173", "1", "174", "1", "140", "1.0", "141", "1.0",
    "175", "1", "  7", "", "176", "1", "177", "1", "178", "1", "143", "1.0", "179", "1", "  8", "", "270", "1", "271",
    "1", "272", "1", "145", "1.0", "146", "1.0", "273", "1", "  9", "", "274", "1", "275", "1", "276", "1", " 42",
    "1.0", " 72", "1", "  3", "", " 73", "1", " 74", "1", " 75", "1", " 94", "63", "  0", "MATERIAL", "  5", "20",
    "102", "{ACAD_REACTORS", "330", "E", "102", "}", "330", "E", "100", "AcDbMaterial", "  1", "ByLayer", "  2", "",
    " 70", "0", " 40", "1.0", " 71", "1", " 41", "1.0", " 91", "-1023410177", " 42", "1.0", " 72", "1", "  3", "",
    " 73", "1", " 74", "1", " 75", "1", " 44", "0.5", " 73", "0", " 45", "1.0", " 46", "1.0", " 77", "1", "  4", "",
    " 78", "1", " 79", "1", "170", "1", " 48", "1.0", "171", "1", "  6", "", "172", "1", "173", "1", "174", "1",
    "140", "1.0", "141", "1.0", "175", "1", "  7", "", "176", "1", "177", "1", "178", "1", "143", "1.0", "179", "1",
    "  8", "", "270", "1", "271", "1", "272", "1", "145", "1.0", "146", "1.0", "273", "1", "  9", "", "274", "1",
    "275", "1", "276", "1", " 42", "1.0", " 72", "1", "  3", "", " 73", "1", " 74", "1", " 75", "1", " 94", "63",
    "  0", "MATERIAL", "  5", "21", "102", "{ACAD_REACTORS", "330", "E", "102", "}", "330", "E", "100",
    "AcDbMaterial", "  1", "Global", "  2", "", " 70", "0", " 40", "1.0", " 71", "1", " 41", "1.0", " 91",
    "-1023410177", " 42", "1.0", " 72", "1", "  3", "", " 73", "1", " 74", "1", " 75", "1", " 44", "0.5", " 73", "0",
    " 45", "1.0", " 46", "1.0", " 77", "1", "  4", "", " 78", "1", " 79", "1", "170", "1", " 48", "1.0", "171", "1",
    "  6", "", "172", "1", "173", "1", "174", "1", "140", "1.0", "141", "1.0", "175", "1", "  7", "", "176", "1",
    "177", "1", "178", "1", "143", "1.0", "179", "1", "  8", "", "270", "1", "271", "1", "272", "1", "145", "1.0",
    "146", "1.0", "273", "1", "  9", "", "274", "1", "275", "1", "276", "1", " 42", "1.0", " 72", "1", "  3", "",
    " 73", "1", " 74", "1", " 75", "1", " 94", "63", "  0", "MLINESTYLE", "  5", "22", "102", "{ACAD_REACTORS", "330",
    "10", "102", "}", "330", "10", "100", "AcDbMlineStyle", "  2", "Standard", " 70", "0", "  3", "", " 62", "256",
    " 51", "90.0", " 52", "90.0", " 71", "2", " 49", "0.5", " 62", "256", "  6", "BYLAYER", " 49", "-0.5", " 62",
    "256", "  6", "BYLAYER", "  0", "MLEADERSTYLE", "  5", "2C", "102", "{ACAD_REACTORS", "330", "F", "102", "}",
    "330", "F", "100", "AcDbMLeaderStyle", "179", "2", "170", "2", "171", "1", "172", "0", " 90", "2", " 40", "0.0",
    " 41", "0.0", "173", "1", " 91", "-1056964608", " 92", "-2", "290", "1", " 42", "2.0", "291", "1", " 43", "8.0",
    "  3", "Standard", " 44", "4.0", "300", "", "342", "29", "174", "1", "175", "1", "176", "0", "178", "1", " 93",
    "-1056964608", " 45", "4.0", "292", "0", "297", "0", " 46", "4.0", " 94", "-1056964608", " 47", "1.0", " 49",
    "1.0", "140", "1.0", "294", "1", "141", "0.0", "177", "0", "142", "1.0", "295", "0", "296", "0", "143", "3.75",
    "271", "0", "272", "9", "273", "9", "  0", "DICTIONARY", "  5", "2D", "330", "A", "100", "AcDbDictionary", "280",
    "1", "281", "1", "  3", "CREATED_BY_EZDXF", "350", "2E", "  3", "WRITTEN_BY_EZDXF", "350", "31", "  0",
    "DICTIONARYVAR", "  5", "2E", "330", "2D", "100", "DictionaryVariables", "280", "0", "  1",
    "1.4.3 @ 2026-08-12T10:02:26.972595+00:00", "  0", "DICTIONARYVAR", "  5", "31", "330", "2D", "100",
    "DictionaryVariables", "280", "0", "  1", "1.4.3 @ 2026-08-12T10:02:26.973142+00:00", "  0", "ENDSEC", "  0",
    "EOF",
];

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn layer_names_lose_illegal_characters_and_surrounding_space() {
        assert_eq!(sanitize_layer_name(""), "0");
        assert_eq!(sanitize_layer_name(" \t\r\n"), "0");
        assert_eq!(
            sanitize_layer_name("  a<b>c/d\\e\"f~g:h;i?j*k=l`m|n  "),
            "a_b_c_d_e_f_g_h_i_j_k_l_m_n"
        );
        assert_eq!(sanitize_layer_name("Walls"), "Walls");
    }

    #[test]
    fn long_layer_names_are_capped_with_a_hash_suffix() {
        let long = "x".repeat(300);
        let clean = sanitize_layer_name(&long);
        assert_eq!(clean.len(), 255);
        assert_eq!(&clean[..247], &format!("{}_", "x".repeat(246)));
        assert_eq!(
            &clean[247..],
            &format!("{:08x}", fnv1a64(long.as_bytes()) & 0xFFFF_FFFF)
        );
        // Two names sharing their first 246 bytes still differ.
        let other = format!("{}y", "x".repeat(299));
        assert_ne!(sanitize_layer_name(&other), clean);
        // A multi-byte name never splits a character.
        let wide = "\u{00e9}".repeat(200);
        assert!(sanitize_layer_name(&wide).len() <= 255);
    }

    #[test]
    fn fnv1a_matches_the_reference_vectors() {
        assert_eq!(fnv1a64(b""), 0xcbf2_9ce4_8422_2325);
        assert_eq!(fnv1a64(b"a"), 0xaf63_dc4c_8601_ec8c);
        assert_eq!(fnv1a64(b"foobar"), 0x8594_4171_f739_67e8);
    }

    #[test]
    fn rgb_maps_to_the_nearest_standard_aci() {
        assert_eq!(rgb_to_aci(255, 0, 0), 1);
        assert_eq!(rgb_to_aci(250, 250, 10), 2);
        assert_eq!(rgb_to_aci(200, 200, 200), 9);
        assert_eq!(rgb_to_aci(120, 130, 125), 8);
        assert_eq!(rgb_to_aci(255, 255, 255), 7);
        assert_eq!(rgb_to_aci(0, 0, 0), 8);
    }

    #[test]
    fn fixed_six_formatting_matches_iostream() {
        assert_eq!(fmt6(0.0), "0.000000");
        assert_eq!(fmt6(-0.0), "-0.000000");
        assert_eq!(fmt6(-1e-9), "-0.000000");
        assert_eq!(fmt6(39.37007874015748), "39.370079");
        assert_eq!(fmt6(1e20), "100000000000000000000.000000");
        assert_eq!(fmt6(f64::INFINITY), "inf");
        assert_eq!(fmt6(f64::NEG_INFINITY), "-inf");
        assert_eq!(fmt6(f64::NAN), "nan");
    }

    #[test]
    fn the_static_scaffold_holds_one_handseed_placeholder() {
        let all: Vec<&str> = [
            HEADER_STATIC_A,
            HEADER_STATIC_B,
            HEADER_STATIC_C,
            OBJECTS_STATIC,
        ]
        .iter()
        .flat_map(|t| t.iter().copied())
        .collect();
        assert_eq!(all.len(), 2783);
        assert_eq!(
            all.iter().filter(|l| **l == HANDSEED_PLACEHOLDER).count(),
            1
        );
        assert!(HEADER_STATIC_A.contains(&HANDSEED_PLACEHOLDER));
    }
}
