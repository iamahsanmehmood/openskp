import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import {
  parseSkp,
  buildScene,
  toGLB,
  toOBJ,
  toSTLAscii,
  toSTLBinary,
  toPLYAscii,
  toPLYBinary,
  toDXF,
  toIFC,
  toJSON,
} from './dist/index.mjs';

// Application state variables
let scene, camera, renderer, controls;
let modelGroup;
let raycaster, mouse;
let selectedMesh = null;
let selectedBoxHelper = null;
let currentModel = null;
let currentScene = null;
let currentLoadedFilename = '';
let layerVisibility = {};

// Three.js Texture objects decoded from currentScene.textures, keyed by index
// into that array - built lazily so a texture shared by several materials is
// only decoded once. Reset (and its object URLs revoked) on every new load.
let threeTextureCache = new Map();
let textureObjectUrls = [];
const textureLoader = new THREE.TextureLoader();

// DOM Elements
const canvasContainer = document.getElementById('canvas-container');
const dropOverlay = document.getElementById('drop-overlay');
const loadingOverlay = document.getElementById('loading-overlay');
const loadingText = document.getElementById('loading-text');
const fileInput = document.getElementById('file-input');
const btnLoad = document.getElementById('btn-load');
const btnExport = document.getElementById('btn-export');
const statusText = document.getElementById('status-text');
const sizeWarningOverlay = document.getElementById('size-warning-overlay');
const sizeWarningMessage = document.getElementById('size-warning-message');
const btnSizeCancel = document.getElementById('btn-size-cancel');
const btnSizeProceed = document.getElementById('btn-size-proceed');

// This viewer runs entirely in one browser tab, which has a fixed JS heap
// ceiling (commonly ~4GB) that can't be raised from a web page the way
// Node's --max-old-space-size can. Verified directly: an 18.5MB file
// (1,264 definitions) loads fine in ~20s; a 113MB file (132,879
// definitions) - the same file confirmed to need 8-16GB of Node heap -
// hangs this tab outright rather than throwing a catchable error. These
// thresholds are deliberately conservative given that failure mode is
// unrecoverable once it starts.
const SOFT_WARNING_BYTES = 20 * 1024 * 1024;
const HARD_WARNING_BYTES = 50 * 1024 * 1024;
let pendingFile = null;

// Layers Panel
const layersListPlaceholder = document.getElementById('layers-list-placeholder');
const layersList = document.getElementById('layers-list');
const layerCountBadge = document.getElementById('layer-count');
const panelLayers = document.getElementById('panel-layers');
const panelInspector = document.getElementById('panel-inspector');
const mtoggleLayers = document.getElementById('mtoggle-layers');
const mtoggleInspector = document.getElementById('mtoggle-inspector');
const mtoggleLayerCount = document.getElementById('mtoggle-layer-count');
const btnCloseLayers = document.getElementById('btn-close-layers');
const btnCloseInspector = document.getElementById('btn-close-inspector');

// Inspector Panel
const inspectorEmptyState = document.getElementById('inspector-empty-state');
const inspectorDetails = document.getElementById('inspector-details');
const propName = document.getElementById('prop-name');
const propDefinition = document.getElementById('prop-definition');
const propLayer = document.getElementById('prop-layer');
const propX = document.getElementById('prop-x');
const propY = document.getElementById('prop-y');
const propZ = document.getElementById('prop-z');
const customPropertiesTable = document.getElementById('custom-properties-table');
const sectionCustomProperties = document.getElementById('section-custom-properties');

// Stats
const modelStats = document.getElementById('model-stats');
const statVersion = document.getElementById('stat-version');
const statMeshes = document.getElementById('stat-meshes');

