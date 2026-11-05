# OpenSKP — Rust

**The open-source SketchUp (`.skp`) file parser, writer, and converter — Rust edition.**

A pure Rust port of the [C++ package](../cpp), with no external crates: DEFLATE and ZIP are
implemented in-tree.

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

## Tests

```sh
cargo test --release
```

Fixtures live in `tests/fixtures`.

## License

MIT — see [LICENSE](LICENSE).
