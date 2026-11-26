//! Tests for built-in PNG colorized texture baking, optional `ImageCodec` JPEG
//! baking, EXIF `APP1` stripping, header-level pixel limits, and scene
//! immutability across `to_glb` / `export_glb` and `to_instanced_glb` /
//! `export_instanced_glb` (#285).

mod common;
mod gltf_helper;

use std::borrow::Cow;
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use common::fixture;
use gltf_helper::{contains_bytes, int, load_glb, numbers, string, Glb};
use openskp::deflate::zlib_compress;
use openskp::glb_textures::{
    colorize_image_u8, count_texture_bake_stats, decode_png_rgba, encode_png_u8,
    read_jpeg_dimensions, read_png_dimensions, strip_jpeg_exif_app1, GltfTextureBakeStats,
    MAX_COLORIZE_BLOCK_PIXELS, MAX_DECODE_TEXTURE_PIXELS,
};
use openskp::instanced_scene::IDENTITY_GLTF;
use openskp::zip::crc32;
use openskp::{
    export_glb, export_instanced_glb, to_glb, to_instanced_glb, ByteBuffer, DecodedRgbaImage,
    GlbOptions, GlbPrimitive, GltfMaterial, ImageCodec, InstancedGlbOptions, InstancedMeshResource,
    InstancedNode, InstancedScene, JsonValue, LocalPrimitive, LogLevel, ParseOptions,
    PbrMetallicRoughness, Scene, SceneTexture, SkpFile,
};

fn factor_of(mat: &JsonValue) -> Vec<f64> {
    let pbr = mat.find("pbrMetallicRoughness").unwrap();
    if pbr.find("baseColorFactor").is_some() {
        numbers(pbr, "baseColorFactor")
    } else {
        vec![1.0, 1.0, 1.0, 1.0]
    }
}

fn image_bytes(glb: &Glb, img_idx: usize) -> &[u8] {
    let img = &glb.list("images")[img_idx];
    let bv = &glb.list("bufferViews")[int(img, "bufferView") as usize];
    let offset = bv.find("byteOffset").map_or(0, |v| v.as_int().unwrap()) as usize;
    let len = int(bv, "byteLength") as usize;
    &glb.bin[offset..offset + len]
}

fn material_image_bytes(glb: &Glb, mat_idx: usize) -> (&JsonValue, &[u8]) {
    let mat = &glb.list("materials")[mat_idx];
    let pbr = mat.find("pbrMetallicRoughness").unwrap();
    let tex_info = pbr.find("baseColorTexture").expect("baseColorTexture");
    let tex_idx = int(tex_info, "index") as usize;
    let tex = &glb.list("textures")[tex_idx];
    let src_idx = int(tex, "source") as usize;
    (mat, image_bytes(glb, src_idx))
}

fn make_quad_prim(mat_idx: usize, name: &str, x_off: f32) -> GlbPrimitive {
    GlbPrimitive {
        positions: vec![
            x_off,
            0.0,
            0.0,
            x_off + 1.0,
            0.0,
            0.0,
            x_off + 1.0,
            1.0,
            0.0,
            x_off,
            1.0,
            0.0,
        ],
        normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        uvs: vec![0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0],
        indices: vec![0, 1, 2, 0, 2, 3],
        back_triangles: Vec::new(),
        material_index: mat_idx,
        geom_name: name.to_owned(),
        unpainted_back: false,
    }
}

fn make_local_quad(mat_idx: usize) -> LocalPrimitive {
    LocalPrimitive {
        positions: vec![0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 0.0, 0.0, 1.0, 0.0],
        normals: vec![0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0],
        uvs: vec![0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0],
        indices: vec![0, 1, 2, 0, 2, 3],
        material_index: mat_idx,
        back_triangles: Vec::new(),
        unpainted_back: false,
    }
}

fn append_chunk(buf: &mut Vec<u8>, tag: &[u8; 4], payload: &[u8]) {
    buf.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    let start = buf.len();
    buf.extend_from_slice(tag);
    buf.extend_from_slice(payload);
    let c = crc32(&buf[start..]);
    buf.extend_from_slice(&c.to_be_bytes());
}

#[allow(clippy::too_many_arguments)]
fn build_custom_png(
    width: u32,
    height: u32,
    bit_depth: u8,
    color_type: u8,
    interlace: u8,
    plte: Option<&[u8]>,
    trns: Option<&[u8]>,
    raw_scanlines: &[u8],
) -> Vec<u8> {
    let mut out = vec![137, 80, 78, 71, 13, 10, 26, 10];
    let mut ihdr = [0u8; 13];
    ihdr[0..4].copy_from_slice(&width.to_be_bytes());
    ihdr[4..8].copy_from_slice(&height.to_be_bytes());
    ihdr[8] = bit_depth;
    ihdr[9] = color_type;
    ihdr[12] = interlace;
    append_chunk(&mut out, b"IHDR", &ihdr);
    if let Some(p) = plte {
        append_chunk(&mut out, b"PLTE", p);
    }
    if let Some(t) = trns {
        append_chunk(&mut out, b"tRNS", t);
    }
    let z = zlib_compress(raw_scanlines, 6);
    append_chunk(&mut out, b"IDAT", &z);
    append_chunk(&mut out, b"IEND", &[]);
    out
}

