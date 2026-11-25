#include "face_groups.hpp"

#include <algorithm>
#include <cctype>
#include <cmath>
#include <cstring>
#include <sstream>
#include <stdexcept>
#include <unordered_map>
#include <utility>

namespace openskp {
namespace {

std::pair<double, double> tile_size(const std::shared_ptr<RawMaterial>& material) {
  double w = 1.0, h = 1.0;
  if (material && material->texture) {
    if (material->texture->x_scale > 1e-9) w = material->texture->x_scale;
    if (material->texture->y_scale > 1e-9) h = material->texture->y_scale;
  }
  return {w, h};
}

// Inverse of a row-major 3x3 matrix, via the cofactor/adjugate method.
std::array<double, 9> invert_3x3(const std::array<double, 9>& m) {
  double a = m[0], b = m[1], c = m[2];
  double d = m[3], e = m[4], f = m[5];
  double g = m[6], h = m[7], i = m[8];
  double det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g);
  if (std::abs(det) < 1e-12) return {1, 0, 0, 0, 1, 0, 0, 0, 1};
  double inv_det = 1.0 / det;
  return {
      (e * i - f * h) * inv_det, (c * h - b * i) * inv_det, (b * f - c * e) * inv_det,
      (f * g - d * i) * inv_det, (a * i - c * g) * inv_det, (c * d - a * f) * inv_det,
      (d * h - e * g) * inv_det, (b * g - a * h) * inv_det, (a * e - b * d) * inv_det,
  };
}

// Face-plane basis vectors (xr, yr) for UV projection, from a unit face normal.
// See packages/python/src/openskp/_face_groups.py::face_uv_basis - U = normalize(Z × n),
// V = n × U. Horizontal (X, Y); downward (−X, +Y). Tilt sine below 1e-3 snaps to those
// world axes (SDK-measured); a 1e-9 cutoff let float noise on a near-horizontal normal
// flip the basis, and (X, −Y) for downward turned every underside.
constexpr double kUvVerticalTolerance = 1e-3;

std::pair<Vec3, Vec3> face_uv_basis(const Vec3& n) {
  double cx = -n[1], cy = n[0];
  double clen = std::sqrt(cx * cx + cy * cy);
  if (clen < kUvVerticalTolerance) {
    Vec3 xr = n[2] >= 0 ? Vec3{1, 0, 0} : Vec3{-1, 0, 0};
    return {xr, Vec3{0, 1, 0}};
  }
  Vec3 xr{cx / clen, cy / clen, 0};
  Vec3 yr{n[1] * xr[2] - n[2] * xr[1], n[2] * xr[0] - n[0] * xr[2], n[0] * xr[1] - n[1] * xr[0]};
  return {xr, yr};
}

// UV of point p (inches, local/object space) on a face with the given
// plane basis, per-face uv_transform (or nullopt for the default
// projection), and material tile size (inches).
std::pair<double, double> compute_face_uv(const Vec3& p, const Vec3& xr, const Vec3& yr,
                                          const std::optional<std::array<double, 9>>& uv_transform,
                                          double tile_w, double tile_h) {
  double px = p[0] * xr[0] + p[1] * xr[1] + p[2] * xr[2];
  double py = p[0] * yr[0] + p[1] * yr[1] + p[2] * yr[2];
  if (!uv_transform) return {px / tile_w, py / tile_h};
  auto inv = invert_3x3(*uv_transform);
  double u = px * inv[0] + py * inv[3] + inv[6];
  double v = px * inv[1] + py * inv[4] + inv[7];
  double q = px * inv[2] + py * inv[5] + inv[8];
  if (std::abs(q) < 1e-12) q = 1.0;
  return {(u / q) / tile_w, (v / q) / tile_h};
}

std::vector<EntityId> loop_vertices(const std::vector<CoEdge>& loop, const GeometryBuilder& b) {
  std::vector<EntityId> v;
  for (auto& c : loop) {
    auto i = b.edges.find(c.edge_id);
    if (i == b.edges.end()) continue;
    auto id = c.orientation == 1 ? i->second.first : i->second.second;
    if (id && (v.empty() || v.back() != *id)) v.push_back(*id);
  }
  if (v.size() > 1 && v.front() == v.back()) v.pop_back();
  return v;
}

}  // namespace

