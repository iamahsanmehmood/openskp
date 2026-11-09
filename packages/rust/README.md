# OpenSKP — Rust

**The open-source SketchUp (`.skp`) file parser, writer, and converter — Rust edition.**

A pure Rust port of the [C++ package](../cpp), with no external crates: DEFLATE, ZIP and
FlatBuffers are implemented in-tree.

> [!IMPORTANT]
> This project was built by reverse engineering a proprietary binary format. It is not affiliated with or endorsed by Trimble Inc. or SketchUp.

Not on crates.io yet; depend on it by path or by git:

```toml
[dependencies]
openskp = { git = "https://github.com/iamahsanmehmood/openskp" }
```

## Reading

```rust
use openskp::{ParseOptions, SkpFile};

let model = SkpFile::open("building.skp")?.parse(&ParseOptions::default())?;
println!("{} {} definitions", model.version, model.definitions.len());
```

Both containers are read: VFF (SketchUp 2021+) and the classic MFC `CArchive` (2013–2020).

## Writing, editing and code generation

- `openskp::create()` returns an `SkpBuilder` for a new file from scratch.
- `openskp::open_existing()` loads a classic-format file to extend it.
- `openskp::to_rust_code()` turns a parsed model into Rust source that rebuilds it.

## Converting

`to_glb` / `export_glb`, `to_instanced_glb`, `to_obj` + `to_mtl`, `to_stl_ascii` / `to_stl_binary`,
`to_ply_ascii` / `to_ply_binary`, `to_dxf`, `to_ifc`, `to_json`, and `to_fragments`. `read_fragments`
reads a `.frag` file back.

## Tests

```sh
cargo test --release
```

Fixtures live in `tests/fixtures`. `tests/fixtures/create_reference` holds files written by the C++
writer (from `oracle_driver.cpp`); the Rust writer must reproduce them byte for byte.

## License

MIT — see [LICENSE](LICENSE).
