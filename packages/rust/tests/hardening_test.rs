//! Untrusted-input hardening of the reading side: recursion limits, allocation sizes and
//! worst-case scans on crafted inputs. The writer's input checks are in create_hardening_test.rs.

use openskp::internal::bytes::parse_tlv_recursive;

fn make_nested_tlv(levels: usize) -> Vec<u8> {
    let mut data = Vec::with_capacity(levels * 6);
    for i in (1..=levels).rev() {
        data.push(0xF4);
        data.push(0x01);
        let child_len = ((i - 1) * 6) as u32;
        data.extend_from_slice(&child_len.to_le_bytes());
    }
    data
}

#[test]
fn tlv_recursion_boundary_test() {
    // MAX_TLV_DEPTH is 64.
    // In make_nested_tlv: level 1 is at depth 0, level 2 is at depth 1, ..., level 65 is at depth 64.
    // Depth 64 satisfies depth <= MAX_TLV_DEPTH (64), so 65 levels must parse successfully.
    let data_65 = make_nested_tlv(65);
    assert!(
        parse_tlv_recursive(&data_65, 0, data_65.len()).is_ok(),
        "Nesting up to depth 64 (65 levels) must parse successfully"
    );

    // 66 levels attempts to recurse to depth 65 (> MAX_TLV_DEPTH 64), which must be rejected cleanly.
    let data_66 = make_nested_tlv(66);
    let result_66 = parse_tlv_recursive(&data_66, 0, data_66.len());
    assert!(
        result_66.is_err(),
        "Nesting exceeding MAX_TLV_DEPTH (depth 65) must be rejected"
    );
}

#[test]
fn dc05_attribute_dict_deep_nesting_does_not_overflow_stack() {
    use openskp::internal::geometry::extract_attribute_dictionaries;
    use openskp::model::ParsedAttrDictionaries;

    // 5,000 nested DD05 containers (6 bytes each = 30 KB), bypassing parse_tlv_recursive
    // because DC05 is a leaf tag in model.dat.
    let levels = 5_000usize;
    let mut payload = Vec::with_capacity(levels * 6);
    for i in (1..=levels).rev() {
        payload.extend_from_slice(&[0xDD, 0x05]);
        let child_len = ((i - 1) * 6) as u32;
        payload.extend_from_slice(&child_len.to_le_bytes());
    }
    let mut out = ParsedAttrDictionaries::new();
    extract_attribute_dictionaries(&payload, &mut out);
    assert!(out.is_empty());
}

#[test]
fn meta_units_deep_6400_nesting_does_not_overflow_stack() {
    use openskp::internal::vff::read_meta_units;

    let levels = 5_000usize;
    let mut payload = Vec::with_capacity(levels * 6);
    for i in (1..=levels).rev() {
        payload.extend_from_slice(&[0x64, 0x00]);
        let child_len = ((i - 1) * 6) as u32;
        payload.extend_from_slice(&child_len.to_le_bytes());
    }
    assert_eq!(read_meta_units(&payload), None);
}

#[test]
fn xml_unclosed_tags_finish_in_linear_time() {
    use openskp::internal::xml::{find_element, find_open_tag};
    use std::time::Instant;

    let repeated = b"<item attr=\"1\">".repeat(10_000);
    let start = Instant::now();
    assert!(find_element(&repeated, 0, "item").is_none());
    let unclosed_open = b"<item attr=\"1\"".repeat(10_000);
    assert!(find_open_tag(&unclosed_open, 0, "item", true).is_none());
    assert!(
        start.elapsed().as_millis() < 500,
        "unclosed XML tag scan took too long: {:?}",
        start.elapsed()
    );
}

/// `find_screenimage` keeps scanning after an unclosed `<screenimage ...>`, as the regex it
/// mirrors does, so a later self-closing tag still matches. That makes it quadratic on a run of
/// unclosed tags, which this only checks stays bounded for a modest input.
#[test]
fn find_screenimage_matches_a_later_tag_after_an_unclosed_one() {
    use openskp::internal::xml::find_screenimage;
    use std::time::Instant;

    let s = b"<screenimage a=\"1\">no closing tag <screenimage b=\"2\"/>";
    let m = find_screenimage(s, 0).expect("the self-closing tag matches");
    assert_eq!(&s[m.attrs], b" b=\"2\"");
    assert!(m.body.is_none());

    let unclosed_screen = b"<screenimage attr=\"1\">".repeat(2_000);
    let start = Instant::now();
    assert!(find_screenimage(&unclosed_screen, 0).is_none());
    assert!(
        start.elapsed().as_millis() < 2000,
        "unclosed screenimage scan took too long: {:?}",
        start.elapsed()
    );
}

