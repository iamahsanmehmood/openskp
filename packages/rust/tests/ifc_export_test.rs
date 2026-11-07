// IFC export: `ifc_export_test.cpp`, plus the Python port's `test_ifc.py`
// (minus its assembly-grouping tests - IfcElementAssembly is not part of the
// C++ exporter), `test_ifc4_conformance.py` and `test_ifc_curve_annotations.py`.
//
// One deliberate difference from the Python assertions: coordinates are
// written the C++ way, `std::fixed` with 6 decimals (`1000.000000`), not as
// Python's shortest repr of `round(v, 6)` (`1000.0`).

mod common;

use std::collections::BTreeMap;

use openskp::{
    classify_element, generate_ifc_guid, to_ifc, CurveArc, CurveSetMetadata, GlbPrimitive,
    GltfMaterial, IfcClassifier, InstanceNode, MeshMetadata, Scene, METRES_TO_INCHES, METRES_TO_MM,
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn prim(
    geom_name: &str,
    material_index: usize,
    positions: &[f32],
    indices: &[u32],
) -> GlbPrimitive {
    let n = positions.len() / 3;
    GlbPrimitive {
        positions: positions.to_vec(),
        normals: [0.0, 0.0, 1.0].repeat(n),
        uvs: [0.0, 0.0].repeat(n),
        indices: indices.to_vec(),
        material_index,
        geom_name: geom_name.to_string(),
    }
}

const TRIANGLE: [f32; 9] = [0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0];

fn material(rgba: [f64; 4]) -> GltfMaterial {
    let mut mat = GltfMaterial::default();
    mat.pbr_metallic_roughness.base_color_factor = rgba;
    mat
}

fn meta(name: &str) -> MeshMetadata {
    MeshMetadata {
        name: name.to_string(),
        ..MeshMetadata::default()
    }
}

fn props(pairs: &[(&str, &str)]) -> BTreeMap<String, String> {
    pairs
        .iter()
        .map(|(k, v)| (k.to_string(), v.to_string()))
        .collect()
}

fn root() -> InstanceNode {
    InstanceNode {
        name: "Root".to_string(),
        ..InstanceNode::default()
    }
}

fn create_mock_scene() -> Scene {
    let mut scene = Scene {
        scene_hierarchy: root(),
        glb_primitives: vec![
            prim("Outer Wall", 0, &TRIANGLE, &[0, 1, 2]),
            prim(
                "Front Door",
                1,
                &[2.0, 0.0, 0.0, 3.0, 0.0, 0.0, 2.0, 1.0, 0.0],
                &[0, 1, 2],
            ),
        ],
        gltf_materials: vec![
            material([0.8, 0.2, 0.2, 1.0]),
            material([0.2, 0.8, 0.2, 0.9]),
        ],
        ..Scene::default()
    };
    let mut wall = meta("Outer Wall");
    wall.properties = props(&[("Thickness", "200mm"), ("LoadBearing", "True")]);
    scene.mesh_index.insert("Outer Wall".to_string(), wall);
    let mut door = meta("Front Door");
    door.properties = props(&[("Material", "Wood")]);
    scene.mesh_index.insert("Front Door".to_string(), door);
    scene
}

/// A one-primitive scene with a mesh_index entry of the same name and a
/// mid-grey material (the shape the Python tests' `_mesh_scene` builds).
fn mesh_scene(name: &str, positions: &[f32], indices: &[u32]) -> Scene {
    let mut scene = Scene {
        scene_hierarchy: root(),
        glb_primitives: vec![prim(name, 0, positions, indices)],
        gltf_materials: vec![material([0.5, 0.5, 0.5, 1.0])],
        ..Scene::default()
    };
    scene.mesh_index.insert(name.to_string(), meta(name));
    scene
}

fn ifc(scene: &Scene) -> String {
    to_ifc(scene, METRES_TO_MM, "IFC4", None, false)
}

/// The first STEP entity line containing `token`.
fn entity_line<'a>(text: &'a str, token: &str) -> &'a str {
    text.lines()
        .find(|l| l.contains(token))
        .unwrap_or_else(|| panic!("no entity line containing {token:?}"))
}

/// The top-level comma-separated attributes of one STEP entity line: commas
/// inside a nested list do not split.
fn split_attributes(line: &str) -> Vec<String> {
    let body = &line[line.find('(').unwrap() + 1..line.rfind(')').unwrap()];
    let mut out = Vec::new();
    let mut depth = 0;
    let mut current = String::new();
    for ch in body.chars() {
        match ch {
            '(' => depth += 1,
            ')' => depth -= 1,
            _ => {}
        }
        if ch == ',' && depth == 0 {
            out.push(std::mem::take(&mut current));
        } else {
            current.push(ch);
        }
    }
    out.push(current);
    out
}

/// The ids of every `#n=` line.
fn entity_ids(text: &str) -> Vec<usize> {
    text.lines()
        .filter_map(|l| l.strip_prefix('#'))
        .filter_map(|l| l.split_once('=').and_then(|(id, _)| id.parse().ok()))
        .collect()
}

/// Every `#n=` line counted once, with no gap before the highest id: ids
/// are handed out contiguously, so a gap means an id was allocated and its
/// entity never written.
fn assert_ids_are_dense(text: &str) -> usize {
    let ids = entity_ids(text);
    assert!(!ids.is_empty(), "no entity lines at all");
    let mut unique = ids.clone();
    unique.sort_unstable();
    unique.dedup();
    assert_eq!(unique.len(), ids.len(), "an entity id was used twice");
    let max = *ids.iter().max().unwrap();
    assert_eq!(
        ids.len(),
        max,
        "{} entity lines but the highest id is {max}",
        ids.len()
    );
    ids.len()
}

