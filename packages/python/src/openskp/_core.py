"""Core parsing engine — battle-tested monolithic implementation.

This module contains the proven parsing logic extracted from the original
SketchUp reverse-engineering work. It is used internally by :class:`SkpFile`
to perform the actual binary parsing.

The public modules (parser.py, geometry.py, etc.) provide the clean API;
this module provides the working implementation.
"""

from __future__ import annotations

import io
import json
import os
import struct
import zipfile
from typing import Any, Dict
import xml.etree.ElementTree as ET

import numpy as np
import trimesh
from shapely.geometry import Polygon, Point, MultiPoint
import shapely.ops


# ── TLV Parsing ──────────────────────────────────────────────────────────

def read_u32(data: bytes, offset: int) -> int:
    return struct.unpack_from('<I', data, offset)[0]

def read_f64(data: bytes, offset: int) -> float:
    return struct.unpack_from('<d', data, offset)[0]

def parse_var_int(data: bytes, offset: int, length: int) -> int:
    val = 0
    for i in range(length):
        val |= data[offset + i] << (8 * i)
    return val

CONTAINER_TAGS = {
    'F401', 'F701', 'D430', 'D530', 'C832',
    '7C15', '8813', '8913', '8A13', '8B13', '8C13', '8D13', '4C1D', '6419',
    'F901', '7017', '7117', 'D007', 'C409', '9411', '9511', '0F01',
    '384A', 'B80B', '9713', '2C4C', 'AC0D', 'AE0D', 'F601', 'F801',
    '983A', '993A', '8C3C', '8D3C',
}

def parse_tlv_recursive(data, start, end, container_tags=None, depth=0):
    if container_tags is None:
        container_tags = CONTAINER_TAGS
    pos = start
    elements = []
    while pos < end - 6:
        tag_bytes = data[pos:pos+2]
        size = read_u32(data, pos+2)
        if pos + 6 + size > end:
            break
        tag_hex = tag_bytes.hex().upper()
        children = []
        is_container = tag_hex in container_tags
        if is_container and size > 0:
            children = parse_tlv_recursive(data, pos+6, pos+6+size, container_tags, depth+1)
        elements.append({
            'offset': pos,
            'tag': tag_hex,
            'size': size,
            'children': children,
            'payload': data[pos+6 : pos+6+size] if not children else b''
        })
        pos += 6 + size
    return elements


# ── 3D planar triangulation ──────────────────────────────────────────────

def triangulate_face_3d(vertices_3d, loops, normal):
    if len(loops) == 1 and len(loops[0]) == 3:
        return [loops[0]]
    if len(loops) == 1 and len(loops[0]) == 4:
        v = loops[0]
        return [[v[0], v[1], v[2]], [v[0], v[2], v[3]]]

    normal = np.array(normal)
    norm_val = np.linalg.norm(normal)
    if norm_val > 1e-6:
        normal = normal / norm_val
    else:
        normal = np.array([0.0, 0.0, 1.0])

    if abs(normal[0]) < 0.9:
        helper = np.array([1.0, 0.0, 0.0])
    else:
        helper = np.array([0.0, 1.0, 0.0])

    u_axis = np.cross(normal, helper)
    u_axis = u_axis / np.linalg.norm(u_axis)
    v_axis = np.cross(normal, u_axis)

    all_v_ids = []
    for loop in loops:
        for v_id in loop:
            if v_id not in all_v_ids:
                all_v_ids.append(v_id)

    v_id_to_2d = {}
    for v_id in all_v_ids:
        if v_id in vertices_3d:
            p3d = np.array(vertices_3d[v_id])
            u = np.dot(p3d, u_axis)
            v = np.dot(p3d, v_axis)
            v_id_to_2d[v_id] = (u, v)
        else:
            return []

    outer_coords = [v_id_to_2d[v_id] for v_id in loops[0]]
    if outer_coords[0] != outer_coords[-1]:
        outer_coords.append(outer_coords[0])

    inner_holes = []
    for hole_loop in loops[1:]:
        hole_coords = [v_id_to_2d[v_id] for v_id in hole_loop]
        if hole_coords[0] != hole_coords[-1]:
            hole_coords.append(hole_coords[0])
        inner_holes.append(hole_coords)

    poly_2d = Polygon(outer_coords, inner_holes)
    points_2d = []
    for coords in [outer_coords] + inner_holes:
        for c in coords[:-1]:
            points_2d.append(Point(c))

    mp = MultiPoint(points_2d)
    triangles = shapely.ops.triangulate(mp)

    inside_triangles = []
    for tri in triangles:
        if poly_2d.contains(tri.centroid):
            tri_coords = list(tri.exterior.coords)[:3]
            tri_v_ids = []
            for tc in tri_coords:
                best_v_id = None
                min_dist = float('inf')
                for v_id, c2d in v_id_to_2d.items():
                    dist = (tc[0] - c2d[0])**2 + (tc[1] - c2d[1])**2
                    if dist < min_dist:
                        min_dist = dist
                        best_v_id = v_id
                tri_v_ids.append(best_v_id)
            inside_triangles.append(tri_v_ids)

    return inside_triangles


