#include "openskp/ifc_export.hpp"

#include <algorithm>
#include <array>
#include <chrono>
#include <cmath>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <fstream>
#include <functional>
#include <iomanip>
#include <map>
#include <optional>
#include <random>
#include <sstream>
#include <stdexcept>
#include <string>
#include <unordered_map>
#include <vector>

#include "curves.hpp"
#include "ifc_attr_counts.hpp"

namespace openskp {

namespace {

constexpr char kIfcBase64[] = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_$";

std::vector<std::uint32_t> decode_utf8_codepoints(const std::string& s) {
  std::vector<std::uint32_t> out;
  out.reserve(s.size());
  std::size_t i = 0;
  const std::size_t n = s.size();
  while (i < n) {
    const auto b0 = static_cast<unsigned char>(s[i]);
    if (b0 < 0x80) {
      out.push_back(b0);
      ++i;
    } else if ((b0 & 0xE0) == 0xC0 && i + 1 < n) {
      const auto b1 = static_cast<unsigned char>(s[i + 1]);
      if ((b1 & 0xC0) == 0x80) {
        const std::uint32_t cp = ((b0 & 0x1Fu) << 6) | (b1 & 0x3Fu);
        out.push_back(cp >= 0x80 ? cp : 0xFFFDu);
        i += 2;
      } else {
        out.push_back(0xFFFDu);
        ++i;
      }
    } else if ((b0 & 0xF0) == 0xE0 && i + 2 < n) {
      const auto b1 = static_cast<unsigned char>(s[i + 1]);
      const auto b2 = static_cast<unsigned char>(s[i + 2]);
      if ((b1 & 0xC0) == 0x80 && (b2 & 0xC0) == 0x80) {
        const std::uint32_t cp = ((b0 & 0x0Fu) << 12) | ((b1 & 0x3Fu) << 6) | (b2 & 0x3Fu);
        out.push_back((cp >= 0x800 && !(cp >= 0xD800 && cp <= 0xDFFF)) ? cp : 0xFFFDu);
        i += 3;
      } else {
        out.push_back(0xFFFDu);
        ++i;
      }
    } else if ((b0 & 0xF8) == 0xF0 && i + 3 < n) {
      const auto b1 = static_cast<unsigned char>(s[i + 1]);
      const auto b2 = static_cast<unsigned char>(s[i + 2]);
      const auto b3 = static_cast<unsigned char>(s[i + 3]);
      if ((b1 & 0xC0) == 0x80 && (b2 & 0xC0) == 0x80 && (b3 & 0xC0) == 0x80) {
        const std::uint32_t cp =
            ((b0 & 0x07u) << 18) | ((b1 & 0x3Fu) << 12) | ((b2 & 0x3Fu) << 6) | (b3 & 0x3Fu);
        out.push_back((cp >= 0x10000 && cp <= 0x10FFFF) ? cp : 0xFFFDu);
        i += 4;
      } else {
        out.push_back(0xFFFDu);
        ++i;
      }
    } else {
      out.push_back(0xFFFDu);
      ++i;
    }
  }
  return out;
}

std::string step_text(const std::string& text) {
  std::string out;
  out.reserve(text.size());
  std::vector<std::uint32_t> run;
  bool wide = false;

  auto flush = [&]() {
    if (run.empty()) return;
    out += wide ? "\\X4\\" : "\\X2\\";
    char buf[16];
    for (std::uint32_t cp : run) {
      std::snprintf(buf, sizeof(buf), wide ? "%08X" : "%04X", static_cast<unsigned int>(cp));
      out += buf;
    }
    out += "\\X0\\";
    run.clear();
  };

  for (std::uint32_t cp : decode_utf8_codepoints(text)) {
    if (cp >= 0x20u && cp <= 0x7Eu) {
      flush();
      if (cp == '\'') {
        out += "''";
      } else if (cp == '\\') {
        out += "\\\\";
      } else {
        out.push_back(static_cast<char>(cp));
      }
    } else {
      run.push_back(cp);
      if (cp > 0xFFFFu) wide = true;
    }
  }
  flush();
  return out;
}

std::string sanitize_name(const std::string& name) {
  if (name.empty()) return "Unnamed";
  // trim
  const std::size_t start = name.find_first_not_of(" \t\r\n");
  if (start == std::string::npos) return "Unnamed";
  const std::size_t end = name.find_last_not_of(" \t\r\n");
  return step_text(name.substr(start, end - start + 1));
}

std::tuple<double, double, double, double> get_prim_rgb(const Scene& scene, size_t prim_mat_idx) {
  double r = 0.8, g = 0.8, b = 0.8, a = 1.0;
  if (prim_mat_idx < scene.gltf_materials.size()) {
    const auto& mat = scene.gltf_materials[prim_mat_idx];
    const auto& col = mat.pbr_metallic_roughness.base_color_factor;
    r = std::max(0.0, std::min(1.0, col[0]));
    g = std::max(0.0, std::min(1.0, col[1]));
    b = std::max(0.0, std::min(1.0, col[2]));
    a = std::max(0.0, std::min(1.0, col[3]));
  }
  return {r, g, b, a};
}

std::uint64_t weld_key(float v) {
  double r = round6(static_cast<double>(v));
  if (r == 0.0) r = 0.0;
  std::uint64_t bits = 0;
  std::memcpy(&bits, &r, sizeof(bits));
  return bits;
}

struct WeldPosHash {
  std::size_t operator()(const std::array<std::uint64_t, 3>& k) const noexcept {
    // The bits of a round coordinate such as 12.0 are all in the upper word, and MSVC takes the
    // bucket from the low bits, so each step has to carry the upper bits down.
    std::uint64_t h = 0;
    for (const std::uint64_t v : k) {
      h ^= v;
      h ^= h >> 32;
      h *= 0x9e3779b97f4a7c15ULL;
      h ^= h >> 29;
    }
    return static_cast<std::size_t>(h);
  }
};

bool mesh_is_closed(const GlbPrimitive& prim) {
  const std::size_t v_count = prim.positions.size() / 3;
  const std::size_t tri_count = prim.indices.size() / 3;
  if (v_count == 0 || tri_count == 0) return false;

  std::unordered_map<std::array<std::uint64_t, 3>, std::uint32_t, WeldPosHash> weld;
  weld.reserve(v_count);
  std::vector<std::uint32_t> remap(v_count);
  for (std::size_t i = 0; i < v_count; ++i) {
    const std::array<std::uint64_t, 3> key{
        weld_key(prim.positions[i * 3]),
        weld_key(prim.positions[i * 3 + 1]),
        weld_key(prim.positions[i * 3 + 2]),
    };
    auto [it, inserted] = weld.emplace(key, static_cast<std::uint32_t>(weld.size()));
    remap[i] = it->second;
  }

  std::unordered_map<std::uint64_t, std::uint32_t> edge_use;
  edge_use.reserve(tri_count * 3 / 2 + 1);
  for (std::size_t i = 0; i < tri_count; ++i) {
    const std::uint32_t i0 = prim.indices[i * 3];
    const std::uint32_t i1 = prim.indices[i * 3 + 1];
    const std::uint32_t i2 = prim.indices[i * 3 + 2];
    if (i0 >= v_count || i1 >= v_count || i2 >= v_count) return false;
    const std::uint32_t a = remap[i0];
    const std::uint32_t b = remap[i1];
    const std::uint32_t c = remap[i2];
    for (const auto& [u, v] :
         std::array<std::pair<std::uint32_t, std::uint32_t>, 3>{{{a, b}, {b, c}, {c, a}}}) {
      const std::uint64_t lo = u < v ? u : v;
      const std::uint64_t hi = u < v ? v : u;
      ++edge_use[(lo << 32) | hi];
    }
  }

  if (edge_use.empty()) return false;
  for (const auto& [ek, count] : edge_use) {
    if (count != 2) return false;
  }
  return true;
}

void append_fixed6(std::string& out, double val) {
  char buf[64];
  const int n = std::snprintf(buf, sizeof(buf), "%.6f", val);
  if (n <= 0) return;
  if (static_cast<std::size_t>(n) < sizeof(buf)) {
    for (int i = 0; i < n; ++i) {
      if (buf[i] == ',') buf[i] = '.';
    }
    out.append(buf, static_cast<std::size_t>(n));
    return;
  }
  std::string dyn(static_cast<std::size_t>(n) + 1, '\0');
  const int n2 = std::snprintf(dyn.data(), dyn.size(), "%.6f", val);
  if (n2 > 0) {
    const std::size_t len = std::min(static_cast<std::size_t>(n2), dyn.size() - 1);
    for (std::size_t i = 0; i < len; ++i) {
      if (dyn[i] == ',') dyn[i] = '.';
    }
    out.append(dyn.data(), len);
  }
}

void append_ifc_coord(std::string& out, double x, double y, double z, double scale) {
  if (!std::isfinite(x) || !std::isfinite(y) || !std::isfinite(z)) {
    throw std::invalid_argument("IFC coordinates must be finite");
  }
  const double vx = x * scale;
  const double vy = -z * scale;
  const double vz = y * scale;
  if (!std::isfinite(vx) || !std::isfinite(vy) || !std::isfinite(vz)) {
    throw std::invalid_argument("IFC coordinates must be finite");
  }
  out.push_back('(');
  append_fixed6(out, vx);
  out.push_back(',');
  append_fixed6(out, vy);
  out.push_back(',');
  append_fixed6(out, vz);
  out.push_back(')');
}

std::string ifc_coord(double x, double y, double z, double scale) {
  std::string out;
  out.reserve(40);
  append_ifc_coord(out, x, y, z, scale);
  return out;
}

std::string fixed6_str(double val) {
  if (!std::isfinite(val)) {
    throw std::invalid_argument("IFC coordinates must be finite");
  }
  std::string out;
  out.reserve(24);
  append_fixed6(out, val);
  return out;
}

std::string fixed6_vec3(const std::array<double, 3>& v) {
  if (!std::isfinite(v[0]) || !std::isfinite(v[1]) || !std::isfinite(v[2])) {
    throw std::invalid_argument("IFC coordinates must be finite");
  }
  std::string out;
  out.reserve(40);
  out.push_back('(');
  append_fixed6(out, v[0]);
  out.push_back(',');
  append_fixed6(out, v[1]);
  out.push_back(',');
  append_fixed6(out, v[2]);
  out.push_back(')');
  return out;
}

struct SolvedArcSeg {
  std::array<double, 3> center;
  double radius;
  std::array<double, 3> axis_z;
  std::array<double, 3> ref_x;
  double sweep;
};

std::optional<SolvedArcSeg> solve_ifc2x3_arc_segment(const std::array<double, 3>& p0,
                                                     const std::array<double, 3>& pm,
                                                     const std::array<double, 3>& p1) {
  constexpr double kTwoPi = 6.28318530717958647692;
  const double ux = pm[0] - p0[0], uy = pm[1] - p0[1], uz = pm[2] - p0[2];
  const double vx = p1[0] - p0[0], vy = p1[1] - p0[1], vz = p1[2] - p0[2];
  const double wx = uy * vz - uz * vy;
  const double wy = uz * vx - ux * vz;
  const double wz = ux * vy - uy * vx;
  const double u2 = ux * ux + uy * uy + uz * uz;
  const double v2 = vx * vx + vy * vy + vz * vz;
  const double w2 = wx * wx + wy * wy + wz * wz;
  if (u2 <= 0.0 || v2 <= 0.0 || w2 <= 1e-24 * std::max(1.0, u2 * v2)) {
    return std::nullopt;
  }

  const double vxw_x = vy * wz - vz * wy;
  const double vxw_y = vz * wx - vx * wz;
  const double vxw_z = vx * wy - vy * wx;
  const double wxu_x = wy * uz - wz * uy;
  const double wxu_y = wz * ux - wx * uz;
  const double wxu_z = wx * uy - wy * ux;

  const double inv_2w2 = 0.5 / w2;
  const double ox = (u2 * vxw_x + v2 * wxu_x) * inv_2w2;
  const double oy = (u2 * vxw_y + v2 * wxu_y) * inv_2w2;
  const double oz = (u2 * vxw_z + v2 * wxu_z) * inv_2w2;
  const double radius = std::sqrt(ox * ox + oy * oy + oz * oz);
  if (!std::isfinite(radius) || radius <= 0.0) return std::nullopt;

  const double cx = p0[0] + ox, cy = p0[1] + oy, cz = p0[2] + oz;
  const double inv_r = 1.0 / radius;
  const double xx = -ox * inv_r, xy = -oy * inv_r, xz = -oz * inv_r;
  const double inv_w = 1.0 / std::sqrt(w2);
  const double zx = wx * inv_w, zy = wy * inv_w, zz = wz * inv_w;
  const double yx = zy * xz - zz * xy;
  const double yy = zz * xx - zx * xz;
  const double yz = zx * xy - zy * xx;

  const double d1x = (p1[0] - cx) * inv_r;
  const double d1y = (p1[1] - cy) * inv_r;
  const double d1z = (p1[2] - cz) * inv_r;
  double sweep = std::atan2(d1x * yx + d1y * yy + d1z * yz, d1x * xx + d1y * xy + d1z * xz);
  if (sweep <= 0.0) sweep += kTwoPi;
  if (!std::isfinite(sweep) || sweep <= 0.0 || sweep >= kTwoPi) return std::nullopt;

  return SolvedArcSeg{{cx, cy, cz}, radius, {zx, zy, zz}, {xx, xy, xz}, sweep};
}

bool needs_ifc2x3_notdefined_attr8(const std::string& step_type) {
  return step_type == "IFCROOF" || step_type == "IFCFOOTING" || step_type == "IFCPILE" ||
         step_type == "IFCRAMP" || step_type == "IFCSTAIR";
}

}  // namespace

std::string generate_ifc_guid() {
  static thread_local std::mt19937_64 rng(std::random_device{}());
  std::uint64_t hi = rng();
  std::uint64_t lo = rng();
  hi = (hi & ~(std::uint64_t{0xF} << 12)) | (std::uint64_t{0x4} << 12);  // version 4
  lo = (lo & ~(std::uint64_t{0x3} << 62)) | (std::uint64_t{0x2} << 62);  // RFC 4122 variant

  std::string result(22, '0');
  for (int i = 21; i >= 0; --i) {
    result[static_cast<std::size_t>(i)] = kIfcBase64[lo & 63u];
    lo = (lo >> 6) | ((hi & 63u) << 58);
    hi >>= 6;
  }
  return result;
}

namespace {

std::optional<std::pair<std::string, std::string>> classify_by_keyword(const std::string& name) {
  std::string l = name;
  std::transform(l.begin(), l.end(), l.begin(), [](unsigned char c) { return std::tolower(c); });
  if (l.find("wall") != std::string::npos) return std::pair{"IFCWALL", "IfcWall"};
  if (l.find("door") != std::string::npos) return std::pair{"IFCDOOR", "IfcDoor"};
  if (l.find("window") != std::string::npos) return std::pair{"IFCWINDOW", "IfcWindow"};
  if (l.find("slab") != std::string::npos || l.find("floor") != std::string::npos)
    return std::pair{"IFCSLAB", "IfcSlab"};
  if (l.find("column") != std::string::npos || l.find("pillar") != std::string::npos)
    return std::pair{"IFCCOLUMN", "IfcColumn"};
  if (l.find("beam") != std::string::npos || l.find("joist") != std::string::npos)
    return std::pair{"IFCBEAM", "IfcBeam"};
  if (l.find("roof") != std::string::npos) return std::pair{"IFCROOF", "IfcRoof"};
  return std::nullopt;
}

}  // namespace

std::pair<std::string, std::string> classify_element(const std::string& geom_name,
                                                     const std::string& layer_name,
                                                     const std::string& path_name,
                                                     bool classify_using_full_path) {
  if (auto by_name = classify_by_keyword(geom_name)) return *by_name;
  if (!layer_name.empty()) {
    if (auto by_layer = classify_by_keyword(layer_name)) return *by_layer;
  }
  if (classify_using_full_path && !path_name.empty()) {
    if (auto by_path = classify_by_keyword(path_name)) return *by_path;
  }
  return {"IFCBUILDINGELEMENTPROXY", "IfcBuildingElementProxy"};
}

namespace {

// One IFCPROPERTYSET (props) attached to product_id via
// IFCRELDEFINESBYPROPERTIES. Shared by both meta.properties
// (Pset_CustomProperties) and every other attribute dictionary an
// instance carries (Pset_<dict-name>) below.
void write_pset(std::ostringstream& ss, const std::function<int()>& next_id, int owner_hist_id,
                int product_id, const std::string& pset_name,
                const std::map<std::string, std::string>& props) {
  if (props.empty()) return;
  std::vector<int> prop_val_ids;
  for (const auto& [pk, pv] : props) {
    std::string clean_k = sanitize_name(pk);
    std::string clean_v = sanitize_name(pv);
    int prop_id = next_id();
    ss << "#" << prop_id << "=IFCPROPERTYSINGLEVALUE('" << clean_k << "',$,IFCTEXT('" << clean_v
       << "'),$);\r\n";
    prop_val_ids.push_back(prop_id);
  }

  int pset_id = next_id();
  std::ostringstream prop_ss;
  for (size_t i = 0; i < prop_val_ids.size(); ++i) {
    if (i > 0) prop_ss << ",";
    prop_ss << "#" << prop_val_ids[i];
  }
  ss << "#" << pset_id << "=IFCPROPERTYSET('" << generate_ifc_guid() << "',#" << owner_hist_id
     << ",'" << sanitize_name(pset_name) << "',$,(" << prop_ss.str() << "));\r\n";
  ss << "#" << next_id() << "=IFCRELDEFINESBYPROPERTIES('" << generate_ifc_guid() << "',#"
     << owner_hist_id << ",$,$,(#" << product_id << "),#" << pset_id << ");\r\n";
}

}  // namespace

std::string to_ifc(const Scene& scene, double scale, const std::string& schema,
                   const IfcClassifier& classifier, bool classify_using_full_path) {
  if (!std::isfinite(scale)) {
    throw std::invalid_argument("IFC scale must be finite");
  }
  // classifier (if given) keeps its original 2-arg (name, layer) public
  // signature; only the built-in classify_element() gains the path/
  // full-path-matching parameters, matching openskp.export.ifc's own
  // to_ifc()/classify_element() split.
  std::function<std::pair<std::string, std::string>(const std::string&, const std::string&,
                                                    const std::string&)>
      classify;
  if (classifier) {
    classify = [&](const std::string& name, const std::string& layer, const std::string&) {
      return classifier(name, layer);
    };
  } else {
    classify = [&](const std::string& name, const std::string& layer, const std::string& path) {
      return classify_element(name, layer, path, classify_using_full_path);
    };
  }
  std::string schema_str = schema.empty() ? "IFC4" : schema;
  std::transform(schema_str.begin(), schema_str.end(), schema_str.begin(),
                 [](unsigned char c) { return std::toupper(c); });
  const bool is_ifc2x3 = schema_str.rfind("IFC2X3", 0) == 0;

  auto now = std::chrono::system_clock::now();
  auto timestamp_epoch =
      std::chrono::duration_cast<std::chrono::seconds>(now.time_since_epoch()).count();
  std::time_t now_time = std::chrono::system_clock::to_time_t(now);
  std::tm gmt{};
#if defined(_WIN32)
  gmtime_s(&gmt, &now_time);
#else
  gmtime_r(&now_time, &gmt);
#endif
  char time_buf[32];
  std::strftime(time_buf, sizeof(time_buf), "%Y-%m-%dT%H:%M:%S", &gmt);

  std::ostringstream ss;
  ss.imbue(std::locale::classic());
  ss << std::fixed << std::setprecision(6);

  ss << "ISO-10303-21;\r\n";
  ss << "HEADER;\r\n";
  ss << "FILE_DESCRIPTION(('ViewDefinition [CoordinationView]'),'2;1');\r\n";
  ss << "FILE_NAME('model.ifc','" << time_buf
     << "',('OpenSKP Author'),('OpenSKP "
        "Organization'),'OpenSKP IFC Exporter','OpenSKP','');\r\n";
  ss << "FILE_SCHEMA(('" << schema_str << "'));\r\n";
  ss << "ENDSEC;\r\n";
  ss << "DATA;\r\n";

  int entity_id = 1;
  auto next_id = [&entity_id]() { return entity_id++; };

  int person_id = next_id();
  ss << "#" << person_id << "=IFCPERSON($,$,'OpenSKP User',$,$,$,$,$);\r\n";

  int org_id = next_id();
  ss << "#" << org_id << "=IFCORGANIZATION($,'OpenSKP',$,$,$);\r\n";

  int person_org_id = next_id();
  ss << "#" << person_org_id << "=IFCPERSONANDORGANIZATION(#" << person_id << ",#" << org_id
     << ",$);\r\n";

  int app_id = next_id();
  ss << "#" << app_id << "=IFCAPPLICATION(#" << org_id
     << ",'0.3.1','OpenSKP Exporter','OpenSKP');\r\n";

  int owner_hist_id = next_id();
  ss << "#" << owner_hist_id << "=IFCOWNERHISTORY(#" << person_org_id << ",#" << app_id
     << ",$,.NOCHANGE.,$,$,$," << timestamp_epoch << ");\r\n";

  int length_unit_id = next_id();
  ss << "#" << length_unit_id << "=IFCSIUNIT(*,.LENGTHUNIT.,.MILLI.,.METRE.);\r\n";

  int angle_unit_id = next_id();
  ss << "#" << angle_unit_id << "=IFCSIUNIT(*,.PLANEANGLEUNIT.,$,.RADIAN.);\r\n";

  int solid_unit_id = next_id();
  ss << "#" << solid_unit_id << "=IFCSIUNIT(*,.SOLIDANGLEUNIT.,$,.STERADIAN.);\r\n";

  int unit_assign_id = next_id();
  ss << "#" << unit_assign_id << "=IFCUNITASSIGNMENT((#" << length_unit_id << ",#" << angle_unit_id
     << ",#" << solid_unit_id << "));\r\n";

  int pt_zero_id = next_id();
  ss << "#" << pt_zero_id << "=IFCCARTESIANPOINT((0.0,0.0,0.0));\r\n";

  int axis_placement_id = next_id();
  ss << "#" << axis_placement_id << "=IFCAXIS2PLACEMENT3D(#" << pt_zero_id << ",$,$);\r\n";

  int geom_ctx_id = next_id();
  ss << "#" << geom_ctx_id << "=IFCGEOMETRICREPRESENTATIONCONTEXT($,'Model',3,1.0E-5,#"
     << axis_placement_id << ",$);\r\n";

  int ann_ctx_id = next_id();
  ss << "#" << ann_ctx_id << "=IFCGEOMETRICREPRESENTATIONSUBCONTEXT('Annotation','Model',*,*,*,*,#"
     << geom_ctx_id << ",$,.MODEL_VIEW.,$);\r\n";

  int proj_id = next_id();
  ss << "#" << proj_id << "=IFCPROJECT('" << generate_ifc_guid() << "',#" << owner_hist_id
     << ",'OpenSKP Project',$,$,$,$,(#" << geom_ctx_id << "),#" << unit_assign_id << ");\r\n";

  int site_placement_id = next_id();
  ss << "#" << site_placement_id << "=IFCLOCALPLACEMENT($,#" << axis_placement_id << ");\r\n";

  int site_id = next_id();
  ss << "#" << site_id << "=IFCSITE('" << generate_ifc_guid() << "',#" << owner_hist_id
     << ",'Site',$,$,#" << site_placement_id << ",$,$,.ELEMENT.,$,$,$,$,$);\r\n";

  int bldg_placement_id = next_id();
  ss << "#" << bldg_placement_id << "=IFCLOCALPLACEMENT(#" << site_placement_id << ",#"
     << axis_placement_id << ");\r\n";

  int bldg_id = next_id();
  ss << "#" << bldg_id << "=IFCBUILDING('" << generate_ifc_guid() << "',#" << owner_hist_id
     << ",'Building',$,$,#" << bldg_placement_id << ",$,$,.ELEMENT.,$,$,$);\r\n";

  int storey_placement_id = next_id();
  ss << "#" << storey_placement_id << "=IFCLOCALPLACEMENT(#" << bldg_placement_id << ",#"
     << axis_placement_id << ");\r\n";

  int storey_id = next_id();
  ss << "#" << storey_id << "=IFCBUILDINGSTOREY('" << generate_ifc_guid() << "',#" << owner_hist_id
     << ",'Level 0',$,$,#" << storey_placement_id << ",$,$,.ELEMENT.,0.0);\r\n";

  ss << "#" << next_id() << "=IFCRELAGGREGATES('" << generate_ifc_guid() << "',#" << owner_hist_id
     << ",$,$,#" << proj_id << ",(#" << site_id << "));\r\n";
  ss << "#" << next_id() << "=IFCRELAGGREGATES('" << generate_ifc_guid() << "',#" << owner_hist_id
     << ",$,$,#" << site_id << ",(#" << bldg_id << "));\r\n";
  ss << "#" << next_id() << "=IFCRELAGGREGATES('" << generate_ifc_guid() << "',#" << owner_hist_id
     << ",$,$,#" << bldg_id << ",(#" << storey_id << "));\r\n";

  std::vector<int> product_ids;
  std::map<std::string, std::pair<std::string, std::vector<int>>> layer_items;
  std::map<std::string, int> mat_style_cache;

  auto add_layer_item = [&](const std::string& raw_layer, int item_id) {
    const bool has_layer = raw_layer.find_first_not_of(" \t\r\n") != std::string::npos;
    const std::string raw = has_layer ? raw_layer : "Layer0";
    const std::string key = has_layer ? sanitize_name(raw_layer) : "Layer0";
    auto [it, inserted] = layer_items.emplace(key, std::make_pair(raw, std::vector<int>{}));
    it->second.second.push_back(item_id);
  };

  for (const auto& cs : scene.curve_sets) {
    if (cs.points_m.size() < 2) continue;
    for (const auto& p : cs.points_m) {
      if (!std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2])) {
        throw std::invalid_argument("IFC coordinates must be finite");
      }
    }