/// A lexical check of the DATA section a STEP reader would choke on: every
/// line is `#n=NAME(...);` with parentheses balanced outside string
/// literals, string literals closed, and only ASCII anywhere.
fn assert_step_well_formed(text: &str) {
    assert!(text.is_ascii(), "non-ASCII byte in STEP text");
    assert!(text.ends_with("ENDSEC;\r\nEND-ISO-10303-21;\r\n"));
    let lines: Vec<&str> = text.split("\r\n").collect();
    let data = lines
        .iter()
        .position(|l| *l == "DATA;")
        .expect("no DATA section")
        + 1;
    for line in &lines[data..] {
        if *line == "ENDSEC;" {
            break;
        }
        let (id, rest) = line
            .split_once('=')
            .unwrap_or_else(|| panic!("not an entity line: {line}"));
        assert!(
            id.len() > 1 && id.starts_with('#') && id[1..].bytes().all(|b| b.is_ascii_digit()),
            "{line}"
        );
        let name_end = rest
            .find('(')
            .unwrap_or_else(|| panic!("no attribute list: {line}"));
        assert!(
            rest[..name_end]
                .bytes()
                .all(|b| b.is_ascii_uppercase() || b.is_ascii_digit()),
            "{line}"
        );
        assert!(rest.ends_with(");"), "{line}");
        let (mut depth, mut in_string) = (0_i32, false);
        for ch in rest[name_end..].chars() {
            match (in_string, ch) {
                (_, '\'') => in_string = !in_string, // '' re-opens at once
                (false, '(') => depth += 1,
                (false, ')') => {
                    depth -= 1;
                    assert!(depth >= 0, "unbalanced: {line}");
                }
                (false, '#') => {}
                _ => {}
            }
        }
        assert!(!in_string && depth == 0, "unbalanced: {line}");
        // An entity reference is one token: `#12`, never `# 12`.
        assert!(!line[1..].contains("# "), "{line}");
    }
}

/// Every `\X2\...\X0\` segment in `text`, decoded from UTF-16BE.
fn x2_segments(text: &str) -> Vec<String> {
    let mut out = Vec::new();
    let mut rest = text;
    while let Some(start) = rest.find("\\X2\\") {
        let after = &rest[start + 4..];
        let end = after.find("\\X0\\").expect("unterminated \\X2\\ segment");
        let hex = &after[..end];
        assert!(
            hex.len().is_multiple_of(4)
                && hex
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'A'..=b'F').contains(&b))
        );
        let units: Vec<u16> = (0..hex.len())
            .step_by(4)
            .map(|i| u16::from_str_radix(&hex[i..i + 4], 16).unwrap())
            .collect();
        out.push(String::from_utf16(&units).unwrap());
        rest = &after[end + 4..];
    }
    out
}

// ---------------------------------------------------------------------------
// ifc_export_test.cpp / test_ifc.py
// ---------------------------------------------------------------------------

#[test]
fn generates_valid_22_char_guid() {
    assert_eq!(generate_ifc_guid().len(), 22);
}

#[test]
fn classifies_element_names() {
    assert_eq!(classify_element("Main Wall", "", "", false).0, "IFCWALL");
    assert_eq!(classify_element("Front Door", "", "", false).0, "IFCDOOR");
    assert_eq!(
        classify_element("Office Window", "", "", false).0,
        "IFCWINDOW"
    );
    assert_eq!(
        classify_element("Concrete Slab", "", "", false).0,
        "IFCSLAB"
    );
    assert_eq!(classify_element("Steel Beam", "", "", false).0, "IFCBEAM");
    assert_eq!(classify_element("Steel Beam", "", "", false).1, "IfcBeam");
}

#[test]
fn classify_element_falls_back_to_layer_name_when_component_name_has_no_keyword() {
    // SketchUp default names carry no signal, but a BIM-style layer/tag
    // often does (openskp#238).
    assert_eq!(
        classify_element("Component#109415", "Walls", "", false).0,
        "IFCWALL"
    );
    assert_eq!(classify_element("Group#3", "Doors", "", false).0, "IFCDOOR");
}

#[test]
fn classify_element_prefers_component_name_over_layer_name() {
    assert_eq!(
        classify_element("Interior Door", "Walls", "", false).0,
        "IFCDOOR"
    );
}

#[test]
fn classify_element_falls_back_to_generic_proxy_when_neither_matches() {
    assert_eq!(
        classify_element("Component#109415", "Layer0", "", false).0,
        "IFCBUILDINGELEMENTPROXY"
    );
    assert_eq!(
        classify_element("Component#109415", "", "", false).0,
        "IFCBUILDINGELEMENTPROXY"
    );
}

#[test]
fn to_ifc_uses_layer_name_fallback_for_unnamed_components() {
    let mut scene = Scene {
        glb_primitives: vec![prim("Component#109415", 0, &TRIANGLE, &[0, 1, 2])],
        ..Scene::default()
    };
    let mut m = meta("Component#109415");
    m.layer = "Walls".to_string();
    scene.mesh_index.insert("Component#109415".to_string(), m);

    let ifc_text = ifc(&scene);
    assert!(ifc_text.contains("IFCWALL("));
    assert!(!ifc_text.contains("IFCBUILDINGELEMENTPROXY"));
}

#[test]
fn to_ifc_accepts_custom_classifier_override() {
    let mut scene = Scene {
        glb_primitives: vec![prim("Outer Wall", 0, &TRIANGLE, &[0, 1, 2])],
        ..Scene::default()
    };
    scene
        .mesh_index
        .insert("Outer Wall".to_string(), meta("Outer Wall"));

    let always_column = |_: &str, _: &str| ("IFCCOLUMN".to_string(), "IfcColumn".to_string());
    let ifc_text = to_ifc(
        &scene,
        METRES_TO_INCHES,
        "IFC4",
        Some(&always_column),
        false,
    );
    assert!(!ifc_text.contains("IFCWALL("));
    assert!(ifc_text.contains("IFCCOLUMN("));
}

#[test]
fn to_ifc_custom_classifier_types_every_element() {
    let always_column: &IfcClassifier<'_> =
        &|_, _| ("IFCCOLUMN".to_string(), "IfcColumn".to_string());
    let ifc_text = to_ifc(
        &create_mock_scene(),
        METRES_TO_MM,
        "IFC4",
        Some(always_column),
        false,
    );
    assert!(!ifc_text.contains("IFCWALL("));
    assert!(!ifc_text.contains("IFCDOOR("));
    assert_eq!(ifc_text.matches("IFCCOLUMN(").count(), 2);
}

#[test]
fn a_custom_classifier_is_called_with_the_display_name_and_layer() {
    let seen = std::cell::RefCell::new(Vec::new());
    let classify = |name: &str, layer: &str| {
        seen.borrow_mut()
            .push((name.to_string(), layer.to_string()));
        ("IFCCOLUMN".to_string(), "IfcColumn".to_string())
    };
    let ifc_text = to_ifc(
        &mesh_scene("Stud 12", &TRIANGLE, &[0, 1, 2]),
        METRES_TO_MM,
        "IFC4",
        Some(&classify),
        false,
    );
    assert_eq!(
        *seen.borrow(),
        [("Stud 12".to_string(), "Layer0".to_string())]
    );
    assert!(ifc_text.contains("IFCCOLUMN("));
}

