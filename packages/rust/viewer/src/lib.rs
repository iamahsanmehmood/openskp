//! WebAssembly entry points for the viewer. No wasm-bindgen: the page calls
//! these raw exports and reads the result straight out of linear memory.
//!
//! Protocol:
//! 1. JS calls `alloc(len)`, copies the .skp bytes in, calls `load(ptr, len)`.
//! 2. `load` returns 0 on success, 1 on failure. Either way `json_ptr/json_len`
//!    then describe a UTF-8 JSON document (the scene header, or `{"error":..}`)
//!    and `bin_ptr/bin_len` a binary blob the header's offsets point into.
//! 3. JS frees the input with `dealloc(ptr, len)`. The result lives until the
//!    next `load`.

use std::cell::RefCell;
use std::fmt::Write as _;

use openskp::{build_scene, parse_skp, ParseOptions, Scene, SkpModel, Vec3};

#[derive(Default)]
struct Output {
    json: String,
    bin: Vec<u8>,
}

thread_local! {
    static OUTPUT: RefCell<Output> = RefCell::new(Output::default());
}

#[no_mangle]
pub extern "C" fn alloc(len: usize) -> *mut u8 {
    let mut buf = Vec::<u8>::with_capacity(len);
    let ptr = buf.as_mut_ptr();
    std::mem::forget(buf);
    ptr
}

/// # Safety
/// `ptr`/`len` must come from a previous `alloc(len)`.
#[no_mangle]
pub unsafe extern "C" fn dealloc(ptr: *mut u8, len: usize) {
    drop(Vec::from_raw_parts(ptr, 0, len));
}

/// # Safety
/// `ptr` must point to `len` initialised bytes obtained from `alloc`.
#[no_mangle]
pub unsafe extern "C" fn load(ptr: *const u8, len: usize) -> u32 {
    let data = std::slice::from_raw_parts(ptr, len);
    let (out, status) = match convert(data) {
        Ok(out) => (out, 0),
        Err(msg) => {
            let mut json = String::from("{\"error\":");
            push_str(&mut json, &msg);
            json.push('}');
            (
                Output {
                    json,
                    bin: Vec::new(),
                },
                1,
            )
        }
    };
    OUTPUT.with(|o| *o.borrow_mut() = out);
    status
}

#[no_mangle]
pub extern "C" fn json_ptr() -> *const u8 {
    OUTPUT.with(|o| o.borrow().json.as_ptr())
}

#[no_mangle]
pub extern "C" fn json_len() -> usize {
    OUTPUT.with(|o| o.borrow().json.len())
}

#[no_mangle]
pub extern "C" fn bin_ptr() -> *const u8 {
    OUTPUT.with(|o| o.borrow().bin.as_ptr())
}

#[no_mangle]
pub extern "C" fn bin_len() -> usize {
    OUTPUT.with(|o| o.borrow().bin.len())
}

fn convert(data: &[u8]) -> Result<Output, String> {
    let opts = ParseOptions::default();
    let model = parse_skp(data, &opts).map_err(|e| e.to_string())?;
    let scene = build_scene(data, &opts).map_err(|e| e.to_string())?;
    Ok(pack(&model, &scene))
}

/// Appends a 4-byte-aligned slice to the blob and returns its byte offset.
fn push_f32(bin: &mut Vec<u8>, values: &[f32]) -> usize {
    let off = bin.len();
    for v in values {
        bin.extend_from_slice(&v.to_le_bytes());
    }
    off
}

fn push_u32(bin: &mut Vec<u8>, values: &[u32]) -> usize {
    let off = bin.len();
    for v in values {
        bin.extend_from_slice(&v.to_le_bytes());
    }
    off
}

fn push_bytes(bin: &mut Vec<u8>, bytes: &[u8]) -> usize {
    let off = bin.len();
    bin.extend_from_slice(bytes);
    while bin.len() % 4 != 0 {
        bin.push(0);
    }
    off
}

