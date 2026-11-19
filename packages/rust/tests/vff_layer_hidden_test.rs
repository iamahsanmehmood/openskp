// The VFF counterpart of the legacy layer-hidden flag: each layer's 8C3C node
// (under a 993A layer-manager list) carries a single-byte 8E3C child next to
// DC05 (id) and 8D3C (name) - 1 = hidden, 0 = visible. collect_layers() is
// called directly with a hand-built TlvNode tree.

use std::collections::BTreeMap;

use openskp::internal::{collect_layers, TlvNode};

fn leaf(tag: &str, payload: &[u8]) -> TlvNode {
    TlvNode::leaf(tag, payload.to_vec())
}

fn run(roots: &[TlvNode]) -> (BTreeMap<i64, String>, BTreeMap<String, bool>) {
    let mut id_to_name = BTreeMap::new();
    let mut hidden = BTreeMap::new();
    collect_layers(roots, &mut id_to_name, &mut hidden).unwrap();
    (id_to_name, hidden)
}

#[test]
fn hidden_and_visible_layers_read_correctly() {
    let hidden_layer = TlvNode::container(
        "8C3C",
        vec![
            leaf("DC05", &[5]),
            leaf("8D3C", b"wall_external_cladding_1"),
            leaf("8E3C", &[1]),
        ],
    );
    let visible_layer = TlvNode::container(
        "8C3C",
        vec![
            leaf("DC05", &[6]),
            leaf("8D3C", b"wall"),
            leaf("8E3C", &[0]),
        ],
    );
    let manager = TlvNode::container("993A", vec![hidden_layer, visible_layer]);

    let (id_to_name, hidden) = run(&[manager]);
    assert_eq!(hidden.get("wall_external_cladding_1"), Some(&true));
    assert_eq!(hidden.get("wall"), Some(&false));
    assert_eq!(
        id_to_name.get(&5).map(String::as_str),
        Some("wall_external_cladding_1")
    );
}

// The hidden-flag branch is gated on the name (8D3C) node being present.
#[test]
fn layer_with_no_name_gets_no_hidden_entry() {
    let nameless = TlvNode::container("8C3C", vec![leaf("DC05", &[7]), leaf("8E3C", &[1])]);
    let (_, hidden) = run(&[TlvNode::container("993A", vec![nameless])]);
    assert!(hidden.is_empty());
}

// A layer with no 8E3C at all must not add a false entry - the caller's
// pre-seeded "assume visible" default stays authoritative.
#[test]
fn layer_with_no_8e3c_tag_leaves_hidden_map_untouched() {
    let no_flag = TlvNode::container(
        "8C3C",
        vec![leaf("DC05", &[8]), leaf("8D3C", b"no_flag_layer")],
    );
    let (_, hidden) = run(&[TlvNode::container("993A", vec![no_flag])]);
    assert!(hidden.is_empty());
}