std::shared_ptr<RawMaterial> find_material(const RawParsed& parsed, std::optional<EntityId> id) {
  if (!id) return nullptr;
  auto name = parsed.material_id_to_name.find(*id);
  if (name == parsed.material_id_to_name.end()) return nullptr;
  auto direct = parsed.materials.find(name->second);
  if (direct != parsed.materials.end()) return direct->second;
  auto folder = parsed.materials_by_folder.find(name->second);
  if (folder != parsed.materials_by_folder.end()) return folder->second;
  return nullptr;
}

std::optional<FaceColorKey> material_color(const std::shared_ptr<RawMaterial>& material) {
  if (!material) return {};
  // Two independent SketchUp mechanisms can reduce a material's opacity:
  // the plain RGBA color record's alpha byte (material->a), and the newer
  // XML material definition's own trans/useTrans attribute (already
  // resolved into material->transparency). A real material only ever
  // populates one of the two, but multiplying both is safe either way: the
  // untouched one defaults to fully-opaque (255 or 1.0), so it never
  // silently darkens a material that only used the other mechanism.
  const double combined =
      (std::clamp(material->a, 0, 255) / 255.0) * std::clamp(material->transparency, 0.0, 1.0);
  return FaceColorKey{material->r, material->g, material->b,
                      static_cast<int>(std::lround(combined * 255.0))};
}

FaceColorKey default_color(const RawParsed& parsed, const std::string& layer) {
  auto found = parsed.layer_colors.find(layer);
  auto color = found == parsed.layer_colors.end() ? Color3{136, 136, 136} : found->second;
  return {color[0], color[1], color[2], 255};
}

FaceLayerSummary summarize_face_layers(const RawParsed& parsed, const GeometryBuilder& b,
                                       const ParseOptions& options) {
  FaceLayerSummary out;
  if (b.faces.empty()) return out;
  std::vector<std::pair<std::string, std::size_t>> counts;
  bool all_same = true;
  std::optional<std::string> common_name;
  for (const auto& [fid, f] : b.faces) {
    std::string fname;
    if (!f.layer.empty() && f.layer != "0") {
      try {
        const auto id = std::stoll(f.layer);
        if (id != 0) {
          auto li = parsed.layer_id_to_name.find(id);
          if (li != parsed.layer_id_to_name.end() && li->second != "Layer0") {
            fname = li->second;
          }
        }
      } catch (...) {
        emit_log(options, LogLevel::debug,
                 "Failed to resolve face layer id '" + f.layer + "' to a name");
      }
    }
    if (!fname.empty()) {
      auto it = std::find_if(counts.begin(), counts.end(),
                             [&](const auto& kv) { return kv.first == fname; });
      if (it != counts.end()) {
        ++it->second;
      } else {
        counts.emplace_back(fname, 1);
      }
      if (!common_name) {
        common_name = fname;
      } else if (*common_name != fname) {
        all_same = false;
      }
    } else {
      all_same = false;
    }
  }
  if (all_same) out.uniform_layer = common_name;
  if (!counts.empty()) {
    const auto best =
        std::max_element(counts.begin(), counts.end(),
                         [](const auto& lhs, const auto& rhs) { return lhs.second < rhs.second; });
    out.majority_layer = best->first;
  }
  return out;
}

std::optional<std::pair<FaceColorKey, FaceColorKey>> unpainted_style_colors(
    const RawParsed& parsed, const std::string& unpainted_faces) {
  if (unpainted_faces != "style" && unpainted_faces != "tag") {
    throw std::invalid_argument("unpainted_faces must be \"style\" or \"tag\", not \"" +
                                unpainted_faces + "\"");
  }
  if (unpainted_faces != "style" || !parsed.face_colors) return std::nullopt;
  const auto& [front, back] = *parsed.face_colors;
  return std::pair<FaceColorKey, FaceColorKey>{{front[0], front[1], front[2], 255},
                                               {back[0], back[1], back[2], 255}};
}