fn build_test_jpeg_with_app1(width: u16, height: u16, include_app1: bool) -> Vec<u8> {
    let mut jpg = vec![0xFF, 0xD8]; // SOI
                                    // APP0 JFIF (16 bytes including length)
    jpg.extend_from_slice(&[
        0xFF, 0xE0, 0x00, 0x10, b'J', b'F', b'I', b'F', 0x00, 0x01, 0x01, 0x00, 0x00, 0x01, 0x00,
        0x01, 0x00, 0x00,
    ]);
    if include_app1 {
        let exif_payload = b"Exif\0\0MM\0*\0\0\0\x08";
        let seg_len = (exif_payload.len() + 2) as u16;
        jpg.extend_from_slice(&[0xFF, 0xE1]);
        jpg.extend_from_slice(&seg_len.to_be_bytes());
        jpg.extend_from_slice(exif_payload);
    }
    // SOF0 baseline (17 bytes length)
    jpg.extend_from_slice(&[0xFF, 0xC0, 0x00, 0x11, 0x08]);
    jpg.extend_from_slice(&height.to_be_bytes());
    jpg.extend_from_slice(&width.to_be_bytes());
    jpg.extend_from_slice(&[0x03, 0x01, 0x11, 0x00, 0x02, 0x11, 0x00, 0x03, 0x11, 0x00]);
    // SOS (12 bytes length) + entropy-coded stub + EOI
    jpg.extend_from_slice(&[
        0xFF, 0xDA, 0x00, 0x0C, 0x03, 0x01, 0x00, 0x02, 0x00, 0x03, 0x00, 0x00, 0x3F, 0x00, 0xA5,
        0x5A, 0xFF, 0x00, 0x42, 0xFF, 0xD9,
    ]);
    jpg
}

#[test]
fn end_to_end_colorized_and_plain_fixture_in_baked_and_instanced_glb() {
    let cpp_fixture = std::path::PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .join("..")
        .join("cpp")
        .join("tests")
        .join("fixtures")
        .join("colorized_and_plain_texture.skp");
    let skp = SkpFile::open(&cpp_fixture).unwrap();
    let scene = skp.build_scene(&ParseOptions::default()).unwrap();
    let inst = skp.build_instanced_scene(&ParseOptions::default()).unwrap();

    let scene_before = scene.clone();
    let inst_before = inst.clone();

    let glb_baked = load_glb(&to_glb(&scene, &GlbOptions::new().with_textures(true)).unwrap());
    let glb_inst = load_glb(
        &to_instanced_glb(&inst, &InstancedGlbOptions::new().with_textures(true)).unwrap(),
    );

    assert_eq!(scene, scene_before, "to_glb must never mutate Scene");
    assert_eq!(
        inst, inst_before,
        "to_instanced_glb must never mutate InstancedScene"
    );

    for (label, glb, mats) in [
        ("baked", &glb_baked, &scene.gltf_materials),
        ("instanced", &glb_inst, &inst.gltf_materials),
    ] {
        assert_eq!(glb.list("images").len(), 2, "{label}: images");
        assert_eq!(glb.list("textures").len(), 2, "{label}: textures");

        let plain_idx = mats.iter().position(|m| !m.colorized).unwrap();
        let tinted_idx = mats.iter().position(|m| m.colorized).unwrap();
        let (plain_mat, plain_bytes) = material_image_bytes(glb, plain_idx);
        let (tinted_mat, tinted_bytes) = material_image_bytes(glb, tinted_idx);

        assert_eq!(factor_of(plain_mat), vec![1.0, 1.0, 1.0, 1.0], "{label}");
        assert_eq!(factor_of(tinted_mat), vec![1.0, 1.0, 1.0, 1.0], "{label}");
        assert_ne!(plain_bytes, tinted_bytes, "{label}: baked PNG must differ");

        let plain_dec = decode_png_rgba(plain_bytes).unwrap();
        let tinted_dec = decode_png_rgba(tinted_bytes).unwrap();
        assert_eq!(plain_dec.width, tinted_dec.width);
        assert_eq!(plain_dec.height, tinted_dec.height);
        assert_ne!(plain_dec.rgba, tinted_dec.rgba);

        let skp_ext = tinted_mat.find("extras").unwrap().find("sketchup").unwrap();
        assert_eq!(skp_ext.find("colorized").unwrap().as_bool(), Some(true));
    }
}