# ── Geometry builder ─────────────────────────────────────────────────────

class _GeometryBuilder:
    def __init__(self):
        self.vertices = {}
        self.edges = {}
        self.faces = {}
        self.instances = []


def find_child_tag(nodes, target):
    for n in nodes:
        if n['tag'] == target:
            return n
        res = find_child_tag(n['children'], target)
        if res:
            return res
    return None

def find_all_nodes_rec(nodes, target_tag, results):
    for n in nodes:
        if n['tag'] == target_tag:
            results.append(n)
        find_all_nodes_rec(n['children'], target_tag, results)

def extract_entity_id(node):
    for child in node['children']:
        if child['tag'] == 'DE05':
            return parse_var_int(child['payload'], 0, len(child['payload']))
        if child['tag'] == 'DC05':
            payload = child['payload']
            if payload.startswith(bytes([0xDE, 0x05])):
                de05_len = read_u32(payload, 2)
                return parse_var_int(payload, 6, de05_len)
    for child in node['children']:
        res = extract_entity_id(child)
        if res is not None:
            return res
    return None


def _extract_geometry_from_nodes(elements, builder):
    for el in elements:
        tag = el['tag']

        if tag == 'C409':
            v_id = extract_entity_id(el)
            c509 = find_child_tag(el['children'], 'C509')
            if v_id is not None and c509 and len(c509['payload']) >= 24:
                x = read_f64(c509['payload'], 0)
                y = read_f64(c509['payload'], 8)
                z = read_f64(c509['payload'], 16)
                builder.vertices[v_id] = (x, y, z)

        elif tag == 'B80B':
            e_id = extract_entity_id(el)
            if e_id is not None:
                v1_node = find_child_tag(el['children'], 'B90B')
                v2_node = find_child_tag(el['children'], 'BA0B')
                v1 = parse_var_int(v1_node['payload'], 0, len(v1_node['payload'])) if v1_node else None
                v2 = parse_var_int(v2_node['payload'], 0, len(v2_node['payload'])) if v2_node else None
                builder.edges[e_id] = (v1, v2)

        elif tag == 'AC0D':
            f_id = extract_entity_id(el)
            if f_id is not None:
                normal = (0.0, 0.0, 1.0)
                ad0d = find_child_tag(el['children'], 'AD0D')
                if ad0d and len(ad0d['payload']) >= 24:
                    nx = read_f64(ad0d['payload'], 0)
                    ny = read_f64(ad0d['payload'], 8)
                    nz = read_f64(ad0d['payload'], 16)
                    normal = (nx, ny, nz)

                ae0d = find_child_tag(el['children'], 'AE0D')
                loops = []
                if ae0d:
                    loop_nodes = []
                    find_all_nodes_rec(ae0d['children'], '9411', loop_nodes)
                    for ln in loop_nodes:
                        co_edges = []
                        co_nodes = []
                        find_all_nodes_rec(ln['children'], 'A00F', co_nodes)
                        for cn in co_nodes:
                            payload = cn['payload']
                            edge_id = None
                            orient = None
                            sub_pos = 0
                            while sub_pos < len(payload) - 6:
                                sub_tag = payload[sub_pos:sub_pos+2]
                                sub_size = read_u32(payload, sub_pos+2)
                                if sub_pos + 6 + sub_size <= len(payload):
                                    val = parse_var_int(payload, sub_pos+6, sub_size)
                                    if sub_tag == bytes([0xA1, 0x0F]):
                                        edge_id = val
                                    elif sub_tag == bytes([0xA2, 0x0F]):
                                        orient = val
                                sub_pos += 6 + sub_size
                            if edge_id is not None and orient is not None:
                                co_edges.append((edge_id, orient))
                        if co_edges:
                            loops.append(co_edges)
                face_mat_id = None
                d007 = next((c for c in el['children'] if c['tag'] == 'D007'), None)
                if d007:
                    d107 = next((c for c in d007['children'] if c['tag'] == 'D107'), None)
                    if d107:
                        face_mat_id = parse_var_int(d107['payload'], 0, len(d107['payload']))
                builder.faces[f_id] = {'loops': loops, 'normal': normal, 'material_id': face_mat_id}

        elif tag == '6419':
            nodes_to_search = el['children'] if el['children'] else [el]
            guid = None
            def_idx = None
            name = None
            matrix = []
            guid_node = find_child_tag(nodes_to_search, '6819')
            if guid_node and len(guid_node['payload']) == 16:
                guid = guid_node['payload'].hex().upper()
            def_idx_node = find_child_tag(nodes_to_search, '6719')
            if def_idx_node:
                def_idx = parse_var_int(def_idx_node['payload'], 0, len(def_idx_node['payload']))
            name_node = find_child_tag(nodes_to_search, '6519')
            if name_node:
                name = name_node['payload'].decode('ascii', errors='ignore')
            mat_node = find_child_tag(nodes_to_search, '6619')
            if mat_node and len(mat_node['payload']) >= 104:
                for idx in range(13):
                    matrix.append(read_f64(mat_node['payload'], idx * 8))

            builder.instances.append({
                'offset': el['offset'],
                'ref_guid': guid,
                'ref_idx': def_idx,
                'name': name,
                'matrix': matrix,
                'children': el['children']
            })

        elif el['children']:
            _extract_geometry_from_nodes(el['children'], builder)