// Identifies an image's MIME type from its magic bytes. Returns nullopt
// for anything glTF cannot carry (glTF only allows PNG and JPEG).
std::optional<std::string> sniff_image_mime(const ByteBuffer& data) {
  if (data.size() >= 3 && data[0] == 0xff && data[1] == 0xd8 && data[2] == 0xff) {
    return "image/jpeg";
  }
  if (data.size() >= 8 && data[0] == 0x89 && data[1] == 0x50 && data[2] == 0x4e &&
      data[3] == 0x47 && data[4] == 0x0d && data[5] == 0x0a && data[6] == 0x1a && data[7] == 0x0a) {
    return "image/png";
  }
  return std::nullopt;
}

namespace {

bool ascii_at(const ByteBuffer& data, std::size_t offset, const char* text) {
  const std::size_t len = std::strlen(text);
  if (offset + len > data.size()) return false;
  return std::memcmp(data.data() + offset, text, len) == 0;
}

std::string basename_of(const std::string& filename) {
  const auto pos = filename.find_last_of("/\\");
  return pos == std::string::npos ? filename : filename.substr(pos + 1);
}

std::string describe_image_format(const ByteBuffer& data, const std::string& filename = {}) {
  if (data.size() >= 4 &&
      ((data[0] == 0x49 && data[1] == 0x49 && data[2] == 0x2a && data[3] == 0x00) ||
       (data[0] == 0x4d && data[1] == 0x4d && data[2] == 0x00 && data[3] == 0x2a))) {
    return "TIFF";
  }
  if (ascii_at(data, 0, "BM")) return "BMP";
  if (ascii_at(data, 0, "GIF87a") || ascii_at(data, 0, "GIF89a")) return "GIF";
  if (ascii_at(data, 0, "RIFF") && ascii_at(data, 8, "WEBP")) return "WebP";
  if (ascii_at(data, 0, "8BPS")) return "PSD";
  if (ascii_at(data, 0, "DDS ")) return "DDS";
  if (data.size() >= 8 && data[0] == 0x00 && data[1] == 0x00 && data[2] == 0x00 &&
      data[3] == 0x0c && ascii_at(data, 4, "jP  ")) {
    return "JPEG 2000";
  }
  const std::size_t tail_start = data.size() > 26 ? data.size() - 26 : 0;
  for (std::size_t i = tail_start; i + 16 <= data.size(); ++i) {
    if (ascii_at(data, i, "TRUEVISION-XFILE")) return "TGA";
  }
  const auto base = basename_of(filename);
  const auto dot = base.rfind('.');
  if (dot != std::string::npos) {
    auto ext = base.substr(dot + 1);
    if (!ext.empty() && ext.size() <= 5) {
      for (auto& ch : ext) {
        ch = static_cast<char>(std::toupper(static_cast<unsigned char>(ch)));
      }
      return ext;
    }
  }
  return "an unrecognized format";
}

}  // namespace

std::optional<std::string> original_image_mime(const ByteBuffer& data) {
  const auto fmt = describe_image_format(data);
  if (fmt == "TIFF") return "image/tiff";
  if (fmt == "GIF") return "image/gif";
  if (fmt == "BMP") return "image/bmp";
  if (fmt == "TGA") return "image/x-tga";
  if (fmt == "WebP") return "image/webp";
  if (fmt == "PSD") return "image/vnd.adobe.photoshop";
  if (fmt == "DDS") return "image/vnd.ms-dds";
  if (fmt == "JPEG 2000") return "image/jp2";
  return std::nullopt;
}

void warn_skipped_texture(const ParseOptions& options,
                          std::set<std::pair<std::string, std::string>>& warned,
                          const std::shared_ptr<RawMaterial>& material) {
  if (!material || !material->texture || !material->texture->data) return;
  const auto& data = *material->texture->data;
  const auto& filename = material->texture->filename;
  const auto fmt = describe_image_format(data, filename);
  if (!warned.emplace(material->name, fmt).second) return;
  const auto base = basename_of(filename);
  const bool keepable = original_image_mime(data).has_value();
  std::string msg = "Texture of material '";
  msg += material->name.empty() ? "(unnamed)" : material->name;
  msg += "'";
  if (!base.empty()) msg += " (" + base + ")";
  msg += " is " + fmt +
         ", which a scene cannot carry (only PNG and JPEG): the material is built without its "
         "texture";
  if (keepable) {
    msg += " (pass original_textures = true to keep it in its own format)";
  }
  emit_log(options, LogLevel::warning, msg);
}