#[test]
fn serializes_scene_to_ifc4_step_text() {
    let mut scene = Scene {
        glb_primitives: vec![prim("Outer Wall", 0, &TRIANGLE, &[0, 1, 2])],
        ..Scene::default()
    };
    let mut m = meta("Outer Wall");
    m.properties
        .insert("Thickness".to_string(), "200mm".to_string());
    scene.mesh_index.insert("Outer Wall".to_string(), m);

    let ifc_text = ifc(&scene);
    for token in [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_SCHEMA(('IFC4'));",
        "IFCPROJECT",
        "IFCSITE",
        "IFCBUILDING",
        "IFCBUILDINGSTOREY",
        "IFCWALL",
        "IFCTRIANGULATEDFACESET",
        "IFCCARTESIANPOINTLIST3D",
        "IFCPROPERTYSET",
        "ENDSEC;",
    ] {
        assert!(ifc_text.contains(token), "missing {token}");
    }
}

#[test]
fn to_ifc_structure() {
    let ifc_text = ifc(&create_mock_scene());
    for token in [
        "ISO-10303-21;",
        "HEADER;",
        "FILE_SCHEMA(('IFC4'));",
        "IFCPROJECT",
        "IFCSITE",
        "IFCBUILDING",
        "IFCBUILDINGSTOREY",
        "IFCWALL",
        "IFCDOOR",
        "IFCTRIANGULATEDFACESET",
        "IFCCARTESIANPOINTLIST3D",
        "IFCPROPERTYSET",
        "IFCPROPERTYSINGLEVALUE",
        "IFCRELCONTAINEDINSPATIALSTRUCTURE",
        "ENDSEC;",
    ] {
        assert!(ifc_text.contains(token), "missing {token}");
    }
    assert_ids_are_dense(&ifc_text);
    assert_step_well_formed(&ifc_text);
}

// The exporter always declares millimetres - the default scale has to
// actually produce millimetre-scaled values, or every coordinate reads back
// ~25.4x too small in any IFC consumer that respects the unit declaration.
#[test]
fn to_ifc_declares_millimetres_and_scales_to_match() {
    let mut scene = mesh_scene(
        "Test Triangle",
        &[0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 1.0],
        &[0, 1, 2],
    );
    scene.scene_hierarchy.name = "Root".to_string();

    let ifc_text = ifc(&scene);
    assert!(ifc_text.contains("IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.)"));
    // glTF (1.0, 0.0, 0.0) = 1 metre along X -> IFC X, 1000.0 millimetres.
    assert!(ifc_text.contains("(1000.000000,-0.000000,0.000000)"));
}

// glTF is Y-up (glTF.y = SketchUp Z, glTF.z = -SketchUp Y); IFC is Z-up.
#[test]
fn to_ifc_converts_gltf_y_up_to_ifc_z_up() {
    let scene = mesh_scene(
        "Test Triangle",
        &[0.0, 0.0, 0.0, 2.0, 3.0, 5.0, 0.0, 1.0, 0.0],
        &[0, 1, 2],
    );
    let ifc_text = to_ifc(&scene, 1.0, "IFC4", None, false);
    // glTF (2.0, 3.0, 5.0) is SketchUp (x=2.0, y=-5.0, z=3.0).
    assert!(ifc_text.contains("(2.000000,-5.000000,3.000000)"));
    assert!(!ifc_text.contains("(2.000000,3.000000,5.000000)"));
}

// prim.geom_name is an internal lookup key; the IFC element's Name comes
// from MeshMetadata.name.
#[test]
fn to_ifc_uses_real_instance_name_not_internal_key() {
    let mut scene = Scene {
        scene_hierarchy: root(),
        glb_primitives: vec![prim("mesh_3_ROOT__W1_Layer0", 0, &TRIANGLE, &[0, 1, 2])],
        gltf_materials: vec![material([0.5, 0.5, 0.5, 1.0])],
        ..Scene::default()
    };
    let mut m = meta("W1");
    m.layer = "Layer0".to_string();
    scene
        .mesh_index
        .insert("mesh_3_ROOT__W1_Layer0".to_string(), m);

    let ifc_text = ifc(&scene);
    assert!(ifc_text.contains("'W1'"));
    assert!(!ifc_text.contains("mesh_3_ROOT"));
}

// LayerOn must match the source file's own hidden/visible state per layer.
#[test]
fn to_ifc_layer_on_reflects_scene_layer_hidden() {
    let mut scene = create_mock_scene();
    scene.mesh_index.get_mut("Outer Wall").unwrap().layer = "Hidden Layer".to_string();
    scene.mesh_index.get_mut("Front Door").unwrap().layer = "Visible Layer".to_string();
    scene.layer_hidden = [
        ("Hidden Layer".to_string(), true),
        ("Visible Layer".to_string(), false),
    ]
    .into();

    let ifc_text = ifc(&scene);
    assert!(ifc_text.contains("IFCPRESENTATIONLAYERWITHSTYLE"));
    assert!(!ifc_text.contains("IFCPRESENTATIONLAYERASSIGNMENT("));
    assert!(ifc_text.contains("'Hidden Layer',$,(#"));

    let hidden_line = entity_line(&ifc_text, "'Hidden Layer'");
    let visible_line = entity_line(&ifc_text, "'Visible Layer'");
    assert!(hidden_line.contains(",.F.,.F.,.F.,())"));
    assert!(visible_line.contains(",.T.,.F.,.F.,())"));
}

// A third-party plugin's attribute dictionary becomes its own named
// property set, separate from Pset_CustomProperties.
#[test]
fn to_ifc_writes_a_property_set_per_extra_attribute_dictionary() {
    let mut scene = Scene {
        scene_hierarchy: root(),
        glb_primitives: vec![prim(
            "mesh_0_ROOT__Profile25_Layer0",
            0,
            &TRIANGLE,
            &[0, 1, 2],
        )],
        gltf_materials: vec![material([0.5, 0.5, 0.5, 1.0])],
        ..Scene::default()
    };
    let mut m = meta("Profile25");
    m.properties = props(&[("width", "10.0")]);
    m.attribute_dictionaries.insert(
        "fbd-einfo".to_string(),
        props(&[("code", "aPf"), ("angle", "90")]),
    );
    scene
        .mesh_index
        .insert("mesh_0_ROOT__Profile25_Layer0".to_string(), m);

    let ifc_text = ifc(&scene);
    for token in [
        "'Pset_CustomProperties'",
        "'Pset_fbd-einfo'",
        "'width'",
        "'code'",
        "'aPf'",
        "'angle'",
        "'90'",
    ] {
        assert!(ifc_text.contains(token), "missing {token}");
    }
}

