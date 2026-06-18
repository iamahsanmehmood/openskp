import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { GLTFExporter } from 'three/addons/exporters/GLTFExporter.js';
import { parseSkp } from './dist/index.mjs';

// Application state variables
let scene, camera, renderer, controls;
let modelGroup;
let raycaster, mouse;
let selectedMesh = null;
let selectedBoxHelper = null;
let currentModel = null;
let layerVisibility = {};

// DOM Elements
const canvasContainer = document.getElementById('canvas-container');
const dropOverlay = document.getElementById('drop-overlay');
const loadingOverlay = document.getElementById('loading-overlay');
const loadingText = document.getElementById('loading-text');
const fileInput = document.getElementById('file-input');
const btnLoad = document.getElementById('btn-load');
const btnExport = document.getElementById('btn-export');
const statusText = document.getElementById('status-text');

// Layers Panel
const layersListPlaceholder = document.getElementById('layers-list-placeholder');
const layersList = document.getElementById('layers-list');
const layerCountBadge = document.getElementById('layer-count');

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
  scene.background = new THREE.Color(0x0a0b10);
  scene.fog = new THREE.FogExp2(0x0a0b10, 0.015);

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
  const gridHelper = new THREE.GridHelper(50, 50, 0x00f0ff, 0x24252d);
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
  selectedBoxHelper = new THREE.BoxHelper(mesh, 0x00f0ff);
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
        if (Array.isArray(child.material)) {
          child.material.forEach((mat) => mat.dispose());
        } else {
          child.material.dispose();
        }
      }
    }
  });

  // Remove all children
  while (modelGroup.children.length > 0) {
    modelGroup.remove(modelGroup.children[0]);
  }
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
    return;
  }

  layersListPlaceholder.style.display = 'none';
  layersList.style.display = 'flex';
  layerCountBadge.textContent = layers.length.toString();

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
      
      const startTime = performance.now();
      currentModel = parseSkp(arrayBuffer);
      const parseTimeMs = performance.now() - startTime;
      
      console.log('Model parsed successfully:', currentModel);
      console.log(`Parsed in ${parseTimeMs.toFixed(1)}ms`);

      statusText.textContent = `Loaded ${filename} (${(arrayBuffer.byteLength / (1024 * 1024)).toFixed(2)} MB) in ${parseTimeMs.toFixed(0)}ms.`;

      // Set up layer panel
      populateLayers(currentModel.layers);

      // Reconstruct Three.js Meshes from pre-triangulated GLB primitives
      const prims = currentModel._glbPrimitives || [];
      console.log(`Building ${prims.length} geometry primitives...`);

      prims.forEach((prim) => {
        const geometry = new THREE.BufferGeometry();
        
        geometry.setAttribute('position', new THREE.BufferAttribute(prim.positions, 3));
        geometry.setAttribute('normal', new THREE.BufferAttribute(prim.normals, 3));
        geometry.setIndex(new THREE.BufferAttribute(prim.indices, 1));

        // Get metadata
        const metadata = currentModel.meshIndex[prim.geomName] || {};
        
        // Material & Color setup (Fallback to layer color if material factor is missing)
        const matIdx = prim.materialIndex;
        let colorFactor = [0.6, 0.6, 0.6, 1.0];
        
        if (currentModel._gltfMaterials && currentModel._gltfMaterials[matIdx]) {
          colorFactor = currentModel._gltfMaterials[matIdx].pbrMetallicRoughness.baseColorFactor;
        } else {
          // Attempt to find layer color
          const lay = currentModel.layers.find((l) => l.name === metadata.layer);
          if (lay) {
            colorFactor = [lay.color.r / 255, lay.color.g / 255, lay.color.b / 255, 1.0];
          }
        }

        const material = new THREE.MeshStandardMaterial({
          color: new THREE.Color(colorFactor[0], colorFactor[1], colorFactor[2]),
          roughness: 0.6,
          metalness: 0.1,
          side: THREE.DoubleSide
        });

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

// Download scene as GLB using Three.js GLTFExporter
function exportToGLB() {
  if (modelGroup.children.length === 0) return;

  setLoader(true, 'Packaging & exporting GLB...');

  setTimeout(() => {
    try {
      const exporter = new GLTFExporter();
      
      // We export the entire model group
      exporter.parse(
        modelGroup,
        (gltfBuffer) => {
          setLoader(false);
          
          const blob = new Blob([gltfBuffer], { type: 'application/octet-stream' });
          const url = URL.createObjectURL(blob);
          
          const link = document.createElement('a');
          link.href = url;
          link.download = `exported_model.glb`;
          link.click();
          
          URL.revokeObjectURL(url);
          statusText.textContent = 'GLB exported and downloaded successfully!';
        },
        (error) => {
          setLoader(false);
          console.error('GLTF Export error:', error);
          alert(`Failed to export GLB: ${error.message}`);
        },
        { binary: true }
      );
    } catch (err) {
      setLoader(false);
      console.error(err);
      alert(`Error during export setup: ${err.message}`);
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
      const reader = new FileReader();
      reader.onload = (event) => {
        loadSkpBuffer(event.target.result, file.name);
      };
      reader.readAsArrayBuffer(file);
    } else {
      alert('Only .skp files are supported!');
    }
  });
}

// Event Bindings
btnLoad.addEventListener('click', () => fileInput.click());
fileInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  if (file) {
    const reader = new FileReader();
    reader.onload = (event) => {
      loadSkpBuffer(event.target.result, file.name);
    };
    reader.readAsArrayBuffer(file);
  }
});

btnExport.addEventListener('click', exportToGLB);

// Kickstart
initViewport();
initDragAndDrop();
setLoader(false);
