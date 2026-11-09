#include <algorithm>
#include <functional>
#include <optional>

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

std::optional<Vec3> dimension_connection_point(
    const std::optional<ByteBuffer>& block_payload, const std::map<std::string, Vec3>& id2pos,
    const std::map<std::string, std::vector<double>>& inst_world) {
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
  auto local_it = id2pos.find(bytes_to_upper_hex(*vid));
  if (local_it == id2pos.end()) return std::nullopt;
  if (iid && !iid->empty()) {
    auto path = length_prefixed_ids(*iid);
    if (path.empty()) path.push_back(bytes_to_upper_hex(*iid));
    for (auto it = path.rbegin(); it != path.rend(); ++it) {
      auto w_it = inst_world.find(*it);
      if (w_it != inst_world.end() && !w_it->second.empty()) {
        return transform_point(w_it->second, local_it->second);
      }
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

std::optional<RawDimension> parse_one_5bcc(
    const ByteBuffer& model_dat, std::size_t j, const std::map<std::string, Vec3>& id2pos,
    const std::map<std::string, std::vector<double>>& inst_world) {
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
  auto a = dimension_connection_point(tlv_find_int(body, 0x5bcd), id2pos, inst_world);
  auto b = dimension_connection_point(tlv_find_int(body, 0x5bce), id2pos, inst_world);
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
                                     const std::map<std::string, std::vector<double>>& transforms) {
  const auto size = read_u32(model_dat, j + 2);
  const auto fields =
      tlv_items_int(ByteBuffer(model_dat.begin() + j + 6, model_dat.begin() + j + 6 + size));
  const auto value = tlv_find_int(fields, 0x55f1);
  const auto connection = tlv_find_int(fields, 0x55f4);
  if (!value || !connection) return std::nullopt;
  TextEntity text;
  text.text.assign(value->begin(), value->end());
  text.point = dimension_connection_point(connection, id2pos, transforms);
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

struct TlvHeader {
  std::size_t offset{};
  std::size_t size{};
};

std::vector<TlvHeader> tlv_headers(const ByteBuffer& md, std::size_t start, std::size_t end) {
  std::vector<TlvHeader> out;
  for (std::size_t p = start; p + 6 <= end;) {
    const auto n = read_u32(md, p + 2);
    if (n > end - p - 6) break;
    out.push_back({p, n});
    p += 6 + n;
  }
  return out;
}

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
                                           const std::map<EntityId, RawDefinition>& defs) {
  std::vector<DefSpan> spans;
  if (md.size() < 6) return spans;
  auto hs = tlv_headers(md, 0, md.size());
  if (hs.size() == 1 && md[hs[0].offset] == 0xf4 && md[hs[0].offset + 1] == 1) {
    hs = tlv_headers(md, hs[0].offset + 6, hs[0].offset + 6 + hs[0].size);
  }
  for (const auto& h : hs) {
    if (tag_at(md, h.offset) != "F901") continue;
    auto level = tlv_headers(md, h.offset + 6, h.offset + 6 + h.size);
    bool ok = true;
    for (const char* expected : {"7017", "7117"}) {
      auto it = std::find_if(level.begin(), level.end(), [&](const TlvHeader& rec) {
        return tag_at(md, rec.offset) == expected;
      });
      if (it == level.end()) {
        ok = false;
        break;
      }
      level = tlv_headers(md, it->offset + 6, it->offset + 6 + it->size);
    }
    if (!ok) continue;
    for (const auto& rec : level) {
      if (tag_at(md, rec.offset) != "7C15") continue;
      auto id = entity_id_from_tlv(md, rec.offset, rec.offset + 6 + rec.size);
      if (!id || !defs.count(*id)) continue;
      spans.push_back({rec.offset, rec.offset + 6 + rec.size, *id});
    }
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

// Recursive tag search over buf[begin, end), in place. Same acceptance as
// walking tlv_items_int level by level (a malformed level contributes
// nothing), without copying every nested payload.
bool tlv_contains_tag(const ByteBuffer& buf, std::size_t begin, std::size_t end, std::uint16_t tag,
                      int depth = 0) {
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
    if (t == tag || tlv_contains_tag(buf, off + 6, off + 6 + ln, tag, depth + 1)) return true;
    off += 6 + ln;
  }
  return false;
}

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

void compose_instance_world(const RawDefinition& root,
                            const std::map<EntityId, RawDefinition>& definitions,
                            std::map<std::string, std::vector<double>>& world) {
  std::function<void(const RawDefinition&, const std::vector<double>&, int)> walk =
      [&](const RawDefinition& def, const std::vector<double>& parent, int depth) {
        if (depth > 64) return;
        for (const auto& inst : def.builder.instances) {
          const std::vector<double> here =
              inst.matrix.empty() ? parent : multiply_matrices(parent, inst.matrix);
          if (!inst.persistent_id.empty()) world[inst.persistent_id] = here;
          if (!inst.ref_idx) continue;
          const auto it = definitions.find(*inst.ref_idx);
          if (it != definitions.end()) walk(it->second, here, depth + 1);
        }
      };
  walk(root, {}, 0);
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
  std::size_t i = 0;
  while (true) {
    auto found = std::search(model_dat.begin() + i, model_dat.end(), needle.begin(), needle.end());
    if (found == model_dat.end()) break;
    std::size_t j = static_cast<std::size_t>(found - model_dat.begin());
    i = j + 1;
    if (auto d = parse_one_5bcc(model_dat, j, id2pos, inst_world)) dims.push_back(std::move(*d));
  }
  return dims;
}

void assign_vff_annotations(const ByteBuffer& model_dat, const std::map<std::string, Vec3>& id2pos,
                            const std::map<std::string, std::vector<double>>& model_world,
                            RawParsed& parsed) {
  const auto spans = scan_definition_spans(model_dat, parsed.definitions);
  // One definition's composed instance transforms at a time. A definition's 5BCC records sit
  // inside its own 7C15 span, so they arrive together; keeping a composed map for every owner
  // grew by gigabytes on large models with many dimensioned components.
  std::optional<EntityId> composed_owner;
  std::map<std::string, std::vector<double>> local_world;
  const std::map<std::string, std::vector<double>> no_xf;
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
    const auto owner = owning_definition(spans, j);
    const std::map<std::string, std::vector<double>>* xf = &model_world;
    if (owner) {
      // Same-def type-2 5BCC has no 53FE; don't compose an instance-xf map
      // that parse_one_5bcc would never consult.
      if (tlv_contains_tag(model_dat, j + 6, j + 6 + ln, 0x53fe)) {
        if (composed_owner != owner) {
          local_world.clear();
          compose_instance_world(parsed.definitions[*owner], parsed.definitions, local_world);
          composed_owner = owner;
        }
        xf = &local_world;
      } else {
        xf = &no_xf;
      }
    }
    if (is_text) {
      if (auto text = parse_text(model_dat, j, id2pos, *xf)) {
        auto& builder = owner ? parsed.definitions[*owner].builder : parsed.root.builder;
        builder.texts.push_back(std::move(*text));
      }
      continue;
    }
    auto d = parse_one_5bcc(model_dat, j, id2pos, *xf);
    if (!d) continue;
    if (owner) {
      parsed.definitions[*owner].builder.dimensions.push_back(public_dimension(*d));
    } else {
      parsed.root.builder.dimensions.push_back(public_dimension(*d));
    }
  }
}

void assign_vff_sections(const ByteBuffer& md, RawParsed& parsed) {
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
  for (const auto& span : scan_definition_spans(md, parsed.definitions))
    read_owner(span.begin + 6, span.end, parsed.definitions.at(span.id).builder);
  auto top = tlv_headers(md, 0, md.size());
  if (top.size() == 1 && tag_at(md, top[0].offset) == "F401")
    top = tlv_headers(md, top[0].offset + 6, top[0].offset + 6 + top[0].size);
  for (const auto& record : top)
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
//
// Each page's 6F54 also carries its entity id (DC05 > DE05), and 6D62 next to
// 6D61 holds the id of the scene selected when the model was saved.
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

        auto hidden = tlv_find_int(items, 0x7150);
        std::size_t off = 0;
        while (hidden && off + 1 <= hidden->size()) {
          std::uint8_t ln = (*hidden)[off];
          if (ln == 0 || off + 1 + ln > hidden->size()) break;
          page.hidden_layer_ids.push_back(
              static_cast<EntityId>(parse_varint(*hidden, off + 1, ln)));
          off += 1 + ln;
        }

        // A scene that doesn't save its camera ("Camera Location" unticked) is
        // still a scene: it keeps its name and hidden layers, eye/target/up unset.
        pages.push_back(std::move(page));
      }
    }
  }
  return pages;
}
}  // namespace openskp