#[test]
fn bakes_2017_fixture_materials_and_matches_sketchup_ground_truth() {
    let model = SkpFile::open(fixture("legacy_textured_colorize_2017.skp"))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let by_name: BTreeMap<&str, &openskp::Material> = model
        .materials
        .iter()
        .map(|m| (m.name.as_str(), m))
        .collect();
    let src_tex = by_name["plain"].texture.as_ref().unwrap();
    let raw_png = ByteBuffer::from(src_tex.data.as_ref().unwrap().as_slice());
    let names = ["plain", "tint", "shift", "tint_half"];

    let gltf_materials: Vec<GltfMaterial> = names
        .iter()
        .map(|&name| {
            let m = by_name[name];
            let factor = if m.colorized {
                [
                    f64::from(m.color[0]) / 255.0,
                    f64::from(m.color[1]) / 255.0,
                    f64::from(m.color[2]) / 255.0,
                    m.transparency,
                ]
            } else {
                [1.0, 1.0, 1.0, m.transparency]
            };
            GltfMaterial {
                name: m.name.clone(),
                texture_path: src_tex.filename.clone(),
                pbr_metallic_roughness: PbrMetallicRoughness {
                    base_color_factor: factor,
                    metallic_factor: 0.0,
                    roughness_factor: 0.8,
                    base_color_texture: Some(0),
                },
                double_sided: true,
                colorized: m.colorized,
                colorize_type: m.colorize_type,
            }
        })
        .collect();

    let scene_textures = vec![SceneTexture {
        data: raw_png.clone(),
        mime_type: "image/png".into(),
        filename: src_tex.filename.clone(),
    }];

    // Repeat each material across multiple faces/components to exercise bake caching.
    let mut primitives = Vec::new();
    for i in 0..8 {
        primitives.push(make_quad_prim(i % 4, &format!("quad_{i}"), (i * 2) as f32));
    }
    let scene = Scene {
        glb_primitives: primitives,
        gltf_materials: gltf_materials.clone(),
        textures: scene_textures.clone(),
        ..Default::default()
    };

    let (img_count, stats) = count_texture_bake_stats(&scene, true, None);
    assert_eq!(img_count, 4);
    assert_eq!(
        stats,
        GltfTextureBakeStats {
            decode_calls: 1,
            colorize_calls: 3,
        }
    );

    // Build matching InstancedScene with root meshes and two placed component instances.
    let mesh_resources: Vec<InstancedMeshResource> = (0..4)
        .map(|i| InstancedMeshResource {
            id: format!("res_{i}"),
            definition_id: Some((i + 1) as i64),
            definition_name: format!("Def_{i}"),
            variant_key: format!("{i}"),
            primitives: vec![make_local_quad(i)],
        })
        .collect();
    let mut root_children = Vec::new();
    for i in 0..8 {
        let mut matrix = IDENTITY_GLTF;
        matrix[12] = (i * 2) as f64;
        root_children.push(InstancedNode {
            name: format!("inst_{i}"),
            matrix,
            mesh_resource_id: Some(format!("res_{}", i % 4)),
            ..Default::default()
        });
    }
    let inst_scene = InstancedScene {
        scene_hierarchy: InstancedNode {
            name: "ROOT".into(),
            children: root_children,
            ..Default::default()
        },
        mesh_resources,
        gltf_materials,
        textures: scene_textures,
        ..Default::default()
    };

    let scene_snapshot = scene.clone();
    let inst_snapshot = inst_scene.clone();

    let glb_opts = GlbOptions::new().with_textures(true);
    let inst_opts = InstancedGlbOptions::new().with_textures(true);

    let baked_mem = to_glb(&scene, &glb_opts).unwrap();
    let inst_mem = to_instanced_glb(&inst_scene, &inst_opts).unwrap();

    let tmp_dir = std::env::temp_dir();
    let pid = std::process::id();
    let baked_path = tmp_dir.join(format!("openskp_rust_colorize_baked_{pid}.glb"));
    let inst_path = tmp_dir.join(format!("openskp_rust_colorize_inst_{pid}.glb"));
    export_glb(&scene, &baked_path, &glb_opts).unwrap();
    export_instanced_glb(&inst_scene, &inst_path, &inst_opts).unwrap();
    let baked_disk = std::fs::read(&baked_path).unwrap();
    let inst_disk = std::fs::read(&inst_path).unwrap();
    let _ = std::fs::remove_file(&baked_path);
    let _ = std::fs::remove_file(&inst_path);

    assert_eq!(baked_mem, baked_disk);
    assert_eq!(inst_mem, inst_disk);
    assert_eq!(scene, scene_snapshot, "Scene must remain untouched");
    assert_eq!(
        inst_scene, inst_snapshot,
        "InstancedScene must remain untouched"
    );

    // Ground-truth pixels from SketchUp's SUTextureGetColorizedImageRep:
    // plain:     [200, 40, 40, 255] | [40, 40, 200, 255], opacity 1.0
    // tint:      [32, 237, 80, 255] | [32, 237, 80, 255], opacity 1.0
    // shift:     [172, 253, 46, 255] | [253, 46, 172, 255], opacity 1.0
    // tint_half: [65, 73, 255, 255] | [65, 73, 255, 255], opacity 0.5
    let expected: [(&str, [u8; 8], [f64; 4]); 4] = [
        (
            "plain",
            [200, 40, 40, 255, 40, 40, 200, 255],
            [1.0, 1.0, 1.0, 1.0],
        ),
        (
            "tint",
            [32, 237, 80, 255, 32, 237, 80, 255],
            [1.0, 1.0, 1.0, 1.0],
        ),
        (
            "shift",
            [172, 253, 46, 255, 253, 46, 172, 255],
            [1.0, 1.0, 1.0, 1.0],
        ),
        (
            "tint_half",
            [65, 73, 255, 255, 65, 73, 255, 255],
            [1.0, 1.0, 1.0, 0.5],
        ),
    ];

    for (mode_label, glb_bytes) in [("baked", &baked_mem), ("instanced", &inst_mem)] {
        let glb = load_glb(glb_bytes);
        assert_eq!(glb.list("images").len(), 4, "{mode_label}");
        assert_eq!(glb.list("textures").len(), 4, "{mode_label}");

        for (mat_idx, (name, exp_px, exp_factor)) in expected.into_iter().enumerate() {
            let (mat, bytes) = material_image_bytes(&glb, mat_idx);
            let f = factor_of(mat);
            for c in 0..4 {
                assert!(
                    (f[c] - exp_factor[c]).abs() < 1e-6,
                    "{mode_label}/{name} factor[{c}]: got {}, expected {}",
                    f[c],
                    exp_factor[c]
                );
            }
            if name == "plain" {
                assert_eq!(
                    bytes,
                    raw_png.as_slice(),
                    "{mode_label}: plain PNG bytes must not be re-encoded"
                );
            } else {
                // Opaque source PNG -> baked as 3-channel RGB PNG (IHDR color_type == 2).
                assert_eq!(bytes[25], 2, "{mode_label}/{name}: expected RGB color_type");
            }
            if name == "tint_half" {
                assert_eq!(string(mat, "alphaMode"), "BLEND");
            }

            let dec = decode_png_rgba(bytes).unwrap();
            assert_eq!((dec.width, dec.height), (2, 1));
            for (i, (&got, &want)) in dec.rgba.iter().zip(exp_px.iter()).enumerate() {
                assert!(
                    got.abs_diff(want) <= 1,
                    "{mode_label}/{name} byte {i}: got {got}, want {want}"
                );
            }
        }
    }
}

