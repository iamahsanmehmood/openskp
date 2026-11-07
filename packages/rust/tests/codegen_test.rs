//! Tests for `openskp::codegen::to_rust_code`, ported from the C++ `codegen_test.cpp` (which
//! tests `to_cpp_code`; this port emits Rust instead, so the expected fragments are the Rust
//! equivalents). Same approach: build a small model, generate code for it, and assert on the TEXT
//! of the generated code - the exact fragments a real Face/Instance/Layer/Material must produce.
//!
//! Cases that get their model by parsing the writer's output, or a pre-2021 fixture, need the
//! hand-built-model and VFF-fixture cases run now.

mod common;

use std::collections::BTreeMap;

use common::fixture;
use openskp::create::{
    create, DefinitionOptions, FaceOptions, InstanceOptions, LayerOptions, Point3, SkpBuilder,
};
use openskp::{
    to_rust_code, CoEdge, Definition, Edge, Face, Instance, Material, ParseOptions, SkpFile,
    SkpModel, Texture, Vertex,
};

fn roundtrip(builder: &mut SkpBuilder) -> SkpModel {
    SkpFile::from_buffer(builder.to_bytes().unwrap())
        .parse(&ParseOptions::default())
        .unwrap()
}

fn square(size: f64) -> Vec<Point3> {
    vec![
        [0.0, 0.0, 0.0],
        [size, 0.0, 0.0],
        [size, size, 0.0],
        [0.0, size, 0.0],
    ]
}

