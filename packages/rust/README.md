# OpenSKP — Rust

**The open-source SketchUp (`.skp`) file parser, writer, and converter — Rust edition.**

A pure Rust port of the [C++ package](https://github.com/iamahsanmehmood/openskp/blob/main/packages/cpp), with no external crates: DEFLATE, ZIP and
FlatBuffers are implemented in-tree.

> [!IMPORTANT]
> This project was built by reverse engineering a proprietary binary format. It is not affiliated with or endorsed by Trimble Inc. or SketchUp.

Not on crates.io yet; depend on it by path or by git:

```toml
[dependencies]
openskp = { git = "https://github.com/iamahsanmehmood/openskp" }
```

Requires **Rust 1.88 or newer** (2021 edition; tested on `1.88` and `stable` in CI).

## Reading

```rust
use openskp::{ParseOptions, SkpFile};

let model = SkpFile::open("building.skp")?.parse(&ParseOptions::default())?;
println!("{} {} definitions", model.version, model.definitions.len());
```

Both containers are read: VFF (SketchUp 2021+) and the classic MFC `CArchive` (SketchUp 4–2020; see [docs/LANGUAGE_PARITY.md](https://github.com/iamahsanmehmood/openskp/blob/main/docs/LANGUAGE_PARITY.md#4-real-sketchup-file-version-support)).

## Writing, editing and code generation

- `openskp::create()` returns an `SkpBuilder` for a new SketchUp 2017 (`{17.0.1}`) classic-format file from scratch.
- `openskp::open_existing()` loads a classic-format file to extend it.
- `openskp::to_rust_code()` turns a parsed model into Rust source that rebuilds it.

## Converting

`to_glb` / `export_glb`, `to_instanced_glb`, `to_obj` + `to_mtl`, `to_stl_ascii` / `to_stl_binary`,
`to_ply_ascii` / `to_ply_binary`, `to_dxf`, `to_ifc`, `to_json`, and `to_fragments`. `read_fragments`
reads a `.frag` file back.

STL contains only the front winding of each SketchUp face, including faces painted differently on
each side; STL does not carry colour. GLB and the colour-capable OBJ, PLY, DXF, IFC and Fragments
exports retain separately painted sides.

When `GlbOptions::new().with_textures(true)` (or `InstancedGlbOptions::new().with_textures(true)`)
is used, colorized PNG textures are baked in-tree on top of `inflate` / `deflate` with no external
crates (matching SketchUp's shift and tint modes within 1/255). To also bake colorized JPEG
textures, pass a synchronous [`ImageCodec`](https://github.com/iamahsanmehmood/openskp/blob/main/packages/rust/src/glb_textures.rs) via `.with_image_codec(...)` (struct or `(decode, encode)`
closure pair).

## Viewer

`viewer/` is a small in-browser `.skp` viewer (Rust → `wasm32`, WebGL2) built into a single HTML file:

```sh
rustup target add wasm32-unknown-unknown
./viewer/build.sh   # or viewer\build.ps1 on Windows
```

## Tests

```sh
cargo test --release
```

Fixtures live in `tests/fixtures`. `tests/fixtures/create_reference` holds files written by the C++
writer (from `oracle_driver.cpp`); the Rust writer must reproduce them byte for byte.

## License

MIT — see [LICENSE](https://github.com/iamahsanmehmood/openskp/blob/main/LICENSE).