#[test]
fn to_ifc_skips_empty_attribute_dictionaries() {
    let mut scene = mesh_scene("Test Triangle", &TRIANGLE, &[0, 1, 2]);
    scene
        .mesh_index
        .get_mut("Test Triangle")
        .unwrap()
        .attribute_dictionaries
        .insert("empty_dict".to_string(), BTreeMap::new());
    let ifc_text = ifc(&scene);
    assert!(!ifc_text.contains("Pset_empty_dict"));
    assert_ids_are_dense(&ifc_text);
}

// Matching keywords against the full hierarchy path stays off by default.
#[test]
fn classify_element_full_path_only_when_opted_in() {
    let path = "ROOT / Wall Frame / Stud 12";
    assert_eq!(
        classify_element("Stud 12", "Layer0", path, false).0,
        "IFCBUILDINGELEMENTPROXY"
    );
    assert_eq!(
        classify_element("Stud 12", "Layer0", path, true).0,
        "IFCWALL"
    );
}

// The path fallback only kicks in once name and layer both miss.
#[test]
fn classify_element_full_path_still_prefers_name_and_layer() {
    let path = "ROOT / Wall Frame / Front Door";
    assert_eq!(
        classify_element("Front Door", "Layer0", path, true).0,
        "IFCDOOR"
    );
}

#[test]
fn to_ifc_classify_using_full_path_opt_in() {
    let mut scene = Scene {
        scene_hierarchy: root(),
        glb_primitives: vec![prim(
            "mesh_0_ROOT__Wall_Frame__Stud_12_Layer0",
            0,
            &TRIANGLE,
            &[0, 1, 2],
        )],
        gltf_materials: vec![GltfMaterial::default()],
        ..Scene::default()
    };
    let mut m = meta("Stud 12");
    m.layer = "Layer0".to_string();
    m.path = "ROOT / Wall Frame / Stud 12".to_string();
    scene
        .mesh_index
        .insert("mesh_0_ROOT__Wall_Frame__Stud_12_Layer0".to_string(), m);

    let default_text = ifc(&scene);
    assert!(default_text.contains("IFCBUILDINGELEMENTPROXY"));
    assert!(!default_text.contains("IFCWALL("));

    let opted_in_text = to_ifc(&scene, METRES_TO_MM, "IFC4", None, true);
    assert!(opted_in_text.contains("IFCWALL("));
    // The real, clean name is still what's shown.
    assert!(opted_in_text.contains("'Stud 12'"));
}

#[test]
fn export_ifc_writes_a_file() {
    let dir = std::env::temp_dir().join(format!("openskp_ifc_export_{}", std::process::id()));
    let path = dir.join("nested").join("test.ifc");
    openskp::export_ifc(
        &create_mock_scene(),
        &path,
        METRES_TO_MM,
        "IFC4",
        None,
        false,
    )
    .unwrap();
    let content = std::fs::read_to_string(&path).unwrap();
    assert!(content.contains("ISO-10303-21;"));
    assert!(content.contains("IFCWALL"));
    let _ = std::fs::remove_dir_all(&dir);
}

// ---------------------------------------------------------------------------
// Hand-built scenes: the rest of the port's own behavior
// ---------------------------------------------------------------------------

#[test]
fn the_header_names_the_schema_and_a_utc_timestamp() {
    let text = to_ifc(&create_mock_scene(), METRES_TO_MM, "ifc2x3", None, false);
    assert!(text
        .starts_with("ISO-10303-21;\r\nHEADER;\r\nFILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');\r\n"));
    assert!(text.contains("FILE_SCHEMA(('IFC2X3'));"));
    let file_name = entity_line(&text, "FILE_NAME(");
    let stamp = &file_name["FILE_NAME('model.ifc','".len()..][..19];
    let b = stamp.as_bytes();
    assert!(
        b.iter().enumerate().all(|(i, c)| match i {
            4 | 7 => *c == b'-',
            10 => *c == b'T',
            13 | 16 => *c == b':',
            _ => c.is_ascii_digit(),
        }),
        "{stamp}"
    );
    assert!(to_ifc(&create_mock_scene(), METRES_TO_MM, "", None, false)
        .contains("FILE_SCHEMA(('IFC4'));"));
}

#[test]
fn the_model_context_references_its_placement_as_one_token() {
    let text = ifc(&create_mock_scene());
    let axis = entity_line(&text, "=IFCAXIS2PLACEMENT3D(");
    let axis_id = &axis[..axis.find('=').unwrap()];
    let ctx = entity_line(&text, "IFCGEOMETRICREPRESENTATIONCONTEXT(");
    assert!(
        ctx.ends_with(&format!(
            "=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-5,{axis_id},$);"
        )),
        "{ctx}"
    );
}

#[test]
fn materials_become_shared_surface_styles() {
    let mut scene = create_mock_scene();
    // A third primitive reusing the wall's color shares its style.
    scene
        .glb_primitives
        .push(prim("Inner Wall", 0, &TRIANGLE, &[0, 1, 2]));
    let text = ifc(&scene);
    assert!(text.contains("=IFCCOLOURRGB($, 0.8000,0.2000,0.2000);"));
    assert!(text.contains("=IFCCOLOURRGB($, 0.2000,0.8000,0.2000);"));
    assert_eq!(text.matches("IFCSURFACESTYLE(").count(), 2);
    assert_eq!(text.matches("IFCSTYLEDITEM(").count(), 3);
    assert!(text.contains("IFCSURFACESTYLE('Outer Wall_Material',.BOTH.,"));
    // Alpha 0.9 is 10% transparent.
    let rendering_lines: Vec<&str> = text
        .lines()
        .filter(|l| l.contains("IFCSURFACESTYLERENDERING("))
        .collect();
    assert_eq!(split_attributes(rendering_lines[0])[1], "0.0000");
    assert_eq!(split_attributes(rendering_lines[1])[1], "0.1000");
    // No material at all: 0.8 grey.
    let mut bare = mesh_scene("Bare", &TRIANGLE, &[0, 1, 2]);
    bare.gltf_materials.clear();
    assert!(ifc(&bare).contains("=IFCCOLOURRGB($, 0.8000,0.8000,0.8000);"));
}

#[test]
fn every_element_is_contained_in_the_storey() {
    let text = ifc(&create_mock_scene());
    let storey = entity_line(&text, "IFCBUILDINGSTOREY(");
    let storey_id = &storey[..storey.find('=').unwrap()];
    let rel = entity_line(&text, "IFCRELCONTAINEDINSPATIALSTRUCTURE(");
    let attrs = split_attributes(rel);
    assert_eq!(attrs[5], storey_id);
    let wall_id = entity_line(&text, "=IFCWALL(").split('=').next().unwrap();
    let door_id = entity_line(&text, "=IFCDOOR(").split('=').next().unwrap();
    assert_eq!(attrs[4], format!("({wall_id},{door_id})"));
}