namespace {

std::uint64_t fnv1a64(const ByteBuffer& data) noexcept {
  std::uint64_t h = 14695981039346656037ull;
  for (const auto b : data) {
    h ^= static_cast<std::uint64_t>(b);
    h *= 1099511628211ull;
  }
  return h;
}

struct TextureDeduper {
  std::unordered_map<const ByteBuffer*, std::size_t> by_ptr;
  std::unordered_map<std::uint64_t, std::vector<std::size_t>> buckets;
  std::vector<std::shared_ptr<RawMaterial>> kept_refs;
  std::set<std::pair<std::string, std::string>> skipped;
};

}  // namespace

std::function<std::optional<std::size_t>(const std::shared_ptr<RawMaterial>&)>
make_texture_index_for(std::vector<SceneTexture>& textures, const ParseOptions& options) {
  auto state = std::make_shared<TextureDeduper>();
  return [&textures, &options,
          state](const std::shared_ptr<RawMaterial>& mat) -> std::optional<std::size_t> {
    if (!mat || !mat->texture || !mat->texture->data || mat->texture->data->empty()) {
      return std::nullopt;
    }
    const auto* ptr = &*mat->texture->data;
    if (const auto hit = state->by_ptr.find(ptr); hit != state->by_ptr.end()) {
      return hit->second;
    }
    const auto& data = *ptr;
    auto mime_type = sniff_image_mime(data);
    if (!mime_type && options.original_textures) mime_type = original_image_mime(data);
    if (!mime_type) {
      warn_skipped_texture(options, state->skipped, mat);
      return std::nullopt;
    }
    const auto hash = fnv1a64(data);
    auto& bucket = state->buckets[hash];
    for (const auto cand : bucket) {
      if (textures[cand].data == data) {
        state->kept_refs.push_back(mat);
        state->by_ptr.emplace(ptr, cand);
        return cand;
      }
    }
    const auto idx = textures.size();
    textures.push_back(SceneTexture{data, *mime_type, mat->texture->filename});
    bucket.push_back(idx);
    state->kept_refs.push_back(mat);
    state->by_ptr.emplace(ptr, idx);
    return idx;
  };
}

FilteredGltfTextures filter_gltf_textures(
    const std::vector<SceneTexture>& raw_textures, bool embed,
    const std::function<void(LogLevel, std::string_view)>& log) {
  FilteredGltfTextures out;
  if (!embed || raw_textures.empty()) return out;
  out.remap.resize(raw_textures.size());
  std::set<std::pair<std::string, std::string>> warned;
  for (std::size_t i = 0; i < raw_textures.size(); ++i) {
    const auto& tex = raw_textures[i];
    if (tex.mime_type == "image/png" || tex.mime_type == "image/jpeg") {
      out.remap[i] = static_cast<int>(out.textures.size());
      out.textures.push_back(&tex);
    } else {
      out.remap[i] = std::nullopt;
      const auto base = basename_of(tex.filename);
      const auto fmt = describe_image_format(tex.data, tex.filename);
      if (warned.emplace(base, fmt).second && log) {
        std::string msg = "Texture";
        if (!base.empty()) msg += " '" + base + "'";
        msg += " is " + fmt +
               ", which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB";
        log(LogLevel::warning, msg);
      }
    }
  }
  return out;
}