fn pack(model: &SkpModel, scene: &Scene) -> Output {
    let mut bin = Vec::new();
    let mut j = String::with_capacity(4096);
    j.push('{');

    j.push_str("\"version\":");
    push_str(&mut j, &model.version);
    j.push_str(",\"units\":");
    match &model.units {
        Some(u) => push_str(&mut j, u),
        None => j.push_str("null"),
    }

    let (mut faces, mut edges, mut instances) = (0usize, 0usize, 0usize);
    for d in model.definitions.values() {
        faces += d.faces.len();
        edges += d.edges.len();
        instances += d.instances.len();
    }
    let root = model.root();
    faces += root.faces.len();
    edges += root.edges.len();
    instances += root.instances.len();
    let triangles: usize = scene
        .glb_primitives
        .iter()
        .map(|p| p.indices.len() / 3)
        .sum();
    let _ = write!(
        j,
        ",\"stats\":{{\"definitions\":{},\"faces\":{faces},\"edges\":{edges},\"instances\":{instances},\"triangles\":{triangles},\"materials\":{}}}",
        model.definitions.len(),
        model.materials.len()
    );

    j.push_str(",\"layers\":[");
    for (i, l) in model.layers.iter().enumerate() {
        if i > 0 {
            j.push(',');
        }
        j.push_str("{\"name\":");
        push_str(&mut j, &l.name);
        let hidden = scene.layer_hidden.get(&l.name).copied().unwrap_or(l.hidden);
        let _ = write!(
            j,
            ",\"hidden\":{hidden},\"color\":[{},{},{}]}}",
            l.color[0], l.color[1], l.color[2]
        );
    }
    j.push(']');

    j.push_str(",\"textures\":[");
    for (i, t) in scene.textures.iter().enumerate() {
        if i > 0 {
            j.push(',');
        }
        let off = push_bytes(&mut bin, &t.data);
        j.push_str("{\"mime\":");
        push_str(&mut j, &t.mime_type);
        let _ = write!(j, ",\"offset\":{off},\"length\":{}}}", t.data.len());
    }
    j.push(']');

    j.push_str(",\"materials\":[");
    for (i, m) in scene.gltf_materials.iter().enumerate() {
        if i > 0 {
            j.push(',');
        }
        let pbr = &m.pbr_metallic_roughness;
        let c = pbr.base_color_factor;
        j.push_str("{\"name\":");
        push_str(&mut j, &m.name);
        let _ = write!(
            j,
            ",\"color\":[{},{},{},{}],\"texture\":",
            c[0], c[1], c[2], c[3]
        );
        match pbr.base_color_texture {
            Some(t) => {
                let _ = write!(j, "{t}");
            }
            None => j.push_str("null"),
        }
        let _ = write!(j, ",\"doubleSided\":{}}}", m.double_sided);
    }
    j.push(']');

    j.push_str(",\"primitives\":[");
    for (i, p) in scene.glb_primitives.iter().enumerate() {
        if i > 0 {
            j.push(',');
        }
        let layer = scene
            .mesh_index
            .get(&p.geom_name)
            .map(|m| m.layer.as_str())
            .unwrap_or("");
        let pos = push_f32(&mut bin, &p.positions);
        let nrm = push_f32(&mut bin, &p.normals);
        let idx = push_u32(&mut bin, &p.indices);
        j.push_str("{\"name\":");
        push_str(&mut j, &p.geom_name);
        j.push_str(",\"layer\":");
        push_str(&mut j, layer);
        let _ = write!(
            j,
            ",\"material\":{},\"vertexCount\":{},\"positions\":{pos},\"normals\":{nrm},\"indices\":{idx},\"indexCount\":{}",
            p.material_index,
            p.positions.len() / 3,
            p.indices.len()
        );
        if p.uvs.len() * 3 == p.positions.len() * 2 && !p.uvs.is_empty() {
            let uv = push_f32(&mut bin, &p.uvs);
            let _ = write!(j, ",\"uvs\":{uv}");
        }
        j.push('}');
    }
    j.push(']');

    // Loose edges as one line list: [x0,y0,z0, x1,y1,z1, ...] segment pairs.
    let mut lines: Vec<f32> = Vec::new();
    let mut line_layers = String::from("[");
    for (i, c) in scene.curve_sets.iter().enumerate() {
        let start = lines.len() / 3;
        let n = c.points_m.len();
        let segs = if c.closed { n } else { n - 1 };
        for s in 0..segs {
            for p in [c.points_m[s], c.points_m[(s + 1) % n]] {
                lines.extend(p.iter().map(|v| *v as f32));
            }
        }
        if i > 0 {
            line_layers.push(',');
        }
        line_layers.push_str("{\"layer\":");
        push_str(&mut line_layers, &c.layer);
        let _ = write!(line_layers, ",\"first\":{start},\"count\":{}}}", segs * 2);
    }
    line_layers.push(']');
    let lines_off = push_f32(&mut bin, &lines);
    let _ = write!(
        j,
        ",\"lines\":{{\"positions\":{lines_off},\"vertexCount\":{},\"runs\":{line_layers}}}",
        lines.len() / 3
    );

    // Saved scenes (pages) and the model camera, converted to the viewer's
    // frame: SketchUp inches Z-up -> metres Y-up, same as the meshes.
    j.push_str(",\"camera\":");
    match &model.camera {
        Some(c) => push_camera(&mut j, "Câmera do arquivo", c.eye, c.target, c.up, c.fov),
        None => j.push_str("null"),
    }
    j.push_str(",\"pages\":[");
    let mut first = true;
    for p in &model.pages {
        if let (Some(eye), Some(target)) = (p.eye, p.target) {
            if !first {
                j.push(',');
            }
            first = false;
            push_camera(
                &mut j,
                &p.name,
                eye,
                target,
                p.up.unwrap_or([0.0, 0.0, 1.0]),
                p.fov,
            );
        }
    }
    j.push(']');

    j.push('}');
    Output { json: j, bin }
}

fn to_view(v: Vec3) -> [f64; 3] {
    const M: f64 = 0.0254;
    [v[0] * M, v[2] * M, -v[1] * M]
}

fn push_camera(j: &mut String, name: &str, eye: Vec3, target: Vec3, up: Vec3, fov: f64) {
    let (e, t) = (to_view(eye), to_view(target));
    // Direction vectors only rotate.
    let u = [up[0], up[2], -up[1]];
    j.push_str("{\"name\":");
    push_str(j, name);
    let fov = if fov.is_finite() && fov > 1.0 {
        fov
    } else {
        35.0
    };
    let _ = write!(
        j,
        ",\"eye\":[{},{},{}],\"target\":[{},{},{}],\"up\":[{},{},{}],\"fov\":{fov}}}",
        num(e[0]),
        num(e[1]),
        num(e[2]),
        num(t[0]),
        num(t[1]),
        num(t[2]),
        num(u[0]),
        num(u[1]),
        num(u[2])
    );
}

/// JSON has no NaN/Infinity.
fn num(v: f64) -> f64 {
    if v.is_finite() {
        v
    } else {
        0.0
    }
}

fn push_str(out: &mut String, s: &str) {
    out.push('"');
    for ch in s.chars() {
        match ch {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            c if (c as u32) < 0x20 => {
                let _ = write!(out, "\\u{:04x}", c as u32);
            }
            c => out.push(c),
        }
    }
    out.push('"');
}
