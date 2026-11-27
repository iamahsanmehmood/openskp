#include <algorithm>
#include <cmath>
#include <functional>
#include <optional>
#include <set>

#include "internal.hpp"

// VFF (2021+) scenes ("pages") and linear dimensions. Ported from Python's
// _core.py (PR #190) - see that module's _scan_vertex_positions /
// _scan_instance_transforms / _parse_dimensions / _find_page_node /
// _parse_pages for the byte-format details this file mirrors.

namespace openskp {
namespace {

// ── flat TLV, integer tags ──────────────────────────────────────────────
// A second, deliberately separate flat-TLV reader from parse_flat/flat_find
// (geometry.cpp): those use the STRING byte-order tag convention the main
// tree already relies on (e.g. "C409"), while the sub-records this feature
// reads (5208, 520A, 53FC, 5BCD, ...) are most directly and safely ported
// from Python's own _tlv_items/_tlv_find (which read the tag as a
// little-endian uint16) by keeping the SAME integer convention here,
// copying Python's numeric constants byte-for-byte rather than
// hand-converting each one to the swapped string form. Every payload is
// copied (ByteBuffer by value) rather than referenced by view: C++17 has
// no std::span, and a pointer into a temporary vector's element would
// dangle the moment that vector goes out of scope - the payloads involved
// here (leaf tags, floats, small ids) are small enough that copying costs
// nothing that matters.

struct FlatTlvItem {
  std::uint16_t tag;
  ByteBuffer payload;
};

std::vector<FlatTlvItem> tlv_items_int(const ByteBuffer& buf) {
  std::vector<FlatTlvItem> items;
  std::size_t off = 0;
  std::size_t n = buf.size();
  while (off < n) {
    if (off + 6 > n) return {};
    std::uint16_t tag = static_cast<std::uint16_t>(buf[off] | (buf[off + 1] << 8));
    auto ln = read_u32(buf, off + 2);
    if (tag == 0 || off + 6 + ln > n) return {};
    items.push_back({tag, ByteBuffer(buf.begin() + off + 6, buf.begin() + off + 6 + ln)});
    off += 6 + ln;
  }
  return items;
}

std::optional<ByteBuffer> tlv_find_int(const std::vector<FlatTlvItem>& items, std::uint16_t tag) {
  for (auto& it : items) {
    if (it.tag == tag) return it.payload;
  }
  return std::nullopt;
}

// 53FE stores SketchUp's instance path as repeated (u8 length, id bytes),
// outer placement first. A root-level dimension on nested groups names the
// cabinet AND each inner instance; the vertex is in the innermost definition.
std::vector<std::string> length_prefixed_ids(const ByteBuffer& p) {
  std::vector<std::string> ids;
  std::size_t off = 0;
  while (off < p.size()) {
    const std::uint8_t ln = p[off];
    if (ln == 0 || off + 1 + ln > p.size()) break;
    ids.push_back(bytes_to_upper_hex(ByteBuffer(p.begin() + off + 1, p.begin() + off + 1 + ln)));
    off += 1 + ln;
  }
  return ids;
}

ByteBuffer strip_de05(const ByteBuffer& p) {
  if (p.size() >= 6 && p[0] == 0xde && p[1] == 0x05) {
    auto idlen = read_u32(p, 2);
    if (idlen <= p.size() - 6) return ByteBuffer(p.begin() + 6, p.begin() + 6 + idlen);
  }
  return p;
}

const TlvNode* find_child_tag(const std::vector<TlvNode>& nodes, const std::string& target) {
  for (auto& n : nodes) {
    if (n.tag == target) return &n;
    if (auto* r = find_child_tag(n.children, target)) return r;
  }
  return nullptr;
}

std::optional<Vec3> vec3_of(const std::optional<ByteBuffer>& p) {
  if (p && p->size() == 24) return Vec3{read_f64(*p, 0), read_f64(*p, 8), read_f64(*p, 16)};
  return std::nullopt;
}

using PathResolverFn = std::function<std::vector<double>(const std::vector<std::string>&)>;
using FacePointResolverFn = std::function<std::optional<Vec3>(EntityId, double, double, double,
                                                              const std::vector<std::string>&)>;

std::optional<Vec3> vff_face_uv_point(const GeometryBuilder* builder, EntityId face_id, double c0,
                                      double c1, double c2) {
  if (!builder) return std::nullopt;
  if (!std::isfinite(c0) || !std::isfinite(c1) || !std::isfinite(c2)) return std::nullopt;
  const auto fit = builder->faces.find(face_id);
  if (fit == builder->faces.end() || fit->second.loops.empty() || fit->second.loops[0].size() < 3) {
    return std::nullopt;
  }
  const Vec3& normal = fit->second.normal;
  if (!std::isfinite(normal[0]) || !std::isfinite(normal[1]) || !std::isfinite(normal[2])) {
    return std::nullopt;
  }
  const double n_len =
      std::sqrt(normal[0] * normal[0] + normal[1] * normal[1] + normal[2] * normal[2]);
  if (!std::isfinite(n_len) || n_len < 1e-12) return std::nullopt;
  const double nhx = normal[0] / n_len;
  const double nhy = normal[1] / n_len;
  const double nhz = normal[2] / n_len;
  std::vector<Vec3> pts;
  pts.reserve(fit->second.loops[0].size());
  for (const auto& co : fit->second.loops[0]) {
    const auto eit = builder->edges.find(co.edge_id);
    if (eit == builder->edges.end()) return std::nullopt;
    const auto vid = co.orientation >= 0 ? eit->second.first : eit->second.second;
    if (!vid) return std::nullopt;
    const auto vit = builder->vertices.find(*vid);
    if (vit == builder->vertices.end()) return std::nullopt;
    const Vec3& vpt = vit->second;
    if (!std::isfinite(vpt[0]) || !std::isfinite(vpt[1]) || !std::isfinite(vpt[2])) {
      return std::nullopt;
    }
    pts.push_back(vpt);
  }
  if (pts.size() < 3) return std::nullopt;
  const double p0x = pts[0][0];
  const double p0y = pts[0][1];
  const double p0z = pts[0][2];
  double uhx = 0.0;
  double uhy = 0.0;
  double uhz = 0.0;
  bool found_u = false;
  for (std::size_t i = 1; i < pts.size(); ++i) {
    const double dx = pts[i][0] - p0x;
    const double dy = pts[i][1] - p0y;
    const double dz = pts[i][2] - p0z;
    const double u_len = std::sqrt(dx * dx + dy * dy + dz * dz);
    if (std::isfinite(u_len) && u_len >= 1e-12) {
      uhx = dx / u_len;
      uhy = dy / u_len;
      uhz = dz / u_len;
      found_u = true;
      break;
    }
  }
  if (!found_u) return std::nullopt;
  const double vhx = nhy * uhz - nhz * uhy;
  const double vhy = nhz * uhx - nhx * uhz;
  const double vhz = nhx * uhy - nhy * uhx;
  double u_min = 0.0;
  double u_max = 0.0;
  double v_min = 0.0;
  double v_max = 0.0;
  for (std::size_t i = 1; i < pts.size(); ++i) {
    const double dx = pts[i][0] - p0x;
    const double dy = pts[i][1] - p0y;
    const double dz = pts[i][2] - p0z;
    const double du = dx * uhx + dy * uhy + dz * uhz;
    const double dv = dx * vhx + dy * vhy + dz * vhz;
    if (du < u_min) u_min = du;
    if (du > u_max) u_max = du;
    if (dv < v_min) v_min = dv;
    if (dv > v_max) v_max = dv;
  }
  const double su = c0 * (u_max - u_min);
  const double sv = c1 * (v_max - v_min);
  const double rx = p0x + su * uhx + sv * vhx + c2 * nhx;
  const double ry = p0y + su * uhy + sv * vhy + c2 * nhy;
  const double rz = p0z + su * uhz + sv * vhz + c2 * nhz;
  if (!std::isfinite(rx) || !std::isfinite(ry) || !std::isfinite(rz)) return std::nullopt;
  return Vec3{rx, ry, rz};
}

PathResolverFn map_path_resolver(const std::map<std::string, std::vector<double>>& inst_world) {
  return [&inst_world](const std::vector<std::string>& path) -> std::vector<double> {
    for (auto it = path.rbegin(); it != path.rend(); ++it) {
      auto w_it = inst_world.find(*it);
      if (w_it != inst_world.end() && !w_it->second.empty()) {
        return w_it->second;
      }
    }
    return {};
  };
}

std::optional<Vec3> dimension_connection_point(const std::optional<ByteBuffer>& block_payload,
                                               const std::map<std::string, Vec3>& id2pos,
                                               const PathResolverFn& resolve_path,
                                               const FacePointResolverFn& resolve_face = {}) {
  if (!block_payload) return std::nullopt;
  auto blk_items = tlv_items_int(*block_payload);
  auto blk = tlv_find_int(blk_items, 0x5208);
  if (!blk) return std::nullopt;
  auto sub = tlv_items_int(*blk);
  auto typ_b = tlv_find_int(sub, 0x5209);
  std::optional<std::uint32_t> typ;
  if (typ_b && typ_b->size() == 4) typ = read_u32(*typ_b, 0);
  if (typ == 1u) {
    auto pos = tlv_find_int(sub, 0x520a);
    return vec3_of(pos);
  }
  auto ref_b = tlv_find_int(sub, 0x520b);
  std::optional<ByteBuffer> f53fc;
  if (ref_b) {
    auto ref_items = tlv_items_int(*ref_b);
    f53fc = tlv_find_int(ref_items, 0x53fc);
  }
  std::vector<FlatTlvItem> fi;
  if (f53fc) fi = tlv_items_int(*f53fc);
  auto vid = tlv_find_int(fi, 0x53fd);
  auto iid = tlv_find_int(fi, 0x53fe);
  if (!vid) return std::nullopt;
  if (typ == 7u && resolve_face) {
    auto pos = tlv_find_int(sub, 0x520a);
    if (!pos || pos->size() != 24 || vid->empty() || vid->size() > 8) return std::nullopt;
    const auto face_id = static_cast<EntityId>(parse_varint(*vid, 0, vid->size()));
    std::vector<std::string> path;
    if (iid && !iid->empty()) {
      path = length_prefixed_ids(*iid);
      if (path.empty()) path.push_back(bytes_to_upper_hex(*iid));
    }
    return resolve_face(face_id, read_f64(*pos, 0), read_f64(*pos, 8), read_f64(*pos, 16), path);
  }
  auto local_it = id2pos.find(bytes_to_upper_hex(*vid));
  if (local_it == id2pos.end()) return std::nullopt;
  if (iid && !iid->empty()) {
    auto path = length_prefixed_ids(*iid);
    if (path.empty()) path.push_back(bytes_to_upper_hex(*iid));
    auto w = resolve_path(path);
    if (!w.empty()) {
      return transform_point(w, local_it->second);
    }
  }
  return local_it->second;
}

// Header-only check that buf[begin, end) is the same well-formed flat TLV
// tlv_items_int accepts, carrying both dimension ends (5BCD, 5BCE). The
// "cc 5b" byte search hits mesh/float data far more often than real 5BCC
// records, and those false hits can claim lengths of hundreds of MB; this
// rejects them before anything is copied.
bool flat_tlv_has_fields(const ByteBuffer& buf, std::size_t begin, std::size_t end,
                         std::uint16_t first = 0x5bcd, std::uint16_t second = 0x5bce) {
  bool has_first = false, has_second = false;
  std::size_t off = begin;
  while (off < end) {
    if (off + 6 > end) return false;
    const auto tag = static_cast<std::uint16_t>(buf[off] | (buf[off + 1] << 8));
    const auto ln = read_u32(buf, off + 2);
    if (tag == 0 || ln > end - off - 6) return false;
    if (tag == first) has_first = true;
    if (tag == second) has_second = true;
    off += 6 + ln;
  }
  return has_first && has_second;
}

std::uint64_t drawing_pid(const std::vector<FlatTlvItem>& fields) {
  const auto base = tlv_find_int(fields, 0x05dc);
  if (!base) return 0;
  const auto id = tlv_find_int(tlv_items_int(*base), 0x05de);
  return id && !id->empty() && id->size() <= 8 ? parse_varint(*id, 0, id->size()) : 0;
}

std::optional<RawDimension> parse_one_5bcc(const ByteBuffer& model_dat, std::size_t j,
                                           const std::map<std::string, Vec3>& id2pos,
                                           const PathResolverFn& resolve_path) {
  const std::size_t n = model_dat.size();
  if (j + 6 > n) return std::nullopt;
  auto ln = read_u32(model_dat, j + 2);
  if (ln < 40 || j + 6 + ln > n) return std::nullopt;
  if (!flat_tlv_has_fields(model_dat, j + 6, j + 6 + ln)) return std::nullopt;
  ByteBuffer body_bytes(model_dat.begin() + j + 6, model_dat.begin() + j + 6 + ln);
  auto body = tlv_items_int(body_bytes);
  if (body.empty()) return std::nullopt;
  bool has_first = false, has_second = false;
  for (auto& it : body) {
    if (it.tag == 0x5bcd) has_first = true;
    if (it.tag == 0x5bce) has_second = true;
  }
  if (!has_first || !has_second) return std::nullopt;
  auto a = dimension_connection_point(tlv_find_int(body, 0x5bcd), id2pos, resolve_path);
  auto b = dimension_connection_point(tlv_find_int(body, 0x5bce), id2pos, resolve_path);
  if (!a || !b) return std::nullopt;
  RawDimension d;
  d.a = *a;
  d.b = *b;
  // Dimension base (59D8) owns the drawing properties. Connection targets may
  // contain other entities: never search those for the dimension's tag/flags.
  if (const auto base = tlv_find_int(body, 0x59d8)) {
    if (const auto draw = tlv_find_int(tlv_items_int(*base), 0x07d0)) {
      const auto properties = tlv_items_int(*draw);
      d.native_pid = drawing_pid(properties);
      if (const auto layer = tlv_find_int(properties, 0x07d2); layer && !layer->empty())
        d.layer = std::to_string(parse_varint(*layer, 0, layer->size()));
      if (const auto flags = tlv_find_int(properties, 0x07d3); flags && !flags->empty())
        d.hidden = ((*flags)[0] & 0x01) != 0;
    }
  }
  d.plane_x = vec3_of(tlv_find_int(body, 0x5bcf));
  d.normal = vec3_of(tlv_find_int(body, 0x5bd0));
  auto off_b = tlv_find_int(body, 0x5bd2);
  d.offset = (off_b && off_b->size() == 8) ? read_f64(*off_b, 0) : 0.0;
  // VFF 5BCF is the plane normal, 5BD0 its X axis, and 5BD1 the
  // constrained axis. Retain the historical public frame fields, but resolve
  // placement explicitly so clients need not guess the side from endpoints.
  const auto axis_bytes = tlv_find_int(body, 0x5bd1);
  if (axis_bytes && axis_bytes->size() == 4 && d.plane_x && d.normal) {
    const auto axis = read_u32(*axis_bytes, 0);
    const Vec3& normal = *d.plane_x;
    const Vec3& x = *d.normal;
    if (axis == 1) {
      d.offset_vector = Vec3{(normal[1] * x[2] - normal[2] * x[1]) * d.offset,
                             (normal[2] * x[0] - normal[0] * x[2]) * d.offset,
                             (normal[0] * x[1] - normal[1] * x[0]) * d.offset};
    } else if (axis == 2) {
      d.offset_vector = Vec3{-x[0] * d.offset, -x[1] * d.offset, -x[2] * d.offset};
    }
  }
  return d;
}

std::optional<TextEntity> parse_text(const ByteBuffer& model_dat, std::size_t j,
                                     const std::map<std::string, Vec3>& id2pos,
                                     const PathResolverFn& resolve_path,
                                     const FacePointResolverFn& resolve_face = {}) {
  const auto size = read_u32(model_dat, j + 2);
  const auto fields =
      tlv_items_int(ByteBuffer(model_dat.begin() + j + 6, model_dat.begin() + j + 6 + size));
  const auto value = tlv_find_int(fields, 0x55f1);
  const auto connection = tlv_find_int(fields, 0x55f4);
  if (!value || !connection) return std::nullopt;
  TextEntity text;
  text.text.assign(value->begin(), value->end());
  text.point = dimension_connection_point(connection, id2pos, resolve_path, resolve_face);
  text.label_point = vec3_of(tlv_find_int(fields, 0x55f5));
  const auto x = tlv_find_int(fields, 0x55f2), y = tlv_find_int(fields, 0x55f3);
  if (x && y && x->size() == 8 && y->size() == 8)
    text.screen_position = std::array<double, 2>{read_f64(*x, 0), read_f64(*y, 0)};
  if (const auto type = tlv_find_int(fields, 0x55fd); type && type->size() == 4)
    text.leader_type = static_cast<int>(read_u32(*type, 0));
  if (const auto arrow = tlv_find_int(fields, 0x55fa); arrow && arrow->size() == 4)
    text.arrow_type = static_cast<int>(read_u32(*arrow, 0));
  if (const auto display = tlv_find_int(fields, 0x55fc); display && display->size() == 1)
    text.display_leader = (*display)[0] != 0;
  if (const auto draw = tlv_find_int(fields, 0x07d0)) {
    const auto properties = tlv_items_int(*draw);
    text.persistent_id = drawing_pid(properties);
    if (const auto layer = tlv_find_int(properties, 0x07d2); layer && !layer->empty())
      text.layer = std::to_string(parse_varint(*layer, 0, layer->size()));
    if (const auto flags = tlv_find_int(properties, 0x07d3); flags && !flags->empty())
      text.hidden = ((*flags)[0] & 0x01) != 0;
  }
  return text;
}

Dimension public_dimension(const RawDimension& d) {
  Dimension dim;
  dim.persistent_id = d.native_pid;
  dim.a = d.a;
  dim.b = d.b;
  dim.offset = d.offset;
  dim.plane_x = d.plane_x;
  dim.normal = d.normal;
  dim.offset_vector = d.offset_vector;
  dim.layer = d.layer;
  dim.hidden = d.hidden;
  return dim;
}

struct DefSpan {
  std::size_t begin{};
  std::size_t end{};
  EntityId id{};
};

// Same first-id rule as collect_definitions: direct DE05 or DC05, then
// recurse only dictionary / entity-header containers. Walking C409/6419
// would steal a vertex or instance id.
std::optional<EntityId> entity_id_from_tlv(const ByteBuffer& md, std::size_t rec,
                                           std::size_t rec_end, int depth = 0) {
  if (depth > 24 || rec + 6 > rec_end) return std::nullopt;
  const auto kids = tlv_headers(md, rec + 6, rec_end);
  for (const auto& k : kids) {
    const std::string t = tag_at(md, k.offset);
    try {
      if (t == "DE05" && k.size > 0 && k.size <= 8) {
        return static_cast<EntityId>(parse_varint(md, k.offset + 6, k.size));
      }
      if (t == "DC05" && k.size >= 6 && md[k.offset + 6] == 0xde && md[k.offset + 7] == 5) {
        const auto z = read_u32(md, k.offset + 8);
        if (z > 0 && z <= 8 && z <= k.size - 6) {
          return static_cast<EntityId>(parse_varint(md, k.offset + 12, z));
        }
      }
    } catch (...) {
      continue;
    }
  }
  for (const auto& k : kids) {
    const std::string t = tag_at(md, k.offset);
    // Same containers collect_definitions actually walks for a 7C15 id
    // (8813 dictionaries / D007). Recursing C409/6419 would bake a vertex
    // or instance id and park 5BCC on the wrong definition.
    if (t != "8813" && t != "D007" && t != "8913" && t != "8A13" && t != "8B13" && t != "8C13" &&
        t != "8D13") {
      continue;
    }
    if (auto id = entity_id_from_tlv(md, k.offset, k.offset + 6 + k.size, depth + 1)) return id;
  }
  return std::nullopt;
}

// Only the F901 → 7017 → 7117 definition list. A raw `7c 15` hunt inside
// F601/geometry false-matches and parks model-root (or in-group) 5BCC on
// the wrong definition.
std::vector<DefSpan> scan_definition_spans(const ByteBuffer& md,
                                           const std::vector<TlvHeader>& records,
                                           const std::map<EntityId, RawDefinition>& defs) {
  std::vector<DefSpan> spans;
  for (const auto& rec : records) {
    if (tag_at(md, rec.offset) != "7C15") continue;
    auto id = entity_id_from_tlv(md, rec.offset, rec.offset + 6 + rec.size);
    if (id && defs.count(*id)) spans.push_back({rec.offset, rec.offset + 6 + rec.size, *id});
  }
  return spans;
}

std::optional<EntityId> owning_definition(const std::vector<DefSpan>& spans, std::size_t off) {
  std::optional<EntityId> best;
  std::size_t best_len = static_cast<std::size_t>(-1);
  for (const auto& s : spans) {
    if (off < s.begin || off >= s.end) continue;
    const std::size_t len = s.end - s.begin;
    if (len < best_len) {
      best_len = len;
      best = s.id;
    }
  }
  return best;
}

bool tlv_has_nonempty_tag(const ByteBuffer& buf, std::size_t begin, std::size_t end,
                          std::uint16_t tag, int depth = 0) {
  if (depth > 64) return false;
  std::size_t off = begin;
  while (off < end) {
    if (off + 6 > end) return false;
    const auto t = static_cast<std::uint16_t>(buf[off] | (buf[off + 1] << 8));
    const auto ln = read_u32(buf, off + 2);
    if (t == 0 || ln > end - off - 6) return false;
    off += 6 + ln;
  }
  for (off = begin; off < end;) {
    const auto t = static_cast<std::uint16_t>(buf[off] | (buf[off + 1] << 8));
    const auto ln = read_u32(buf, off + 2);
    if ((t == tag && ln > 0) || tlv_has_nonempty_tag(buf, off + 6, off + 6 + ln, tag, depth + 1))
      return true;
    off += 6 + ln;
  }
  return false;
}

class InstancePathResolver {
 public:
  InstancePathResolver(const RawDefinition& root,
                       const std::map<EntityId, RawDefinition>& definitions, std::size_t max_visits)
      : root_(root), definitions_(definitions), max_visits_(max_visits) {}

