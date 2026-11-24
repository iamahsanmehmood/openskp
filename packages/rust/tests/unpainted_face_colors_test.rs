mod common;
mod gltf_helper;

use std::collections::BTreeSet;

use gltf_helper::{load_glb, numbers};
use openskp::create::{create, DefinitionOptions, FaceOptions, InstanceOptions, LayerOptions};
use openskp::dxf_export::to_dxf;
use openskp::fragments::{from_fragments, to_fragments};
use openskp::glb::{to_glb, GlbOptions};
use openskp::ifc_export::{to_ifc, METRES_TO_MM};
use openskp::instanced_glb::{to_instanced_glb, InstancedGlbOptions};
use openskp::obj_export::to_obj;
use openskp::ply_export::to_ply_ascii;
use openskp::stl_export::{to_stl_ascii, to_stl_binary};
use openskp::{
    Color3, GltfMaterial, InstancedScene, LocalPrimitive, ParseOptions, ParseStage, SkpFile,
    METRES_TO_INCHES,
};

const FRONT: Color3 = [11, 122, 233];
const BACK: Color3 = [201, 102, 3];

fn rgb_of(materials: &[GltfMaterial], index: usize) -> Color3 {
    let f = materials[index].pbr_metallic_roughness.base_color_factor;
    [
        (f[0] * 255.0).round() as u8,
        (f[1] * 255.0).round() as u8,
        (f[2] * 255.0).round() as u8,
    ]
}

type Pt = [i64; 3];
type Tri = [Pt; 3];

fn quantize(x: f32, y: f32, z: f32) -> Pt {
    [
        (f64::from(x) * 1e5).round() as i64,
        (f64::from(y) * 1e5).round() as i64,
        (f64::from(z) * 1e5).round() as i64,
    ]
}

fn corners(positions: &[f32], indices: &[u32]) -> Vec<Tri> {
    let mut pts = Vec::with_capacity(positions.len() / 3);
    for p in positions.as_chunks::<3>().0 {
        pts.push(quantize(p[0], p[1], p[2]));
    }
    let mut out = Vec::with_capacity(indices.len() / 3);
    for t in indices.as_chunks::<3>().0 {
        out.push([pts[t[0] as usize], pts[t[1] as usize], pts[t[2] as usize]]);
    }
    out
}

fn same_winding(a: &Tri, b: &Tri) -> bool {
    *b == *a || *b == [a[1], a[2], a[0]] || *b == [a[2], a[0], a[1]]
}

fn quad_at_z(z: f64) -> [[f64; 3]; 4] {
    [
        [0.0, 0.0, z],
        [10.0, 0.0, z],
        [10.0, 10.0, z],
        [0.0, 10.0, z],
    ]
}

fn build_box_skp() -> SkpFile {
    let mut b = create();
    let mut pts = Vec::new();
    for z in [0.0, 10.0] {
        for y in [0.0, 10.0] {
            for x in [0.0, 10.0] {
                pts.push([x, y, z]);
            }
        }
    }
    let quads = [
        [0, 2, 3, 1],
        [4, 5, 7, 6],
        [0, 1, 5, 4],
        [1, 3, 7, 5],
        [3, 2, 6, 7],
        [2, 0, 4, 6],
    ];
    let face_opts = FaceOptions::default();
    for q in quads {
        let loop_pts = [pts[q[0]], pts[q[1]], pts[q[2]], pts[q[3]]];
        b.add_face(&loop_pts, &face_opts).unwrap();
    }
    SkpFile::from_buffer(b.to_bytes().unwrap())
}

fn linear_to_srgb(c: f64) -> f64 {
    if c <= 0.0 {
        0.0
    } else if c >= 1.0 {
        1.0
    } else if c <= 0.0031308 {
        c * 12.92
    } else {
        1.055 * c.powf(1.0 / 2.4) - 0.055
    }
}

fn glb_materials(glb_bytes: &[u8]) -> Vec<(Color3, bool)> {
    let glb = load_glb(glb_bytes);
    let mut out = Vec::new();
    for mat in glb.list("materials") {
        let pbr = mat.find("pbrMetallicRoughness").unwrap();
        let f = numbers(pbr, "baseColorFactor");
        let rgb = [
            (linear_to_srgb(f[0]) * 255.0).round() as u8,
            (linear_to_srgb(f[1]) * 255.0).round() as u8,
            (linear_to_srgb(f[2]) * 255.0).round() as u8,
        ];
        let ds = mat
            .find("doubleSided")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        out.push((rgb, ds));
    }
    out.sort();
    out
}

