// material_xml()/style_xml() extract attributes without a real XML parser, so
// entities are decoded explicitly. Without decoding, a name like SketchUp's
// own "<auto>" default-material convention - which the raw XML MUST spell as
// "&lt;auto&gt;" - came through still escaped.

use openskp::internal::decode_xml_entities;

#[test]
fn decodes_lt_gt() {
    assert_eq!(decode_xml_entities("&lt;auto&gt;"), "<auto>");
}

#[test]
fn decodes_amp_apostrophe_quote() {
    assert_eq!(
        decode_xml_entities("Tom &amp; Jerry&apos;s &quot;Wood&quot;"),
        "Tom & Jerry's \"Wood\""
    );
}

#[test]
fn decodes_numeric_character_references_decimal_and_hex() {
    assert_eq!(decode_xml_entities("&#65;&#x42;"), "AB");
    assert_eq!(
        decode_xml_entities("&#xE9;&#8364;&#x1F600;"),
        "\u{e9}\u{20ac}\u{1F600}"
    );
}

// A single left-to-right pass must consume "&amp;" as one match, leaving the
// trailing "lt;" as literal text - never re-scanning its own output.
#[test]
fn does_not_double_decode_amp_lt() {
    assert_eq!(decode_xml_entities("&amp;lt;"), "&lt;");
}

#[test]
fn leaves_plain_text_unchanged() {
    assert_eq!(decode_xml_entities("Plain Wood Oak"), "Plain Wood Oak");
}

// Not a real entity (no trailing ";", unknown name) - passed through rather
// than guessed at.
#[test]
fn leaves_malformed_entity_like_text_as_is() {
    assert_eq!(decode_xml_entities("Fish & Chips"), "Fish & Chips");
    assert_eq!(decode_xml_entities("&nbsp;"), "&nbsp;");
    assert_eq!(decode_xml_entities("&#;&#x;&#12"), "&#;&#x;&#12");
    assert_eq!(
        decode_xml_entities("&#99999999999999999999999;"),
        "&#99999999999999999999999;"
    );
}
