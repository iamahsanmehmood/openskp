// InstancedNode::hidden - the instance's own "Hide" flag, which the parsed
// instance already carries (and the baked path's Instance::hidden reports),
// so a consumer of the instanced scene can skip hidden placements
// (openskp#527, ported from TypeScript's `instanced-hidden.test.ts`).

use openskp::create::{create, DefinitionOptions, FaceOptions, InstanceOptions, Point3};
use openskp::{build_instanced_scene, from_fragments, to_fragments, InstancedNode, ParseOptions};

fn square() -> Vec<Point3> {
    vec![
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [10.0, 10.0, 0.0],
        [0.0, 10.0, 0.0],
    ]
}

fn named(name: &str, hidden: bool, x: f64) -> InstanceOptions {
    InstanceOptions {
        name: Some(name.to_owned()),
        hidden,
        translation: [x, 0.0, 0.0],
        ..Default::default()
    }
}

fn names_and_hidden(nodes: &[InstancedNode]) -> Vec<(String, Option<bool>)> {
    nodes.iter().map(|n| (n.name.clone(), n.hidden)).collect()
}

fn hidden_fixture() -> Vec<u8> {
    let mut builder = create();
    let opts = DefinitionOptions::default();
    let mut def = builder.add_component_definition("Box", &opts).unwrap();
    def.add_face(&square(), &FaceOptions::default()).unwrap();
    let boxed = def.close().unwrap();
    let mut def = builder.add_component_definition("Inner", &opts).unwrap();
    def.add_face(&square(), &FaceOptions::default()).unwrap();
    let inner = def.close().unwrap();
    let mut def = builder.add_component_definition("Outer", &opts).unwrap();
    def.add_instance(inner, &named("NestedVisible", false, 0.0))
        .unwrap();
    def.add_instance(inner, &named("NestedHidden", true, 20.0))
        .unwrap();
    let outer = def.close().unwrap();
    builder
        .add_instance(boxed, &named("Visible", false, 0.0))
        .unwrap();
    builder
        .add_instance(boxed, &named("Hidden", true, 20.0))
        .unwrap();
    builder
        .add_instance(outer, &named("OuterHidden", true, 40.0))
        .unwrap();
    builder.to_bytes().unwrap()
}

#[test]
fn reports_each_placements_own_hide_flag_nested_ones_included() {
    let scene = build_instanced_scene(&hidden_fixture(), &ParseOptions::default()).unwrap();
    let root = &scene.scene_hierarchy;

    assert_eq!(root.hidden, Some(false));
    assert_eq!(
        names_and_hidden(&root.children),
        vec![
            ("Visible".to_owned(), Some(false)),
            ("Hidden".to_owned(), Some(true)),
            ("OuterHidden".to_owned(), Some(true)),
        ]
    );
    assert_eq!(
        names_and_hidden(&root.children[2].children),
        vec![
            ("NestedVisible".to_owned(), Some(false)),
            ("NestedHidden".to_owned(), Some(true)),
        ]
    );
}

#[test]
fn a_scene_read_back_from_fragments_carries_no_hide_flag() {
    let scene = build_instanced_scene(&hidden_fixture(), &ParseOptions::default()).unwrap();
    let back = from_fragments(&to_fragments(&scene, true)).unwrap();

    fn assert_unknown(node: &InstancedNode) {
        assert_eq!(node.hidden, None, "{}", node.name);
        node.children.iter().for_each(assert_unknown);
    }
    assert_unknown(&back.scene_hierarchy);
}