#[test]
fn each_side_shows_models_face_colour_across_versions() {
    let versions = [
        "v4", "v5", "v6", "2016", "2017", "2018", "2019", "2020", "2021",
    ];
    let opts = ParseOptions::default();
    for v in versions {
        let fname = format!("face_colors_{v}.skp");
        let skp = SkpFile::open(common::fixture(&fname)).unwrap();
        let baked = skp.build_scene(&opts).unwrap();
        let instanced = skp.build_instanced_scene(&opts).unwrap();

        assert_eq!(baked.glb_primitives.len(), 2, "{v}");
        let mut baked_prims = baked.glb_primitives.clone();
        baked_prims.sort_by_key(|p| p.unpainted_back);
        let bp0 = &baked_prims[0];
        let bp1 = &baked_prims[1];
        assert!(!bp0.unpainted_back, "{v}");
        assert!(bp1.unpainted_back, "{v}");
        assert_eq!(rgb_of(&baked.gltf_materials, bp0.material_index), FRONT);
        assert_eq!(rgb_of(&baked.gltf_materials, bp1.material_index), BACK);
        assert!(!baked.gltf_materials[bp0.material_index].double_sided);
        assert!(!baked.gltf_materials[bp1.material_index].double_sided);

        let fronts = corners(&bp0.positions, &bp0.indices);
        let backs = corners(&bp1.positions, &bp1.indices);
        assert!(!fronts.is_empty());
        assert_eq!(fronts.len(), backs.len());
        for (ft, bt) in fronts.iter().zip(&backs) {
            let sa: BTreeSet<Pt> = ft.iter().copied().collect();
            let sb: BTreeSet<Pt> = bt.iter().copied().collect();
            assert_eq!(sa, sb);
            assert!(!same_winding(ft, bt));
        }

        let mut inst_prims: Vec<LocalPrimitive> = Vec::new();
        for r in &instanced.mesh_resources {
            inst_prims.extend(r.primitives.iter().cloned());
        }
        assert_eq!(inst_prims.len(), 2, "{v}");
        inst_prims.sort_by_key(|p| p.unpainted_back);
        let ip0 = &inst_prims[0];
        let ip1 = &inst_prims[1];
        assert!(!ip0.unpainted_back);
        assert!(ip1.unpainted_back);
        assert_eq!(rgb_of(&instanced.gltf_materials, ip0.material_index), FRONT);
        assert_eq!(rgb_of(&instanced.gltf_materials, ip1.material_index), BACK);
        assert!(!instanced.gltf_materials[ip0.material_index].double_sided);
        assert!(!instanced.gltf_materials[ip1.material_index].double_sided);
    }
}

#[test]
fn tag_option_is_old_behaviour() {
    let opts = ParseOptions::new().with_unpainted_faces("tag");
    for v in ["v4", "2017", "2021"] {
        let fname = format!("face_colors_{v}.skp");
        let skp = SkpFile::open(common::fixture(&fname)).unwrap();
        let layer_rgb = skp.parse(&opts).unwrap().layers[0].color;

        let baked = skp.build_scene(&opts).unwrap();
        assert_eq!(baked.glb_primitives.len(), 1);
        let bp = &baked.glb_primitives[0];
        assert_eq!(rgb_of(&baked.gltf_materials, bp.material_index), layer_rgb);
        assert!(baked.gltf_materials[bp.material_index].double_sided);
        assert!(!bp.unpainted_back);

        let instanced = skp.build_instanced_scene(&opts).unwrap();
        assert_eq!(instanced.mesh_resources.len(), 1);
        assert_eq!(instanced.mesh_resources[0].primitives.len(), 1);
        let prim = &instanced.mesh_resources[0].primitives[0];
        let ip_rgb = rgb_of(&instanced.gltf_materials, prim.material_index);
        assert_eq!(ip_rgb, layer_rgb);
        assert!(instanced.gltf_materials[prim.material_index].double_sided);
        assert!(!prim.unpainted_back);
    }
}

#[test]
fn file_without_face_colours_uses_tag_colour() {
    let path = common::fixture("legacy_v3_synthetic.skp");
    let skp = SkpFile::open(path).unwrap();
    let style_opts = ParseOptions::default();
    let tag_opts = ParseOptions::new().with_unpainted_faces("tag");
    assert_eq!(skp.parse(&style_opts).unwrap().face_front_color, None);

    let style_baked = skp.build_scene(&style_opts).unwrap();
    let tag_baked = skp.build_scene(&tag_opts).unwrap();
    let style_prims = &style_baked.glb_primitives;
    let tag_prims = &tag_baked.glb_primitives;
    assert_eq!(style_prims.len(), tag_prims.len());
    for (sp, tp) in style_prims.iter().zip(tag_prims) {
        let sc = rgb_of(&style_baked.gltf_materials, sp.material_index);
        let tc = rgb_of(&tag_baked.gltf_materials, tp.material_index);
        assert_eq!(sc, tc);
        assert!(!sp.unpainted_back);
    }

    let style_inst = skp.build_instanced_scene(&style_opts).unwrap();
    let tag_inst = skp.build_instanced_scene(&tag_opts).unwrap();
    assert_eq!(
        style_inst.mesh_resources.len(),
        tag_inst.mesh_resources.len()
    );
    for r in &style_inst.mesh_resources {
        for p in &r.primitives {
            assert!(!p.unpainted_back);
        }
    }
}

