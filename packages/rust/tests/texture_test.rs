mod common;

use std::collections::BTreeMap;

use common::{build_zip, bytes, wrap_vff, ZipEntry};
use openskp::{parse_skp, Material, ParseOptions, SkpModel};

/// A VFF buffer: 32-byte header, then a stored ZIP with an empty model.dat and
/// `entries` in name order (as the C++ test's std::map iteration adds them).
fn synthetic_skp(entries: &[(&str, Vec<u8>)]) -> Vec<u8> {
    let sorted: BTreeMap<&str, &Vec<u8>> = entries.iter().map(|(n, d)| (*n, d)).collect();
    let mut zip_entries = vec![ZipEntry {
        name: "model.dat",
        data: &[],
        level: 0,
    }];
    for (name, data) in &sorted {
        zip_entries.push(ZipEntry {
            name,
            data,
            level: 0,
        });
    }
    wrap_vff(&build_zip(&zip_entries), 32)
}

fn textured_xml(name: &str, filename: &str) -> Vec<u8> {
    bytes(&format!(
        "<?xml version=\"1.0\"?><materialDocument \
         xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">\
         <mat:material name=\"{name}\" type=\"1\" colorRed=\"10\" colorGreen=\"20\" \
         colorBlue=\"30\" trans=\"1\" hasTexture=\"1\">\
         <mat:texture textureFilename=\"{filename}\" xScale=\"24\" yScale=\"12\"/>\
         </mat:material></materialDocument>"
    ))
}

fn material<'a>(model: &'a SkpModel, name: &str) -> &'a Material {
    model
        .materials
        .iter()
        .find(|m| m.name == name)
        .expect("test material not found")
}

fn parse(buf: &[u8]) -> SkpModel {
    parse_skp(buf, &ParseOptions::default()).unwrap()
}

#[test]
fn extracts_image_filename_and_tile_size() {
    let jpeg = vec![
        0xff, 0xd8, b's', b'y', b'n', b't', b'h', b'e', b't', b'i', b'c',
    ];
    let plain = bytes(
        "<materialDocument xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">\
         <mat:material name=\"Plain\" type=\"0\" colorRed=\"1\" colorGreen=\"2\" \
         colorBlue=\"3\"/></materialDocument>",
    );
    let model = parse(&synthetic_skp(&[
        (
            "materials/Wood/material.xml",
            textured_xml("Wood", "wood.jpg"),
        ),
        ("materials/Wood/wood.jpg", jpeg.clone()),
        ("materials/Plain/material.xml", plain),
    ]));

    let wood = material(&model, "Wood");
    let t = wood.texture.as_ref().unwrap();
    assert_eq!(t.filename, "wood.jpg");
    assert_eq!(t.width, 24.0);
    assert_eq!(t.height, 12.0);
    assert_eq!(t.data.as_ref(), Some(&jpeg));
    assert!(material(&model, "Plain").texture.is_none());
    assert_eq!(material(&model, "Plain").color, [1, 2, 3, 255]);
}

#[test]
fn falls_back_to_folder_sibling_for_mismatched_filename() {
    let jpeg = vec![0xff, 0xd8, b's', b'i', b'b', b'l', b'i', b'n', b'g'];
    let model = parse(&synthetic_skp(&[
        (
            "materials/Glass/material.xml",
            textured_xml("Glass", "glass_safety.jpg"),
        ),
        ("materials/Glass/glass_saftey.jpg", jpeg.clone()),
    ]));
    let glass = material(&model, "Glass");
    assert_eq!(glass.texture.as_ref().unwrap().data.as_ref(), Some(&jpeg));
}

#[test]
fn resolves_colorized_copy_sharing_source_image() {
    let png = vec![0x89, b'P', b'N', b'G', b's', b'h', b'a', b'r', b'e', b'd'];
    let colorized = bytes(
        "<materialDocument xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">\
         <mat:material name=\"[Fence]1\" type=\"2\" colorRed=\"27\" colorGreen=\"135\" \
         colorBlue=\"59\" colorizeType=\"0\">\
         <mat:texture textureFilename=\"fence.png\" xScale=\"2.75\" yScale=\"2.75\">\
         <mat:images><mat:image path=\"materials/Fence/fence.png\"/></mat:images>\
         </mat:texture></mat:material></materialDocument>",
    );
    let model = parse(&synthetic_skp(&[
        (
            "materials/Fence/material.xml",
            textured_xml("Fence", "fence.png"),
        ),
        ("materials/Fence/fence.png", png.clone()),
        ("materials/[Fence]1/material.xml", colorized),
    ]));
    let copy = material(&model, "[Fence]1");
    assert_eq!(copy.texture.as_ref().unwrap().data.as_ref(), Some(&png));
    assert!(copy.colorized);
    assert_eq!(copy.colorize_type, 0);
    assert_eq!(copy.color, [27, 135, 59, 255]);
    assert!(!material(&model, "Fence").colorized);
}

#[test]
fn material_applies_transparency_only_when_enabled() {
    let enabled = bytes(
        "<materialDocument xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">\
         <mat:material name=\"Enabled\" trans=\"0.27\" useTrans=\"1\"/></materialDocument>",
    );
    let disabled = bytes(
        "<materialDocument xmlns:mat=\"http://sketchup.google.com/schemas/sketchup/1.0/material\">\
         <mat:material name=\"Disabled\" trans=\"0.27\" useTrans=\"0\"/></materialDocument>",
    );
    let model = parse(&synthetic_skp(&[
        ("materials/Enabled/material.xml", enabled),
        ("materials/Disabled/material.xml", disabled),
    ]));
    common::assert_near(material(&model, "Enabled").transparency, 0.73, 1e-9);
    assert_eq!(material(&model, "Disabled").transparency, 1.0);
}

// Entity-encoded names decode (SketchUp's own "<auto>" default material).
#[test]
fn material_names_are_entity_decoded() {
    let xml = bytes(
        "<materialDocument><mat:material name=\"&lt;auto&gt; &amp; co\" colorRed=\"300\"/></materialDocument>",
    );
    let model = parse(&synthetic_skp(&[("materials/auto/material.xml", xml)]));
    let m = material(&model, "<auto> & co");
    assert_eq!(m.color[0], 300u32 as u8);
}