#[test]
fn shares_baked_variant_across_different_opacities_and_preserves_png_alpha() {
    let src_rgba = [200u8, 40, 40, 128, 40, 40, 200, 64];
    let png = encode_png_u8(&src_rgba, 2, 1, 4).unwrap();

    let mk_mat = |name: &str, alpha: f64| GltfMaterial {
        name: name.into(),
        texture_path: "alpha.png".into(),
        pbr_metallic_roughness: PbrMetallicRoughness {
            base_color_factor: [200.0 / 255.0, 90.0 / 255.0, 20.0 / 255.0, alpha],
            metallic_factor: 0.0,
            roughness_factor: 0.8,
            base_color_texture: Some(0),
        },
        double_sided: true,
        colorized: true,
        colorize_type: 0, // shift
    };

    let scene = Scene {
        glb_primitives: vec![make_quad_prim(0, "q0", 0.0), make_quad_prim(1, "q1", 2.0)],
        gltf_materials: vec![mk_mat("shift_opaque", 1.0), mk_mat("shift_half", 0.5)],
        textures: vec![SceneTexture {
            data: ByteBuffer::from(png),
            mime_type: "image/png".into(),
            filename: "alpha.png".into(),
        }],
        ..Default::default()
    };

    let (img_count, stats) = count_texture_bake_stats(&scene, true, None);
    assert_eq!(img_count, 1);
    assert_eq!(
        stats,
        GltfTextureBakeStats {
            decode_calls: 1,
            colorize_calls: 1,
        }
    );

    let glb = load_glb(&to_glb(&scene, &GlbOptions::new().with_textures(true)).unwrap());
    assert_eq!(glb.list("images").len(), 1);
    assert_eq!(glb.list("textures").len(), 1);

    let (m0, b0) = material_image_bytes(&glb, 0);
    let (m1, b1) = material_image_bytes(&glb, 1);
    assert_eq!(b0.as_ptr(), b1.as_ptr());
    assert_eq!(factor_of(m0), vec![1.0, 1.0, 1.0, 1.0]);
    assert_eq!(factor_of(m1), vec![1.0, 1.0, 1.0, 0.5]);

    // Translucent alpha -> encoded as 4-channel RGBA PNG (IHDR color_type == 6).
    assert_eq!(b0[25], 6);
    let dec = decode_png_rgba(b0).unwrap();
    let want = [172u8, 253, 46, 128, 253, 46, 172, 64];
    for (got, exp) in dec.rgba.iter().zip(want.iter()) {
        assert!(got.abs_diff(*exp) <= 1, "got {got}, expected {exp}");
    }
}