// Initialize the 3D viewport
function initViewport() {
  // Scene
  scene = new THREE.Scene();
  scene.background = new THREE.Color(0x08090b);
  scene.fog = new THREE.FogExp2(0x08090b, 0.015);

  // Camera
  camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.1, 1000);
  camera.position.set(15, 10, 15);

  // Renderer
  renderer = new THREE.WebGLRenderer({ antialias: true });
  renderer.setPixelRatio(window.devicePixelRatio);
  renderer.setSize(window.innerWidth, window.innerHeight);
  renderer.shadowMap.enabled = true;
  renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 1.0;
  canvasContainer.appendChild(renderer.domElement);
  // Required for OrbitControls' own touch gesture handling (rotate/pan/
  // pinch-zoom) to receive events at all - without this, mobile browsers
  // intercept single/multi-finger drags for page scroll/zoom first.
  renderer.domElement.style.touchAction = 'none';

  // Controls
  controls = new OrbitControls(camera, renderer.domElement);
  controls.enableDamping = true;
  controls.dampingFactor = 0.05;
  controls.maxPolarAngle = Math.PI / 2 + 0.1; // allow looking slightly below ground

  // Lighting
  const ambientLight = new THREE.AmbientLight(0xffffff, 0.6);
  scene.add(ambientLight);

  const mainLight = new THREE.DirectionalLight(0xffffff, 0.8);
  mainLight.position.set(20, 40, 20);
  mainLight.castShadow = true;
  mainLight.shadow.mapSize.width = 2048;
  mainLight.shadow.mapSize.height = 2048;
  mainLight.shadow.bias = -0.0001;
  scene.add(mainLight);

  const fillLight = new THREE.DirectionalLight(0x90b0ff, 0.4);
  fillLight.position.set(-20, 20, -20);
  scene.add(fillLight);

  // Helpers (Grid & Axes)
  const gridHelper = new THREE.GridHelper(50, 50, 0x3a3d46, 0x1b1c21);
  gridHelper.position.y = -0.01; // slightly lower than ground
  scene.add(gridHelper);

  const axesHelper = new THREE.AxesHelper(5);
  scene.add(axesHelper);

  // Model Group Container
  modelGroup = new THREE.Group();
  scene.add(modelGroup);

  // Raycaster & Interaction
  raycaster = new THREE.Raycaster();
  mouse = new THREE.Vector2();

  // Resize Handler
  window.addEventListener('resize', onWindowResize);

  // Viewport Click Handler
  renderer.domElement.addEventListener('pointerdown', onPointerDown);

  // Start animation loop
  animate();
}

function animate() {
  requestAnimationFrame(animate);
  controls.update();
  renderer.render(scene, camera);
}

function onWindowResize() {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
}

// Raycasting / Selection logic
function onPointerDown(event) {
  // Prevent raycast on drag/pan
  const startX = event.clientX;
  const startY = event.clientY;

  const onPointerUp = (upEvent) => {
    renderer.domElement.removeEventListener('pointerup', onPointerUp);
    
    const deltaX = Math.abs(upEvent.clientX - startX);
    const deltaY = Math.abs(upEvent.clientY - startY);

    if (deltaX < 3 && deltaY < 3) {
      // It's a clean click
      mouse.x = (upEvent.clientX / window.innerWidth) * 2 - 1;
      mouse.y = -(upEvent.clientY / window.innerHeight) * 2 + 1;

      raycaster.setFromCamera(mouse, camera);
      const intersects = raycaster.intersectObjects(modelGroup.children, true);

      if (intersects.length > 0) {
        // Find first visible SKP mesh intersection
        let match = null;
        for (let hit of intersects) {
          if (hit.object.userData && hit.object.userData.isSkpMesh && hit.object.visible) {
            match = hit.object;
            break;
          }
        }

        if (match) {
          selectMesh(match);
        } else {
          clearSelection();
        }
      } else {
        clearSelection();
      }
    }
  };

  renderer.domElement.addEventListener('pointerup', onPointerUp);
}

