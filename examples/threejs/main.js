import * as THREE from 'three';
import { GLTFLoader } from 'three/addons/loaders/GLTFLoader.js';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { buildScene, toGLB } from 'openskp';

// A plain three.js scene: renderer, camera, orbit controls and some light.
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(window.devicePixelRatio);
renderer.setSize(window.innerWidth, window.innerHeight);
document.body.appendChild(renderer.domElement);

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x08090b);
scene.add(new THREE.HemisphereLight(0xffffff, 0x888888, 2));
const sun = new THREE.DirectionalLight(0xffffff, 1.5);
sun.position.set(3, 5, 4);
scene.add(sun);

const camera = new THREE.PerspectiveCamera(45, window.innerWidth / window.innerHeight, 0.01, 1000);
camera.position.set(2, 2, 2);
const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;

let model = null;
const status = document.getElementById('status');

document.getElementById('file').addEventListener('change', async (event) => {
    const file = event.target.files[0];
    if (!file) return;
    status.textContent = `Converting ${file.name}...`;
    try {
        // The whole pipeline: .skp bytes -> OpenSKP scene -> GLB bytes -> three.js.
        const skp = await file.arrayBuffer();
        const glb = toGLB(buildScene(skp), { textures: true });
        const gltf = await new GLTFLoader().parseAsync(glb.buffer, '');

        // OpenSKP writes material colours as the file's sRGB values, while
        // GLTFLoader reads baseColorFactor as linear. Convert them, or every
        // colour comes out washed out. Meshes share materials, so convert each
        // material once.
        const materials = new Set();
        gltf.scene.traverse((obj) => obj.isMesh && materials.add(obj.material));
        materials.forEach((material) => material.color.convertSRGBToLinear());

        if (model) scene.remove(model);
        model = gltf.scene;
        scene.add(model);
        frame(model);
        status.textContent = file.name;
    } catch (err) {
        console.error(err);
        status.textContent = `Could not open ${file.name}: ${err.message}`;
    }
});

// Point the camera at the model, whatever its size.
function frame(object) {
    const box = new THREE.Box3().setFromObject(object);
    const center = box.getCenter(new THREE.Vector3());
    const size = box.getSize(new THREE.Vector3()).length();
    controls.target.copy(center);
    camera.position.copy(center).add(new THREE.Vector3(1, 0.7, 1).normalize().multiplyScalar(size * 1.2));
    camera.near = size / 1000;
    camera.far = size * 100;
    camera.updateProjectionMatrix();
}

window.addEventListener('resize', () => {
    camera.aspect = window.innerWidth / window.innerHeight;
    camera.updateProjectionMatrix();
    renderer.setSize(window.innerWidth, window.innerHeight);
});

renderer.setAnimationLoop(() => {
    controls.update();
    renderer.render(scene, camera);
});