#[test]
fn unknown_option_returns_parse_error() {
    let skp = SkpFile::open(common::fixture("face_colors_2017.skp")).unwrap();
    let bad_opts = ParseOptions::new().with_unpainted_faces("layer");
    let err1 = skp.build_scene(&bad_opts).unwrap_err();
    assert_eq!(err1.stage(), Some(ParseStage::BuildScene));
    let err2 = skp.build_instanced_scene(&bad_opts).unwrap_err();
    assert_eq!(err2.stage(), Some(ParseStage::BuildScene));
}

#[test]
fn paint_wins_over_face_colours() {
    let mut b = create();
    let red = b.add_material("Red", [200u8, 10, 10], None).unwrap();
    let mut comp_builder = b
        .add_component_definition("Painted", &DefinitionOptions::default())
        .unwrap();
    comp_builder
        .add_face(&quad_at_z(9.0), &FaceOptions::default())
        .unwrap();
    let comp = comp_builder.close().unwrap();

    b.add_face(
        &quad_at_z(0.0),
        &FaceOptions {
            material: Some(red),
            ..Default::default()
        },
    )
    .unwrap();
    b.add_face(
        &quad_at_z(5.0),
        &FaceOptions {
            back_material: Some(red),
            ..Default::default()
        },
    )
    .unwrap();
    b.add_instance(
        comp,
        &InstanceOptions {
            material: Some(red),
            ..Default::default()
        },
    )
    .unwrap();

    let opts = ParseOptions::default();
    let skp = SkpFile::from_buffer(b.to_bytes().unwrap());
    let model = skp.parse(&opts).unwrap();
    let front = model.face_front_color.unwrap();
    let back = model.face_back_color.unwrap();
    assert_ne!(front, back);

    let baked = skp.build_scene(&opts).unwrap();
    let mut baked_sides: BTreeSet<(Color3, bool, bool)> = BTreeSet::new();
    for p in &baked.glb_primitives {
        assert!(!p.unpainted_back);
        let c = rgb_of(&baked.gltf_materials, p.material_index);
        let ds = baked.gltf_materials[p.material_index].double_sided;
        baked_sides.insert((c, ds, p.unpainted_back));
    }
    assert!(baked_sides.contains(&([200, 10, 10], true, false)));
    assert!(baked_sides.contains(&(front, false, false)));
    assert!(baked_sides.contains(&(back, false, false)));

    let instanced = skp.build_instanced_scene(&opts).unwrap();
    let mut inst_sides: BTreeSet<(Color3, bool, bool)> = BTreeSet::new();
    for r in &instanced.mesh_resources {
        for p in &r.primitives {
            assert!(!p.unpainted_back);
            let c = rgb_of(&instanced.gltf_materials, p.material_index);
            let ds = instanced.gltf_materials[p.material_index].double_sided;
            inst_sides.insert((c, ds, p.unpainted_back));
        }
    }
    assert!(inst_sides.contains(&([200, 10, 10], true, false)));
    assert!(inst_sides.contains(&(front, false, false)));
    assert!(inst_sides.contains(&(back, false, false)));
}

#[test]
fn component_on_two_tags_is_one_mesh_resource() {
    let mut b = create();
    let tag_a = b
        .add_layer(
            "A",
            &LayerOptions {
                color: Some([255, 0, 0, 255]),
                ..Default::default()
            },
        )
        .unwrap();
    let tag_b = b
        .add_layer(
            "B",
            &LayerOptions {
                color: Some([0, 0, 255, 255]),
                ..Default::default()
            },
        )
        .unwrap();

    let mut part_builder = b
        .add_component_definition("Part", &DefinitionOptions::default())
        .unwrap();
    let unit_sq = [
        [0.0, 0.0, 0.0],
        [1.0, 0.0, 0.0],
        [1.0, 1.0, 0.0],
        [0.0, 1.0, 0.0],
    ];
    part_builder
        .add_face(&unit_sq, &FaceOptions::default())
        .unwrap();
    let part = part_builder.close().unwrap();

    b.add_instance(
        part,
        &InstanceOptions {
            layer: Some(tag_a),
            ..Default::default()
        },
    )
    .unwrap();
    b.add_instance(
        part,
        &InstanceOptions {
            layer: Some(tag_b),
            translation: [5.0, 0.0, 0.0],
            ..Default::default()
        },
    )
    .unwrap();

    let skp = SkpFile::from_buffer(b.to_bytes().unwrap());
    let inst = skp.build_instanced_scene(&ParseOptions::default()).unwrap();
    assert_eq!(inst.mesh_resources.len(), 1);
}

