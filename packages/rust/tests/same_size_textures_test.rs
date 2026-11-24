use openskp::create::{create, FaceOptions};
use openskp::{GltfMaterial, ParseOptions, SkpFile};

const PNG_RED: &[u8] = &[
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
    0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xfb, 0xff,
    0x00, 0xdc, 0x28, 0x1e, 0x03, 0x06, 0x01, 0x23, 0xe0, 0x4f, 0x0f, 0xcc, 0x00, 0x00, 0x00, 0x00,
    0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
];

const PNG_BLUE: &[u8] = &[
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d, 0x49, 0x48, 0x44, 0x52,
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01, 0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,
    0xde, 0x00, 0x00, 0x00, 0x0f, 0x49, 0x44, 0x41, 0x54, 0x78, 0x01, 0x01, 0x04, 0x00, 0xfb, 0xff,
    0x00, 0x1e, 0x5a, 0xdc, 0x01, 0xee, 0x01, 0x55, 0xda, 0x6c, 0x0b, 0x85, 0x00, 0x00, 0x00, 0x00,
    0x49, 0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
];

fn textured_indices(materials: &[GltfMaterial]) -> Vec<usize> {
    materials
        .iter()
        .filter_map(|m| m.pbr_metallic_roughness.base_color_texture)
        .collect()
}

#[test]
fn two_different_pngs_of_equal_byte_length_give_two_textures() {
    assert_eq!(PNG_RED.len(), PNG_BLUE.len());
    assert_eq!(&PNG_RED[..16], &PNG_BLUE[..16]);

    let dir = std::env::temp_dir().join("openskp-rs-645-distinct");
    std::fs::create_dir_all(&dir).unwrap();
    let path_a = dir.join("a.png");
    let path_b = dir.join("b.png");
    std::fs::write(&path_a, PNG_RED).unwrap();
    std::fs::write(&path_b, PNG_BLUE).unwrap();

    let mut b = create();
    let mat_a = b
        .add_texture_material("MatA", &path_a, None, None, None)
        .unwrap();
    let mat_b = b
        .add_texture_material("MatB", &path_b, None, None, None)
        .unwrap();
    let fa = FaceOptions {
        material: Some(mat_a),
        ..Default::default()
    };
    let fb = FaceOptions {
        material: Some(mat_b),
        ..Default::default()
    };
    b.add_face(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &fa,
    )
    .unwrap();
    b.add_face(
        &[
            [15.0, 0.0, 0.0],
            [25.0, 0.0, 0.0],
            [25.0, 10.0, 0.0],
            [15.0, 10.0, 0.0],
        ],
        &fa,
    )
    .unwrap();
    b.add_face(
        &[
            [30.0, 0.0, 0.0],
            [40.0, 0.0, 0.0],
            [40.0, 10.0, 0.0],
            [30.0, 10.0, 0.0],
        ],
        &fb,
    )
    .unwrap();

    let skp = SkpFile::from_buffer(b.to_bytes().unwrap());
    for instanced in [false, true] {
        let (textures, mats) = if instanced {
            let s = skp.build_instanced_scene(&ParseOptions::default()).unwrap();
            (s.textures, s.gltf_materials)
        } else {
            let s = skp.build_scene(&ParseOptions::default()).unwrap();
            (s.textures, s.gltf_materials)
        };
        assert_eq!(textures.len(), 2);
        assert_eq!(textures[0].data, PNG_RED);
        assert_eq!(textures[1].data, PNG_BLUE);
        let mut indices = textured_indices(&mats);
        indices.sort_unstable();
        assert_eq!(indices, vec![0, 1]);
    }

    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn identical_png_bytes_in_two_materials_give_one_texture() {
    let dir = std::env::temp_dir().join("openskp-rs-645-dup");
    std::fs::create_dir_all(&dir).unwrap();
    let path_1 = dir.join("one.png");
    let path_2 = dir.join("two.png");
    std::fs::write(&path_1, PNG_RED).unwrap();
    std::fs::write(&path_2, PNG_RED).unwrap();

    let mut b = create();
    let mat_1 = b
        .add_texture_material("Mat1", &path_1, None, None, None)
        .unwrap();
    let mat_2 = b
        .add_texture_material("Mat2", &path_2, None, None, Some(0.6))
        .unwrap();
    let f1 = FaceOptions {
        material: Some(mat_1),
        ..Default::default()
    };
    let f2 = FaceOptions {
        material: Some(mat_2),
        ..Default::default()
    };
    b.add_face(
        &[
            [0.0, 0.0, 0.0],
            [10.0, 0.0, 0.0],
            [10.0, 10.0, 0.0],
            [0.0, 10.0, 0.0],
        ],
        &f1,
    )
    .unwrap();
    b.add_face(
        &[
            [20.0, 0.0, 0.0],
            [30.0, 0.0, 0.0],
            [30.0, 10.0, 0.0],
            [20.0, 10.0, 0.0],
        ],
        &f2,
    )
    .unwrap();

    let skp = SkpFile::from_buffer(b.to_bytes().unwrap());
    for instanced in [false, true] {
        let (textures, mats) = if instanced {
            let s = skp.build_instanced_scene(&ParseOptions::default()).unwrap();
            (s.textures, s.gltf_materials)
        } else {
            let s = skp.build_scene(&ParseOptions::default()).unwrap();
            (s.textures, s.gltf_materials)
        };
        assert_eq!(textures.len(), 1);
        assert_eq!(textures[0].data, PNG_RED);
        assert_eq!(textured_indices(&mats), vec![0, 0]);
    }

    let _ = std::fs::remove_dir_all(&dir);
}