#[test]
fn zip64_overflow_and_directory_allocation_bomb_are_rejected() {
    use openskp::zip::ZipArchive;

    // Craft a minimal single-entry ZIP archive with a ZIP64 extra field in the CDH.
    fn build_zip64_archive(comp_size: u64, uncomp_size: u64, external_attr: u32) -> Vec<u8> {
        let name = b"model.dat";
        let mut buf = Vec::new();
        // Local File Header (30 + 9 = 39 bytes)
        buf.extend_from_slice(&0x04034b50u32.to_le_bytes());
        buf.extend_from_slice(&45u16.to_le_bytes()); // version needed
        buf.extend_from_slice(&0u16.to_le_bytes()); // flags
        buf.extend_from_slice(&8u16.to_le_bytes()); // method = deflate
        buf.extend_from_slice(&0u16.to_le_bytes()); // time
        buf.extend_from_slice(&0u16.to_le_bytes()); // date
        buf.extend_from_slice(&0u32.to_le_bytes()); // crc32
        buf.extend_from_slice(&0xFFFF_FFFFu32.to_le_bytes()); // comp_size (zip64)
        buf.extend_from_slice(&0xFFFF_FFFFu32.to_le_bytes()); // uncomp_size (zip64)
        buf.extend_from_slice(&(name.len() as u16).to_le_bytes());
        buf.extend_from_slice(&0u16.to_le_bytes()); // extra len
        buf.extend_from_slice(name);

        let cd_ofs = buf.len() as u32;
        // ZIP64 extra field: header id 0x0001, size 16, uncomp_size (8B), comp_size (8B)
        let mut extra = Vec::new();
        extra.extend_from_slice(&0x0001u16.to_le_bytes());
        extra.extend_from_slice(&16u16.to_le_bytes());
        extra.extend_from_slice(&uncomp_size.to_le_bytes());
        extra.extend_from_slice(&comp_size.to_le_bytes());

        // Central Directory Header (46 + 9 + 20 = 75 bytes)
        buf.extend_from_slice(&0x02014b50u32.to_le_bytes());
        buf.extend_from_slice(&45u16.to_le_bytes()); // version made by
        buf.extend_from_slice(&45u16.to_le_bytes()); // version needed
        buf.extend_from_slice(&0u16.to_le_bytes()); // flags
        buf.extend_from_slice(&8u16.to_le_bytes()); // method = deflate
        buf.extend_from_slice(&0u16.to_le_bytes()); // time
        buf.extend_from_slice(&0u16.to_le_bytes()); // date
        buf.extend_from_slice(&0u32.to_le_bytes()); // crc32
        buf.extend_from_slice(&0xFFFF_FFFFu32.to_le_bytes()); // comp_size
        buf.extend_from_slice(&0xFFFF_FFFFu32.to_le_bytes()); // uncomp_size
        buf.extend_from_slice(&(name.len() as u16).to_le_bytes());
        buf.extend_from_slice(&(extra.len() as u16).to_le_bytes());
        buf.extend_from_slice(&0u16.to_le_bytes()); // comment len
        buf.extend_from_slice(&0u16.to_le_bytes()); // disk number
        buf.extend_from_slice(&0u16.to_le_bytes()); // internal attr
        buf.extend_from_slice(&external_attr.to_le_bytes()); // external attr
        buf.extend_from_slice(&0u32.to_le_bytes()); // local header offset
        buf.extend_from_slice(name);
        buf.extend_from_slice(&extra);

        let cd_size = (buf.len() as u32) - cd_ofs;
        // End of Central Directory (22 bytes)
        buf.extend_from_slice(&0x06054b50u32.to_le_bytes());
        buf.extend_from_slice(&0u16.to_le_bytes());
        buf.extend_from_slice(&0u16.to_le_bytes());
        buf.extend_from_slice(&1u16.to_le_bytes());
        buf.extend_from_slice(&1u16.to_le_bytes());
        buf.extend_from_slice(&cd_size.to_le_bytes());
        buf.extend_from_slice(&cd_ofs.to_le_bytes());
        buf.extend_from_slice(&0u16.to_le_bytes());
        buf
    }

    // 1. comp_size = u64::MAX (used to overflow data_ofs + st.comp_size and panic)
    let zip_overflow = build_zip64_archive(u64::MAX, 64, 0);
    let archive = ZipArchive::new(&zip_overflow).expect("central dir parses");
    assert!(
        archive.extract(0).is_err(),
        "u64::MAX comp_size must return Err instead of panicking on overflow"
    );

    // 2. Directory flag (0x10) with 8 GB declared uncomp_size (used to allocate vec![0; 8GB])
    let zip_dir_bomb = build_zip64_archive(8 * 1024 * 1024 * 1024, 8 * 1024 * 1024 * 1024, 0x10);
    let archive_bomb = ZipArchive::new(&zip_dir_bomb).expect("central dir parses");
    assert!(
        archive_bomb.extract(0).is_err(),
        "directory entry with non-zero uncomp_size must be rejected without allocating"
    );
}