# ── Transforms ───────────────────────────────────────────────────────────

def transform_point(p, mat):
    if not mat or len(mat) < 12:
        return p
    x, y, z = p
    tx = mat[0]*x + mat[1]*y + mat[2]*z + mat[9]
    ty = mat[3]*x + mat[4]*y + mat[5]*z + mat[10]
    tz = mat[6]*x + mat[7]*y + mat[8]*z + mat[11]
    return (tx, ty, tz)

def multiply_matrices(parent, child):
    if not parent:
        return child
    if not child:
        return parent
    p_r0 = [parent[0], parent[1], parent[2], parent[9]]
    p_r1 = [parent[3], parent[4], parent[5], parent[10]]
    p_r2 = [parent[6], parent[7], parent[8], parent[11]]
    c_c0 = [child[0], child[3], child[6], 0]
    c_c1 = [child[1], child[4], child[7], 0]
    c_c2 = [child[2], child[5], child[8], 0]
    c_c3 = [child[9], child[10], child[11], 1]
    def dot(row, col):
        return sum(r*c for r, c in zip(row, col))
    out = [0.0] * 13
    out[0] = dot(p_r0, c_c0)
    out[1] = dot(p_r0, c_c1)
    out[2] = dot(p_r0, c_c2)
    out[3] = dot(p_r1, c_c0)
    out[4] = dot(p_r1, c_c1)
    out[5] = dot(p_r1, c_c2)
    out[6] = dot(p_r2, c_c0)
    out[7] = dot(p_r2, c_c1)
    out[8] = dot(p_r2, c_c2)
    out[9] = dot(p_r0, c_c3)
    out[10] = dot(p_r1, c_c3)
    out[11] = dot(p_r2, c_c3)
    out[12] = parent[12] * child[12]
    return out


# ── Dynamic properties ───────────────────────────────────────────────────