  std::size_t visits() const { return visits_; }

  struct PathRes {
    bool matched{false};
    std::vector<double> xf;
    std::optional<EntityId> leaf_def;
  };

  PathRes resolve_path_with_leaf(std::optional<EntityId> owner,
                                 const std::vector<std::string>& raw_path,
                                 std::optional<EntityId> fallback_owner = std::nullopt) {
    if (raw_path.empty()) return {};
    std::vector<std::string> norm;
    norm.reserve(raw_path.size());
    std::string key = owner ? std::to_string(*owner) : std::string("ROOT");
    key.push_back(':');
    key.append(fallback_owner ? std::to_string(*fallback_owner) : std::string("NONE"));
    for (const auto& part : raw_path) {
      std::string upper = to_upper_ascii(part);
      key.push_back('/');
      key.append(upper);
      norm.push_back(std::move(upper));
    }
    if (auto it = resolved_.find(key); it != resolved_.end()) return it->second;

    std::size_t query_visits = 0;
    const auto* start_builder = builder_for(owner);
    auto res = walk_exact(owner, start_builder, norm, 0, {}, 0, query_visits);
    if (res.matched) {
      resolved_.emplace(std::move(key), res);
      return res;
    }
    if (query_visits >= max_visits_) return {};

    ensure_index();
    std::set<std::optional<EntityId>> valid_holders;
    std::optional<PathRes> fb_result;
    if (auto it = holders_of_pid_.find(norm[0]); it != holders_of_pid_.end()) {
      for (const auto& holder : it->second) {
        if (holder == owner) continue;
        if (query_visits >= max_visits_) break;
        auto h_res = walk_exact(holder, builder_for(holder), norm, 0, {}, 0, query_visits);
        if (h_res.matched) {
          valid_holders.insert(holder);
          if (fallback_owner && holder == fallback_owner) fb_result = std::move(h_res);
        }
      }
    }

    if (!valid_holders.empty() && query_visits < max_visits_) {
      std::set<std::optional<EntityId>> can_reach = valid_holders;
      std::vector<EntityId> queue;
      for (const auto& h : valid_holders) {
        if (h) queue.push_back(*h);
      }
      for (std::size_t qi = 0; qi < queue.size(); ++qi) {
        const EntityId cur = queue[qi];
        auto pit = parents_of_def_.find(cur);
        if (pit == parents_of_def_.end()) continue;
        for (const auto& parent_owner : pit->second) {
          if (can_reach.insert(parent_owner).second && parent_owner) {
            queue.push_back(*parent_owner);
          }
        }
      }

      if (can_reach.count(owner)) {
        std::set<EntityId> active_defs;
        if (owner) active_defs.insert(*owner);
        std::map<EntityId, int> failed_min_depth;

        std::function<PathRes(std::optional<EntityId>, const GeometryBuilder*,
                              const std::vector<double>&, int)>
            walk_to_holder = [&](std::optional<EntityId> cur_owner,
                                 const GeometryBuilder* cur_builder,
                                 const std::vector<double>& parent_xf, int depth) -> PathRes {
          if (depth > 64 || !cur_builder || query_visits >= max_visits_) return {};
          if (depth > 0 && valid_holders.count(cur_owner) &&
              depth + static_cast<int>(norm.size()) <= 65) {
            auto tail = walk_exact(cur_owner, cur_builder, norm, 0, parent_xf, depth, query_visits);
            if (tail.matched || query_visits >= max_visits_) return tail;
          }
          for (const auto& inst : cur_builder->instances) {
            if (query_visits >= max_visits_) return {};
            if (!inst.ref_idx) continue;
            const EntityId ref_idx = *inst.ref_idx;
            if (!can_reach.count(ref_idx) || active_defs.count(ref_idx)) continue;
            if (auto fit = failed_min_depth.find(ref_idx);
                fit != failed_min_depth.end() && depth + 1 >= fit->second) {
              continue;
            }
            const auto* child_builder = builder_for(ref_idx);
            if (!child_builder) continue;
            ++visits_;
            ++query_visits;
            std::vector<double> here =
                inst.matrix.empty() ? parent_xf : multiply_matrices(parent_xf, inst.matrix);
            active_defs.insert(ref_idx);
            auto found = walk_to_holder(ref_idx, child_builder, here, depth + 1);
            active_defs.erase(ref_idx);
            if (found.matched) return found;
            if (query_visits >= max_visits_) return {};
          }
          if (cur_owner) {
            auto fit = failed_min_depth.find(*cur_owner);
            if (fit == failed_min_depth.end() || depth < fit->second) {
              failed_min_depth[*cur_owner] = depth;
            }
          }
          return {};
        };

        res = walk_to_holder(owner, start_builder, {}, 0);
      }
    }

    if (!res.matched && fallback_owner && fallback_owner != owner) {
      if (fb_result) {
        res = std::move(*fb_result);
      } else if (query_visits < max_visits_ && valid_holders.count(fallback_owner)) {
        res = walk_exact(fallback_owner, builder_for(fallback_owner), norm, 0, {}, 0, query_visits);
      }
    }

    if (query_visits < max_visits_) {
      resolved_.emplace(std::move(key), res);
    }
    return res;
  }