#[test]
fn single_colour_exports_and_fragments_keep_one_copy_of_each_face() {
    let skp = build_box_skp();
    let style_opts = ParseOptions::default();
    let tag_opts = ParseOptions::new().with_unpainted_faces("tag");
    let style = skp.build_scene(&style_opts).unwrap();
    let tag = skp.build_scene(&tag_opts).unwrap();

    let mut glb_tris = 0usize;
    for p in &style.glb_primitives {
        glb_tris += p.indices.len() / 3;
    }
    let mut surf_tris = 0usize;
    for p in style.surface_primitives() {
        surf_tris += p.indices.len() / 3;
    }
    assert_eq!(glb_tris, 24);
    assert_eq!(surf_tris, 12);

    assert_eq!(to_stl_ascii(&style, 1.0), to_stl_ascii(&tag, 1.0));
    let stl_bin = to_stl_binary(&style, 1.0);
    let stl_count = u32::from_le_bytes(stl_bin[80..84].try_into().unwrap());
    assert_eq!(stl_count, 12);

    assert_eq!(to_obj(&style, "").matches("\nf ").count(), 12);
    assert_eq!(to_obj(&tag, "").matches("\nf ").count(), 12);
    assert!(to_ply_ascii(&style).contains("element face 12\n"));

    let dxf_style = to_dxf(&style, METRES_TO_INCHES, "3dface")
        .unwrap()
        .matches("3DFACE")
        .count();
    let dxf_tag = to_dxf(&tag, METRES_TO_INCHES, "3dface")
        .unwrap()
        .matches("3DFACE")
        .count();
    assert!(dxf_style > 0);
    assert_eq!(dxf_style, dxf_tag);

    let ifc_style = to_ifc(&style, METRES_TO_MM, "IFC4", None, false).unwrap();
    let ifc_tag = to_ifc(&tag, METRES_TO_MM, "IFC4", None, false).unwrap();
    assert_eq!(ifc_style.matches("IFCTRIANGULATEDFACESET").count(), 1);
    assert_eq!(ifc_tag.matches("IFCTRIANGULATEDFACESET").count(), 1);

    let frag_tris = |inst: &InstancedScene| -> usize {
        let round_trip = from_fragments(&to_fragments(inst, false)).unwrap();
        let mut total = 0usize;
        for r in &round_trip.mesh_resources {
            for p in &r.primitives {
                total += p.indices.len() / 3;
            }
        }
        total
    };
    let inst_style = skp.build_instanced_scene(&style_opts).unwrap();
    let inst_tag = skp.build_instanced_scene(&tag_opts).unwrap();
    assert_eq!(frag_tris(&inst_style), 12);
    assert_eq!(frag_tris(&inst_tag), 12);
}

#[test]
fn glb_exports_show_both_sides() {
    let skp = SkpFile::open(common::fixture("face_colors_2017.skp")).unwrap();
    let style_opts = ParseOptions::default();
    let tag_opts = ParseOptions::new().with_unpainted_faces("tag");
    let layer_rgb = skp.parse(&style_opts).unwrap().layers[0].color;

    let mut expected_style = vec![(FRONT, false), (BACK, false)];
    expected_style.sort();

    let baked_style = skp.build_scene(&style_opts).unwrap();
    let baked_tag = skp.build_scene(&tag_opts).unwrap();
    let glb_opts = GlbOptions::default();
    let baked_style_glb = to_glb(&baked_style, &glb_opts).unwrap();
    let baked_tag_glb = to_glb(&baked_tag, &glb_opts).unwrap();
    assert_eq!(glb_materials(&baked_style_glb), expected_style);
    assert_eq!(glb_materials(&baked_tag_glb), vec![(layer_rgb, true)]);

    let inst_style = skp.build_instanced_scene(&style_opts).unwrap();
    let inst_tag = skp.build_instanced_scene(&tag_opts).unwrap();
    let iglb_opts = InstancedGlbOptions::default();
    let inst_style_glb = to_instanced_glb(&inst_style, &iglb_opts).unwrap();
    let inst_tag_glb = to_instanced_glb(&inst_tag, &iglb_opts).unwrap();
    assert_eq!(glb_materials(&inst_style_glb), expected_style);
    assert_eq!(glb_materials(&inst_tag_glb), vec![(layer_rgb, true)]);
}
