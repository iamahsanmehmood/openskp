# OpenSKP — TypeScript Package

> 🚧 **Under active development** — The Python package is fully functional today. The TypeScript implementation is coming soon.

## Planned Features

- Parse SketchUp 2021+ (VFF format) files in browser and Node.js
- Zero native dependencies (uses `fflate` for ZIP, `earcut` for triangulation)
- Export to GLB binary format
- Full type definitions for all SketchUp entities

## Installation (Coming Soon)

```bash
npm install openskp
```

## Usage (Coming Soon)

```typescript
import { parseSkp, toGLB } from 'openskp';

// Browser: parse from file input
const file = document.querySelector('input').files[0];
const buffer = await file.arrayBuffer();
const model = parseSkp(buffer);

// Access data
console.log(model.layers);
console.log(model.definitions);

// Export to GLB
const glb = toGLB(model);
```

## Contributing

We welcome contributions to the TypeScript implementation! The Python package in `../python/` serves as the reference implementation. See [CONTRIBUTING.md](../../CONTRIBUTING.md) for guidelines.

## License

MIT — see [LICENSE](../../LICENSE)