  std::vector<double> resolve_path(std::optional<EntityId> owner,
                                   const std::vector<std::string>& raw_path,
                                   std::optional<EntityId> fallback_owner = std::nullopt) {
    auto res = resolve_path_with_leaf(owner, raw_path, fallback_owner);
    return res.matched ? std::move(res.xf) : std::vector<double>{};
  }

  std::optional<Vec3> resolve_face_point(std::optional<EntityId> owner, EntityId face_id, double c0,
                                         double c1, double c2,
                                         const std::vector<std::string>& raw_path) {
    if (raw_path.empty()) {
      return vff_face_uv_point(builder_for(owner), face_id, c0, c1, c2);
    }
    auto res = resolve_path_with_leaf(owner, raw_path);
    if (!res.matched || !res.leaf_def) return std::nullopt;
    auto local = vff_face_uv_point(builder_for(*res.leaf_def), face_id, c0, c1, c2);
    if (!local) return std::nullopt;
    if (!res.xf.empty()) {
      const Vec3 pt = transform_point(res.xf, *local);
      if (!std::isfinite(pt[0]) || !std::isfinite(pt[1]) || !std::isfinite(pt[2])) {
        return std::nullopt;
      }
      return pt;
    }
    return local;
  }

 private:
  static std::string to_upper_ascii(const std::string& s) {
    std::string upper = s;
    for (char& ch : upper) {
      if (ch >= 'a' && ch <= 'z') ch = static_cast<char>(ch - 'a' + 'A');
    }
    return upper;
  }