#[test]
fn emits_solid_material_layer_and_face_with_field_assignment() {
    let mut builder = create();
    let red = builder.add_material("Red", [255, 0, 0, 255], None).unwrap();
    let lopts = LayerOptions {
        hidden: true,
        ..Default::default()
    };
    builder.add_layer("Roof", &lopts).unwrap();
    let fopts = FaceOptions {
        material: Some(red),
        ..Default::default()
    };
    builder.add_face(&square(10.0), &fopts).unwrap();
    let model = roundtrip(&mut builder);

    let code = to_rust_code(&model);

    assert!(
        code.contains(r#"add_material("Red", [255, 0, 0, 255], None)"#),
        "{code}"
    );
    // The reader always reports an implicit "Layer0" first (layer_opts0/layer0), so "Roof" - the
    // only layer this test explicitly created - comes out as layer_opts1/layer1.
    assert!(code.contains("let mut layer_opts1 = LayerOptions::default();"));
    assert!(code.contains("layer_opts1.hidden = true;"));
    assert!(code.contains(r#"add_layer("Roof", &layer_opts1)"#));
    assert!(code.contains("let mut face_opts0 = FaceOptions::default();"));
    assert!(code.contains("face_opts0.material = Some(mat0);"));
    // No UV on this face (no textured material), so auto_triangulate should be set.
    assert!(code.contains("face_opts0.auto_triangulate = true;"));
}

#[test]
fn emits_textured_material_with_applied_width_and_opacity() {
    let mut model = SkpModel::default();
    model.materials.push(Material {
        name: "Brick".into(),
        transparency: 0.75,
        texture: Some(Texture {
            filename: "brick.png".into(),
            width: 24.0,
            height: 12.0,
            data: Some(vec![0x89, b'P', b'N', b'G', 0x0D, 0x0A, 0x1A, 0x0A]),
        }),
        ..Default::default()
    });

    let code = to_rust_code(&model);

    assert!(
        code.contains(
            r#"add_texture_material("Brick", &tex_path_0, Some(12.0), Some(24.0), Some(0.75))"#
        ),
        "{code}"
    );
    // The image bytes are embedded as base64 and decoded/staged by helpers the file defines.
    assert!(code.contains(r#""iVBORw0KGgo=","#));
    assert!(code.contains("openskp_codegen_base64_decode(b64_0)"));
    assert!(code.contains("fn openskp_codegen_base64_decode(s: &str) -> Vec<u8> {"));
    assert!(code.contains(r#"openskp_codegen_write_temp_file(&tex_bytes_0, ".png")"#));
    assert!(code.contains("fn openskp_codegen_write_temp_file("));
}

#[test]
fn reconstructs_face_holes() {
    let mut builder = create();
    let fopts = FaceOptions {
        holes: vec![vec![
            [20.0, 20.0, 0.0],
            [80.0, 20.0, 0.0],
            [80.0, 80.0, 0.0],
            [20.0, 80.0, 0.0],
        ]],
        ..Default::default()
    };
    builder.add_face(&square(100.0), &fopts).unwrap();
    let model = roundtrip(&mut builder);

    let code = to_rust_code(&model);

    let holes_pos = code.find(".holes = vec![vec![").expect("holes line");
    assert!(code[holes_pos..].contains("[20.0, 20.0, 0.0]"));
    assert!(code[holes_pos..].contains("[80.0, 80.0, 0.0]"));
}

#[test]
fn preserves_genuinely_empty_instance_name_and_instance_paint() {
    let mut builder = create();
    let red = builder.add_material("Red", [255, 0, 0, 255], None).unwrap();
    let mut def = builder
        .add_component_definition("Widget", &DefinitionOptions::default())
        .unwrap();
    def.add_face(&square(10.0), &FaceOptions::default())
        .unwrap();
    let def = def.close().unwrap();

    // Equal to the definition's own name - the name line should be omitted.
    let named = InstanceOptions {
        name: Some("Widget".into()),
        ..Default::default()
    };
    builder.add_instance(def, &named).unwrap();

    // Genuinely empty, not omitted - must stay empty, not fall back.
    let empty_named = InstanceOptions {
        name: Some(String::new()),
        material: Some(red),
        ..Default::default()
    };
    builder.add_instance(def, &empty_named).unwrap();

    let model = roundtrip(&mut builder);
    assert_eq!(model.root().instances.len(), 2);
    assert_eq!(model.root().instances[0].name, "Widget");
    assert_eq!(model.root().instances[1].name, "");
    assert!(model.root().instances[1].material_id.is_some());

    let code = to_rust_code(&model);

    // The instance whose stored name already equals the definition's name gets no explicit
    // `.name =` line at all (add_instance's own default already reproduces it).
    let first = code.find("inst_opts0.translation").expect("inst_opts0");
    let second = code.find("inst_opts1.translation").expect("inst_opts1");
    assert!(!code[first..second].contains(".name ="));

    assert!(code.contains(r#"inst_opts1.name = Some("".to_owned());"#));
    assert!(code.contains("inst_opts1.material = Some(mat0);"));
}

#[test]
fn preserves_genuinely_empty_definition_name() {
    // Same bug class as the empty instance name case above: a fabricated "Def<id>" name used to
    // replace a genuinely empty one. SketchUp Groups are internally just unnamed component
    // definitions, so an empty name is common in real files.
    let mut builder = create();
    let mut def = builder
        .add_component_definition("", &DefinitionOptions::default())
        .unwrap();
    def.add_face(&square(10.0), &FaceOptions::default())
        .unwrap();
    let def = def.close().unwrap();
    builder
        .add_instance(def, &InstanceOptions::default())
        .unwrap();

    let model = roundtrip(&mut builder);
    assert_eq!(model.definitions.len(), 1);
    assert_eq!(model.definitions.values().next().unwrap().name, "");

    let code = to_rust_code(&model);

    // add_component_definition("") - not a fabricated "Def123" name - and the def-vs-instance
    // name comparison must still use the real (empty) name.
    assert!(code.contains(r#"add_component_definition("", "#));
    assert!(!code.contains(r#"add_component_definition("Def"#));
}

#[test]
fn places_definitions_through_their_closed_handles() {
    let mut builder = create();
    let mut inner = builder
        .add_component_definition("Inner", &DefinitionOptions::default())
        .unwrap();
    inner
        .add_face(&square(10.0), &FaceOptions::default())
        .unwrap();
    let inner = inner.close().unwrap();

    let mut outer = builder
        .add_component_definition("Outer", &DefinitionOptions::default())
        .unwrap();
    outer
        .add_instance(inner, &InstanceOptions::default())
        .unwrap();
    let outer = outer.close().unwrap();

    builder
        .add_instance(outer, &InstanceOptions::default())
        .unwrap();
    let model = roundtrip(&mut builder);

    let code = to_rust_code(&model);

    // A definition is built through its ComponentDefinitionBuilder, then shadowed by the handle
    // close() returns - which every later add_instance (nested or root) takes by value.
    assert!(code.contains("def0.add_face("));
    assert!(code.contains("let def0 = def0.close()?;"));
    assert!(code.contains("def1.add_instance(def0, &"));
    assert!(code.contains("let def1 = def1.close()?;"));
    assert!(code.contains("builder.add_instance(def1, &"));
    assert!(!code.contains("def0->") && !code.contains("builder->"));
    assert!(!code.contains("add_instance(*def"));
}

// ---------------------------------------------------------------------------------------------
// Hand-built models: the same paths as the round-trip cases above, without needing the reader.
// ---------------------------------------------------------------------------------------------

/// Adds one closed polygon loop to `defn` (fresh vertex/edge ids from `next_id`) and returns it.
fn add_loop(defn: &mut Definition, next_id: &mut i64, points: &[Point3]) -> Vec<CoEdge> {
    let first = *next_id;
    for (i, p) in points.iter().enumerate() {
        let id = first + i as i64;
        defn.vertices.insert(
            id,
            Vertex {
                id,
                x: p[0],
                y: p[1],
                z: p[2],
            },
        );
    }
    *next_id += points.len() as i64;
    let mut coedges = Vec::new();
    for i in 0..points.len() {
        let id = *next_id;
        *next_id += 1;
        let v1_id = first + i as i64;
        let v2_id = first + ((i + 1) % points.len()) as i64;
        defn.edges.insert(
            id,
            Edge {
                id,
                v1_id,
                v2_id,
                ..Default::default()
            },
        );
        coedges.push(CoEdge {
            edge_id: id,
            orientation: 1,
        });
    }
    coedges
}

fn add_face(defn: &mut Definition, next_id: &mut i64, outer: &[Point3], holes: &[Vec<Point3>]) {
    let mut loops = vec![add_loop(defn, next_id, outer)];
    for hole in holes {
        loops.push(add_loop(defn, next_id, hole));
    }
    let id = *next_id;
    *next_id += 1;
    defn.faces.insert(
        id,
        Face {
            id,
            loops,
            normal: Some([0.0, 0.0, 1.0]),
            ..Default::default()
        },
    );
}

fn placement(ref_idx: i64, name: &str, translation: [f64; 3]) -> Instance {
    let mut matrix = vec![1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0];
    matrix.extend_from_slice(&translation);
    Instance {
        name: name.into(),
        ref_idx: Some(ref_idx),
        matrix,
        ..Default::default()
    }
}

#[test]
fn hand_built_model_emits_holes_names_and_nested_handles() {
    let mut next_id = 1000;
    let mut model = SkpModel::default();

    let mut leaf = Definition {
        id: 1,
        name: String::new(),
        ..Default::default()
    };
    add_face(&mut leaf, &mut next_id, &square(10.0), &[]);
    let mut outer = Definition {
        id: 2,
        name: "Outer \"A\"\nB".into(),
        ..Default::default()
    };
    add_face(&mut outer, &mut next_id, &square(50.0), &[]);
    outer.instances.push(placement(1, "", [5.0, 0.0, 0.0]));
    model.definitions = BTreeMap::from([(1, leaf), (2, outer)]);

    add_face(
        model.root_mut(),
        &mut next_id,
        &square(100.0),
        &[vec![
            [20.0, 20.0, 0.0],
            [80.0, 20.0, 0.0],
            [80.0, 80.0, 0.0],
            [20.0, 80.0, 0.0],
        ]],
    );
    model
        .root_mut()
        .instances
        .push(placement(2, "Outer \"A\"\nB", [1234.56789, 0.0, -3.0]));
    model
        .root_mut()
        .instances
        .push(placement(2, "", [0.0, 0.0, 0.0]));

    let code = to_rust_code(&model);

    // Dependency order: the leaf (def0) is built and closed before the outer one places it.
    let leaf_close = code.find("let def0 = def0.close()?;").expect("def0 closed");
    let outer_open = code
        .find(r#"let mut def1 = builder.add_component_definition("Outer \"A\"\nB", "#)
        .expect("def1 opened");
    assert!(leaf_close < outer_open, "{code}");
    assert!(code.contains(r#"let mut def0 = builder.add_component_definition("", "#));
    // The header comment can't be ended early by a line break in the name.
    assert!(code.contains("    // Outer \"A\" B - 1 faces, 1 nested instances\n"));
    // The nested instance's genuinely empty name stays explicit.
    assert!(code.contains("inst_opts0.translation = [5.0, 0.0, 0.0];"));
    assert!(code
        .contains("inst_opts0.matrix3x3 = Some([1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]);"));
    assert!(code.contains(r#"inst_opts0.name = Some("".to_owned());"#));
    assert!(code.contains("def1.add_instance(def0, &inst_opts0)?;"));
    // Root placements: the matching name is implied, the empty one is explicit.
    assert!(code.contains("inst_opts1.translation = [1234.5679, 0.0, -3.0];"));
    assert!(!code.contains("inst_opts1.name"));
    assert!(code.contains(r#"inst_opts2.name = Some("".to_owned());"#));
    assert!(code.contains("builder.add_instance(def1, &inst_opts2)?;"));
    // Root face with a hole.
    let holes_pos = code
        .find("face_opts2.holes = vec![vec![")
        .expect("holes line");
    assert!(code[holes_pos..].contains("[20.0, 20.0, 0.0], [80.0, 20.0, 0.0]"));
    assert!(code.contains(
        "builder.add_face(&[[0.0, 0.0, 0.0], [100.0, 0.0, 0.0], [100.0, 100.0, 0.0], [0.0, 100.0, 0.0]], &face_opts2)?;"
    ));
    assert!(code.trim_end().ends_with("builder.to_bytes()\n}"));
    // No texture, so no helpers.
    assert!(!code.contains("openskp_codegen_base64_decode"));
}

#[test]
fn a_large_model_is_emitted_in_closure_chunks() {
    // Rust-only: one flat body for a real 43k-face model made rustc's borrow checker run out of
    // memory, so past 500 faces/placements every body is split into closures of at most 500.
    let mut model = SkpModel::default();
    let mut next_id = 1;
    for i in 0..600 {
        let x = f64::from(i) * 10.0;
        let tri = [[x, 0.0, 0.0], [x + 1.0, 0.0, 0.0], [x, 1.0, 0.0]];
        add_face(model.root_mut(), &mut next_id, &tri, &[]);
    }

    let code = to_rust_code(&model);

    assert_eq!(
        code.matches("    (|| -> Result<(), SkpWriteError> {\n")
            .count(),
        2
    );
    assert_eq!(code.matches("        Ok(())\n    })()?;\n").count(), 2);
    assert!(code.contains("        let mut face_opts0 = FaceOptions::default();\n"));
    assert!(code.contains("        builder.add_face(&[[5990.0, 0.0, 0.0], "));

    // Below the threshold the transcript stays flat.
    let mut small = SkpModel::default();
    add_face(small.root_mut(), &mut next_id, &square(1.0), &[]);
    assert!(!to_rust_code(&small).contains("(||"));
}

#[test]
fn degenerate_faces_are_counted_in_a_leading_comment() {
    let mut model = SkpModel::default();
    let mut next_id = 1;
    add_face(model.root_mut(), &mut next_id, &square(1.0), &[]);
    let id = next_id;
    model.root_mut().faces.insert(
        id,
        Face {
            id,
            loops: vec![vec![CoEdge {
                edge_id: 1,
                orientation: 1,
            }]],
            ..Default::default()
        },
    );

    let code = to_rust_code(&model);

    assert!(code.starts_with(
        "// 1 degenerate face(s) (fewer than 3 resolvable vertices) were skipped during generation.\n"
    ));
}

// ---------------------------------------------------------------------------------------------
// Real fixtures.
// ---------------------------------------------------------------------------------------------

fn generates_plausible_well_formed_code(name: &str) {
    let model = SkpFile::open(fixture(name))
        .unwrap()
        .parse(&ParseOptions::default())
        .unwrap();
    let code = to_rust_code(&model);

    assert!(!code.is_empty());
    // Re-checked against real, much larger geometry: definitions are placed by handle, by value,
    // and nothing is called through `->` (only fn and chunk-closure signatures use it).
    for line in code.lines().filter(|l| l.contains("->")) {
        let decl = line.trim_start();
        assert!(
            decl.starts_with("fn ")
                || decl.starts_with("pub fn ")
                || decl == "(|| -> Result<(), SkpWriteError> {",
            "{name}: {line}"
        );
    }
    // A large model's statements come in closure chunks, each closed and propagated.
    assert_eq!(
        code.matches("(|| -> Result<(), SkpWriteError> {").count(),
        code.matches("})()?;").count(),
        "{name}"
    );
    assert!(!code.contains("add_instance(*def"));

    // Base64 texture data is chunked into <=16000-char literals, so no single line comes close
    // to that regardless of how large the source texture is.
    for line in code.lines() {
        assert!(line.len() < 16200, "{name}: line of {} chars", line.len());
    }

    // If any texture material was emitted, its helper functions are defined too.
    if code.contains("openskp_codegen_base64_decode(b64_") {
        assert!(code.contains("fn openskp_codegen_base64_decode(s: &str) -> Vec<u8> {"));
        assert!(code.contains("fn openskp_codegen_write_temp_file("));
    }

    // Every placed definition was closed (turned into a handle) earlier in the file.
    for (pos, _) in code.match_indices("add_instance(def") {
        let var: String = code[pos + "add_instance(".len()..]
            .chars()
            .take_while(|c| c.is_ascii_alphanumeric())
            .collect();
        let closed = code
            .find(&format!("let {var} = {var}.close()?;"))
            .unwrap_or_else(|| panic!("{name}: {var} never closed"));
        assert!(closed < pos, "{name}: {var} placed before it closed");
    }
}

#[test]
fn su_file_generates_plausible_well_formed_code() {
    generates_plausible_well_formed_code("SU_File.skp");
}

#[test]
fn untitled_generates_plausible_well_formed_code() {
    generates_plausible_well_formed_code("Untitled.skp");
}

#[test]
fn nesting_2021_generates_plausible_well_formed_code() {
    generates_plausible_well_formed_code("nesting_2021.skp");
}

#[test]
fn gondola_v20_generates_plausible_well_formed_code() {
    generates_plausible_well_formed_code("gondola_v20.skp");
}

#[test]
fn capilla_quiroz_v17_generates_plausible_well_formed_code() {
    generates_plausible_well_formed_code("capilla_quiroz_v17.skp");
}

/// One parent definition placing 300 distinct leaf definitions: wide, only one level deep.
/// The parent has the lowest id, so codegen first reaches every leaf through it. The
/// nesting-depth limit must not count siblings, so every placement is emitted.
#[test]
fn a_wide_definition_keeps_every_placement() {
    const LEAVES: i64 = 300;
    let mut next_id = 10_000;
    let mut model = SkpModel::default();
    let mut parent = Definition {
        id: 1,
        name: "Parent".into(),
        ..Default::default()
    };
    for id in 2..=LEAVES + 1 {
        let mut leaf = Definition {
            id,
            name: format!("Leaf{id}"),
            ..Default::default()
        };
        add_face(&mut leaf, &mut next_id, &square(1.0), &[]);
        model.definitions.insert(id, leaf);
        parent
            .instances
            .push(placement(id, "", [id as f64, 0.0, 0.0]));
    }
    model.definitions.insert(1, parent);
    model
        .root_mut()
        .instances
        .push(placement(1, "Parent", [0.0, 0.0, 0.0]));

    let code = to_rust_code(&model);

    let placed = code.matches(".add_instance(def").count();
    assert_eq!(placed as i64, LEAVES + 1, "every placement is emitted");
    assert!(
        !code.contains("return Err("),
        "a wide model is not too deep"
    );
}

/// A chain of 300 nested definitions exceeds the component-depth limit. The generated build()
/// fails with an error, as the writer would, rather than silently dropping the deep definitions.
#[test]
fn a_too_deep_definition_chain_makes_the_generated_build_fail() {
    const DEPTH: i64 = 300;
    let mut next_id = 10_000;
    let mut model = SkpModel::default();
    for id in 1..=DEPTH {
        let mut defn = Definition {
            id,
            name: format!("D{id}"),
            ..Default::default()
        };
        add_face(&mut defn, &mut next_id, &square(1.0), &[]);
        if id < DEPTH {
            defn.instances.push(placement(id + 1, "", [0.0, 0.0, 0.0]));
        }
        model.definitions.insert(id, defn);
    }
    model
        .root_mut()
        .instances
        .push(placement(1, "D1", [0.0, 0.0, 0.0]));

    let code = to_rust_code(&model);

    assert!(
        code.contains("return Err(SkpWriteError::new(") && code.contains("nesting depth exceeds"),
        "the generated build() reports the depth error"
    );
}
