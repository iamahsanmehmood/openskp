// Face::hidden / Instance::hidden carried through build_model() from a
// synthetic RawParsed.

use openskp::internal::{build_model, RawFace, RawInstance, RawParsed};
use openskp::ParseOptions;

#[test]
fn reports_hidden_face_and_instance_as_hidden() {
    let mut parsed = RawParsed::default();
    parsed.root.builder.faces.insert(
        1,
        RawFace {
            hidden: true,
            ..Default::default()
        },
    );
    parsed.root.builder.faces.insert(
        2,
        RawFace {
            hidden: false,
            ..Default::default()
        },
    );
    parsed.root.builder.instances.push(RawInstance {
        name: "hidden_one".into(),
        hidden: true,
        ..Default::default()
    });
    parsed.root.builder.instances.push(RawInstance {
        name: "visible_one".into(),
        hidden: false,
        ..Default::default()
    });

    let model = build_model(parsed, &ParseOptions::default());

    assert!(model.root().faces[&1].hidden);
    assert!(!model.root().faces[&2].hidden);
    let hidden = model
        .root()
        .instances
        .iter()
        .find(|i| i.name == "hidden_one")
        .expect("hidden_one");
    let visible = model
        .root()
        .instances
        .iter()
        .find(|i| i.name == "visible_one")
        .expect("visible_one");
    assert!(hidden.hidden);
    assert!(!visible.hidden);
}

#[test]
fn defaults_to_visible() {
    let mut parsed = RawParsed::default();
    parsed.root.builder.faces.insert(1, RawFace::default());
    parsed.root.builder.instances.push(RawInstance {
        name: "n".into(),
        ..Default::default()
    });

    let model = build_model(parsed, &ParseOptions::default());

    assert!(!model.root().faces[&1].hidden);
    assert!(!model.root().instances[0].hidden);
}