#[test]
fn an_empty_scene_is_a_spatial_skeleton() {
    let text = ifc(&Scene::default());
    assert!(text.contains("IFCBUILDINGSTOREY("));
    assert!(!text.contains("IFCRELCONTAINEDINSPATIALSTRUCTURE("));
    assert!(!text.contains("IFCPRESENTATIONLAYERWITHSTYLE("));
    assert_ids_are_dense(&text);
    assert_step_well_formed(&text);
}

#[test]
fn primitives_without_triangles_or_vertices_are_skipped() {
    let mut scene = mesh_scene("Wall", &TRIANGLE, &[]);
    scene.glb_primitives.push(prim("Wall", 0, &[], &[0, 1, 2]));
    let text = ifc(&scene);
    assert!(!text.contains("IFCWALL("));
    assert!(!text.contains("IFCTRIANGULATEDFACESET("));
    assert_ids_are_dense(&text);
}

#[test]
fn quotes_and_backslashes_in_names_are_escaped_once() {
    let mut scene = mesh_scene("O'Brien\\Wall", &TRIANGLE, &[0, 1, 2]);
    let m = scene.mesh_index.get_mut("O'Brien\\Wall").unwrap();
    m.layer = "Bob's \\ layer".to_string();
    scene
        .layer_hidden
        .insert("Bob's \\ layer".to_string(), true);
    let text = ifc(&scene);
    assert!(text.contains("=IFCWALL('"));
    assert!(text.contains(",'O''Brien\\\\Wall',$,$,#"));
    let layer = entity_line(&text, "IFCPRESENTATIONLAYERWITHSTYLE(");
    assert!(layer.contains("('Bob''s \\\\ layer',$,(#"), "{layer}");
    // The layer is found in layer_hidden by its own name, not the escaped one.
    assert!(layer.ends_with(",$,.F.,.F.,.F.,());"), "{layer}");
    assert_step_well_formed(&text);
}

#[test]
fn a_non_ascii_layer_is_escaped_once_and_keeps_its_visibility() {
    let mut scene = mesh_scene("Panel", &TRIANGLE, &[0, 1, 2]);
    scene.mesh_index.get_mut("Panel").unwrap().layer = "剪力墙".to_string();
    scene.layer_hidden.insert("剪力墙".to_string(), true);
    let text = ifc(&scene);
    let layer = entity_line(&text, "IFCPRESENTATIONLAYERWITHSTYLE(");
    assert!(
        layer.contains("('\\X2\\526A529B5899\\X0\\',$,(#"),
        "{layer}"
    );
    assert!(layer.ends_with(",$,.F.,.F.,.F.,());"), "{layer}");
    assert_step_well_formed(&text);
}

#[test]
fn ifc2x3_products_use_that_schemas_attribute_counts() {
    let text = to_ifc(
        &mesh_scene("Outer Wall", &TRIANGLE, &[0, 1, 2]),
        METRES_TO_MM,
        "IFC2X3",
        None,
        false,
    );
    assert_eq!(split_attributes(entity_line(&text, "=IFCWALL(")).len(), 8);
    let door = to_ifc(
        &mesh_scene("Front Door", &TRIANGLE, &[0, 1, 2]),
        METRES_TO_MM,
        "IFC2X3",
        None,
        false,
    );
    assert_eq!(split_attributes(entity_line(&door, "=IFCDOOR(")).len(), 10);
}

#[test]
fn a_proxy_gets_a_notdefined_predefined_type() {
    let text = ifc(&mesh_scene("Thing", &TRIANGLE, &[0, 1, 2]));
    let attrs = split_attributes(entity_line(&text, "=IFCBUILDINGELEMENTPROXY("));
    assert_eq!(attrs.len(), 9);
    assert_eq!(attrs[8], ".NOTDEFINED.");
    assert_eq!(attrs[2], "'Thing'");
}

#[test]
fn an_unknown_custom_type_keeps_nine_attributes() {
    let classify = |_: &str, _: &str| ("IFCMADEUPTHING".to_string(), "IfcMadeUpThing".to_string());
    let text = to_ifc(
        &mesh_scene("Thing", &TRIANGLE, &[0, 1, 2]),
        METRES_TO_MM,
        "IFC4",
        Some(&classify),
        false,
    );
    let attrs = split_attributes(entity_line(&text, "=IFCMADEUPTHING("));
    assert_eq!(attrs.len(), 9);
    assert_eq!(attrs[8], "$");
}

// ---------------------------------------------------------------------------
// test_ifc4_conformance.py
// ---------------------------------------------------------------------------

mod conformance {
    use super::*;

    fn closed_flag(text: &str) -> String {
        split_attributes(entity_line(text, "IFCTRIANGULATEDFACESET("))[2].clone()
    }

    #[test]
    fn a_product_carries_every_attribute_its_type_declares() {
        // IfcDoor and IfcWall differ (13 vs 9) - one hardcoded count cannot
        // be right for both.
        for (geom_name, step_type, count) in
            [("Front Door", "IFCDOOR", 13), ("Outer Wall", "IFCWALL", 9)]
        {
            let text = ifc(&mesh_scene(geom_name, &TRIANGLE, &[0, 1, 2]));
            let line = entity_line(&text, &format!("{step_type}("));
            assert_eq!(split_attributes(line).len(), count, "{line}");
        }
    }

    #[test]
    fn ifc2x_spellings_are_gone() {
        let text = ifc(&mesh_scene("Front Door", &TRIANGLE, &[0, 1, 2]));
        assert!(!text.contains(".TRUE."));
        assert!(!text.contains(".FALSE."));
        assert!(!text.contains(".READWRITE."));
        assert!(!text.contains(".STERADIANUNIT."));
    }

    #[test]
    fn ifc4_spellings_are_used() {
        let text = ifc(&mesh_scene("Front Door", &TRIANGLE, &[0, 1, 2]));
        assert!(text.contains(".T.")); // IfcBoolean true
        assert!(text.contains(".F.")); // IfcBoolean false
        assert!(text.contains(".SOLIDANGLEUNIT.")); // IfcUnitEnum
        assert!(text.contains(".STERADIAN.")); // the unit's own name
        assert!(text.contains(".NOCHANGE.")); // IfcChangeActionEnum
    }