  const GeometryBuilder* builder_for(std::optional<EntityId> owner) const {
    if (!owner) return &root_.builder;
    auto it = definitions_.find(*owner);
    return it != definitions_.end() ? &it->second.builder : nullptr;
  }

  const std::map<std::string, const RawInstance*>& pid_map(std::optional<EntityId> owner,
                                                           const GeometryBuilder& builder) {
    const EntityId key = owner.value_or(-1);
    if (auto it = by_pid_.find(key); it != by_pid_.end()) return it->second;
    auto& map = by_pid_[key];
    for (const auto& inst : builder.instances) {
      if (inst.persistent_id.empty()) continue;
      map[to_upper_ascii(inst.persistent_id)] = &inst;
    }
    return map;
  }

  void ensure_index() {
    if (indexed_) return;
    indexed_ = true;
    std::vector<std::optional<EntityId>> owners;
    owners.reserve(1 + definitions_.size());
    owners.push_back(std::nullopt);
    for (const auto& kv : definitions_) owners.push_back(kv.first);
    for (const auto& def_owner : owners) {
      const auto* builder = builder_for(def_owner);
      if (!builder) continue;
      std::set<std::string> seen_pids;
      std::set<EntityId> seen_refs;
      for (const auto& inst : builder->instances) {
        if (!inst.persistent_id.empty()) {
          std::string norm_pid = to_upper_ascii(inst.persistent_id);
          if (seen_pids.insert(norm_pid).second) {
            holders_of_pid_[norm_pid].push_back(def_owner);
          }
        }
        if (inst.ref_idx && definitions_.count(*inst.ref_idx) &&
            seen_refs.insert(*inst.ref_idx).second) {
          parents_of_def_[*inst.ref_idx].push_back(def_owner);
        }
      }
    }
  }

