# OpenSKP + three.js

The smallest way to show a SketchUp model in a three.js page. You pick a `.skp` file, [OpenSKP](../../packages/typescript) converts it to GLB in the browser, and three.js loads the GLB with `GLTFLoader` and lets you orbit around it with `OrbitControls`. Nothing is uploaded and nothing is built: OpenSKP and three.js both load from a CDN.

## Run it

The page uses ES modules, so open it through a local web server, not as a `file://` URL. From the repository root:

```bash
npx serve examples/threejs
```

or, with Python:

```bash
python -m http.server 8000 --directory examples/threejs
```

Then open the URL it prints, click **Choose File** and pick a `.skp` file. To try it, use [`examples/web-viewer/samples/chair_and_table.skp`](../web-viewer/samples/chair_and_table.skp), a table and a chair made of nested components with four materials.

## How it works

The conversion is three calls, in [`main.js`](main.js):

```js
import { buildScene, toGLB } from 'openskp';

const skp = await file.arrayBuffer();
const glb = toGLB(buildScene(skp), { textures: true });
const gltf = await new GLTFLoader().parseAsync(glb.buffer, '');
scene.add(gltf.scene);
```

- `buildScene()` parses the file and places every component instance in world space as triangulated meshes.
- `toGLB()` writes those meshes as a binary glTF. `{ textures: true }` embeds texture images; leave it out if you only need geometry and colours.
- The GLB is already Y-up and in metres, so three.js needs no rotation or scaling.

One detail: OpenSKP stores material colours as the file's sRGB values, while `GLTFLoader` reads glTF colour factors as linear values. Without a fix, every colour looks washed out, so `main.js` converts each material's colour with `color.convertSRGBToLinear()` after loading.

For layers (tags), picking, measurements and export to other formats, see the full [web viewer](../web-viewer).