    int curve_id = 0;
    if (cs.arc && cs.arc->points.size() >= 3 && (!is_ifc2x3 || !cs.arc->segments.empty())) {
      if (!is_ifc2x3) {
        std::ostringstream seg_ss;
        for (std::size_t i = 0; i < cs.arc->segments.size(); ++i) {
          if (i > 0) seg_ss << ",";
          const auto& s = cs.arc->segments[i];
          seg_ss << "IFCARCINDEX((" << (s[0] + 1) << "," << (s[1] + 1) << "," << (s[2] + 1) << "))";
        }
        std::string coord_str;
        coord_str.reserve(cs.arc->points.size() * 36);
        for (std::size_t i = 0; i < cs.arc->points.size(); ++i) {
          if (i > 0) coord_str.push_back(',');
          const auto& p = cs.arc->points[i];
          append_ifc_coord(coord_str, p[0], p[1], p[2], scale);
        }
        const int pt_list_id = next_id();
        ss << "#" << pt_list_id << "=IFCCARTESIANPOINTLIST3D((" << coord_str << "));\r\n";
        curve_id = next_id();
        ss << "#" << curve_id << "=IFCINDEXEDPOLYCURVE(#" << pt_list_id << ",(" << seg_ss.str()
           << "),.F.);\r\n";
      } else {
        std::vector<std::array<double, 3>> arc_pts_zup;
        arc_pts_zup.reserve(cs.arc->points.size());
        for (const auto& p : cs.arc->points) {
          if (!std::isfinite(p[0]) || !std::isfinite(p[1]) || !std::isfinite(p[2])) {
            throw std::invalid_argument("IFC coordinates must be finite");
          }
          const double sx = p[0] * scale, sy = -p[2] * scale, sz = p[1] * scale;
          if (!std::isfinite(sx) || !std::isfinite(sy) || !std::isfinite(sz)) {
            throw std::invalid_argument("IFC coordinates must be finite");
          }
          arc_pts_zup.push_back({sx, sy, sz});
        }

        std::vector<SolvedArcSeg> solved_segs;
        solved_segs.reserve(cs.arc->segments.size());
        bool valid_arc = true;
        for (const auto& s : cs.arc->segments) {
          if (s[0] >= arc_pts_zup.size() || s[1] >= arc_pts_zup.size() ||
              s[2] >= arc_pts_zup.size()) {
            valid_arc = false;
            break;
          }
          auto solved =
              solve_ifc2x3_arc_segment(arc_pts_zup[s[0]], arc_pts_zup[s[1]], arc_pts_zup[s[2]]);
          if (!solved) {
            valid_arc = false;
            break;
          }
          solved_segs.push_back(*solved);
        }

        auto emit_circle = [&](const SolvedArcSeg& seg) -> int {
          const int c_id = next_id();
          ss << "#" << c_id << "=IFCCARTESIANPOINT(" << fixed6_vec3(seg.center) << ");\r\n";
          const int z_id = next_id();
          ss << "#" << z_id << "=IFCDIRECTION(" << fixed6_vec3(seg.axis_z) << ");\r\n";
          const int x_id = next_id();
          ss << "#" << x_id << "=IFCDIRECTION(" << fixed6_vec3(seg.ref_x) << ");\r\n";
          const int pl_id = next_id();
          ss << "#" << pl_id << "=IFCAXIS2PLACEMENT3D(#" << c_id << ",#" << z_id << ",#" << x_id
             << ");\r\n";
          const int circ_id = next_id();
          ss << "#" << circ_id << "=IFCCIRCLE(#" << pl_id << "," << fixed6_str(seg.radius)
             << ");\r\n";
          return circ_id;
        };

        if (valid_arc && solved_segs.size() == 1) {
          const int circ_id = emit_circle(solved_segs[0]);
          curve_id = next_id();
          ss << "#" << curve_id << "=IFCTRIMMEDCURVE(#" << circ_id
             << ",(IFCPARAMETERVALUE(0.0)),(IFCPARAMETERVALUE(" << fixed6_str(solved_segs[0].sweep)
             << ")),.T.,.PARAMETER.);\r\n";
        } else if (valid_arc && solved_segs.size() == 2 &&
                   cs.arc->segments[0] == std::array<std::size_t, 3>{0, 1, 2} &&
                   cs.arc->segments[1] == std::array<std::size_t, 3>{2, 3, 0} &&
                   std::abs(solved_segs[0].radius - solved_segs[1].radius) <=
                       1e-3 * std::max(1.0, solved_segs[0].radius)) {
          const double dcx = solved_segs[0].center[0] - solved_segs[1].center[0];
          const double dcy = solved_segs[0].center[1] - solved_segs[1].center[1];
          const double dcz = solved_segs[0].center[2] - solved_segs[1].center[2];
          if (std::sqrt(dcx * dcx + dcy * dcy + dcz * dcz) <=
              1e-3 * std::max(1.0, solved_segs[0].radius)) {
            curve_id = emit_circle(solved_segs[0]);
          }
        }
        if (valid_arc && curve_id == 0 && solved_segs.size() >= 2) {
          std::vector<int> comp_seg_ids;
          comp_seg_ids.reserve(solved_segs.size());
          for (const auto& seg : solved_segs) {
            const int circ_id = emit_circle(seg);
            const int trim_id = next_id();
            ss << "#" << trim_id << "=IFCTRIMMEDCURVE(#" << circ_id
               << ",(IFCPARAMETERVALUE(0.0)),(IFCPARAMETERVALUE(" << fixed6_str(seg.sweep)
               << ")),.T.,.PARAMETER.);\r\n";
            const int cseg_id = next_id();
            ss << "#" << cseg_id << "=IFCCOMPOSITECURVESEGMENT(.CONTINUOUS.,.T.,#" << trim_id
               << ");\r\n";
            comp_seg_ids.push_back(cseg_id);
          }
          curve_id = next_id();
          ss << "#" << curve_id << "=IFCCOMPOSITECURVE((";
          for (std::size_t i = 0; i < comp_seg_ids.size(); ++i) {
            if (i > 0) ss << ",";
            ss << "#" << comp_seg_ids[i];
          }
          ss << "),.F.);\r\n";
        }
      }
    }
    if (curve_id == 0) {
      std::vector<std::string> coords;
      coords.reserve(cs.points_m.size() + (cs.closed ? 1 : 0));
      for (const auto& p : cs.points_m) {
        coords.push_back(ifc_coord(p[0], p[1], p[2], scale));
      }
      if (cs.closed) coords.push_back(coords.front());

      std::vector<int> pt_ids;
      pt_ids.reserve(coords.size());
      for (const auto& c : coords) {
        const int pt_id = next_id();
        ss << "#" << pt_id << "=IFCCARTESIANPOINT(" << c << ");\r\n";
        pt_ids.push_back(pt_id);
      }
      curve_id = next_id();
      ss << "#" << curve_id << "=IFCPOLYLINE((";
      for (std::size_t i = 0; i < pt_ids.size(); ++i) {
        if (i > 0) ss << ",";
        ss << "#" << pt_ids[i];
      }
      ss << "));\r\n";
    }

