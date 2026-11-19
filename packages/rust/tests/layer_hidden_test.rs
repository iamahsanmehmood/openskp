// Layer::hidden - the on/off visibility bit, carried through
// RawParsed::layer_hidden. build_model() is called directly with a synthetic
// RawParsed.

use openskp::internal::{build_model, RawParsed};
use openskp::ParseOptions;

#[test]
fn reports_hidden_layer_as_hidden() {
    let mut parsed = RawParsed {
        layer_order: vec!["Layer0".into(), "Furniture".into()],
        ..Default::default()
    };
    parsed.layer_colors.insert("Layer0".into(), [136, 136, 136]);
    parsed
        .layer_colors
        .insert("Furniture".into(), [200, 50, 50]);
    parsed.layer_hidden.insert("Layer0".into(), false);
    parsed.layer_hidden.insert("Furniture".into(), true);

    let model = build_model(parsed, &ParseOptions::default());

    let layer0 = model
        .layers
        .iter()
        .find(|l| l.name == "Layer0")
        .expect("Layer0");
    let furniture = model
        .layers
        .iter()
        .find(|l| l.name == "Furniture")
        .expect("Furniture");
    assert!(!layer0.hidden);
    assert!(furniture.hidden);
}

#[test]
fn defaults_to_visible_when_missing_from_map() {
    let mut parsed = RawParsed {
        layer_order: vec!["Layer0".into()],
        ..Default::default()
    };
    parsed.layer_colors.insert("Layer0".into(), [136, 136, 136]);

    let model = build_model(parsed, &ParseOptions::default());
    assert_eq!(model.layers.len(), 1);
    assert!(!model.layers[0].hidden);
}

// model.layers must come out in the file's own order, not alphabetical:
// layer_colors is sorted by name, so layer_order is the only place that order
// survives.
#[test]
fn preserves_file_order_not_alphabetical_order() {
    let mut parsed = RawParsed {
        layer_order: vec!["Zebra".into(), "Apple".into(), "Middle".into()],
        ..Default::default()
    };
    parsed.layer_colors.insert("Zebra".into(), [1, 1, 1]);
    parsed.layer_colors.insert("Apple".into(), [2, 2, 2]);
    parsed.layer_colors.insert("Middle".into(), [3, 3, 3]);

    let model = build_model(parsed, &ParseOptions::default());
    let names: Vec<&str> = model.layers.iter().map(|l| l.name.as_str()).collect();
    assert_eq!(names, ["Zebra", "Apple", "Middle"]);
}