#[test]
fn decodes_indexed_subbyte_greyscale_trns_and_16bit_adam7_pngs() {
    // 1) 4-bit indexed PNG (3x1) with PLTE + tRNS: indices [0, 1, 2] packed as [0x01, 0x20].
    let plte = [200u8, 40, 40, 40, 200, 80, 10, 20, 230];
    let trns_idx = [255u8, 128]; // entry 2 defaults to 255
    let raw_idx = [0u8, 0x01, 0x20];
    let png_idx = build_custom_png(3, 1, 4, 3, 0, Some(&plte), Some(&trns_idx), &raw_idx);
    let dec_idx = decode_png_rgba(&png_idx).unwrap();
    assert_eq!((dec_idx.width, dec_idx.height), (3, 1));
    assert_eq!(
        dec_idx.rgba,
        vec![200, 40, 40, 255, 40, 200, 80, 128, 10, 20, 230, 255]
    );

    // 2) 2-bit greyscale PNG (4x1) with tRNS key == sample 2: samples [0, 1, 2, 3] -> byte 0b00_01_10_11 = 0x1B.
    let trns_grey = [0u8, 2];
    let raw_grey = [0u8, 0x1B];
    let png_grey = build_custom_png(4, 1, 2, 0, 0, None, Some(&trns_grey), &raw_grey);
    let dec_grey = decode_png_rgba(&png_grey).unwrap();
    assert_eq!(
        dec_grey.rgba,
        vec![
            0, 0, 0, 255, // sample 0 -> 0
            85, 85, 85, 255, // sample 1 -> 85
            170, 170, 170, 0, // sample 2 -> transparent via tRNS
            255, 255, 255, 255 // sample 3 -> 255
        ]
    );

    // 3) 16-bit RGB Adam7 interlaced PNG (5x5).
    let w = 5usize;
    let h = 5usize;
    let mut expected_rgba = vec![0u8; w * h * 4];
    for y in 0..h {
        for x in 0..w {
            let r = (x * 50 + y * 10) as u8;
            let g = (200 - x * 20 - y * 15) as u8;
            let b = (30 + x * 30 + y * 25) as u8;
            let i = (y * w + x) * 4;
            expected_rgba[i..i + 4].copy_from_slice(&[r, g, b, 255]);
        }
    }
    let passes: [(usize, usize, usize, usize); 7] = [
        (0, 8, 0, 8),
        (4, 8, 0, 8),
        (0, 4, 4, 8),
        (2, 4, 0, 4),
        (0, 2, 2, 4),
        (1, 2, 0, 2),
        (0, 1, 1, 2),
    ];
    let mut adam7_raw = Vec::new();
    for (x0, dx, y0, dy) in passes {
        let pw = if w > x0 { (w - x0).div_ceil(dx) } else { 0 };
        let ph = if h > y0 { (h - y0).div_ceil(dy) } else { 0 };
        if pw == 0 || ph == 0 {
            continue;
        }
        for py in 0..ph {
            adam7_raw.push(0); // filter None
            let y = y0 + py * dy;
            for px in 0..pw {
                let x = x0 + px * dx;
                let i = (y * w + x) * 4;
                for c in 0..3 {
                    let v8 = u16::from(expected_rgba[i + c]);
                    let v16 = v8 * 257; // exact 8->16 expansion
                    adam7_raw.extend_from_slice(&v16.to_be_bytes());
                }
            }
        }
    }
    let png_adam7 = build_custom_png(5, 5, 16, 2, 1, None, None, &adam7_raw);
    let dec_adam7 = decode_png_rgba(&png_adam7).unwrap();
    assert_eq!((dec_adam7.width, dec_adam7.height), (5, 5));
    assert_eq!(dec_adam7.rgba, expected_rgba);
}

#[test]
fn hls_colorize_edge_cases_and_row_block_chunk_invariance() {
    // 1) Greyscale source pixels tinted and shifted to chromatic target, preserving alpha.
    let grey_rgba = [0u8, 0, 0, 255, 128, 128, 128, 200, 255, 255, 255, 90];
    for mode in [0, 1] {
        let out = colorize_image_u8(
            &grey_rgba,
            3,
            1,
            [30, 160, 60],
            mode,
            true,
            MAX_COLORIZE_BLOCK_PIXELS,
        );
        assert_eq!(out.len(), 12);
        assert_eq!(out[3], 255);
        assert_eq!(out[7], 200);
        assert_eq!(out[11], 90);
        // Middle pixel should become green-dominant.
        assert!(out[5] > out[4] && out[5] > out[6], "mode {mode}: {out:?}");
    }

    // 2) Chromatic pixels colorized to grey target (tgt_s == 0 && avg_s > 0 => delta_s = -1.0).
    let chrom_rgba = [200u8, 40, 40, 255, 40, 200, 80, 255];
    for mode in [0, 1] {
        let out = colorize_image_u8(&chrom_rgba, 2, 1, [128, 128, 128], mode, false, 1024);
        assert_eq!(out.len(), 6);
        assert_eq!(out[0], out[1]);
        assert_eq!(out[1], out[2]);
        assert_eq!(out[3], out[4]);
        assert_eq!(out[4], out[5]);
    }

    // 3) Chromatic pixels whose mean RGB is neutral grey (avg_s == 0.0) shifted to chromatic target.
    let balanced_rgba = [200u8, 50, 50, 255, 50, 200, 200, 255];
    let shifted = colorize_image_u8(&balanced_rgba, 2, 1, [30, 160, 60], 0, false, 1024);
    assert_eq!(shifted.len(), 6);
    assert_ne!(&shifted[0..3], &shifted[3..6]);

    // 4) Row-block chunk invariance: small max_block_pixels == single full-image block.
    let w = 19u32;
    let h = 37u32;
    let mut img = vec![0u8; (w * h * 4) as usize];
    for (i, px) in img.as_chunks_mut::<4>().0.iter_mut().enumerate() {
        px[0] = ((i * 73 + 19) % 256) as u8;
        px[1] = ((i * 151 + 47) % 256) as u8;
        px[2] = ((i * 211 + 103) % 256) as u8;
        px[3] = ((i * 29 + 11) % 256) as u8;
    }
    for mode in [0, 1] {
        let chunked = colorize_image_u8(&img, w, h, [210, 85, 35], mode, true, 40);
        let full = colorize_image_u8(&img, w, h, [210, 85, 35], mode, true, 100_000);
        assert_eq!(chunked, full, "mode {mode} row-block mismatch");
    }
}

