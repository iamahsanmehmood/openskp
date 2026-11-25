mod common;
mod gltf_helper;

use std::sync::{Arc, Mutex};

use gltf_helper::{contains_bytes, int, load_glb, numbers, string};
use openskp::{
    to_glb, to_instanced_glb, GlbOptions, GltfMaterial, InstancedGlbOptions, JsonValue, LogLevel,
    ParseOptions, SkpFile,
};

fn alpha_mode(m: &JsonValue) -> &str {
    m.find("alphaMode")
        .and_then(JsonValue::as_str)
        .unwrap_or("OPAQUE")
}

struct KeptCase {
    fixture: &'static str,
    material_name: &'static str,
    mime: &'static str,
    format: &'static str,
    file: &'static str,
    signature: &'static [u8],
}

const KEPT_CASES: &[KeptCase] = &[
    KeptCase {
        fixture: "texture_tiff_2017.skp",
        material_name: "Tex_TIF",
        mime: "image/tiff",
        format: "TIFF",
        file: "tex.tif",
        signature: b"II*\x00",
    },
    KeptCase {
        fixture: "texture_tiff_2021.skp",
        material_name: "Tex_TIF",
        mime: "image/tiff",
        format: "TIFF",
        file: "tex.tif",
        signature: b"II*\x00",
    },
    KeptCase {
        fixture: "texture_tga_2021.skp",
        material_name: "Tex_TGA",
        mime: "image/x-tga",
        format: "TGA",
        file: "tex.tga",
        signature: b"",
    },
    KeptCase {
        fixture: "texture_gif_2021.skp",
        material_name: "Tex_GIF",
        mime: "image/gif",
        format: "GIF",
        file: "tex.gif",
        signature: b"GIF89a",
    },
];

fn count_textured_materials(materials: &[GltfMaterial]) -> usize {
    let mut count = 0;
    for m in materials {
        if let Some(idx) = m.pbr_metallic_roughness.base_color_texture {
            assert_eq!(idx, 0);
            count += 1;
        }
    }
    count
}

#[test]
fn keeps_non_gltf_textures_in_own_format_when_requested() {
    for instanced in [false, true] {
        for tc in KEPT_CASES {
            let skp = SkpFile::open(common::fixture(tc.fixture)).unwrap();
            let warnings: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
            let log_sink = Arc::clone(&warnings);
            let opts =
                ParseOptions::new()
                    .with_original_textures(true)
                    .with_log(move |lvl, msg| {
                        if lvl == LogLevel::Warning && msg.starts_with("Texture of material") {
                            log_sink.lock().unwrap().push(msg.to_owned());
                        }
                    });

            let (textures, materials) = if instanced {
                let s = skp.build_instanced_scene(&opts).unwrap();
                (s.textures, s.gltf_materials)
            } else {
                let s = skp.build_scene(&opts).unwrap();
                (s.textures, s.gltf_materials)
            };

            assert_eq!(textures.len(), 1);
            assert_eq!(textures[0].mime_type, tc.mime);
            if !tc.signature.is_empty() {
                assert!(textures[0].data.starts_with(tc.signature));
            }

            let model = skp.parse(&ParseOptions::default()).unwrap();
            let stored = model
                .materials
                .iter()
                .find(|m| m.name == tc.material_name)
                .and_then(|m| m.texture.as_ref())
                .unwrap();
            assert_eq!(Some(&textures[0].data), stored.data.as_ref());
            assert_eq!(count_textured_materials(&materials), 1);
            assert!(warnings.lock().unwrap().is_empty());
        }
    }
}

#[test]
fn drops_non_gltf_textures_by_default_and_warns_naming_option() {
    for instanced in [false, true] {
        for tc in KEPT_CASES {
            let skp = SkpFile::open(common::fixture(tc.fixture)).unwrap();
            let warnings: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
            let log_sink = Arc::clone(&warnings);
            let opts = ParseOptions::new().with_log(move |lvl, msg| {
                if lvl == LogLevel::Warning && msg.starts_with("Texture of material") {
                    log_sink.lock().unwrap().push(msg.to_owned());
                }
            });

            if instanced {
                let s = skp.build_instanced_scene(&opts).unwrap();
                assert!(s.textures.is_empty());
                assert_eq!(count_textured_materials(&s.gltf_materials), 0);
            } else {
                let s = skp.build_scene(&opts).unwrap();
                assert!(s.textures.is_empty());
                assert_eq!(count_textured_materials(&s.gltf_materials), 0);
            }

            let expected = format!(
                "Texture of material '{}' ({}) is {}, which a scene cannot carry \
                 (only PNG and JPEG): the material is built without its texture \
                 (pass original_textures: true to keep it in its own format)",
                tc.material_name, tc.file, tc.format
            );
            let logged = warnings.lock().unwrap();
            assert_eq!(logged.as_slice(), [expected]);
        }
    }
}

#[test]
fn leaves_png_and_jpeg_textures_unchanged() {
    for instanced in [false, true] {
        for fixture in ["texture_bmp_2021.skp", "capilla_quiroz_v17.skp"] {
            let skp = SkpFile::open(common::fixture(fixture)).unwrap();
            let kept_opts = ParseOptions::new().with_original_textures(true);
            if instanced {
                let plain = skp.build_instanced_scene(&ParseOptions::default()).unwrap();
                let kept = skp.build_instanced_scene(&kept_opts).unwrap();
                assert!(!plain.textures.is_empty());
                assert_eq!(kept.textures, plain.textures);
                assert_eq!(kept.gltf_materials, plain.gltf_materials);
            } else {
                let plain = skp.build_scene(&ParseOptions::default()).unwrap();
                let kept = skp.build_scene(&kept_opts).unwrap();
                assert!(!plain.textures.is_empty());
                assert_eq!(kept.textures, plain.textures);
                assert_eq!(kept.gltf_materials, plain.gltf_materials);
            }
        }
    }
}