#[test]
fn deep_component_chain_and_exponential_dag_are_rejected() {
    use openskp::instanced_scene_build::build_instanced_scene_raw;
    use openskp::internal::{RawDefinition, RawInstance, RawParsed};
    use openskp::scene_build::build_scene_raw;
    use openskp::ParseOptions;

    // 1. Deep acyclic component chain: D0 -> D1 -> ... -> D300 (> MAX_COMPONENT_DEPTH 256)
    let mut deep = RawParsed::default();
    for id in 1..=300i64 {
        let mut def = RawDefinition {
            name: format!("Def_{id}"),
            ..Default::default()
        };
        if id < 300 {
            def.builder.instances.push(RawInstance {
                ref_idx: Some(id + 1),
                ..Default::default()
            });
        }
        deep.definitions.insert(id, def);
    }
    deep.root.builder.instances.push(RawInstance {
        ref_idx: Some(1),
        ..Default::default()
    });
    assert!(
        build_scene_raw(deep.clone(), &ParseOptions::default()).is_err(),
        "component chain exceeding MAX_COMPONENT_DEPTH must be rejected in baked scene builder"
    );
    assert!(
        build_instanced_scene_raw(deep, &ParseOptions::default()).is_err(),
        "component chain exceeding MAX_COMPONENT_DEPTH must be rejected in instanced scene builder"
    );

    // 2. Exponential "Billion Laughs" DAG: D1..D30 where each Di places TWO instances of D(i+1)
    // Depth is only 30 (<= 64), but total instances = 2^30 (> 1 billion).
    let mut dag = RawParsed::default();
    for id in 1..=30i64 {
        let mut def = RawDefinition {
            name: format!("Dag_{id}"),
            ..Default::default()
        };
        if id < 30 {
            for _ in 0..2 {
                def.builder.instances.push(RawInstance {
                    ref_idx: Some(id + 1),
                    ..Default::default()
                });
            }
        }
        dag.definitions.insert(id, def);
    }
    dag.root.builder.instances.push(RawInstance {
        ref_idx: Some(1),
        ..Default::default()
    });
    assert!(
        build_scene_raw(dag.clone(), &ParseOptions::default()).is_err(),
        "exponential component DAG must be rejected by MAX_EXPANDED_INSTANCES in baked scene builder"
    );
    assert!(
        build_instanced_scene_raw(dag, &ParseOptions::default()).is_err(),
        "exponential component DAG must be rejected by MAX_EXPANDED_INSTANCES in instanced scene builder"
    );
}