function selectMesh(mesh) {
  selectedMesh = mesh;

  // Add Box Highlight Helper
  if (selectedBoxHelper) {
    scene.remove(selectedBoxHelper);
  }
  selectedBoxHelper = new THREE.BoxHelper(mesh, 0xe8a33d);
  selectedBoxHelper.material.depthTest = false;
  selectedBoxHelper.material.transparent = true;
  selectedBoxHelper.material.opacity = 0.8;
  scene.add(selectedBoxHelper);

  // Update Inspector UI
  const data = mesh.userData;
  propName.textContent = data.name || 'Unnamed Component';
  propDefinition.textContent = data.definitionName || 'ROOT_MODEL';
  propLayer.textContent = data.layer || 'Layer0';
  
  // Coordinates (mm)
  propX.textContent = data.positionMm[0].toFixed(1);
  propY.textContent = data.positionMm[1].toFixed(1);
  propZ.textContent = data.positionMm[2].toFixed(1);

  // Custom attributes
  customPropertiesTable.innerHTML = '';
  const propKeys = Object.keys(data.properties || {});
  
  if (propKeys.length > 0) {
    sectionCustomProperties.style.display = 'block';
    for (const key of propKeys) {
      const row = document.createElement('tr');
      const keyCell = document.createElement('td');
      const valCell = document.createElement('td');
      
      keyCell.textContent = key;
      valCell.textContent = data.properties[key];
      valCell.className = 'selectable-text';
      
      row.appendChild(keyCell);
      row.appendChild(valCell);
      customPropertiesTable.appendChild(row);
    }
  } else {
    sectionCustomProperties.style.display = 'none';
  }

  // Switch display
  inspectorEmptyState.style.display = 'none';
  inspectorDetails.style.display = 'block';

  // On mobile, surface the drawer the selection just populated (no-op on
  // desktop, where .open only affects layout inside the phone media query).
  setDrawer(panelInspector, mtoggleInspector, true);
  setDrawer(panelLayers, mtoggleLayers, false);

  // Log to console for debugging
  console.log('Selected Mesh:', data);
}

function clearSelection() {
  selectedMesh = null;
  if (selectedBoxHelper) {
    scene.remove(selectedBoxHelper);
    selectedBoxHelper = null;
  }

  inspectorDetails.style.display = 'none';
  inspectorEmptyState.style.display = 'flex';

  // A tap on empty canvas is a signal the user wants the 3D view, not a
  // drawer, in front - close both (no-op on desktop).
  setDrawer(panelLayers, mtoggleLayers, false);
  setDrawer(panelInspector, mtoggleInspector, false);
}

// Clear scene of old loaded model
function clearScene() {
  clearSelection();
  layerVisibility = {};

  // Traverse model group and dispose geometry & material
  modelGroup.traverse((child) => {
    if (child.isMesh) {
      if (child.geometry) child.geometry.dispose();
      if (child.material) {
        const materials = Array.isArray(child.material) ? child.material : [child.material];
        materials.forEach((mat) => {
          if (mat.map) mat.map.dispose();
          mat.dispose();
        });
      }
    }
  });

  // Remove all children
  while (modelGroup.children.length > 0) {
    modelGroup.remove(modelGroup.children[0]);
  }

  // Drop the decoded-texture cache from the previous model and free the
  // object URLs backing it - each load gets its own set of textures.
  textureObjectUrls.forEach((url) => URL.revokeObjectURL(url));
  textureObjectUrls = [];
  threeTextureCache.clear();
}

// Decodes currentScene.textures[index] (raw PNG/JPEG bytes from the .skp) into
// a THREE.Texture, caching by index so a texture shared by several materials
// is only decoded once per load.
function getSceneTexture(index) {
  if (threeTextureCache.has(index)) return threeTextureCache.get(index);

  const sceneTexture = currentScene.textures && currentScene.textures[index];
  if (!sceneTexture) return null;

  const blob = new Blob([sceneTexture.data], { type: sceneTexture.mimeType });
  const url = URL.createObjectURL(blob);
  textureObjectUrls.push(url);

  const texture = textureLoader.load(url);
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.wrapS = THREE.RepeatWrapping;
  texture.wrapT = THREE.RepeatWrapping;

  threeTextureCache.set(index, texture);
  return texture;
}

// Show/hide loader
function setLoader(show, text = 'Parsing SketchUp model...') {
  if (show) {
    loadingText.textContent = text;
    loadingOverlay.classList.remove('hidden');
  } else {
    loadingOverlay.classList.add('hidden');
  }
}