    #[test]
    fn non_ascii_is_escaped_and_round_trips() {
        let name = "剪力墙 JLQ-1";
        let text = ifc(&mesh_scene(name, &TRIANGLE, &[0, 1, 2]));
        assert!(!text.contains(name));
        assert!(text.is_ascii());
        let decoded = x2_segments(&text);
        assert!(!decoded.is_empty());
        assert!(decoded.iter().any(|s| s == "剪力墙"));
        // ASCII runs inside the same string are not escaped.
        assert!(text.contains("JLQ-1"));
    }

    #[test]
    fn the_property_set_keys_are_escaped_too() {
        let mut scene = mesh_scene("Panel", &TRIANGLE, &[0, 1, 2]);
        scene.mesh_index.get_mut("Panel").unwrap().properties = props(&[("名称", "承重墙")]);
        let decoded = x2_segments(&ifc(&scene));
        assert!(decoded.iter().any(|s| s == "名称"));
        assert!(decoded.iter().any(|s| s == "承重墙"));
    }

    #[test]
    fn pure_ascii_is_left_verbatim() {
        let text = ifc(&mesh_scene("Outer Wall", &TRIANGLE, &[0, 1, 2]));
        assert!(text.contains("Outer Wall"));
        assert!(!text.contains("\\X2\\"));
    }

    #[test]
    fn the_segment_is_utf16be_and_the_check_can_tell() {
        let text = ifc(&mesh_scene("剪力墙", &TRIANGLE, &[0, 1, 2]));
        let start = text.find("\\X2\\").unwrap() + 4;
        let payload = &text[start..start + text[start..].find("\\X0\\").unwrap()];
        assert_eq!(payload, payload.to_ascii_uppercase());
        let bytes: Vec<u8> = (0..payload.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&payload[i..i + 2], 16).unwrap())
            .collect();
        let be: Vec<u16> = bytes
            .chunks(2)
            .map(|c| u16::from_be_bytes([c[0], c[1]]))
            .collect();
        let le: Vec<u16> = bytes
            .chunks(2)
            .map(|c| u16::from_le_bytes([c[0], c[1]]))
            .collect();
        assert_eq!(String::from_utf16(&be).unwrap(), "剪力墙");
        assert_ne!(String::from_utf16_lossy(&le), "剪力墙");
    }

    // A tetrahedron's four corners.
    const A: [f32; 3] = [0.0, 0.0, 0.0];
    const B: [f32; 3] = [1.0, 0.0, 0.0];
    const C: [f32; 3] = [0.0, 1.0, 0.0];
    const D: [f32; 3] = [0.0, 0.0, 1.0];

    fn pts(corners: &[[f32; 3]]) -> Vec<f32> {
        corners.concat()
    }

    #[test]
    fn an_open_mesh_is_not_closed() {
        assert_eq!(
            closed_flag(&ifc(&mesh_scene("Open", &pts(&[A, B, C]), &[0, 1, 2]))),
            ".F."
        );
    }

    #[test]
    fn a_closed_tetra_is_closed() {
        let text = ifc(&mesh_scene(
            "Tetra",
            &pts(&[A, B, C, D]),
            &[0, 2, 1, 0, 1, 3, 1, 2, 3, 2, 0, 3],
        ));
        assert_eq!(closed_flag(&text), ".T.");
    }

    #[test]
    fn vertices_are_welded_by_position_not_by_index() {
        // One face written with duplicate vertices: the index buffer alone
        // reports two boundary edges; a weld by position sees all six edges
        // shared twice.
        let text = ifc(&mesh_scene(
            "Welded",
            &pts(&[A, B, C, D, A, B, C, D]),
            &[0, 1, 2, 4, 5, 7, 0, 2, 3, 1, 2, 3],
        ));
        assert_eq!(closed_flag(&text), ".T.");
    }

    #[test]
    fn per_face_vertices_do_not_make_a_closed_mesh_look_open() {
        // What the exporter actually receives: every triangle with its own
        // three vertices, no sharing at all.
        let text = ifc(&mesh_scene(
            "PerFace",
            &pts(&[A, B, C, A, B, D, A, C, D, B, C, D]),
            &[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
        ));
        assert_eq!(closed_flag(&text), ".T.");
    }

    #[test]
    fn an_edge_used_three_times_is_not_closed() {
        // A T-junction: no boundary edge, but three triangles meet along one
        // edge; "exactly twice" is the rule, not "at least twice".
        let text = ifc(&mesh_scene(
            "TJunction",
            &pts(&[A, B, C, D, C, D, A, B]),
            &[0, 1, 2, 0, 1, 3, 0, 1, 4, 0, 1, 5],
        ));
        assert_eq!(closed_flag(&text), ".F.");
    }

    #[test]
    fn an_out_of_range_index_reads_as_open_rather_than_panicking() {
        let text = ifc(&mesh_scene("Broken", &pts(&[A, B, C]), &[0, 1, 9]));
        assert_eq!(closed_flag(&text), ".F.");
        assert!(text.contains("((1,2,10))"));
    }
}

// ---------------------------------------------------------------------------
// test_ifc_curve_annotations.py
// ---------------------------------------------------------------------------

mod curve_annotations {
    use super::*;

    fn curve_set(name: &str, points: &[[f64; 3]]) -> CurveSetMetadata {
        CurveSetMetadata {
            name: name.to_string(),
            layer: "Layer0".to_string(),
            path: "ROOT".to_string(),
            points_m: points.to_vec(),
            ..CurveSetMetadata::default()
        }
    }