  PathRes walk_exact(std::optional<EntityId> owner, const GeometryBuilder* builder,
                     const std::vector<std::string>& path, std::size_t idx,
                     const std::vector<double>& parent_xf, int depth, std::size_t& query_visits) {
    if (depth > 64 || !builder || query_visits >= max_visits_) return {};
    const auto& map = pid_map(owner, *builder);
    auto it = map.find(path[idx]);
    if (it == map.end()) return {};
    ++visits_;
    ++query_visits;
    const RawInstance* inst = it->second;
    std::vector<double> here =
        inst->matrix.empty() ? parent_xf : multiply_matrices(parent_xf, inst->matrix);
    if (idx + 1 == path.size()) return {true, std::move(here), inst->ref_idx};
    if (!inst->ref_idx) return {};
    const auto* child_builder = builder_for(*inst->ref_idx);
    if (!child_builder) return {};
    return walk_exact(*inst->ref_idx, child_builder, path, idx + 1, here, depth + 1, query_visits);
  }

  const RawDefinition& root_;
  const std::map<EntityId, RawDefinition>& definitions_;
  std::size_t max_visits_;
  std::size_t visits_{0};
  bool indexed_{false};
  std::map<EntityId, std::map<std::string, const RawInstance*>> by_pid_;
  std::map<std::string, std::vector<std::optional<EntityId>>> holders_of_pid_;
  std::map<EntityId, std::vector<std::optional<EntityId>>> parents_of_def_;
  std::map<std::string, PathRes> resolved_;
};

}  // namespace

// Accumulate every vertex's persistent id (hex) -> (x, y, z) inches. A
// vertex is a "C409" record: "DC05" holds its persistent id (the "DE05"
// var-int payload), "C509" its 3xf64 position. Dimension connection points
// reference geometry by this id. Called once per top-level record -
// full_parse streams the TLV tree and never holds it whole.
void scan_vertex_positions(const TlvNode& top, std::map<std::string, Vec3>& id2pos) {
  // Operates on `top` (and el.children, all real members) by reference the
  // whole way down - never wraps a node in a synthetic {node} vector,
  // which would construct a TEMPORARY vector (a value-type TlvNode copies
  // its whole subtree) whose lifetime ends at that expression, dangling
  // any pointer taken into it.
  std::function<void(const TlvNode&)> visit = [&](const TlvNode& el) {
    if (el.tag == "C409") {
      auto* dc05 = find_child_tag(el.children, "DC05");
      auto* c509 = find_child_tag(el.children, "C509");
      if (dc05 && c509 && c509->payload.size() == 24) {
        auto idb = strip_de05(dc05->payload);
        id2pos[bytes_to_upper_hex(idb)] = {read_f64(c509->payload, 0), read_f64(c509->payload, 8),
                                           read_f64(c509->payload, 16)};
      }
    }
    for (auto& c : el.children) visit(c);
  };
  visit(top);
}

// Accumulate each instance's persistent id (hex) -> its WORLD transform (a
// 13-double matrix, or an empty vector for the identity/no-transform
// case), walking the instance tree and composing parent x local at every
// "6419". Per top-level record, like scan_vertex_positions - an instance
// chain never crosses top-level records.
//
// A dimension connects to geometry INSIDE a placed component; its
// connection reference names the vertex AND the instance holding it. The
// vertex position is definition-local, so it must be lifted to world by
// the instance's transform for the dimension to land where the author
// drew it.
void scan_instance_transforms(const TlvNode& top,
                              std::map<std::string, std::vector<double>>& world) {
  // Same reference-only traversal discipline as scan_vertex_positions -
  // no synthetic {node} vector wrapping.
  std::function<void(const TlvNode&, const std::vector<double>&)> visit =
      [&](const TlvNode& el, const std::vector<double>& parent) {
        if (el.tag == "6419") {
          auto* d007 = find_child_tag(el.children, "D007");
          auto* dc05 = d007 ? find_child_tag(d007->children, "DC05") : nullptr;
          std::optional<std::string> iid;
          if (dc05) iid = bytes_to_upper_hex(strip_de05(dc05->payload));

          auto* m = find_child_tag(el.children, "6619");
          std::optional<std::vector<double>> mat;
          if (m && m->payload.size() == 104) {
            std::vector<double> v(13);
            for (int i = 0; i < 13; ++i) v[i] = read_f64(m->payload, i * 8);
            mat = std::move(v);
          }
          std::vector<double> here = mat ? multiply_matrices(parent, *mat) : parent;
          if (iid) world[*iid] = here;
          for (auto& c : el.children) visit(c, here);
        } else {
          for (auto& c : el.children) visit(c, parent);
        }
      };
  visit(top, {});
}

std::size_t compose_instance_world(const RawDefinition& root,
                                   const std::map<EntityId, RawDefinition>& definitions,
                                   std::map<std::string, std::vector<double>>& world,
                                   std::size_t max_visits) {
  std::size_t visits = 0;
  std::function<void(const RawDefinition&, const std::vector<double>&, int)> walk =
      [&](const RawDefinition& def, const std::vector<double>& parent, int depth) {
        if (depth > 64 || visits >= max_visits) return;
        for (const auto& inst : def.builder.instances) {
          if (visits >= max_visits) return;
          ++visits;
          const std::vector<double> here =
              inst.matrix.empty() ? parent : multiply_matrices(parent, inst.matrix);
          if (!inst.persistent_id.empty()) world[inst.persistent_id] = here;
          if (!inst.ref_idx) continue;
          const auto it = definitions.find(*inst.ref_idx);
          if (it != definitions.end()) walk(it->second, here, depth + 1);
        }
      };
  walk(root, {}, 0);
  return visits;
}

// Linear dimensions (SketchUp's Dimension tool). 5BCC records inside a
// 7C15 component/group belong to that definition (local space); records
// outside any definition are model-root (world space). See
// assign_vff_annotations. parse_dimensions keeps the older "every 5BCC
// against one xf map" behaviour for unit tests.
std::vector<RawDimension> parse_dimensions(
    const ByteBuffer& model_dat, const std::map<std::string, Vec3>& id2pos,
    const std::map<std::string, std::vector<double>>& inst_world) {
  std::vector<RawDimension> dims;
  const ByteBuffer needle{0xcc, 0x5b};
  const auto resolve_path = map_path_resolver(inst_world);
  std::size_t i = 0;
  while (true) {
    auto found = std::search(model_dat.begin() + i, model_dat.end(), needle.begin(), needle.end());
    if (found == model_dat.end()) break;
    std::size_t j = static_cast<std::size_t>(found - model_dat.begin());
    i = j + 1;
    if (auto d = parse_one_5bcc(model_dat, j, id2pos, resolve_path)) dims.push_back(std::move(*d));
  }
  return dims;
}

std::size_t assign_vff_annotations(const ByteBuffer& model_dat,
                                   const std::vector<TlvHeader>& records,
                                   const std::map<std::string, Vec3>& id2pos, RawParsed& parsed,
                                   std::size_t max_visits) {
  std::optional<std::vector<DefSpan>> spans;
  InstancePathResolver resolver(parsed.root, parsed.definitions, max_visits);
  std::size_t i = 0;
  while (true) {
    auto found = model_dat.begin() + i;
    for (; found != model_dat.end(); ++found) {
      if (model_dat.end() - found < 2) {
        found = model_dat.end();
        break;
      }
      if ((found[0] == 0xcc && found[1] == 0x5b) || (found[0] == 0xf0 && found[1] == 0x55)) break;
    }
    if (found == model_dat.end()) break;
    std::size_t j = static_cast<std::size_t>(found - model_dat.begin());
    i = j + 1;
    if (j + 6 > model_dat.size()) continue;
    const auto ln = read_u32(model_dat, j + 2);
    if (ln < 40 || j + 6 + ln > model_dat.size()) continue;
    const bool is_text = model_dat[j] == 0xf0;
    if (!flat_tlv_has_fields(model_dat, j + 6, j + 6 + ln, is_text ? 0x55f1 : 0x5bcd,
                             is_text ? 0x55f4 : 0x5bce))
      continue;
    if (!spans) spans = scan_definition_spans(model_dat, records, parsed.definitions);
    const auto owner = owning_definition(*spans, j);
    const PathResolverFn owner_resolver = [&](const std::vector<std::string>& path) {
      return resolver.resolve_path(owner, path);
    };
    if (is_text) {
      const FacePointResolverFn owner_face_resolver = [&](EntityId face_id, double c0, double c1,
                                                          double c2,
                                                          const std::vector<std::string>& path) {
        return resolver.resolve_face_point(owner, face_id, c0, c1, c2, path);
      };
      if (auto text = parse_text(model_dat, j, id2pos, owner_resolver, owner_face_resolver)) {
        auto& builder = owner ? parsed.definitions[*owner].builder : parsed.root.builder;
        builder.texts.push_back(std::move(*text));
      }
      continue;
    }
    auto d = parse_one_5bcc(model_dat, j, id2pos, owner_resolver);
    if (d) {
      if (owner) {
        parsed.definitions[*owner].builder.dimensions.push_back(public_dimension(*d));
      } else {
        parsed.root.builder.dimensions.push_back(public_dimension(*d));
      }
    }
    if (!owner || !tlv_has_nonempty_tag(model_dat, j + 6, j + 6 + ln, 0x53fe)) {
      if (d) parsed.dimensions.push_back(std::move(*d));
    } else {
      const PathResolverFn world_resolver = [&](const std::vector<std::string>& path) {
        return resolver.resolve_path(std::nullopt, path, owner);
      };
      if (auto wd = parse_one_5bcc(model_dat, j, id2pos, world_resolver)) {
        parsed.dimensions.push_back(std::move(*wd));
      }
    }
  }
  return resolver.visits();
}

void assign_vff_sections(const ByteBuffer& md, const std::vector<TlvHeader>& records,
                         RawParsed& parsed) {
  const auto read_owner = [&](std::size_t begin, std::size_t end, GeometryBuilder& builder) {
    for (const auto& container : tlv_headers(md, begin, end)) {
      if (tag_at(md, container.offset) != "8813") continue;
      const auto fields =
          tlv_headers(md, container.offset + 6, container.offset + 6 + container.size);
      std::optional<EntityId> active;
      for (const auto& field : fields) {
        if (tag_at(md, field.offset) == "9413" && field.size > 0 && field.size <= 8)
          active = static_cast<EntityId>(parse_varint(md, field.offset + 6, field.size));
      }
      for (const auto& field : fields) {
        if (tag_at(md, field.offset) != "9313") continue;
        for (const auto& record :
             tlv_headers(md, field.offset + 6, field.offset + 6 + field.size)) {
          if (tag_at(md, record.offset) != "5C44") continue;
          const auto body = tlv_items_int(ByteBuffer(md.begin() + record.offset + 6,
                                                     md.begin() + record.offset + 6 + record.size));
          const auto plane = tlv_find_int(body, 0x445d);
          if (!plane || plane->size() != 32) continue;
          SectionPlane section;
          for (int i = 0; i < 4; ++i) section.plane[i] = read_f64(*plane, i * 8);
          if (auto name = tlv_find_int(body, 0x445e))
            section.name.assign(name->begin(), name->end());
          if (auto label = tlv_find_int(body, 0x445f))
            section.label.assign(label->begin(), label->end());
          if (auto draw = tlv_find_int(body, 0x07d0)) {
            const auto properties = tlv_items_int(*draw);
            section.persistent_id = drawing_pid(properties);
            if (auto layer = tlv_find_int(properties, 0x07d2);
                layer && !layer->empty() && layer->size() <= 8)
              section.layer = std::to_string(parse_varint(*layer, 0, layer->size()));
            if (auto flags = tlv_find_int(properties, 0x07d3); flags && !flags->empty())
              section.hidden = ((*flags)[0] & kDrawHidden) != 0;
          }
          const auto id = entity_id_from_tlv(md, record.offset, record.offset + 6 + record.size);
          section.active = active && id && *active == *id;
          builder.section_planes.push_back(std::move(section));
        }
      }
    }
  };
  for (const auto& span : scan_definition_spans(md, records, parsed.definitions))
    read_owner(span.begin + 6, span.end, parsed.definitions.at(span.id).builder);
  for (const auto& record : records)
    if (tag_at(md, record.offset) == "F601")
      read_owner(record.offset + 6, record.offset + 6 + record.size, parsed.root.builder);
}

// Return the "0702" scenes node inside top's subtree, or nullptr. Called
// per top-level record; retaining the (small) 0702 subtree is the only
// thing kept alive past the streaming loop.
const TlvNode* find_page_node(const TlvNode& top) {
  // Checks `top` itself first, then delegates to find_child_tag on
  // top.children (a real member) - never wraps `top` in a synthetic
  // {top} vector (see scan_vertex_positions's comment on why that
  // dangles).
  if (top.tag == "0702") return &top;
  return find_child_tag(top.children, "0702");
}

// A view camera (34BC): 34BD eye, 34BE target, 34BF up (3xf64, inches), 34C4
// field of view (degrees), 34C2 u8 = PERSPECTIVE flag (00 = parallel
// projection - calibrated against the bundled scene thumbnails: parallel
// plans/elevations carry 00 and their 34C3 visible height matches the
// thumbnail framing exactly, while perspective scenes carry 01 with a stale
// 34C3), 34C3 f64 = visible height when parallel (inches). Scenes and the
// model's current view (FA01) use the same record.
RawCamera parse_camera(const ByteBuffer& record) {
  auto cam = tlv_items_int(record);
  RawCamera out;
  out.eye = vec3_of(tlv_find_int(cam, 0x34bd));
  out.target = vec3_of(tlv_find_int(cam, 0x34be));
  out.up = vec3_of(tlv_find_int(cam, 0x34bf));
  auto fov = tlv_find_int(cam, 0x34c4);
  if (fov && fov->size() == 8) out.fov = read_f64(*fov, 0);
  auto flag = tlv_find_int(cam, 0x34c2);
  out.parallel = flag && !flag->empty() && (*flag)[0] == 0;
  auto height = tlv_find_int(cam, 0x34c3);
  if (height && height->size() == 8) out.ortho_height = read_f64(*height, 0);
  return out;
}

// Scenes ("pages"). The 0702 node's payload nests 6D60 > 6D61 > one 7148
// record per page:
//
// * 6F54 > 6F55 - page name (UTF-8)
// * 714A > 34BC - camera (see parse_camera)
// * 7150 - layers hidden in this page: (u8 length, var-int layer id) runs
// * 714B - entities hidden in this page, as their persistent ids, in the same runs (empty, or
//   absent, when the page hides none)
//
// Each page's 6F54 also carries its entity id (DC05 > DE05), and 6D62 next to
// 6D61 holds the id of the scene selected when the model was saved.
// (u8 length, var-int id) runs, as 7150 and 714B store them.
std::vector<EntityId> parse_id_runs(const std::optional<ByteBuffer>& runs) {
  std::vector<EntityId> ids;
  std::size_t off = 0;
  while (runs && off + 1 <= runs->size()) {
    std::uint8_t ln = (*runs)[off];
    if (ln == 0 || off + 1 + ln > runs->size()) break;
    ids.push_back(static_cast<EntityId>(parse_varint(*runs, off + 1, ln)));
    off += 1 + ln;
  }
  return ids;
}

std::vector<RawPage> parse_pages(const TlvNode* node) {
  std::vector<RawPage> pages;
  if (!node) return pages;

  auto t60_items = tlv_items_int(node->payload);
  for (auto& it60 : t60_items) {
    if (it60.tag != 0x6d60) continue;
    auto t61_items = tlv_items_int(it60.payload);
    auto selected_id = tlv_find_int(t61_items, 0x6d62);
    for (auto& it61 : t61_items) {
      if (it61.tag != 0x6d61) continue;
      auto t48_items = tlv_items_int(it61.payload);
      for (auto& it48 : t48_items) {
        if (it48.tag != 0x7148) continue;
        auto items = tlv_items_int(it48.payload);
        if (items.empty()) continue;

        RawPage page;

        auto flags = tlv_find_int(items, 0x7149);
        if (flags && flags->size() == 4) {
          page.capture_flags = read_u32(*flags, 0);
        }

        auto head_payload = tlv_find_int(items, 0x6f54);
        std::vector<FlatTlvItem> head;
        if (head_payload) head = tlv_items_int(*head_payload);
        auto name = tlv_find_int(head, 0x6f55);
        if (name && !name->empty()) {
          page.name.assign(reinterpret_cast<const char*>(name->data()), name->size());
        }
        if (selected_id) {
          auto ids = tlv_find_int(head, 0x05dc);
          auto id = ids ? tlv_find_int(tlv_items_int(*ids), 0x05de) : std::nullopt;
          page.selected = id && *id == *selected_id;
        }

        auto cam_wrap_payload = tlv_find_int(items, 0x714a);
        std::vector<FlatTlvItem> cam_wrap;
        if (cam_wrap_payload) cam_wrap = tlv_items_int(*cam_wrap_payload);
        if (auto cam_payload = tlv_find_int(cam_wrap, 0x34bc)) {
          auto cam = parse_camera(*cam_payload);
          page.eye = cam.eye;
          page.target = cam.target;
          page.up = cam.up;
          page.fov = cam.fov;
          page.parallel = cam.parallel;
          page.ortho_height = cam.ortho_height;
        }

        page.hidden_layer_ids = parse_id_runs(tlv_find_int(items, 0x7150));
        page.hidden_entity_ids = parse_id_runs(tlv_find_int(items, 0x714b));

        // A scene that doesn't save its camera ("Camera Location" unticked) is
        // still a scene: it keeps its name and hidden layers, eye/target/up unset.
        pages.push_back(std::move(page));
      }
    }
  }
  return pages;
}
}  // namespace openskp