// Render dynamic layers list
function populateLayers(layers) {
  layersList.innerHTML = '';
  
  if (!layers || layers.length === 0) {
    layersListPlaceholder.style.display = 'flex';
    layersList.style.display = 'none';
    layerCountBadge.textContent = '0';
    mtoggleLayerCount.textContent = '0';
    return;
  }

  layersListPlaceholder.style.display = 'none';
  layersList.style.display = 'flex';
  layerCountBadge.textContent = layers.length.toString();
  mtoggleLayerCount.textContent = layers.length.toString();

  layers.forEach((layer) => {
    layerVisibility[layer.name] = true;

    const li = document.createElement('li');
    li.className = 'layer-item';

    const left = document.createElement('div');
    left.className = 'layer-left';

    const pill = document.createElement('div');
    pill.className = 'layer-color-pill';
    pill.style.backgroundColor = `rgb(${layer.color.r}, ${layer.color.g}, ${layer.color.b})`;

    const label = document.createElement('span');
    label.className = 'layer-name';
    label.textContent = layer.name;
    label.title = layer.name;

    left.appendChild(pill);
    left.appendChild(label);

    const toggle = document.createElement('label');
    toggle.className = 'switch';

    const checkbox = document.createElement('input');
    checkbox.type = 'checkbox';
    checkbox.checked = true;
    checkbox.addEventListener('change', (e) => {
      toggleLayer(layer.name, e.target.checked);
    });

    const slider = document.createElement('span');
    slider.className = 'slider';

    toggle.appendChild(checkbox);
    toggle.appendChild(slider);

    li.appendChild(left);
    li.appendChild(toggle);
    layersList.appendChild(li);
  });
}

function toggleLayer(layerName, visible) {
  layerVisibility[layerName] = visible;
  
  modelGroup.traverse((child) => {
    if (child.isMesh && child.userData && child.userData.layer === layerName) {
      child.visible = visible;
    }
  });

  // Clear outline helper if the selected object is hidden
  if (selectedMesh && !selectedMesh.visible) {
    clearSelection();
  }
}

// Fit camera view to bounding box of loaded model
function zoomToFit() {
  const box = new THREE.Box3().setFromObject(modelGroup);
  if (box.isEmpty()) return;

  const center = box.getCenter(new THREE.Vector3());
  const size = box.getSize(new THREE.Vector3());

  const maxDim = Math.max(size.x, size.y, size.z);
  const fov = camera.fov * (Math.PI / 180);
  let cameraZ = Math.abs(maxDim / 2 / Math.tan(fov / 2));
  
  cameraZ *= 1.35; // Add padding

  // Animate camera to look at the model center
  camera.position.set(center.x + cameraZ * 0.7, center.y + cameraZ * 0.5, center.z + cameraZ * 0.7);
  controls.target.copy(center);
  camera.lookAt(center);
  controls.update();
}