#[test]
fn legacy_mfc_object_recursion_and_attr_bombs_are_rejected() {
    // Run on a thread with 8 MiB stack (matching Linux default) so unoptimized
    // Windows debug builds can traverse down to MAX_OBJECT_DEPTH (256)
    // without exhausting the smaller Windows MSVC stack.
    let handle = std::thread::Builder::new()
        .stack_size(8 * 1024 * 1024)
        .spawn(|| {
            use openskp::legacy::parse_legacy;
            use openskp::ParseOptions;

            // Craft a legacy v18 header + CMaterial class record whose preamble's
            // CAttributeContainer chains 5,000 nested 2-byte class-ref tags (0x8001).
            let mut legacy = Vec::new();
            legacy.extend_from_slice(&[0xff, 0xfe, 0xff, 0x0e]);
            for b in b"SketchUp Model" {
                legacy.push(*b);
                legacy.push(0);
            }
            legacy.extend_from_slice(&[0xff, 0xfe, 0xff, 0x0e]);
            for b in b"{18.0.0}" {
                legacy.push(*b);
                legacy.push(0);
            }
            legacy.resize(96, 0);
            // Material count = 2, followed by CMaterial class declaration (slot 0)
            legacy.extend_from_slice(&2u32.to_le_bytes());
            legacy.extend_from_slice(&0xFFFFu16.to_le_bytes());
            legacy.extend_from_slice(&0u16.to_le_bytes()); // schema
            legacy.extend_from_slice(&9u16.to_le_bytes());
            legacy.extend_from_slice(b"CMaterial");
            // CMaterial::read -> preamble -> object(Some("CAttributeContainer")):
            // First tag 0x8001 registers slot 1 as CAttributeContainer and enters CAttributeContainer::read -> preamble
            for _ in 0..5_000 {
                legacy.extend_from_slice(&0x8001u16.to_le_bytes());
            }
            assert!(
                parse_legacy(&legacy, &ParseOptions::default()).is_err(),
                "5,000 nested legacy MFC object class-refs must be rejected without stack overflow"
            );
        })
        .unwrap();
    handle.join().unwrap();
}

// ---------------------------------------------------------------------------
// Fragments (.frag) FlatBuffers. Every offset in the buffer is untrusted: the
// reader bounds-checks each access (fragments_export_test.rs patches every
// byte of a real buffer), caps nesting at 256 levels (the same as
// MAX_COMPONENT_DEPTH; tested there with a 300-level chain), caps the nodes
// read at 10 million and the inflated payload at 2 GiB (unit-tested in
// fragments/mod.rs with a smaller cap). The test below builds a hostile
// buffer by hand.
// ---------------------------------------------------------------------------

/// A model whose only content is `spatial_structure`.
fn fragments_model(
    build: impl FnOnce(&mut openskp::flatbuffers::Builder) -> openskp::flatbuffers::Offset,
) -> Vec<u8> {
    use openskp::fragments::schema::{create_model, finish_model_buffer, ModelArgs};
    let mut fbb = openskp::flatbuffers::Builder::default();
    let spatial_structure = build(&mut fbb);
    let model = create_model(
        &mut fbb,
        &ModelArgs {
            spatial_structure,
            ..ModelArgs::default()
        },
    );
    finish_model_buffer(&mut fbb, model);
    fbb.into_finished_data()
}

#[test]
fn fragments_spatial_dag_aliasing_is_rejected_in_linear_time() {
    use openskp::flatbuffers::Offset;
    use openskp::fragments::schema::create_spatial_structure;
    use std::time::Instant;

    // 64 levels (within the depth limit), each listing the level below
    // twice: under 2 KB of buffer that would expand to 2^64 nodes.
    let buf = fragments_model(|fbb| {
        let mut node = create_spatial_structure(fbb, None, Offset::NULL, Offset::NULL);
        for _ in 0..64 {
            let children = fbb.create_vector_of_offsets(&[node, node]);
            node = create_spatial_structure(fbb, None, Offset::NULL, children);
        }
        node
    });
    assert!(buf.len() < 2048, "{} bytes", buf.len());
    let start = Instant::now();
    let err = openskp::from_fragments(&buf).unwrap_err();
    assert!(
        err.to_string().contains("more than one parent"),
        "unexpected error: {err}"
    );
    assert!(
        start.elapsed().as_millis() < 500,
        "aliased spatial structure took too long: {:?}",
        start.elapsed()
    );

    // The same shape without aliasing (a real tree) still reads.
    let buf = fragments_model(|fbb| {
        let mut node = create_spatial_structure(fbb, None, Offset::NULL, Offset::NULL);
        for _ in 0..64 {
            let sibling = create_spatial_structure(fbb, None, Offset::NULL, Offset::NULL);
            let children = fbb.create_vector_of_offsets(&[node, sibling]);
            node = create_spatial_structure(fbb, None, Offset::NULL, children);
        }
        node
    });
    let scene = openskp::from_fragments(&buf).unwrap();
    assert_eq!(scene.scene_hierarchy.children.len(), 2);
}