#[test]
fn jpeg_exif_strip_no_codec_fallback_and_custom_image_codec_baking() {
    let raw_jpg_with_app1 = build_test_jpeg_with_app1(2, 1, true);
    let raw_jpg_clean = build_test_jpeg_with_app1(2, 1, false);
    assert!(contains_bytes(&raw_jpg_with_app1, b"Exif\0\0"));
    assert_eq!(read_jpeg_dimensions(&raw_jpg_with_app1), Some((2, 1)));

    let stripped = strip_jpeg_exif_app1(&raw_jpg_with_app1);
    assert!(matches!(stripped, Cow::Owned(_)));
    assert_eq!(stripped.as_ref(), raw_jpg_clean.as_slice());
    assert!(!contains_bytes(&stripped, b"Exif\0\0"));
    assert!(matches!(strip_jpeg_exif_app1(&stripped), Cow::Borrowed(_)));

    let mk_mat = |name: &str, rgb: [u8; 3], mode: i32, alpha: f64| GltfMaterial {
        name: name.into(),
        texture_path: "photo.jpg".into(),
        pbr_metallic_roughness: PbrMetallicRoughness {
            base_color_factor: [
                f64::from(rgb[0]) / 255.0,
                f64::from(rgb[1]) / 255.0,
                f64::from(rgb[2]) / 255.0,
                alpha,
            ],
            metallic_factor: 0.0,
            roughness_factor: 0.8,
            base_color_texture: Some(0),
        },
        double_sided: true,
        colorized: true,
        colorize_type: mode,
    };

    let materials = vec![
        mk_mat("jpg_tint", [30, 160, 60], 1, 1.0),
        mk_mat("jpg_shift_half", [200, 90, 20], 0, 0.5),
    ];
    let textures = vec![SceneTexture {
        data: ByteBuffer::from(raw_jpg_with_app1.clone()),
        mime_type: "image/jpeg".into(),
        filename: "photo.jpg".into(),
    }];

    let scene = Scene {
        glb_primitives: vec![make_quad_prim(0, "q0", 0.0), make_quad_prim(1, "q1", 2.0)],
        gltf_materials: materials.clone(),
        textures: textures.clone(),
        ..Default::default()
    };
    let inst_scene = InstancedScene {
        scene_hierarchy: InstancedNode {
            name: "ROOT".into(),
            children: vec![
                InstancedNode {
                    name: "n0".into(),
                    matrix: IDENTITY_GLTF,
                    mesh_resource_id: Some("r0".into()),
                    ..Default::default()
                },
                InstancedNode {
                    name: "n1".into(),
                    matrix: IDENTITY_GLTF,
                    mesh_resource_id: Some("r1".into()),
                    ..Default::default()
                },
            ],
            ..Default::default()
        },
        mesh_resources: vec![
            InstancedMeshResource {
                id: "r0".into(),
                primitives: vec![make_local_quad(0)],
                ..Default::default()
            },
            InstancedMeshResource {
                id: "r1".into(),
                primitives: vec![make_local_quad(1)],
                ..Default::default()
            },
        ],
        gltf_materials: materials,
        textures,
        ..Default::default()
    };

    // A) Without image_codec: 1 warning, APP1 stripped, tinted baseColorFactor kept.
    let warnings_baked: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let w_clone = Arc::clone(&warnings_baked);
    let glb_no_codec = load_glb(
        &to_glb(
            &scene,
            &GlbOptions::new()
                .with_textures(true)
                .with_log(move |lvl, msg| {
                    if lvl == LogLevel::Warning {
                        w_clone.lock().unwrap().push(msg.to_owned());
                    }
                }),
        )
        .unwrap(),
    );
    {
        let msgs = warnings_baked.lock().unwrap();
        assert_eq!(msgs.len(), 1, "expected exactly 1 warning, got {msgs:?}");
        assert!(msgs[0].contains("image_codec"));
    }
    assert_eq!(glb_no_codec.list("images").len(), 1);
    let (m_tint, b_tint) = material_image_bytes(&glb_no_codec, 0);
    let (m_shift, b_shift) = material_image_bytes(&glb_no_codec, 1);
    assert_eq!(b_tint, raw_jpg_clean.as_slice());
    assert_eq!(b_shift, raw_jpg_clean.as_slice());
    assert!((factor_of(m_tint)[0] - 30.0 / 255.0).abs() < 1e-6);
    assert!((factor_of(m_tint)[1] - 160.0 / 255.0).abs() < 1e-6);
    assert!((factor_of(m_shift)[0] - 200.0 / 255.0).abs() < 1e-6);
    assert!((factor_of(m_shift)[3] - 0.5).abs() < 1e-6);

    // B) With struct `ImageCodec` on `GlbOptions` and tuple `(decode, encode)` on `InstancedGlbOptions`.
    struct MockJpegCodec {
        decodes: AtomicUsize,
        encodes: Mutex<Vec<(u8, Vec<u8>)>>,
    }
    impl ImageCodec for MockJpegCodec {
        fn decode(&self, data: &[u8], mime_type: &str) -> Result<DecodedRgbaImage, String> {
            assert_eq!(mime_type, "image/jpeg");
            assert!(data.starts_with(&[0xFF, 0xD8]));
            self.decodes.fetch_add(1, Ordering::SeqCst);
            Ok(DecodedRgbaImage {
                width: 2,
                height: 1,
                rgba: vec![200, 40, 40, 255, 40, 40, 200, 255],
            })
        }
        fn encode(
            &self,
            rgba: &[u8],
            width: u32,
            height: u32,
            mime_type: &str,
            quality: u8,
        ) -> Result<Vec<u8>, String> {
            assert_eq!((width, height), (2, 1));
            assert_eq!(mime_type, "image/jpeg");
            assert_eq!(quality, 95);
            let mut guard = self.encodes.lock().unwrap();
            let tag = guard.len() as u8 + 1;
            guard.push((quality, rgba.to_vec()));
            Ok(vec![0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x02, tag, 0xFF, 0xD9])
        }
    }

    let codec = Arc::new(MockJpegCodec {
        decodes: AtomicUsize::new(0),
        encodes: Mutex::new(Vec::new()),
    });
    struct SharedCodec(Arc<MockJpegCodec>);
    impl ImageCodec for SharedCodec {
        fn decode(&self, data: &[u8], mime_type: &str) -> Result<DecodedRgbaImage, String> {
            self.0.decode(data, mime_type)
        }
        fn encode(
            &self,
            rgba: &[u8],
            width: u32,
            height: u32,
            mime_type: &str,
            quality: u8,
        ) -> Result<Vec<u8>, String> {
            self.0.encode(rgba, width, height, mime_type, quality)
        }
    }

    let warn_with_codec: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let wc = Arc::clone(&warn_with_codec);
    let glb_baked_jpg = load_glb(
        &to_glb(
            &scene,
            &GlbOptions::new()
                .with_textures(true)
                .with_image_codec(SharedCodec(Arc::clone(&codec)))
                .with_log(move |lvl, msg| {
                    if lvl == LogLevel::Warning {
                        wc.lock().unwrap().push(msg.to_owned());
                    }
                }),
        )
        .unwrap(),
    );
    assert!(warn_with_codec.lock().unwrap().is_empty());
    assert_eq!(codec.decodes.load(Ordering::SeqCst), 1);
    {
        let encs = codec.encodes.lock().unwrap();
        assert_eq!(encs.len(), 2);
        let want_tint = [32u8, 237, 80, 255, 32, 237, 80, 255];
        let want_shift = [172u8, 253, 46, 255, 253, 46, 172, 255];
        for (got, want) in encs[0].1.iter().zip(want_tint.iter()) {
            assert!(got.abs_diff(*want) <= 1);
        }
        for (got, want) in encs[1].1.iter().zip(want_shift.iter()) {
            assert!(got.abs_diff(*want) <= 1);
        }
    }
    assert_eq!(glb_baked_jpg.list("images").len(), 2);
    let (mt, bt) = material_image_bytes(&glb_baked_jpg, 0);
    let (ms, bs) = material_image_bytes(&glb_baked_jpg, 1);
    assert_eq!(factor_of(mt), vec![1.0, 1.0, 1.0, 1.0]);
    assert_eq!(factor_of(ms), vec![1.0, 1.0, 1.0, 0.5]);
    assert_ne!(bt, bs);

    // Tuple `(decode_fn, encode_fn)` on `InstancedGlbOptions`.
    let tuple_decodes = Arc::new(AtomicUsize::new(0));
    let td = Arc::clone(&tuple_decodes);
    let glb_inst_jpg = load_glb(
        &to_instanced_glb(
            &inst_scene,
            &InstancedGlbOptions::new()
                .with_textures(true)
                .with_image_codec((
                    move |_data: &[u8], mime: &str| {
                        assert_eq!(mime, "image/jpeg");
                        td.fetch_add(1, Ordering::SeqCst);
                        Ok(DecodedRgbaImage {
                            width: 2,
                            height: 1,
                            rgba: vec![200, 40, 40, 255, 40, 40, 200, 255],
                        })
                    },
                    |rgba: &[u8], w: u32, h: u32, mime: &str, quality: u8| {
                        assert_eq!((w, h, mime, quality), (2, 1, "image/jpeg", 95));
                        Ok(vec![0xFF, 0xD8, rgba[0], rgba[4], 0xFF, 0xD9])
                    },
                )),
        )
        .unwrap(),
    );
    assert_eq!(tuple_decodes.load(Ordering::SeqCst), 1);
    assert_eq!(glb_inst_jpg.list("images").len(), 2);
    let (imt, _) = material_image_bytes(&glb_inst_jpg, 0);
    let (ims, _) = material_image_bytes(&glb_inst_jpg, 1);
    assert_eq!(factor_of(imt), vec![1.0, 1.0, 1.0, 1.0]);
    assert_eq!(factor_of(ims), vec![1.0, 1.0, 1.0, 0.5]);
}