// Load and Parse SKP ArrayBuffer
function loadSkpBuffer(arrayBuffer, filename) {
  setLoader(true, 'Extracting & parsing SKP binary...');

  // Use a setTimeout to allow the browser thread to render the spinner before blocking parsing
  setTimeout(() => {
    try {
      clearScene();
      currentLoadedFilename = filename || 'model.skp';
      
      const startTime = performance.now();
      // parseSkp() is the light, per-definition raw parse (version, layers,
      // materials); buildScene() is the separate, opt-in step that resolves
      // the full placed scene graph into triangulated GLB-ready meshes. The
      // viewer needs both, since it renders the baked scene but reports
      // model-level metadata (version, layer list) from the light parse.
      currentModel = parseSkp(arrayBuffer);
      currentScene = buildScene(arrayBuffer);
      const parseTimeMs = performance.now() - startTime;

      console.log('Model parsed successfully:', currentModel);
      console.log(`Parsed in ${parseTimeMs.toFixed(1)}ms`);

      statusText.textContent = `Loaded ${filename} (${(arrayBuffer.byteLength / (1024 * 1024)).toFixed(2)} MB) in ${parseTimeMs.toFixed(0)}ms.`;

      // Set up layer panel
      populateLayers(currentModel.layers);

      // Reconstruct Three.js Meshes from pre-triangulated GLB primitives
      const prims = currentScene.glbPrimitives || [];
      console.log(`Building ${prims.length} geometry primitives...`);

      prims.forEach((prim) => {
        const geometry = new THREE.BufferGeometry();

        geometry.setAttribute('position', new THREE.BufferAttribute(prim.positions, 3));
        geometry.setAttribute('normal', new THREE.BufferAttribute(prim.normals, 3));
        if (prim.uvs && prim.uvs.length > 0) {
          geometry.setAttribute('uv', new THREE.BufferAttribute(prim.uvs, 2));
        }
        geometry.setIndex(new THREE.BufferAttribute(prim.indices, 1));

        // Get metadata
        const metadata = currentScene.meshIndex[prim.geomName] || {};

        // Material & Color setup (Fallback to layer color if material factor is missing)
        const matIdx = prim.materialIndex;
        let colorFactor = [0.6, 0.6, 0.6, 1.0];
        let baseColorTextureIndex = null;

        if (currentScene.gltfMaterials && currentScene.gltfMaterials[matIdx]) {
          const pbr = currentScene.gltfMaterials[matIdx].pbrMetallicRoughness;
          colorFactor = pbr.baseColorFactor;
          if (pbr.baseColorTexture) baseColorTextureIndex = pbr.baseColorTexture.index;
        } else {
          // Attempt to find layer color
          const lay = currentModel.layers.find((l) => l.name === metadata.layer);
          if (lay) {
            colorFactor = [lay.color.r / 255, lay.color.g / 255, lay.color.b / 255, 1.0];
          }
        }

        const materialOptions = {
          color: new THREE.Color(colorFactor[0], colorFactor[1], colorFactor[2]),
          roughness: 0.6,
          metalness: 0.1,
          side: THREE.DoubleSide
        };

        // A textured material may be a cutout (leaf clusters, chain-link,
        // signage) where the image's own alpha channel carves the visible
        // shape out of an otherwise plain rectangle - very common on
        // SketchUp Warehouse trees/foliage/fences. The parsed file's
        // material.transparency flag says which materials mean this, but
        // that flag isn't in the exported glTF material yet (tracked
        // separately as a cross-language library bug). Until that lands,
        // alphaTest is applied to every textured material as a safe
        // default: a fully-opaque texture (alpha=255 everywhere) renders
        // identically with or without it, so this only changes anything
        // for textures that actually carry a cutout.
        if (baseColorTextureIndex !== null) {
          const texture = getSceneTexture(baseColorTextureIndex);
          if (texture) {
            materialOptions.map = texture;
            materialOptions.transparent = true;
            materialOptions.alphaTest = 0.5;
          }
        }

        const material = new THREE.MeshStandardMaterial(materialOptions);

        const mesh = new THREE.Mesh(geometry, material);
        mesh.castShadow = true;
        mesh.receiveShadow = true;

        // Store metadata details
        mesh.userData = {
          isSkpMesh: true,
          geomName: prim.geomName,
          name: metadata.name || 'Component',
          definitionName: metadata.definitionName || 'ROOT_MODEL',
          layer: metadata.layer || 'Layer0',
          positionMm: metadata.positionMm || [0, 0, 0],
          properties: metadata.properties || {}
        };

        // Align visible state with layers
        mesh.visible = layerVisibility[mesh.userData.layer] !== false;

        modelGroup.add(mesh);
      });

      // Fit Viewport
      zoomToFit();

      // Update HUD Stats
      modelStats.style.visibility = 'visible';
      statVersion.textContent = `SKP v${currentModel.version || 'Unknown'}`;
      statMeshes.textContent = `Meshes: ${prims.length}`;
      
      btnExport.disabled = false;

    } catch (err) {
      console.error(err);
      statusText.textContent = `Error parsing ${filename}: ${err.message}`;
      alert(`Failed to load file: ${err.message}`);
      btnExport.disabled = true;
      modelStats.style.visibility = 'hidden';
    } finally {
      setLoader(false);
    }
  }, 100);
}