def extract_dynamic_properties(d007):
    dc05 = next((c for c in d007['children'] if c['tag'] == 'DC05'), None)
    if not dc05:
        return {}
    prop_container_tags = ['DD05', 'B536', 'B136', 'B236', 'B336', 'B036', 'A438']
    prop_elements = parse_tlv_recursive(dc05['payload'], 0, len(dc05['payload']), prop_container_tags)
    properties = {}
    current_key = None
    def extract_props(nodes):
        nonlocal current_key
        for n in nodes:
            tag = n['tag']
            if tag == 'B636':
                current_key = n['payload'].decode('ascii', errors='ignore')
            elif tag == 'AD38' and current_key:
                val = n['payload'].decode('ascii', errors='ignore')
                properties[current_key] = val
                current_key = None
            extract_props(n['children'])
    extract_props(prop_elements)
    return properties


# ── Full parse pipeline ──────────────────────────────────────────────────

def full_parse(skp_path: str) -> Dict[str, Any]:
    """Run the complete SKP parsing pipeline.

    Args:
        skp_path: Path to .skp file.

    Returns:
        Dict with keys: version, definitions_dict, layer_colors,
        layer_id_to_name, materials, elements, defs_dict
    """
    # 1. Find ZIP offset
    with open(skp_path, 'rb') as f:
        header = f.read(256)

    if not header.startswith(b'\xFF\xFE\xFF\x0E'):
        raise ValueError("Not a valid SketchUp file")

    version = "unknown"
    second_marker = header.find(b'\xFF\xFE\xFF', 4)
    if second_marker > 0:
        ver_start = second_marker + 4
        ver_bytes = header[ver_start:]
        ver_text = ver_bytes.decode('utf-16-le', errors='ignore')
        brace_start = ver_text.find('{')
        brace_end = ver_text.find('}')
        if brace_start >= 0 and brace_end > brace_start:
            version = ver_text[brace_start:brace_end+1]

    pk_pos = header.find(b'PK\x03\x04')
    if pk_pos < 0:
        with open(skp_path, 'rb') as f:
            chunk = f.read(4096)
            pk_pos = chunk.find(b'PK\x03\x04')
    if pk_pos < 0:
        raise ValueError("No ZIP container found")

    # 2. Extract ZIP contents
    with open(skp_path, 'rb') as f:
        f.seek(pk_pos)
        zip_bytes = f.read()
    zf = zipfile.ZipFile(io.BytesIO(zip_bytes), 'r')

    # Materials & layer colors
    layer_colors = {}
    materials = {}
    materials_by_folder = {}
    MAT_NS = {'mat': 'http://sketchup.google.com/schemas/sketchup/1.0/material'}
    for name in zf.namelist():
        if name.endswith('material.xml') and name.startswith('materials/'):
            xml_data = zf.read(name)
            try:
                root = ET.fromstring(xml_data)
                mat_elem = root.find('.//mat:material', MAT_NS)
                if mat_elem is not None:
                    mat_name = mat_elem.get('name', 'unknown')
                    r = int(mat_elem.get('colorRed', 128))
                    g = int(mat_elem.get('colorGreen', 128))
                    b = int(mat_elem.get('colorBlue', 128))
                    trans = float(mat_elem.get('trans', 0.5))
                    folder_name = name.split('/')[1] if len(name.split('/')) > 1 else ''
                    mat_obj = {'name': mat_name, 'color': {'r': r, 'g': g, 'b': b}, 'transparency': trans}
                    materials[mat_name] = mat_obj
                    if folder_name:
                        materials_by_folder[folder_name] = mat_obj
                    if mat_name.startswith('Layer_'):
                        layer_colors[mat_name[6:]] = (r, g, b)
            except Exception:
                pass

    # Thumbnail
    thumbnail_data = None
    if 'meta/model_thumbnail.png' in zf.namelist():
        thumbnail_data = zf.read('meta/model_thumbnail.png')

    # Styles: face colors live in styles/*/style.xml as signed-int32 ARGB
    # variants — item id 4000 is the front (default) face color, 4001 the
    # back face color. Viewers need them to shade unpainted faces the way
    # SketchUp does (an author may e.g. set a green back color so unpainted
    # garden faces read as grass).
    styles = []
    for name in zf.namelist():
        if not (name.startswith('styles/') and name.endswith('style.xml')):
            continue
        try:
            sroot = ET.fromstring(zf.read(name))
        except ET.ParseError:
            continue
        STY = '{http://sketchup.google.com/schemas/sketchup/1.0/style}'
        TYP = '{http://sketchup.google.com/schemas/1.0/types}'
        style_el = sroot.find(f'{STY}style')
        if style_el is None:
            continue
        colors = {}
        for item in style_el.findall(f'{STY}item'):
            iid = item.get('id')
            var = item.find(f'{TYP}variant')
            if iid in ('4000', '4001') and var is not None and var.text:
                try:
                    v = int(var.text) & 0xFFFFFFFF
                except ValueError:
                    continue
                colors[iid] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
        styles.append({'name': style_el.get('name', ''),
                       'front_color': colors.get('4000'),
                       'back_color': colors.get('4001')})

    model_dat = zf.read('model.dat')
    zf.close()

    # 3. Parse TLV tree
    elements = parse_tlv_recursive(model_dat, 0, len(model_dat), CONTAINER_TAGS)
    if len(elements) == 1 and elements[0]['tag'] == 'F401':
        elements = elements[0]['children']

    # Layer ID -> name
    layer_id_to_name = {}
    def collect_layers(nodes):
        for el in nodes:
            if el['tag'] == '993A':
                for child in el['children']:
                    if child['tag'] == '8C3C':
                        dc05 = find_child_tag(child['children'], 'DC05')
                        name_node = find_child_tag(child['children'], '8D3C')
                        if dc05 and name_node:
                            payload = dc05['payload']
                            if payload.startswith(bytes([0xDE, 0x05])):
                                de05_len = read_u32(payload, 2)
                                l_id = parse_var_int(payload, 6, de05_len)
                            else:
                                l_id = parse_var_int(payload, 0, len(payload))
                            l_name = name_node['payload'].decode('ascii', errors='ignore')
                            layer_id_to_name[l_id] = l_name
            collect_layers(el['children'])
    collect_layers(elements)

    if 1 not in layer_id_to_name:
        layer_id_to_name[1] = 'Layer0'
    if 'Layer0' not in layer_colors:
        layer_colors['Layer0'] = (136, 136, 136)

    # Material ID -> name
    material_id_to_name = {}
    def collect_material_ids(nodes):
        for el in nodes:
            if el['tag'] == 'C832':
                dc05 = find_child_tag(el['children'], 'DC05')
                name_node = find_child_tag(el['children'], 'CC32')
                if dc05 and name_node:
                    payload = dc05['payload']
                    if payload.startswith(bytes([0xDE, 0x05])):
                        de05_len = read_u32(payload, 2)
                        m_id = parse_var_int(payload, 6, de05_len)
                    else:
                        m_id = parse_var_int(payload, 0, len(payload))
                    m_name = name_node['payload'].decode('ascii', errors='ignore')
                    material_id_to_name[m_id] = m_name
            collect_material_ids(el['children'])
    collect_material_ids(elements)

    # Component definitions
    defs_dict = {}
    def collect_defs(nodes):
        for el in nodes:
            if el['tag'] == '7C15':
                guid = None
                name = None
                for child in el['children']:
                    if child['tag'] == '7D15' and len(child['payload']) == 16:
                        guid = child['payload'].hex().upper()
                    elif child['tag'] == '7E15':
                        name = child['payload'].decode('ascii', errors='ignore')
                ent_id = extract_entity_id(el)
                builder = _GeometryBuilder()
                _extract_geometry_from_nodes(el['children'], builder)
                defs_dict[ent_id] = {'guid': guid, 'name': name, 'builder': builder}
            collect_defs(el['children'])
    collect_defs(elements)

    # Root definition
    root_builder = _GeometryBuilder()
    for el in elements:
        if el['tag'] == 'F601':
            _extract_geometry_from_nodes(el['children'], root_builder)
    defs_dict['ROOT'] = {'guid': 'ROOT', 'name': 'ROOT_MODEL', 'builder': root_builder}

    return {
        'version': version,
        'layer_colors': layer_colors,
        'layer_id_to_name': layer_id_to_name,
        'material_id_to_name': material_id_to_name,
        'materials': materials,
        'materials_by_folder': materials_by_folder,
        'defs_dict': defs_dict,
        'elements': elements,
        'thumbnail_data': thumbnail_data,
        'styles': styles,
    }