std::map<GroupKey, Group> build_local_face_groups(const RawParsed& parsed, const GeometryBuilder& b,
                                                  const FaceGroupContext& ctx) {
  std::map<GroupKey, Group> groups;
  const auto fallback_back = ctx.fallback_back_color.value_or(ctx.fallback_color);

  // Built once per definition, not per face: rebuilding it inside the loop
  // makes the whole pass O(faces x vertices).
  std::map<EntityId, Vertex> vv;
  for (auto& x : b.vertices) vv[x.first] = {x.first, x.second[0], x.second[1], x.second[2]};

  for (auto& fv : b.faces) {
    auto& f = fv.second;
    auto front_mat = find_material(parsed, f.material_id);
    if (!front_mat) front_mat = ctx.fallback_material;
    auto back_mat = find_material(parsed, f.back_material_id);
    if (!back_mat) back_mat = ctx.fallback_material;
    const auto front = material_color(front_mat).value_or(ctx.fallback_color);
    const auto back = material_color(back_mat).value_or(fallback_back);

    std::vector<std::vector<EntityId>> loops;
    for (auto& l : f.loops) {
      auto x = loop_vertices(l, b);
      if (!x.empty()) loops.push_back(std::move(x));
    }
    if (loops.empty()) continue;

    const auto triangles = triangulate_face_3d(vv, loops, f.normal);

    // Not a structured binding: those can't be captured by the nested
    // add_side lambda below in C++17 (only from C++20 onward).
    const auto uv_basis = face_uv_basis(f.normal);
    const Vec3& xr = uv_basis.first;
    const Vec3& yr = uv_basis.second;
    const auto add_side = [&](const GroupKey& key, bool reverse,
                              const std::optional<std::array<double, 9>>& uv_transform,
                              double tile_w, double tile_h) {
      auto& group = groups[key];
      for (auto triangle : triangles) {
        if (reverse) std::swap(triangle[1], triangle[2]);
        std::array<size_t, 3> indices{};
        for (int vertex = 0; vertex < 3; ++vertex) {
          auto id = triangle[vertex];
          const auto& pos = b.vertices.at(id);
          const auto [u, v] = compute_face_uv(pos, xr, yr, uv_transform, tile_w, tile_h);
          const FaceVKey vkey{id, u, v};
          auto it = group.map.find(vkey);
          if (it == group.map.end()) {
            it = group.map.emplace(vkey, group.verts.size()).first;
            group.verts.push_back(pos);
            group.uvs.push_back({float(u), float(v)});
          }
          indices[vertex] = it->second;
          auto& normal = group.normals[vkey];
          for (int axis = 0; axis < 3; ++axis)
            normal[axis] += reverse ? -f.normal[axis] : f.normal[axis];
        }
        if (reverse) {
          group.back_tris.push_back(static_cast<std::uint32_t>(group.tris.size()));
        }
        group.tris.push_back(indices);
      }
    };
    const bool front_raw_col = front_mat && front_mat->colorized;
    const bool back_raw_col = back_mat && back_mat->colorized;
    const int front_raw_ctype = front_raw_col ? front_mat->colorize_type : 0;
    const int back_raw_ctype = back_raw_col ? back_mat->colorize_type : 0;
    const bool same_colorization = (front_mat && back_mat) ? (front_raw_col == back_raw_col &&
                                                              front_raw_ctype == back_raw_ctype)
                                                           : true;
    if (front == back && same_colorization) {
      const auto [tw, th] = tile_size(front_mat);
      const auto tex = ctx.texture_index_for(front_mat);
      const bool col = front_mat && tex.has_value() && front_mat->colorized;
      const int ctype = col ? front_mat->colorize_type : 0;
      add_side({front, true, tex, false, col, ctype}, false, f.uv_transform, tw, th);
    } else {
      const auto [ftw, fth] = tile_size(front_mat);
      const auto ftex = ctx.texture_index_for(front_mat);
      const bool fcol = front_mat && ftex.has_value() && front_mat->colorized;
      const int fctype = fcol ? front_mat->colorize_type : 0;
      add_side({front, false, ftex, false, fcol, fctype}, false, f.uv_transform, ftw, fth);
      const auto [btw, bth] = tile_size(back_mat);
      const auto btex = ctx.texture_index_for(back_mat);
      const bool bcol = back_mat && btex.has_value() && back_mat->colorized;
      const int bctype = bcol ? back_mat->colorize_type : 0;
      add_side({back, false, btex, !front_mat && !back_mat, bcol, bctype}, true,
               f.uv_transform_back, btw, bth);
    }
  }

  return groups;
}

}  // namespace openskp
