//! Verifies that crate-private implementation modules are not leaked into the
//! public root namespace (`openskp::*`), even under `#[doc(hidden)]`.
//!
//! If any of the seven internal modules (`curves`, `earcut`, `face_groups`,
//! `instanced_scene_build`, `internal`, `legacy`, `scene_build`) were still
//! `pub mod` in `lib.rs`, `combined::<name>` below would fail to compile with
//! `E0659` (`ambiguous` between the two glob imports).

mod local_names {
    pub struct Marker;

    pub mod curves {
        pub const MARKER: super::Marker = super::Marker;
    }
    pub mod earcut {
        pub const MARKER: super::Marker = super::Marker;
    }
    pub mod face_groups {
        pub const MARKER: super::Marker = super::Marker;
    }
    pub mod instanced_scene_build {
        pub const MARKER: super::Marker = super::Marker;
    }
    pub mod internal {
        pub const MARKER: super::Marker = super::Marker;
    }
    pub mod legacy {
        pub const MARKER: super::Marker = super::Marker;
    }
    pub mod scene_build {
        pub const MARKER: super::Marker = super::Marker;
    }
}

mod combined {
    pub use super::local_names::*;
    pub use openskp::*;
}

#[test]
fn internal_modules_are_not_part_of_the_public_crate_root() {
    let _: Option<combined::SkpModel> = None;
    let markers: [local_names::Marker; 7] = [
        combined::curves::MARKER,
        combined::earcut::MARKER,
        combined::face_groups::MARKER,
        combined::instanced_scene_build::MARKER,
        combined::internal::MARKER,
        combined::legacy::MARKER,
        combined::scene_build::MARKER,
    ];
    assert_eq!(markers.len(), 7);
}