// Helper function to trigger browser file download
function downloadFile(filename, content, mimeType) {
  const blob = content instanceof Blob ? content : new Blob([content], { type: mimeType });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

// Multi-format exporter router
function handleExportFormat(format) {
  if (!currentScene || !currentModel) {
    alert('No active model loaded to export.');
    return;
  }

  const stem = currentLoadedFilename ? currentLoadedFilename.replace(/\.skp$/i, '') : 'model';

  setLoader(true, `Exporting model as ${format.toUpperCase()}...`);

  setTimeout(() => {
    try {
      if (format === 'json') {
        const data = toJSON(currentModel, currentScene);
        downloadFile(`${stem}_metadata.json`, JSON.stringify(data, null, 2), 'application/json');
        statusText.textContent = 'JSON metadata exported and downloaded!';
      } else if (format === 'ifc') {
        const ifcText = toIFC(currentScene);
        downloadFile(`${stem}.ifc`, ifcText, 'application/x-step');
        statusText.textContent = 'IFC4 BIM model exported and downloaded!';
      } else if (format === 'dxf-polyface' || format === 'dxf') {
        const dxfText = toDXF(currentScene, 39.37007874015748, 'polyface');
        downloadFile(`${stem}_polyface.dxf`, dxfText, 'image/vnd.dxf');
        statusText.textContent = 'AutoCAD 3D Polyface Mesh DXF exported and downloaded!';
      } else if (format === 'dxf-3dface') {
        const dxfText = toDXF(currentScene, 39.37007874015748, '3dface');
        downloadFile(`${stem}_3dface.dxf`, dxfText, 'image/vnd.dxf');
        statusText.textContent = 'AutoCAD 3DFACE Entities DXF exported and downloaded!';
      } else if (format === 'ply') {
        const plyBytes = toPLYBinary(currentScene);
        downloadFile(`${stem}.ply`, plyBytes, 'application/octet-stream');
        statusText.textContent = 'Stanford PLY mesh exported and downloaded!';
      } else if (format === 'stl') {
        const stlBytes = toSTLBinary(currentScene);
        downloadFile(`${stem}.stl`, stlBytes, 'application/octet-stream');
        statusText.textContent = '3D Printing STL exported and downloaded!';
      } else if (format === 'glb') {
        const glbBytes = toGLB(currentScene);
        downloadFile(`${stem}.glb`, glbBytes, 'model/gltf-binary');
        statusText.textContent = 'glTF 2.0 GLB binary exported and downloaded!';
      } else if (format === 'obj') {
        const objText = toOBJ(currentScene);
        downloadFile(`${stem}.obj`, objText, 'text/plain');
        statusText.textContent = 'Wavefront OBJ exported and downloaded!';
      }
    } catch (err) {
      console.error(`Export ${format} error:`, err);
      alert(`Failed to export ${format.toUpperCase()}: ${err.message}`);
    } finally {
      setLoader(false);
    }
  }, 50);
}

// Drag-and-drop HUD triggers
function initDragAndDrop() {
  ['dragenter', 'dragover'].forEach((eventName) => {
    window.addEventListener(eventName, (e) => {
      e.preventDefault();
      dropOverlay.classList.add('active');
    }, false);
  });

  ['dragleave', 'drop'].forEach((eventName) => {
    // We bind dragleave specifically to dropOverlay to avoid flickering when hovering items
    dropOverlay.addEventListener(eventName, (e) => {
      e.preventDefault();
      if (e.target === dropOverlay || eventName === 'drop') {
        dropOverlay.classList.remove('active');
      }
    }, false);
  });

  window.addEventListener('drop', (e) => {
    e.preventDefault();
    dropOverlay.classList.remove('active');

    const file = e.dataTransfer.files[0];
    if (file && file.name.toLowerCase().endsWith('.skp')) {
      handleFile(file);
    } else {
      alert('Only .skp files are supported!');
    }
  });
}

// Format a byte count as a human-readable MB string.
function formatMB(bytes) {
  return (bytes / (1024 * 1024)).toFixed(1);
}

// Entry point for any newly selected/dropped file: checks size before
// touching the parser at all, since a file large enough to exhaust this
// tab's heap can freeze it mid-parse with no chance to show an error
// afterward - the only reliable point to warn is before starting.
function handleFile(file) {
  if (file.size >= HARD_WARNING_BYTES) {
    pendingFile = file;
    sizeWarningMessage.textContent =
      `"${file.name}" is ${formatMB(file.size)} MB. Files around this size or ` +
      `larger have been confirmed to freeze this viewer's browser tab.`;
    sizeWarningOverlay.classList.remove('hidden');
    return;
  }
  if (file.size >= SOFT_WARNING_BYTES) {
    statusText.textContent = `Loading ${file.name} (${formatMB(file.size)} MB) - large files parse slower in-browser than via the CLI packages...`;
  }
  readAndLoad(file);
}

function readAndLoad(file) {
  const reader = new FileReader();
  reader.onload = (event) => {
    loadSkpBuffer(event.target.result, file.name);
  };
  reader.readAsArrayBuffer(file);
}

// Event Bindings
btnLoad.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (file) {
    handleFile(file);
  }
  // Allow re-selecting the same file path twice in a row.
  fileInput.value = '';
});