def build_scene(parsed: Dict[str, Any], output_dir: str, filename_stem: str) -> Dict[str, Any]:
    """Build trimesh scene and metadata from parsed data.

    Args:
        parsed: Output of full_parse()
        output_dir: Directory to write GLB and JSON files
        filename_stem: Base filename without extension

    Returns:
        Dict with glb_path, json_path, metadata, mesh_count
    """
    defs_dict = parsed['defs_dict']
    layer_colors = parsed['layer_colors']
    layer_id_to_name = parsed['layer_id_to_name']
    material_id_to_name = parsed.get('material_id_to_name', {})
    materials = parsed['materials']
    materials_by_folder = parsed.get('materials_by_folder', {})

    scene = trimesh.Scene()
    mesh_counter = [0]
    mesh_index = {}

    def instantiate(def_id, current_matrix, parent_layer='Layer0', path_name="ROOT", inherited_color=None):
        if def_id not in defs_dict:
            return []
        d = defs_dict[def_id]
        builder = d['builder']

        if builder.faces:
            local_verts = []
            local_faces = []
            local_v_map = {}
            face_colors_list = []
            for f_id, f_data in builder.faces.items():
                # Resolve color for this face
                face_color = None
                face_mat_id = f_data.get('material_id')
                if face_mat_id is not None:
                    mat_name = material_id_to_name.get(face_mat_id)
                    mat = materials.get(mat_name) or materials_by_folder.get(mat_name)
                    if mat:
                        c = mat['color']
                        face_color = (c['r'], c['g'], c['b'])

                if face_color is None and inherited_color is not None:
                    face_color = inherited_color

                if face_color is None:
                    face_color = layer_colors.get(parent_layer, (136, 136, 136))

                loops = []
                for loop in f_data['loops']:
                    loop_verts = []
                    for edge_id, orient in loop:
                        if edge_id in builder.edges:
                            v1, v2 = builder.edges[edge_id]
                            v_start = v1 if orient == 1 else v2
                            if not loop_verts or loop_verts[-1] != v_start:
                                loop_verts.append(v_start)
                    if len(loop_verts) > 1 and loop_verts[0] == loop_verts[-1]:
                        loop_verts = loop_verts[:-1]
                    if loop_verts:
                        loops.append(loop_verts)
                if not loops:
                    continue
                triangles = triangulate_face_3d(builder.vertices, loops, f_data['normal'])
                for tri in triangles:
                    face_indices = []
                    for v_id in tri:
                        if v_id in builder.vertices:
                            if v_id not in local_v_map:
                                local_verts.append(builder.vertices[v_id])
                                local_v_map[v_id] = len(local_verts) - 1
                            face_indices.append(local_v_map[v_id])
                    if len(face_indices) == 3:
                        local_faces.append(face_indices)
                        face_colors_list.append(list(face_color) + [255])

            if local_faces:
                v_trans = []
                for v in local_verts:
                    pt = transform_point(v, current_matrix)
                    ox = pt[0] * 25.4
                    oy = pt[2] * 25.4
                    oz = -pt[1] * 25.4
                    v_trans.append((ox, oy, oz))
                mesh = trimesh.Trimesh(vertices=v_trans, faces=local_faces)
                mesh.visual.face_colors = face_colors_list
                safe_path = path_name.replace(' / ', '__').replace(' ', '_')[:80]
                geom_name = f"mesh_{mesh_counter[0]}_{safe_path}_{parent_layer}"
                mesh_counter[0] += 1
                scene.add_geometry(mesh, geom_name=geom_name)

        child_instances_info = []
        for inst in builder.instances:
            ref_idx = inst['ref_idx']
            inst_matrix = inst['matrix']
            new_matrix = multiply_matrices(current_matrix, inst_matrix)

            l_name = parent_layer
            inst_color = inherited_color
            d007 = next((c for c in inst['children'] if c['tag'] == 'D007'), None)
            properties = {}
            if d007:
                d207 = next((c for c in d007['children'] if c['tag'] == 'D207'), None)
                if d207 and d207['payload']:
                    p = d207['payload']
                    if len(p) == 1:
                        l_id = p[0]
                    else:
                        l_id = parse_var_int(p, 0, len(p))
                    l_name = layer_id_to_name.get(l_id, parent_layer)

                d107 = next((c for c in d007['children'] if c['tag'] == 'D107'), None)
                if d107:
                    inst_mat_id = parse_var_int(d107['payload'], 0, len(d107['payload']))
                    mat_name = material_id_to_name.get(inst_mat_id)
                    mat = materials.get(mat_name) or materials_by_folder.get(mat_name)
                    if mat:
                        c = mat['color']
                        inst_color = (c['r'], c['g'], c['b'])

                try:
                    properties = extract_dynamic_properties(d007)
                except Exception:
                    pass

            inst_name = inst['name'] if inst['name'] else f"Component_{ref_idx}"
            full_path_name = f"{path_name} / {inst_name}"
            child_nodes = instantiate(ref_idx, new_matrix, l_name, full_path_name, inst_color)

            tx = new_matrix[9] * 25.4 if len(new_matrix) > 11 else 0
            ty = new_matrix[10] * 25.4 if len(new_matrix) > 11 else 0
            tz = new_matrix[11] * 25.4 if len(new_matrix) > 11 else 0

            inst_info = {
                'name': inst['name'] if inst['name'] else '',
                'definition_id': ref_idx,
                'definition_name': defs_dict.get(ref_idx, {}).get('name', ''),
                'layer': l_name,
                'position_mm': [round(tx, 2), round(ty, 2), round(tz, 2)],
                'matrix_3x4': inst_matrix,
                'properties': properties,
                'children': child_nodes
            }
            child_instances_info.append(inst_info)

            safe_child_path = full_path_name.replace(' / ', '__').replace(' ', '_')[:80]
            for gname in list(scene.geometry.keys()):
                if safe_child_path in gname and gname not in mesh_index:
                    mesh_index[gname] = {
                        'name': inst['name'] if inst['name'] else '',
                        'definition_name': defs_dict.get(ref_idx, {}).get('name', ''),
                        'layer': l_name,
                        'position_mm': [round(tx, 2), round(ty, 2), round(tz, 2)],
                        'properties': properties,
                        'path': full_path_name
                    }

        return child_instances_info

    identity_mat = [1,0,0, 0,1,0, 0,0,1, 0,0,0, 1.0]
    root_children = instantiate('ROOT', identity_mat)

    # Export GLB
    os.makedirs(output_dir, exist_ok=True)
    glb_path = os.path.join(output_dir, f"{filename_stem}.glb")
    scene.export(glb_path, file_type='glb')

    # Register root meshes
    for gname in list(scene.geometry.keys()):
        if gname not in mesh_index:
            mesh_index[gname] = {
                'name': 'ROOT', 'definition_name': 'ROOT_MODEL',
                'layer': 'Layer0', 'position_mm': [0, 0, 0],
                'properties': {}, 'path': 'ROOT'
            }

    # Build layers list
    layers_list = [{'name': n, 'color': {'r': c[0], 'g': c[1], 'b': c[2]}}
                   for n, c in layer_colors.items()]

    metadata = {
        'format_version': '1.0',
        'source_file': filename_stem,
        'model_source': 'sketchup',
        'sketchup_version': parsed['version'],
        'total_definitions': len(defs_dict) - 1,
        'total_meshes': len(scene.geometry),
        'total_layers': len(layers_list),
        'layers': layers_list,
        'materials': list(materials.values()),
        'mesh_index': mesh_index,
        'scene_hierarchy': {
            'name': 'ROOT', 'layer': 'Layer0',
            'properties': {}, 'children': root_children
        }
    }

    json_path = os.path.join(output_dir, f"{filename_stem}_metadata.json")
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)

    # Save thumbnail
    if parsed.get('thumbnail_data'):
        thumb_path = os.path.join(output_dir, f"{filename_stem}_thumbnail.png")
        with open(thumb_path, 'wb') as f:
            f.write(parsed['thumbnail_data'])

    return {
        'glb_path': glb_path,
        'json_path': json_path,
        'metadata': metadata,
        'mesh_count': len(scene.geometry),
    }