#[test]
fn glb_export_omits_lone_tiff_and_warns_once() {
    for instanced in [false, true] {
        let skp = SkpFile::open(common::fixture("texture_tiff_2021.skp")).unwrap();
        let parse_opts = ParseOptions::new().with_original_textures(true);
        let warnings: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&warnings);

        let glb = if instanced {
            let scene = skp.build_instanced_scene(&parse_opts).unwrap();
            let opts = InstancedGlbOptions::new()
                .with_textures(true)
                .with_log(move |lvl, msg| {
                    if lvl == LogLevel::Warning {
                        sink.lock().unwrap().push(msg.to_owned());
                    }
                });
            to_instanced_glb(&scene, &opts).unwrap()
        } else {
            let scene = skp.build_scene(&parse_opts).unwrap();
            let opts = GlbOptions::new()
                .with_textures(true)
                .with_log(move |lvl, msg| {
                    if lvl == LogLevel::Warning {
                        sink.lock().unwrap().push(msg.to_owned());
                    }
                });
            to_glb(&scene, &opts).unwrap()
        };

        assert!(!contains_bytes(&glb, b"\"images\""));
        assert!(!contains_bytes(&glb, b"\"textures\""));
        assert!(!contains_bytes(&glb, b"\"samplers\""));
        let model = load_glb(&glb);
        assert!(model.list("images").is_empty());
        assert!(model.list("textures").is_empty());
        assert!(model.list("samplers").is_empty());
        for m in model.list("materials") {
            assert!(m
                .find("pbrMetallicRoughness")
                .and_then(|p| p.find("baseColorTexture"))
                .is_none());
            assert_ne!(alpha_mode(m), "MASK");
        }
        let logged = warnings.lock().unwrap();
        assert_eq!(
            logged.as_slice(),
            ["Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB"]
        );
    }
}

#[test]
fn glb_export_drops_tiff_keeps_color_remaps_png_index_and_warns_once() {
    for instanced in [false, true] {
        let skp = SkpFile::open(common::fixture("texture_tiff_and_png_2021.skp")).unwrap();
        let parse_opts = ParseOptions::new().with_original_textures(true);
        let warnings: Arc<Mutex<Vec<String>>> = Arc::new(Mutex::new(Vec::new()));
        let sink = Arc::clone(&warnings);

        let glb = if instanced {
            let scene = skp.build_instanced_scene(&parse_opts).unwrap();
            assert_eq!(scene.textures.len(), 2);
            assert_eq!(scene.textures[0].mime_type, "image/tiff");
            assert_eq!(scene.textures[1].mime_type, "image/png");
            let opts = InstancedGlbOptions::new()
                .with_textures(true)
                .with_log(move |lvl, msg| {
                    if lvl == LogLevel::Warning {
                        sink.lock().unwrap().push(msg.to_owned());
                    }
                });
            to_instanced_glb(&scene, &opts).unwrap()
        } else {
            let scene = skp.build_scene(&parse_opts).unwrap();
            assert_eq!(scene.textures.len(), 2);
            assert_eq!(scene.textures[0].mime_type, "image/tiff");
            assert_eq!(scene.textures[1].mime_type, "image/png");
            let opts = GlbOptions::new()
                .with_textures(true)
                .with_log(move |lvl, msg| {
                    if lvl == LogLevel::Warning {
                        sink.lock().unwrap().push(msg.to_owned());
                    }
                });
            to_glb(&scene, &opts).unwrap()
        };

        let model = load_glb(&glb);
        assert_eq!(model.list("images").len(), 1);
        assert_eq!(string(&model.list("images")[0], "mimeType"), "image/png");
        assert_eq!(model.list("textures").len(), 1);
        assert_eq!(int(&model.list("textures")[0], "source"), 0);
        assert_eq!(model.list("samplers").len(), 1);

        let srgb_to_linear = |c: f64| {
            if c <= 0.04045 {
                c / 12.92
            } else {
                ((c + 0.055) / 1.055).powf(2.4)
            }
        };
        let mut found_tiff = false;
        let mut found_png = false;
        for m in model.list("materials") {
            let pbr = m.find("pbrMetallicRoughness").unwrap();
            let bcf = numbers(pbr, "baseColorFactor");
            let (r, g, b) = (bcf[0], bcf[1], bcf[2]);
            if r > 0.5 {
                found_tiff = true;
                assert!(pbr.find("baseColorTexture").is_none());
                assert_eq!(alpha_mode(m), "OPAQUE");
                assert!((r - srgb_to_linear(200.0 / 255.0)).abs() < 1e-4);
                assert!((g - srgb_to_linear(60.0 / 255.0)).abs() < 1e-4);
                assert!((b - srgb_to_linear(40.0 / 255.0)).abs() < 1e-4);
            } else if b > 0.8 {
                found_png = true;
                let tex_ref = pbr.find("baseColorTexture").unwrap();
                assert_eq!(int(tex_ref, "index"), 0);
                assert_eq!(alpha_mode(m), "MASK");
            }
        }
        assert!(found_tiff);
        assert!(found_png);

        let logged = warnings.lock().unwrap();
        assert_eq!(
            logged.as_slice(),
            ["Texture 'tex.tif' is TIFF, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB"]
        );
    }
}