btnSizeCancel.addEventListener('click', () => {
  sizeWarningOverlay.classList.add('hidden');
  pendingFile = null;
});

btnSizeProceed.addEventListener('click', () => {
  sizeWarningOverlay.classList.add('hidden');
  const file = pendingFile;
  pendingFile = null;
  if (file) {
    readAndLoad(file);
  }
});

const exportDropdown = document.getElementById('export-dropdown');
const exportMenu = document.getElementById('export-menu');

btnExport.addEventListener('click', (e) => {
  e.stopPropagation();
  if (!btnExport.disabled && exportDropdown) {
    exportDropdown.classList.toggle('active');
  }
});

document.addEventListener('click', (e) => {
  if (exportDropdown && !exportDropdown.contains(e.target)) {
    exportDropdown.classList.remove('active');
  }
});

if (exportMenu) {
  exportMenu.querySelectorAll('.dropdown-item').forEach((item) => {
    item.addEventListener('click', (e) => {
      e.stopPropagation();
      const fmt = item.getAttribute('data-format');
      if (exportDropdown) exportDropdown.classList.remove('active');
      if (fmt) {
        handleExportFormat(fmt);
      }
    });
  });
}

// Mobile drawers: on phone-width screens the Layers/Inspector panels are
// hidden by default (see the max-width:768px block in style.css) so the
// 3D canvas stays full-screen and interactive. These toggles open them as
// dismissible drawers instead of permanently competing with the canvas
// for space.
function setDrawer(panel, toggleBtn, open) {
  panel.classList.toggle('open', open);
  if (toggleBtn) toggleBtn.classList.toggle('active', open);
}

mtoggleLayers.addEventListener('click', () => {
  const willOpen = !panelLayers.classList.contains('open');
  setDrawer(panelLayers, mtoggleLayers, willOpen);
  if (willOpen) setDrawer(panelInspector, mtoggleInspector, false);
});

mtoggleInspector.addEventListener('click', () => {
  const willOpen = !panelInspector.classList.contains('open');
  setDrawer(panelInspector, mtoggleInspector, willOpen);
  if (willOpen) setDrawer(panelLayers, mtoggleLayers, false);
});

btnCloseLayers.addEventListener('click', () => setDrawer(panelLayers, mtoggleLayers, false));
btnCloseInspector.addEventListener('click', () => setDrawer(panelInspector, mtoggleInspector, false));

// Kickstart
initViewport();
initDragAndDrop();
setLoader(false);

// Load a real, non-trivial sample model by default so a first-time visitor
// sees something rendered immediately instead of an empty drop zone - the
// AI-generated chair + table showcase model (see docs/AI_MODELING.md).
// Failure here (e.g. a deployment that doesn't bundle samples/) just falls
// back to the normal empty "drop a file" state, silently.
fetch('samples/chair_and_table.skp')
  .then((res) => (res.ok ? res.arrayBuffer() : Promise.reject(res.status)))
  .then((buffer) => loadSkpBuffer(buffer, 'chair_and_table.skp'))
  .catch((err) => console.warn('Default sample model did not load:', err));