    const int curve_set_id = next_id();
    ss << "#" << curve_set_id << "=IFCGEOMETRICCURVESET((#" << curve_id << "));\r\n";

    add_layer_item(cs.layer, curve_id);

    const std::string ann_name = sanitize_name(cs.name.empty() ? "Curve" : cs.name);
    const int ann_rep_id = next_id();
    ss << "#" << ann_rep_id << "=IFCSHAPEREPRESENTATION(#" << ann_ctx_id
       << ",'Annotation','GeometricCurveSet',(#" << curve_set_id << "));\r\n";
    const int ann_shape_id = next_id();
    ss << "#" << ann_shape_id << "=IFCPRODUCTDEFINITIONSHAPE($,$,(#" << ann_rep_id << "));\r\n";
    const int ann_placement_id = next_id();
    ss << "#" << ann_placement_id << "=IFCLOCALPLACEMENT(#" << storey_placement_id << ",#"
       << axis_placement_id << ");\r\n";
    const int ann_id = next_id();
    ss << "#" << ann_id << "=IFCANNOTATION('" << generate_ifc_guid() << "',#" << owner_hist_id
       << ",'" << ann_name << "',$,$,#" << ann_placement_id << ",#" << ann_shape_id << ");\r\n";

    for (const auto& [dict_name, entries] : cs.attribute_dictionaries) {
      write_pset(ss, next_id, owner_hist_id, ann_id, "Pset_" + dict_name, entries);
    }
    product_ids.push_back(ann_id);
  }

  for (const auto& prim : scene.glb_primitives) {
    if (prim.unpainted_back) continue;
    size_t tri_count = prim.indices.size() / 3;
    size_t v_count = prim.positions.size() / 3;
    if (tri_count == 0 || v_count == 0) continue;

    auto meta_it = scene.mesh_index.find(prim.geom_name);
    // prim.geom_name is an internal lookup key (mesh index + hierarchy
    // path + layer, e.g. "mesh_3115_ROOT__Component_6205261_Layer0") -
    // never the element's real name. meta.name is the actual SketchUp
    // instance name (or SketchUp's own "Component_<id>" default when
    // nobody renamed it) - use that for both classification and the
    // element's IFC Name, falling back to the internal key only if a
    // primitive somehow has no mesh_index entry.
    const std::string& raw_name_src =
        (meta_it != scene.mesh_index.end() && !meta_it->second.name.empty()) ? meta_it->second.name
                                                                             : prim.geom_name;
    size_t ns = raw_name_src.find_first_not_of(" \t\r\n");
    std::string raw_name =
        ns == std::string::npos
            ? "Unnamed"
            : raw_name_src.substr(ns, raw_name_src.find_last_not_of(" \t\r\n") - ns + 1);
    std::string display_name = sanitize_name(raw_name);
    std::string raw_layer = meta_it != scene.mesh_index.end() ? meta_it->second.layer : "";
    size_t ls = raw_layer.find_first_not_of(" \t\r\n");
    std::string classify_layer =
        ls == std::string::npos
            ? "Layer0"
            : raw_layer.substr(ls, raw_layer.find_last_not_of(" \t\r\n") - ls + 1);
    std::string path_name = meta_it != scene.mesh_index.end() ? meta_it->second.path : "";

    auto [step_type, _] = classify(raw_name, classify_layer, path_name);

    // scene.glb_primitives positions are baked in glTF's Y-up convention
    // (glTF.y = SketchUp Z/height, glTF.z = -SketchUp Y/depth) - correct
    // for GLB export, but IFC (like SketchUp itself) is Z-up, so it has
    // to be converted back rather than passed through raw, or the
    // exported building comes out rotated ~90 degrees and mirrored.
    int face_set_id = 0;
    const char* body_rep_type = "Tessellation";
    if (!is_ifc2x3) {
      std::string pt_coords;
      pt_coords.reserve(v_count * 36);
      for (size_t i = 0; i < v_count; ++i) {
        if (i > 0) pt_coords.push_back(',');
        append_ifc_coord(pt_coords, prim.positions[i * 3], prim.positions[i * 3 + 1],
                         prim.positions[i * 3 + 2], scale);
      }

      int pt_list_id = next_id();
      ss << "#" << pt_list_id << "=IFCCARTESIANPOINTLIST3D((" << pt_coords << "));\r\n";

      std::ostringstream face_ss;
      for (size_t i = 0; i < tri_count; ++i) {
        if (i > 0) face_ss << ",";
        face_ss << "(" << (prim.indices[i * 3] + 1) << "," << (prim.indices[i * 3 + 1] + 1) << ","
                << (prim.indices[i * 3 + 2] + 1) << ")";
      }

      const char* closed = mesh_is_closed(prim) ? ".T." : ".F.";
      face_set_id = next_id();
      ss << "#" << face_set_id << "=IFCTRIANGULATEDFACESET(#" << pt_list_id << ",$," << closed
         << ",(" << face_ss.str() << "),$);\r\n";
    } else {
      std::unordered_map<std::string, std::uint32_t> coord_to_uid;
      coord_to_uid.reserve(v_count);
      std::vector<std::string> unique_coords;
      unique_coords.reserve(v_count);
      std::vector<std::uint32_t> v_to_uid(v_count, 0);
      for (size_t i = 0; i < v_count; ++i) {
        std::string c_str = ifc_coord(prim.positions[i * 3], prim.positions[i * 3 + 1],
                                      prim.positions[i * 3 + 2], scale);
        auto [it, inserted] =
            coord_to_uid.emplace(c_str, static_cast<std::uint32_t>(unique_coords.size()));
        if (inserted) {
          unique_coords.push_back(std::move(c_str));
        }
        v_to_uid[i] = it->second;
      }

      std::vector<std::array<std::uint32_t, 3>> valid_tris;
      valid_tris.reserve(tri_count);
      bool out_of_range = false;
      for (size_t i = 0; i < tri_count; ++i) {
        const std::uint32_t i0 = prim.indices[i * 3];
        const std::uint32_t i1 = prim.indices[i * 3 + 1];
        const std::uint32_t i2 = prim.indices[i * 3 + 2];
        if (i0 >= v_count || i1 >= v_count || i2 >= v_count) {
          out_of_range = true;
          continue;
        }
        const std::uint32_t u0 = v_to_uid[i0];
        const std::uint32_t u1 = v_to_uid[i1];
        const std::uint32_t u2 = v_to_uid[i2];
        if (u0 == u1 || u1 == u2 || u2 == u0) continue;
        valid_tris.push_back({u0, u1, u2});
      }
      if (valid_tris.empty()) continue;

      std::vector<int> used_pt_id(unique_coords.size(), 0);
      for (const auto& tri : valid_tris) {
        for (const std::uint32_t uid : tri) {
          if (used_pt_id[uid] == 0) {
            const int pt_id = next_id();
            ss << "#" << pt_id << "=IFCCARTESIANPOINT(" << unique_coords[uid] << ");\r\n";
            used_pt_id[uid] = pt_id;
          }
        }
      }

      std::vector<int> face_ids;
      face_ids.reserve(valid_tris.size());
      std::unordered_map<std::uint64_t, std::uint32_t> half_edges;
      half_edges.reserve(valid_tris.size() * 3);
      for (const auto& [u0, u1, u2] : valid_tris) {
        for (const auto& [ha, hb] : std::array<std::pair<std::uint32_t, std::uint32_t>, 3>{
                 {{u0, u1}, {u1, u2}, {u2, u0}}}) {
          ++half_edges[(static_cast<std::uint64_t>(ha) << 32) | hb];
        }
        const int loop_id = next_id();
        ss << "#" << loop_id << "=IFCPOLYLOOP((#" << used_pt_id[u0] << ",#" << used_pt_id[u1]
           << ",#" << used_pt_id[u2] << "));\r\n";
        const int bound_id = next_id();
        ss << "#" << bound_id << "=IFCFACEOUTERBOUND(#" << loop_id << ",.T.);\r\n";
        const int face_id = next_id();
        ss << "#" << face_id << "=IFCFACE((#" << bound_id << "));\r\n";
        face_ids.push_back(face_id);
      }

      bool is_closed_solid = !out_of_range && valid_tris.size() >= 4;
      if (is_closed_solid) {
        for (const auto& [hk, cnt] : half_edges) {
          const std::uint32_t ha = static_cast<std::uint32_t>(hk >> 32);
          const std::uint32_t hb = static_cast<std::uint32_t>(hk & 0xFFFFFFFFu);
          const auto rev_it = half_edges.find((static_cast<std::uint64_t>(hb) << 32) | ha);
          if (cnt != 1 || rev_it == half_edges.end() || rev_it->second != 1) {
            is_closed_solid = false;
            break;
          }
        }
      }

      std::ostringstream fref_ss;
      for (std::size_t i = 0; i < face_ids.size(); ++i) {
        if (i > 0) fref_ss << ",";
        fref_ss << "#" << face_ids[i];
      }
      if (is_closed_solid) {
        const int shell_id = next_id();
        ss << "#" << shell_id << "=IFCCLOSEDSHELL((" << fref_ss.str() << "));\r\n";
        face_set_id = next_id();
        ss << "#" << face_set_id << "=IFCFACETEDBREP(#" << shell_id << ");\r\n";
        body_rep_type = "Brep";
      } else {
        const int cfs_id = next_id();
        ss << "#" << cfs_id << "=IFCCONNECTEDFACESET((" << fref_ss.str() << "));\r\n";
        face_set_id = next_id();
        ss << "#" << face_set_id << "=IFCFACEBASEDSURFACEMODEL((#" << cfs_id << "));\r\n";
        body_rep_type = "SurfaceModel";
      }
    }

    add_layer_item(raw_layer, face_set_id);

    auto [r, g, b, a] = get_prim_rgb(scene, prim.material_index);
    std::ostringstream key_ss;
    key_ss.imbue(std::locale::classic());
    key_ss << std::fixed << std::setprecision(4) << r << "," << g << "," << b << "," << a;
    std::string rgba_key = key_ss.str();

    int style_assign_id;
    auto style_it = mat_style_cache.find(rgba_key);
    if (style_it == mat_style_cache.end()) {
      int col_id = next_id();
      ss << std::fixed << std::setprecision(4);
      ss << "#" << col_id << "=IFCCOLOURRGB($, " << r << "," << g << "," << b << ");\r\n";

      double transparency = 1.0 - a;
      int rendering_id = next_id();
      ss << "#" << rendering_id << "=IFCSURFACESTYLERENDERING(#" << col_id << "," << transparency
         << ",$,$,$,$,$,$,.FLAT.);\r\n";

      int style_id = next_id();
      ss << "#" << style_id << "=IFCSURFACESTYLE('" << display_name << "_Material',.BOTH.,(#"
         << rendering_id << "));\r\n";

      style_assign_id = next_id();
      ss << "#" << style_assign_id << "=IFCPRESENTATIONSTYLEASSIGNMENT((#" << style_id << "));\r\n";
      mat_style_cache[rgba_key] = style_assign_id;
    } else {
      style_assign_id = style_it->second;
    }

    int styled_item_id = next_id();
    ss << "#" << styled_item_id << "=IFCSTYLEDITEM(#" << face_set_id << ",(#" << style_assign_id
       << "),$);\r\n";

    int shape_rep_id = next_id();
    ss << "#" << shape_rep_id << "=IFCSHAPEREPRESENTATION(#" << geom_ctx_id << ",'Body','"
       << body_rep_type << "',(#" << face_set_id << "));\r\n";

    int prod_shape_id = next_id();
    ss << "#" << prod_shape_id << "=IFCPRODUCTDEFINITIONSHAPE($,$,(#" << shape_rep_id << "));\r\n";

    int prod_placement_id = next_id();
    ss << "#" << prod_placement_id << "=IFCLOCALPLACEMENT(#" << storey_placement_id << ",#"
       << axis_placement_id << ");\r\n";

    int product_id = next_id();
    std::vector<std::string> attrs{
        "'" + generate_ifc_guid() + "'",
        "#" + std::to_string(owner_hist_id),
        "'" + display_name + "'",
        "$",
        "$",
        "#" + std::to_string(prod_placement_id),
        "#" + std::to_string(prod_shape_id),
        "$",
    };
    const auto declared = ifc_attr_count(schema_str, step_type);
    const std::size_t total =
        std::max(attrs.size(), (declared && *declared > 0) ? *declared : std::size_t{9});
    attrs.resize(total, "$");
    if (is_ifc2x3) {
      if (step_type == "IFCBUILDINGELEMENTPROXY" && total == 9) {
        attrs[8] = ".ELEMENT.";
      } else if (needs_ifc2x3_notdefined_attr8(step_type) && total >= 9) {
        attrs[8] = ".NOTDEFINED.";
      }
    } else if (step_type == "IFCBUILDINGELEMENTPROXY" && total == 9) {
      attrs[8] = ".NOTDEFINED.";
    }
    ss << "#" << product_id << "=" << step_type << "(";
    for (std::size_t i = 0; i < attrs.size(); ++i) {
      if (i > 0) ss << ",";
      ss << attrs[i];
    }
    ss << ");\r\n";
    product_ids.push_back(product_id);

    if (meta_it != scene.mesh_index.end()) {
      write_pset(ss, next_id, owner_hist_id, product_id, "Pset_CustomProperties",
                 meta_it->second.properties);

      // Any OTHER attribute dictionaries the instance carries (third-party
      // BIM/steel-detailing plugins, etc. - see MeshMetadata::
      // attribute_dictionaries) - each becomes its own named property set
      // instead of being merged into Pset_CustomProperties, since these
      // come from a distinct source and commonly share key names with
      // each other (e.g. multiple plugins using "name").
      for (const auto& [dict_name, entries] : meta_it->second.attribute_dictionaries) {
        write_pset(ss, next_id, owner_hist_id, product_id, "Pset_" + dict_name, entries);
      }
    }
  }

  // Presentation Layer Assignments (preserve layers and their on/off
  // state). IFCPRESENTATIONLAYERWITHSTYLE - not the plain
  // IFCPRESENTATIONLAYERASSIGNMENT neither SketchUp's own IFC exporter
  // nor the IFC-manager SketchUp extension use - is the only IFC4 entity
  // that can carry a layer's visibility at all (LayerOn); every other
  // attribute here besides Name and LayerOn is left unset/false since
  // this project doesn't track them (freeze/block/layer-level styles).
  for (const auto& [l_name, entry] : layer_items) {
    const auto& [raw_name, item_ids] = entry;
    if (!item_ids.empty()) {
      std::ostringstream item_ss;
      for (size_t i = 0; i < item_ids.size(); ++i) {
        if (i > 0) item_ss << ",";
        item_ss << "#" << item_ids[i];
      }
      auto hidden_it = scene.layer_hidden.find(raw_name);
      const char* layer_on =
          (hidden_it != scene.layer_hidden.end() && hidden_it->second) ? ".F." : ".T.";
      ss << "#" << next_id() << "=IFCPRESENTATIONLAYERWITHSTYLE('" << l_name << "',$,("
         << item_ss.str() << "),$," << layer_on << ",.F.,.F.,());\r\n";
    }
  }

  if (!product_ids.empty()) {
    std::ostringstream prod_ss;
    for (size_t i = 0; i < product_ids.size(); ++i) {
      if (i > 0) prod_ss << ",";
      prod_ss << "#" << product_ids[i];
    }
    ss << "#" << next_id() << "=IFCRELCONTAINEDINSPATIALSTRUCTURE('" << generate_ifc_guid() << "',#"
       << owner_hist_id << ",$,$,(" << prod_ss.str() << "),#" << storey_id << ");\r\n";
  }

  ss << "ENDSEC;\r\n";
  ss << "END-ISO-10303-21;\r\n";
  return ss.str();
}

void export_ifc(const Scene& scene, const std::filesystem::path& path, double scale,
                const std::string& schema, const IfcClassifier& classifier,
                bool classify_using_full_path) {
  std::string text = to_ifc(scene, scale, schema, classifier, classify_using_full_path);
  if (path.has_parent_path()) {
    std::filesystem::create_directories(path.parent_path());
  }
  std::ofstream file(path, std::ios::out | std::ios::binary);
  if (!file.is_open()) {
    throw std::runtime_error("Failed to open file for writing: " + path.string());
  }
  file.write(text.data(), text.size());
}

}  // namespace openskp