    fn line_run() -> CurveSetMetadata {
        curve_set("Curve", &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]])
    }

    /// One circular arc as the scene emits it: the ends and the middle, and
    /// 0-based index triples into them.
    fn arc() -> CurveArc {
        CurveArc {
            points: vec![[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [2.0, 0.0, 0.0]],
            segments: vec![[0, 1, 2]],
        }
    }

    fn arc_run(name: &str) -> CurveSetMetadata {
        CurveSetMetadata {
            arc: Some(arc()),
            ..curve_set(name, &[[0.0, 0.0, 0.0]; 3])
        }
    }

    fn curve_scene(curve_sets: Vec<CurveSetMetadata>) -> Scene {
        Scene {
            scene_hierarchy: root(),
            curve_sets,
            ..Scene::default()
        }
    }

    fn polyline_point_ids(text: &str) -> Vec<String> {
        let refs = split_attributes(entity_line(text, "IFCPOLYLINE("))[0].clone();
        refs.trim_matches(|c| c == '(' || c == ')')
            .split(',')
            .map(|r| r.trim_start_matches('#').to_string())
            .collect()
    }

    fn point_coords(text: &str, id: &str) -> String {
        let line = entity_line(text, &format!("#{id}=IFCCARTESIANPOINT("));
        line[line.find("((").unwrap()..].to_string()
    }

    #[test]
    fn a_run_becomes_a_polyline_of_its_points() {
        let text = ifc(&curve_scene(vec![curve_set(
            "Curve",
            &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
        )]));
        let ids = polyline_point_ids(&text);
        assert_eq!(ids.len(), 3);
        for id in &ids {
            assert!(text.contains(&format!("#{id}=IFCCARTESIANPOINT(")));
        }
        // glTF Y-up metres -> IFC Z-up millimetres: y becomes -z, z becomes y.
        assert!(text.contains("IFCCARTESIANPOINT((0.000000,-0.000000,0.000000))"));
        assert!(text.contains("IFCCARTESIANPOINT((1000.000000,-0.000000,0.000000))"));
        assert!(text.contains("IFCCARTESIANPOINT((0.000000,-1000.000000,0.000000))"));
    }

    #[test]
    fn a_closed_run_repeats_its_first_point() {
        let mut cs = curve_set(
            "Curve",
            &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 0.0, 1.0]],
        );
        cs.closed = true;
        let text = ifc(&curve_scene(vec![cs]));
        let ids = polyline_point_ids(&text);
        assert_eq!(ids.len(), 4);
        let coords: Vec<String> = ids.iter().map(|id| point_coords(&text, id)).collect();
        assert_eq!(coords[0], coords[3]);
        let mut unique = coords.clone();
        unique.sort();
        unique.dedup();
        assert_eq!(unique.len(), 3);
    }

    #[test]
    fn an_arc_becomes_an_indexed_polycurve() {
        let text = ifc(&curve_scene(vec![arc_run("A1")]));
        assert!(text.contains("IFCCARTESIANPOINTLIST3D("));
        assert!(text.contains("IFCINDEXEDPOLYCURVE("));
        assert!(text.contains("(IFCARCINDEX((1,2,3)))"));
        // SelfIntersect, the third attribute.
        assert_eq!(
            split_attributes(entity_line(&text, "IFCINDEXEDPOLYCURVE("))[2],
            ".F."
        );
        assert!(!text.contains("IFCPOLYLINE("));
    }

    #[test]
    fn the_segment_is_inline_and_has_no_entity_of_its_own() {
        // `#24=IFCARCINDEX((1,2,3));` is a syntax error a reader truncates at.
        let text = ifc(&curve_scene(vec![arc_run("Curve")]));
        assert!(!text
            .lines()
            .any(|l| l.starts_with('#') && l.contains("=IFCARCINDEX")));
        assert_eq!(text.matches("IFCARCINDEX").count(), 1);
    }

    #[test]
    fn the_coord_list_is_1_based() {
        let text = ifc(&curve_scene(vec![arc_run("Curve")]));
        let points = &split_attributes(entity_line(&text, "IFCCARTESIANPOINTLIST3D("))[0];
        let count = points.matches("),(").count() + 1;
        let start = text.find("IFCARCINDEX((").unwrap() + "IFCARCINDEX((".len();
        let index: Vec<usize> = text[start..start + text[start..].find(')').unwrap()]
            .split(',')
            .map(|i| i.parse().unwrap())
            .collect();
        assert_eq!(index, [1, 2, count]);
        assert_eq!(count, arc().points.len());
    }

    #[test]
    fn the_arc_points_get_the_same_axis_swap_and_scale() {
        let mut cs = arc_run("Curve");
        cs.arc = Some(CurveArc {
            points: vec![[0.0, 0.0, 0.0], [1.0, 2.0, 3.0], [2.0, 0.0, 0.0]],
            segments: vec![[0, 1, 2]],
        });
        let text = ifc(&curve_scene(vec![cs]));
        assert!(text.contains(
            "IFCCARTESIANPOINTLIST3D(((0.000000,-0.000000,0.000000),(1000.000000,-3000.000000,2000.000000),\
             (2000.000000,-0.000000,0.000000)));"
        ));
    }

    #[test]
    fn a_two_segment_arc_shares_the_point_between_them() {
        let mut cs = curve_set("Curve", &[[0.0, 0.0, 0.0]; 4]);
        cs.arc = Some(CurveArc {
            points: vec![
                [0.0, 0.0, 0.0],
                [1.0, 0.0, 0.0],
                [2.0, 0.0, 0.0],
                [3.0, 0.0, 0.0],
            ],
            segments: vec![[0, 1, 2], [2, 3, 0]],
        });
        let text = ifc(&curve_scene(vec![cs]));
        assert!(text.contains("(IFCARCINDEX((1,2,3)),IFCARCINDEX((3,4,1)))"));
    }

    #[test]
    fn an_arc_with_fewer_than_three_points_stays_a_polyline() {
        let mut cs = line_run();
        cs.arc = Some(CurveArc {
            points: vec![[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
            segments: vec![],
        });
        let text = ifc(&curve_scene(vec![cs]));
        assert!(text.contains("IFCPOLYLINE("));
        assert!(!text.contains("IFCINDEXEDPOLYCURVE("));
    }

    #[test]
    fn a_curve_set_becomes_an_annotation() {
        let text = ifc(&curve_scene(vec![curve_set(
            "Panel",
            &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        )]));
        assert_eq!(text.matches("IFCANNOTATION(").count(), 1);
        assert!(text.contains("IFCGEOMETRICCURVESET("));
        assert!(text.contains("'Annotation','GeometricCurveSet'"));
        let attrs = split_attributes(entity_line(&text, "IFCANNOTATION("));
        assert_eq!(attrs.len(), 7);
        assert_eq!(attrs[2], "'Panel'");
    }

    #[test]
    fn an_unnamed_run_is_called_curve() {
        let text = ifc(&curve_scene(vec![curve_set(
            "",
            &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]],
        )]));
        assert_eq!(
            split_attributes(entity_line(&text, "IFCANNOTATION("))[2],
            "'Curve'"
        );
    }

    #[test]
    fn the_sub_context_is_an_annotation_under_the_model_context() {
        let text = ifc(&curve_scene(vec![line_run()]));
        let attrs = split_attributes(entity_line(&text, "IFCGEOMETRICREPRESENTATIONSUBCONTEXT("));
        assert_eq!(attrs[0], "'Annotation'");
        // Dimension, precision, world coordinate system and true north are
        // DERIVE FROM ParentContext in IFC4.
        assert_eq!(attrs[2..6], ["*", "*", "*", "*"]);
        let ctx = entity_line(&text, "IFCGEOMETRICREPRESENTATIONCONTEXT(");
        assert_eq!(attrs[6], ctx[..ctx.find('=').unwrap()]);
        // The annotation's representation lives in the sub-context.
        let sub = entity_line(&text, "IFCGEOMETRICREPRESENTATIONSUBCONTEXT(");
        let rep = entity_line(&text, "'Annotation','GeometricCurveSet'");
        assert_eq!(split_attributes(rep)[0], sub[..sub.find('=').unwrap()]);
    }

    #[test]
    fn every_curve_set_gets_its_own_annotation() {
        let text = ifc(&curve_scene(vec![
            curve_set("A", &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
            curve_set("B", &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
            curve_set("C", &[[0.0, 0.0, 0.0], [1.0, 0.0, 0.0]]),
        ]));
        assert_eq!(text.matches("IFCANNOTATION(").count(), 3);
        assert_eq!(text.matches("IFCGEOMETRICCURVESET(").count(), 3);
        assert!(text.matches("IFCPRODUCTDEFINITIONSHAPE(").count() >= 3);
    }

    #[test]
    fn the_layer_assignment_carries_the_curve() {
        let mut cs = line_run();
        cs.layer = "A-GLAZ-CWMG".to_string();
        let text = ifc(&curve_scene(vec![cs]));
        let curve_line = text
            .lines()
            .find(|l| l.starts_with('#') && l.contains("=IFCPOLYLINE("))
            .unwrap();
        let curve_id = &curve_line[..curve_line.find('=').unwrap()];
        let line = entity_line(&text, "IFCPRESENTATIONLAYERWITHSTYLE(");
        assert!(line.contains("'A-GLAZ-CWMG'"));
        assert!(split_attributes(line)[2].contains(curve_id));
    }

    #[test]
    fn a_curve_on_a_hidden_layer_is_off() {
        let mut cs = line_run();
        cs.layer = "Lines".to_string();
        let mut scene = curve_scene(vec![cs]);
        scene.layer_hidden.insert("Lines".to_string(), true);
        let line = ifc(&scene);
        assert!(
            entity_line(&line, "IFCPRESENTATIONLAYERWITHSTYLE(").ends_with(",$,.F.,.F.,.F.,());")
        );
    }

    #[test]
    fn a_run_without_a_layer_goes_on_layer0_with_the_meshes() {
        let mut cs = line_run();
        cs.layer.clear();
        let mut scene = mesh_scene("Thing", &TRIANGLE, &[0, 1, 2]);
        scene.curve_sets.push(cs);
        let text = ifc(&scene);
        assert_eq!(text.matches("IFCPRESENTATIONLAYERWITHSTYLE(").count(), 1);
        let layer = entity_line(&text, "IFCPRESENTATIONLAYERWITHSTYLE(");
        assert!(layer.contains("('Layer0',$,(#"));
        assert_eq!(split_attributes(layer)[2].matches('#').count(), 2);
    }

    #[test]
    fn a_run_of_fewer_than_two_points_is_skipped() {
        for points in [&[][..], &[[1.0, 2.0, 3.0]][..]] {
            let text = ifc(&curve_scene(vec![curve_set("Curve", points)]));
            assert!(!text.contains("IFCANNOTATION("));
            assert!(!text.contains("IFCPOLYLINE("));
            assert_ids_are_dense(&text);
        }
    }

    #[test]
    fn attribute_dictionaries_become_property_sets() {
        let mut cs = line_run();
        cs.attribute_dictionaries.insert(
            "AI_Classification".to_string(),
            props(&[("ifc_class", "IfcCurtainWall"), ("basis", "A-GLAZ-CWMG")]),
        );
        let text = ifc(&curve_scene(vec![cs]));
        assert!(text.contains("'Pset_AI_Classification'"));
        assert!(text.contains("IFCTEXT('IfcCurtainWall')"));
        assert!(text.contains("IFCTEXT('A-GLAZ-CWMG')"));
        let ann = entity_line(&text, "IFCANNOTATION(");
        let ann_id = &ann[..ann.find('=').unwrap()];
        assert!(
            entity_line(&text, "IFCRELDEFINESBYPROPERTIES(").contains(&format!(",({ann_id}),#"))
        );
    }

    #[test]
    fn annotations_come_before_meshes_in_the_containment() {
        let mut scene = mesh_scene("Outer Wall", &TRIANGLE, &[0, 1, 2]);
        scene.curve_sets.push(line_run());
        let text = ifc(&scene);
        let ann = entity_line(&text, "=IFCANNOTATION(");
        let wall = entity_line(&text, "=IFCWALL(");
        let rel = split_attributes(entity_line(&text, "IFCRELCONTAINEDINSPATIALSTRUCTURE("));
        assert_eq!(
            rel[4],
            format!(
                "({},{})",
                &ann[..ann.find('=').unwrap()],
                &wall[..wall.find('=').unwrap()]
            )
        );
        assert_ids_are_dense(&text);
        assert_step_well_formed(&text);
    }

    #[test]
    fn a_curve_only_export_is_dense() {
        let text = ifc(&curve_scene(vec![line_run()]));
        assert_ids_are_dense(&text);
        assert_step_well_formed(&text);
    }

    #[test]
    fn the_annotation_survives_an_arc() {
        // The annotation, its layer assignment and the containment
        // relationship all come after the arc's segment - they are what a
        // truncation at an unparsable segment removes.
        let text = ifc(&curve_scene(vec![arc_run("Curve")]));
        assert_ids_are_dense(&text);
        assert_step_well_formed(&text);
        assert!(text.contains("IFCANNOTATION("));
        assert!(text.contains("IFCPRESENTATIONLAYERWITHSTYLE("));
        assert!(text.contains("IFCRELCONTAINEDINSPATIALSTRUCTURE("));
    }

    // test_a_reader_actually_parses_the_whole_file needs ifcopenshell; its
    // writer-side half is assert_step_well_formed above.

    // capilla's 20 loose-edge runs: 5 at the model root, 15 inside the
    // "puerta" component. None of them is a proven arc, so they stay
    // polylines.
    #[test]
    fn a_real_files_curve_sets_arrive_intact() {
        let scene = openskp::SkpFile::open(common::fixture("capilla_quiroz_v17.skp"))
            .unwrap()
            .build_scene(&openskp::ParseOptions::default())
            .unwrap();
        let text = ifc(&scene);
        assert_ids_are_dense(&text);
        assert_step_well_formed(&text);
        assert_eq!(text.matches("IFCANNOTATION(").count(), 20);
        assert_eq!(text.matches("IFCPOLYLINE(").count(), 20);
        assert!(!text.contains("IFCINDEXEDPOLYCURVE("));
    }
}
