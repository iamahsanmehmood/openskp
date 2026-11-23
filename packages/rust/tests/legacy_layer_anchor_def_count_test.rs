use openskp::create::{create, DefinitionOptions, FaceOptions, InstanceOptions, LayerOptions};
use openskp::{ParseOptions, SkpFile};

// Regression (#534 / #285): after the last CLayer the reader sees
// `<anchor u16><def_count u32>` and then the first CComponentDefinition.
// skip_clayer_parent_ref consumed the anchor as a v18 "parent" back-ref
// whenever both u16s at that position pointed at CLayer objects - which also
// happens when the definition count (11, 12, or 13) coincides with a tag's
// slot.

fn build(n_tags: usize, n_defs: usize) -> Vec<u8> {
    let mut builder = create();
    let mat = builder
        .add_material("M", [200, 100, 50, 255], None)
        .unwrap();
    let mut layers = Vec::with_capacity(n_tags);
    for i in 0..n_tags {
        layers.push(
            builder
                .add_layer(&format!("T{i}"), &LayerOptions::default())
                .unwrap(),
        );
    }
    let mut defs = Vec::with_capacity(n_defs);
    for i in 0..n_defs {
        let mut comp = builder
            .add_component_definition(&format!("P{i}"), &DefinitionOptions::default())
            .unwrap();
        let x = 1.0 + i as f64;
        comp.add_face(
            &[
                [0.0, 0.0, 0.0],
                [x, 0.0, 0.0],
                [x, 1.0, 0.0],
                [0.0, 1.0, 0.0],
            ],
            &FaceOptions {
                material: Some(mat),
                ..FaceOptions::default()
            },
        )
        .unwrap();
        defs.push(comp.close().unwrap());
    }
    let container = {
        let mut c = builder
            .add_component_definition("Container", &DefinitionOptions::default())
            .unwrap();
        for (i, &comp) in defs.iter().enumerate() {
            c.add_instance(
                comp,
                &InstanceOptions {
                    name: Some(format!("i{i}")),
                    translation: [0.0, 2.0 * i as f64, 0.0],
                    layer: Some(layers[0]),
                    ..InstanceOptions::default()
                },
            )
            .unwrap();
        }
        c.close().unwrap()
    };
    builder
        .add_instance(
            container,
            &InstanceOptions {
                name: Some("root".to_owned()),
                layer: Some(layers[0]),
                ..InstanceOptions::default()
            },
        )
        .unwrap();
    builder.to_bytes().unwrap()
}

#[test]
fn definition_count_matching_a_tag_slot_round_trips() {
    for n_tags in [1, 2] {
        for n_defs in [10, 11, 12, 13, 14] {
            let model = SkpFile::from_buffer(build(n_tags, n_defs))
                .parse(&ParseOptions::default())
                .unwrap_or_else(|e| panic!("n_tags={n_tags} n_defs={n_defs}: {e}"));
            let mut expected_layers = vec!["Layer0".to_owned()];
            for i in 0..n_tags {
                expected_layers.push(format!("T{i}"));
            }
            let actual_layers: Vec<String> = model.layers.iter().map(|l| l.name.clone()).collect();
            assert_eq!(actual_layers, expected_layers);
            assert_eq!(model.definitions.len(), n_defs + 1);
        }
    }
}