#[test]
fn enforces_header_pixel_limit_before_decode_and_handles_corrupt_images() {
    assert_eq!(MAX_DECODE_TEXTURE_PIXELS, 178_956_970);

    // 1) Header-only 40000x40000 PNG (1,600,000,000 pixels > 178,956,970) with tiny IDAT.
    let mut huge_png = vec![137, 80, 78, 71, 13, 10, 26, 10];
    let mut ihdr = [0u8; 13];
    ihdr[0..4].copy_from_slice(&40_000u32.to_be_bytes());
    ihdr[4..8].copy_from_slice(&40_000u32.to_be_bytes());
    ihdr[8] = 8;
    ihdr[9] = 2;
    append_chunk(&mut huge_png, b"IHDR", &ihdr);
    append_chunk(
        &mut huge_png,
        b"IDAT",
        &[0x78, 0x9C, 0x03, 0x00, 0x00, 0x00, 0x00, 0x01],
    );
    append_chunk(&mut huge_png, b"IEND", &[]);
    assert_eq!(read_png_dimensions(&huge_png), Some((40_000, 40_000)));
    let err = decode_png_rgba(&huge_png).unwrap_err();
    assert!(err.contains("178956970"), "unexpected error: {err}");

    // 2) Header-only 50000x50000 JPEG (2,500,000,000 pixels > i32::MAX and > 178,956,970):
    // ImageCodec::decode must NOT be invoked at all.
    let huge_jpg = build_test_jpeg_with_app1(50_000, 50_000, true);
    assert_eq!(read_jpeg_dimensions(&huge_jpg), Some((50_000, 50_000)));

    // 3) Corrupt 8-byte PNG signature with no chunks.
    let corrupt_png = vec![137, 80, 78, 71, 13, 10, 26, 10];

    let mk_mat = |name: &str, tex_idx: usize| GltfMaterial {
        name: name.into(),
        texture_path: format!("tex_{tex_idx}"),
        pbr_metallic_roughness: PbrMetallicRoughness {
            base_color_factor: [0.2, 0.6, 0.8, 1.0],
            metallic_factor: 0.0,
            roughness_factor: 0.8,
            base_color_texture: Some(tex_idx),
        },
        double_sided: true,
        colorized: true,
        colorize_type: 1,
    };

    let scene = Scene {
        glb_primitives: vec![
            make_quad_prim(0, "p0", 0.0),
            make_quad_prim(1, "p1", 2.0),
            make_quad_prim(2, "p2", 4.0),
        ],
        gltf_materials: vec![
            mk_mat("huge_png_mat", 0),
            mk_mat("huge_jpg_mat", 1),
            mk_mat("corrupt_png_mat", 2),
        ],
        textures: vec![
            SceneTexture {
                data: ByteBuffer::from(huge_png.clone()),
                mime_type: "image/png".into(),
                filename: "huge.png".into(),
            },
            SceneTexture {
                data: ByteBuffer::from(huge_jpg.clone()),
                mime_type: "image/jpeg".into(),
                filename: "huge.jpg".into(),
            },
            SceneTexture {
                data: ByteBuffer::from(corrupt_png.clone()),
                mime_type: "image/png".into(),
                filename: "corrupt.png".into(),
            },
        ],
        ..Default::default()
    };

    let decode_called = Arc::new(AtomicUsize::new(0));
    let dc = Arc::clone(&decode_called);
    let warnings: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
    let wc = Arc::clone(&warnings);

    let glb = load_glb(
        &to_glb(
            &scene,
            &GlbOptions::new()
                .with_textures(true)
                .with_image_codec((
                    move |_data: &[u8], _mime: &str| {
                        dc.fetch_add(1, Ordering::SeqCst);
                        panic!("ImageCodec::decode must not be called for oversized SOF0 JPEG");
                    },
                    |_rgba: &[u8], _w: u32, _h: u32, _mime: &str, _q: u8| {
                        Ok(vec![0xFF, 0xD8, 0xFF, 0xD9])
                    },
                ))
                .with_log(move |lvl, msg| {
                    if lvl == LogLevel::Warning {
                        wc.lock().unwrap().push(msg.to_owned());
                    }
                }),
        )
        .unwrap(),
    );

    assert_eq!(decode_called.load(Ordering::SeqCst), 0);
    {
        let msgs = warnings.lock().unwrap();
        assert_eq!(msgs.len(), 3, "expected 3 decode warnings, got {msgs:?}");
        assert!(msgs[0].contains("178956970"), "msg[0]: {}", msgs[0]);
        assert!(msgs[1].contains("178956970"), "msg[1]: {}", msgs[1]);
        assert!(msgs[2].contains("truncated"), "msg[2]: {}", msgs[2]);
    }

    // All three fall back gracefully: keep source bytes (with APP1 stripped on JPEG) + tinted factor.
    let (m0, b0) = material_image_bytes(&glb, 0);
    let (m1, b1) = material_image_bytes(&glb, 1);
    let (m2, b2) = material_image_bytes(&glb, 2);
    assert_eq!(b0, huge_png.as_slice());
    assert_eq!(b1, strip_jpeg_exif_app1(&huge_jpg).as_ref());
    assert_eq!(b2, corrupt_png.as_slice());
    for m in [m0, m1, m2] {
        let f = factor_of(m);
        assert!((f[0] - 0.2).abs() < 1e-6);
        assert!((f[1] - 0.6).abs() < 1e-6);
        assert!((f[2] - 0.8).abs() < 1e-6);
        assert!((f[3] - 1.0).abs() < 1e-6);
    }
}
