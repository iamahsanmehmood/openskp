#include <algorithm>
#include <cmath>
#include <regex>
#include <unordered_map>

#include "internal.hpp"

namespace openskp {
bool legacy_instance_has_guid(const std::string& class_name, std::optional<int> schema) {
  if (!schema) return true;
  return *schema >= (class_name == "CGroup" ? 1 : 5);
}

namespace {
struct R {
  const ByteBuffer& d;
  size_t p{};

  void need(size_t n) {
    if (p > d.size() || n > d.size() - p) throw std::out_of_range("legacy archive truncated");
  }

  uint8_t u8() {
    need(1);
    return d[p++];
  }

  uint16_t u16() {
    auto v = read_u16(d, p);
    p += 2;
    return v;
  }

  uint32_t u32() {
    auto v = read_u32(d, p);
    p += 4;
    return v;
  }

  double f64() {
    auto v = read_f64(d, p);
    p += 8;
    return v;
  }

  std::vector<double> f64s(size_t n) {
    std::vector<double> v;
    v.reserve(n);
    while (n--) v.push_back(f64());
    return v;
  }

  ByteBuffer raw(size_t n) {
    need(n);
    ByteBuffer v(d.begin() + p, d.begin() + p + n);
    p += n;
    return v;
  }

  bool marker() const {
    return p + 3 <= d.size() && d[p] == 255 && d[p + 1] == 254 && d[p + 2] == 255;
  }

  std::string utf16() {
    if (!marker()) throw std::runtime_error("expected legacy string record");
    p += 3;
    auto n = u8();
    uint32_t z = n;
    if (n == 255) {
      z = u16();
      if (z == 65535) z = u32();
    }
    need(size_t(z) * 2);
    std::string s;
    for (uint32_t i = 0; i < z; ++i) {
      uint16_t c = uint16_t(d[p]) | uint16_t(d[p + 1]) << 8;
      p += 2;
      if (c < 0x80)
        s += char(c);
      else if (c < 0x800) {
        s += char(0xc0 | (c >> 6));
        s += char(0x80 | (c & 63));
      } else {
        s += char(0xe0 | (c >> 12));
        s += char(0x80 | ((c >> 6) & 63));
        s += char(0x80 | (c & 63));
      }
    }
    return s;
  }
};

struct V {
  std::string k;
  std::string name;
  std::string label;
  std::string text;
  std::string guid;
  Vec3 xyz{};
  std::vector<double> plane;
  std::vector<double> xf;
  std::vector<double> uvf;
  std::vector<double> uvb;
  bool front_projected{};
  bool back_projected{};
  std::uint64_t v1{};
  std::uint64_t v2{};
  std::uint64_t edge{};
  std::uint64_t def{};
  // Slot of this entity's CAttributeContainer (resolved through
  // Archive::slots), or nullopt when it has none. Not a bare slot number:
  // slot 0 is a legitimate real slot (the first object allocated in the
  // archive), so 0 can't double as a sentinel for "absent."
  std::optional<std::uint64_t> attrs;
  // Only populated for "dict" (CAttributeNamed) entities: this
  // dictionary's own key/value pairs, already stringified (see
  // Archive::typed()).
  std::map<std::string, std::string> entries;
  std::uint64_t tex_dib{};
  bool sense{};
  bool faces_camera{};
  bool shadows_face_sun{};
  bool colorized{};
  int mat{};
  int back_mat{};
  int layer{};
  int hidden{};
  int soft{};
  int smooth{};
  int r{128};
  int g{128};
  int b{128};
  int a{255};
  double opacity{};
  double tw{};
  double th{};
  std::string tex_file;
  ByteBuffer blob;
  std::vector<std::shared_ptr<V>> loops;
  std::vector<std::shared_ptr<V>> uses;
  std::vector<std::tuple<uint64_t, std::string, std::shared_ptr<V>>> ents;
};

struct Entry {
  bool cls{};
  std::string name;
  int schema{};
  std::shared_ptr<V> v;
};

// True when the bytes at p are an MFC class-ref to class `slot`. Mirrors both
// encodings Archive::object() decodes: the short 16-bit form (0x8000|slot)
// and, for slots past 0x7fff, the big-tag escape (0x7fff followed by a u32
// of 0x80000000|slot).
bool is_class_ref(const ByteBuffer& d, size_t p, uint64_t slot) {
  if (slot <= 0x7fff) {
    return p + 2 <= d.size() && read_u16(d, p) == (0x8000 | slot);
  }
  return p + 6 <= d.size() && read_u16(d, p) == 0x7fff &&
         read_u32(d, p + 2) == (0x80000000u | uint32_t(slot));
}

struct Archive {
  R r;
  int ver;
  bool pid;
  uint64_t next{};
  uint64_t base{};
  uint64_t current_loop{};
  bool in_entity_list{};
  std::unordered_map<uint64_t, Entry> slots;
  std::unordered_map<std::string, uint64_t> class_slot;
  std::unordered_map<std::string, int> class_schema;

  Archive(const ByteBuffer& d, int v) : r{d}, ver(v), pid(v >= 17) {}

  uint64_t alloc(Entry e) {
    auto s = next++;
    slots[s] = std::move(e);
    return s;
  }

  std::tuple<uint64_t, std::string, std::shared_ptr<V>> object(
      std::optional<std::string> expect = {}) {
    auto tag = r.u16();
    if (!tag) return {};
    if (tag == 0x7fff) {
      auto big = r.u32();
      if (big & 0x80000000) return new_class_ref(big & 0x7fffffff, expect);
      return back(big);
    }
    if (tag == 0xffff) {
      auto schema = r.u16();
      auto n = r.u16();
      if (n > 40) throw std::runtime_error("implausible legacy class name");
      auto b = r.raw(n);
      std::string name(b.begin(), b.end());
      auto cs = alloc({true, name, int(schema), {}});
      class_slot[name] = cs;
      class_schema[name] = int(schema);
      return new_obj(name);
    }
    if (tag & 0x8000) return new_class_ref(tag & 0x7fff, expect);
    return back(tag);
  }

  std::tuple<uint64_t, std::string, std::shared_ptr<V>> new_class_ref(
      uint64_t s, std::optional<std::string> expect) {
    auto i = slots.find(s);
    if (i == slots.end()) {
      if (!expect) throw std::runtime_error("unknown legacy class slot");
      slots[s] = {true, *expect, 0, {}};
      class_slot[*expect] = s;
      i = slots.find(s);
    }
    if (!i->second.cls) throw std::runtime_error("class ref points to object");
    return new_obj(i->second.name);
  }

  std::tuple<uint64_t, std::string, std::shared_ptr<V>> back(uint64_t s) {
    auto i = slots.find(s);
    if (i == slots.end()) {
      if (s < base) return {s, "premodel", {}};
      throw std::runtime_error("legacy backref to unknown slot");
    }
    if (i->second.cls) throw std::runtime_error("legacy backref to class");
    return {s, i->second.name, i->second.v};
  }

  // Returns the CAttributeContainer's slot, or nullopt when this entity
  // has none (a null object reference: tag 0, Archive::object() returns a
  // default-constructed tuple with an empty class name).
  std::optional<std::uint64_t> preamble() {
    auto attrs = object("CAttributeContainer");
    if (pid) {
      auto mask = r.u8();
      for (int i = 0; i < 8; ++i)
        if (mask & (1 << i)) r.u8();
    }
    if (std::get<1>(attrs).empty()) return std::nullopt;
    return std::get<0>(attrs);
  }

  void draw(V& v) {
    auto b = r.raw(10);
    v.mat = int(b[0] | b[1] << 8);
    v.hidden = b[2];
    v.soft = b[5];
    v.smooth = b[6];
    v.layer = int(b[8] | b[9] << 8);
  }

  std::tuple<uint64_t, std::string, std::shared_ptr<V>> new_obj(const std::string& n) {
    auto slot = alloc({false, n, 0, {}});
    auto v = read(n, slot);
    slots[slot].v = v;
    return {slot, n, v};
  }

  std::shared_ptr<V> read(const std::string& n, uint64_t self) {
    auto v = std::make_shared<V>();
    if (n == "CVertex") {
      preamble();
      v->k = "vertex";
      auto a = r.f64s(3);
      v->xyz = {a[0], a[1], a[2]};
    } else if (n == "CEdge") {
      preamble();
      v->k = "edge";
      draw(*v);
      v->v1 = std::get<0>(object("CVertex"));
      v->v2 = std::get<0>(object("CVertex"));
      object();
    } else if (n == "CCurve") {
      preamble();
      v->k = "curve";
      r.u8();
      r.u32();
    } else if (n == "CArcCurve") {
      preamble();
      v->k = "curve";
      r.raw(5);
      r.f64s(14);
    } else if (n == "CEdgeUse") {
      preamble();
      v->k = "edgeuse";
      v->edge = std::get<0>(object("CEdge"));
      v->sense = r.u8() != 0;
      auto parent = std::get<0>(object());
      if (parent != current_loop) throw std::runtime_error("edge-use parent mismatch");
    } else if (n == "CLoop") {
      auto old = current_loop;
      current_loop = self;
      preamble();
      r.raw(2);
      v->k = "loop";
      while (r.p + 2 <= r.d.size() && read_u16(r.d, r.p)) {
        auto q = std::get<2>(object("CEdgeUse"));
        if (q) v->uses.push_back(q);
      }
      r.u16();
      current_loop = old;
    } else if (n == "CFace") {
      v->attrs = preamble();
      v->k = "face";
      draw(*v);
      v->plane = r.f64s(4);
      auto count = r.u32();
      if (count > 10000) throw std::runtime_error("implausible loop count");
      while (count--) {
        auto q = std::get<2>(object("CLoop"));
        if (q) v->loops.push_back(q);
      }
      v->back_mat = r.u16();
    } else if (n == "CAttributeContainer") {
      preamble();
      v->k = "attrs";
      while (r.p + 2 <= r.d.size() && read_u16(r.d, r.p))
        v->ents.push_back(object("CAttributeNamed"));
      r.u16();
    } else if (n == "CAttributeNamed") {
      preamble();
      v->k = "dict";
      r.raw(4);
      v->name = r.utf16();
      while (true) {
        auto key = r.utf16();
        if (key.empty()) break;
        v->entries[key] = typed(r.u8());
      }
      r.u32();
    } else if (n == "CLayer") {
      preamble();
      v->k = "layer";
      v->name = r.utf16();
      ByteBuffer mid;
      while (mid.size() < 8 && !r.marker()) mid.push_back(r.u8());
      v->hidden = mid.empty() ? 0 : mid[0];
      r.utf16();
      r.u16();
      auto c = r.raw(4);
      v->r = c[0];
      v->g = c[1];
      v->b = c[2];
      r.utf16();
      r.raw(21);
    } else if (n == "CMaterial") {
      preamble();
      v->k = "material";
      v->name = r.utf16();
      auto flag = r.u16();
      if (!flag) {
        auto c = r.raw(4);
        v->r = c[0];
        v->g = c[1];
        v->b = c[2];
        v->a = c[3];
        r.utf16();
        r.raw(8);
        v->opacity = r.f64();
        if (!r.u8()) v->opacity = 0;
      } else {
        r.raw(ver >= 17 ? 2 : 1);
        auto q = object("CDib");
        v->tex_dib = std::get<0>(q);
        auto begin = r.p, limit = std::min(r.d.size(), r.p + 28);
        size_t marker = begin;
        for (; marker + 3 <= limit; ++marker)
          if (r.d[marker] == 255 && r.d[marker + 1] == 254 && r.d[marker + 2] == 255) break;
        if (marker - begin == 20)
          r.u32();
        else if (marker - begin != 16)
          throw std::runtime_error("texture size block misaligned");
        v->tw = r.f64();
        v->th = r.f64();
        v->tex_file = r.utf16();
        auto c = r.raw(9);
        v->r = c[0];
        v->g = c[1];
        v->b = c[2];
        // c[3] here is not a colour alpha byte - it feeds the colorized
        // check below alongside blob[4]. v->a keeps its default (255);
        // textured materials carry no separate alpha channel in this
        // record shape.
        r.utf16();
        auto blob = r.raw(8);
        v->opacity = r.f64();
        if (!r.u8()) v->opacity = 0;
        v->colorized = blob[4] != 0 || c[3] == 255;
      }
    } else if (n == "CDib") {
      v->k = "dib";
      r.u32();
      auto z = r.u32();
      if (z > r.d.size()) throw std::runtime_error("implausible dib length");
      v->blob = r.raw(z);
    } else if (n == "CFaceTextureCoords") {
      preamble();
      v->k = "ftc";
      r.u32();
      auto a = r.f64s(24);
      v->uvf.assign(a.begin(), a.begin() + 9);
      v->uvb.assign(a.begin() + 12, a.begin() + 21);
      auto z = r.u32();
      while (z--) r.f64s(4);
      z = r.u32();
      while (z--) r.f64s(4);
      auto fflags = r.u32();
      auto bflags = r.u32();
      v->front_projected = (fflags & 2) != 0;
      v->back_projected = (bflags & 2) != 0;
    } else if (n == "CCamera") {
      r.raw(137);
      r.u16();
      r.utf16();
      r.raw(33);
    } else if (n == "CThumbnail") {
      preamble();
      object("CCamera");
      object("CDib");
    } else if (n == "CRelationship") {
      preamble();
      object();
      object();
    } else if (n == "CConstructionLine") {
      preamble();
      draw(*v);
      r.f64s(8);
      r.raw(ver >= 17 ? 7 : 4);
    } else if (n == "CConstructionPoint") {
      preamble();
      draw(*v);
      r.f64s(6);
      r.u8();
    } else if (n == "CSectionPlane") {
      preamble();
      v->k = "sectionplane";
      draw(*v);
      auto first = read_f64(r.d, r.p);
      if (std::abs(first) > 1.0001) object();
      v->plane = r.f64s(4);
      if (r.marker()) {
        v->name = r.utf16();
        v->label = r.utf16();
      }
    } else if (n == "CSkFont") {
      object("CAttributeContainer");
      if (pid) r.u8();
      r.utf16();
      r.raw(15);
    } else if (n == "CDimensionLinear") {
      preamble();
      v->k = "dimension";
      draw(*v);
      v->text = r.utf16();
      object("CSkFont");
      r.raw(165);
    } else if (n == "CText") {
      preamble();
      v->k = "text";
      draw(*v);
      object("CSkFont");
      size_t found = std::string::npos;
      for (size_t q = r.p; q + 14 <= std::min(r.d.size(), r.p + 512); ++q)
        if (r.d[q] == 1 && r.d[q + 1] == 0 && r.d[q + 2] == 0 && r.d[q + 3] == 0 &&
            r.d[q + 6] == 3 && r.d[q + 7] == 0 && r.d[q + 8] == 0 && r.d[q + 9] == 0 &&
            r.d[q + 10] == 1 && r.d[q + 11] == 255 && r.d[q + 12] == 254 && r.d[q + 13] == 255) {
          found = q + 11;
          break;
        }
      if (found == std::string::npos) throw std::runtime_error("text delimiter not found");
      r.raw(found - r.p);
      v->text = r.utf16();
      r.raw(5);
    } else if (n == "CComponentDefinition") {
      preamble();
      v->k = "definition";
      r.raw(ver >= 17 ? 22 : 20);
      auto nl = r.u32();
      if (nl > 10000) throw std::runtime_error("implausible def layers");
      while (nl--) object("CLayer");
      auto decl = r.u16();
      if (decl == 0x7fff) r.u32();
      r.u32();
      auto count = r.u32();
      if (count > 5000000) throw std::runtime_error("implausible def entities");
      v->ents = entity_list(count, false);
      auto nr = r.u32();
      if (nr > 100000) throw std::runtime_error("definition list misaligned");
      while (nr--) object("CRelationship");
      r.u16();
      auto g = r.raw(16);
      static char h[] = "0123456789ABCDEF";
      for (auto x : g) {
        v->guid += h[x >> 4];
        v->guid += h[x & 15];
      }
      v->name = r.utf16();
      r.utf16();
      r.utf16();
      r.u32();
      size_t tpos = std::string::npos;
      auto thumb_cs = class_slot.find("CThumbnail");
      for (size_t off = 0; off < 96 && r.p + off + 26 <= r.d.size(); ++off) {
        auto p = r.p + off;
        if (r.d[p] == 255 && r.d[p + 1] == 255 && r.d[p + 4] == 10 && r.d[p + 5] == 0 &&
            std::equal(r.d.begin() + p + 6, r.d.begin() + p + 16, "CThumbnail")) {
          tpos = p;
          break;
        }
        if (thumb_cs != class_slot.end() && is_class_ref(r.d, p, thumb_cs->second)) {
          tpos = p;
          break;
        }
      }
      if (tpos == std::string::npos) throw std::runtime_error("definition thumbnail not found");
      auto gap = r.raw(tpos - r.p);
      v->faces_camera = gap.size() >= 9 && (gap[gap.size() - 9] & 1);
      v->shadows_face_sun = gap.size() >= 9 && (gap[gap.size() - 9] & 2);
      object("CThumbnail");
    } else if (n == "CComponentInstance" || n == "CGroup") {
      v->attrs = preamble();
      v->k = "instance";
      draw(*v);
      auto q = object("CComponentDefinition");
      v->def = std::get<0>(q);
      v->xf = r.f64s(13);
      v->name = r.utf16();
      // The trailing GUID was introduced in CComponentInstance schema 5 and
      // CGroup schema 1. SketchUp 2013 writes component-instance schema 4,
      // whose record ends at the name.
      auto schema = class_schema.find(n);
      std::optional<int> schema_number;
      if (schema != class_schema.end()) schema_number = schema->second;
      if (legacy_instance_has_guid(n, schema_number)) r.raw(16);
    } else
      throw std::runtime_error("no legacy reader for " + n);
    return v;
  }

  // Reads one typed CAttributeNamed value off the stream and returns its
  // string representation, matching the string-valued properties contract
  // extract_legacy_dynamic_properties() (below) produces.
  std::string typed(uint8_t t) {
    switch (t) {
      case 0:
        return "";
      case 4:
        return std::to_string(read_i32(r.raw(4), 0));
      case 6:
        return std::to_string(r.f64());
      case 7:
        return std::to_string(r.u8());
      case 9:
        return std::to_string(r.u32());
      case 10:
        return r.utf16();
      case 11: {
        auto n = r.u32();
        if (n > 100000) throw std::runtime_error("attr array too large");
        std::string joined;
        while (n--) {
          if (!joined.empty()) joined += ",";
          joined += typed(r.u8());
        }
        return joined;
      }
      case 18: {
        auto v = r.f64s(3);
        return std::to_string(v[0]) + "," + std::to_string(v[1]) + "," + std::to_string(v[2]);
      }
      default:
        throw std::runtime_error("unknown legacy attribute type");
    }
  }

  std::vector<std::tuple<uint64_t, std::string, std::shared_ptr<V>>> entity_list(uint32_t n,
                                                                                 bool root) {
    std::vector<std::tuple<uint64_t, std::string, std::shared_ptr<V>>> v;
    while (v.size() < n) {
      auto save = r.p;
      try {
        v.push_back(object());
      } catch (...) {
        if (!root) throw;
        r.p = save;
        break;
      }
    }
    return v;
  }
};

void add_edge(GeometryBuilder& b, uint64_t s, const V& e,
              const std::unordered_map<uint64_t, Entry>& slots) {
  if (b.edges.count(s)) return;
  for (auto id : {e.v1, e.v2}) {
    auto i = slots.find(id);
    if (i != slots.end() && i->second.v && i->second.v->k == "vertex")
      b.vertices[id] = i->second.v->xyz;
  }
  b.edges[s] = {EntityId(e.v1), EntityId(e.v2)};
  int f = (e.soft ? 8 : 0) | (e.smooth ? 16 : 0) | (e.hidden ? 1 : 0);
  if (f) b.edge_flags[s] = f;
}

// SketchUp's Dynamic Components extension stores its data in an attribute
// dictionary literally named "dynamic_attributes" - a stable, publicly
// documented part of the SketchUp Ruby API
// (Entity#attribute_dictionary("dynamic_attributes")). CAttributeContainer/
// CAttributeNamed reading above already fully decodes an entity's
// attribute dictionaries for other purposes (CFaceTextureCoords lookup on
// faces) - this just looks up that one dictionary by name, mirroring what
// the VFF path's dynamic-properties extraction does for D007/DC05 TLV
// data.
std::map<std::string, std::string> extract_legacy_dynamic_properties(
    std::optional<uint64_t> attrs_slot, const std::unordered_map<uint64_t, Entry>& slots) {
  if (!attrs_slot) return {};
  auto ai = slots.find(*attrs_slot);
  if (ai == slots.end() || !ai->second.v) return {};
  for (auto& ent : ai->second.v->ents) {
    auto& ev = std::get<2>(ent);
    if (ev && ev->k == "dict" && ev->name == "dynamic_attributes") return ev->entries;
  }
  return {};
}

void fill(GeometryBuilder& b,
          const std::vector<std::tuple<uint64_t, std::string, std::shared_ptr<V>>>& ents,
          const std::unordered_map<uint64_t, Entry>& slots) {
  for (auto& x : ents) {
    auto s = std::get<0>(x);
    auto v = std::get<2>(x);
    if (!v) continue;
    if (v->k == "edge")
      add_edge(b, s, *v, slots);
    else if (v->k == "face") {
      RawFace f;
      f.normal = {v->plane[0], v->plane[1], v->plane[2]};
      if (v->mat) f.material_id = v->mat;
      if (v->back_mat) f.back_material_id = v->back_mat;
      f.hidden = v->hidden != 0;
      for (auto& lp : v->loops) {
        std::vector<CoEdge> co;
        for (auto& u : lp->uses) {
          auto i = slots.find(u->edge);
          if (i != slots.end() && i->second.v) {
            add_edge(b, u->edge, *i->second.v, slots);
            co.push_back({EntityId(u->edge), u->sense ? 1 : 0});
          }
        }
        f.loops.push_back(std::move(co));
      }
      // A positioned/photo-fitted texture mapping (CFaceTextureCoords)
      // lives as a child of this face's attribute container, alongside
      // any CAttributeNamed dictionaries - so it has to be found by
      // scanning the resolved attrs' children for one tagged "ftc"
      // rather than read directly off the face record.
      if (v->attrs) {
        auto ai = slots.find(*v->attrs);
        if (ai != slots.end() && ai->second.v) {
          for (auto& ent : ai->second.v->ents) {
            auto& ev = std::get<2>(ent);
            if (!ev || ev->k != "ftc") continue;
            if (ev->uvf.size() == 9) {
              std::array<double, 9> m{};
              std::copy(ev->uvf.begin(), ev->uvf.end(), m.begin());
              f.uv_transform = m;
            }
            if (ev->uvb.size() == 9) {
              std::array<double, 9> m{};
              std::copy(ev->uvb.begin(), ev->uvb.end(), m.begin());
              f.uv_transform_back = m;
            }
            f.uv_projected = ev->front_projected;
            f.uv_projected_back = ev->back_projected;
          }
        }
      }
      b.faces[s] = std::move(f);
    } else if (v->k == "instance") {
      RawInstance i;
      i.name = v->name;
      i.ref_idx = v->def;
      i.matrix = v->xf;
      if (v->mat) i.material_id = v->mat;
      if (v->layer) i.layer = std::to_string(v->layer);
      i.hidden = v->hidden != 0;
      i.properties = extract_legacy_dynamic_properties(v->attrs, slots);
      b.instances.push_back(std::move(i));
    } else if (v->k == "sectionplane") {
      SectionPlane sp;
      if (v->plane.size() == 4) {
        sp.plane = {v->plane[0], v->plane[1], v->plane[2], v->plane[3]};
      }
      sp.name = v->name;
      sp.label = v->label;
      sp.hidden = v->hidden != 0;
      b.section_planes.push_back(std::move(sp));
    } else if (v->k == "text") {
      TextEntity te;
      te.text = v->text;
      te.hidden = v->hidden != 0;
      b.texts.push_back(std::move(te));
    } else if (v->k == "dimension") {
      Dimension dim;
      dim.text = v->text;
      dim.hidden = v->hidden != 0;
      b.dimensions.push_back(std::move(dim));
    }
  }
}
}  // namespace

RawParsed parse_legacy(const ByteBuffer& data, const ParseOptions& o) {
  emit_log(o, LogLevel::information,
           "Parsing legacy MFC container (" + std::to_string(data.size()) + " bytes)");
  RawParsed out;
  out.version = extract_version(data);
  // Legacy (pre-2021 MFC) files carry no meta/meta.dat container - that's
  // a VFF/ZIP-only construct - so there is no known source for the
  // model's unit-system string here (out.units stays unset).
  try {
    std::string ascii;
    for (size_t i = 0; i < std::min<size_t>(96, data.size()); ++i)
      if (data[i]) ascii += char(data[i]);
    std::smatch vm;
    std::regex_search(ascii, vm, std::regex("\\{(\\d+)\\."));
    int ver = std::stoi(vm[1]);
    size_t mh = std::string::npos;
    for (size_t i = 0; i + 15 < data.size(); ++i)
      if (data[i] == 255 && data[i + 1] == 255 && read_u16(data, i + 4) == 9 &&
          std::equal(data.begin() + i + 6, data.begin() + i + 15, "CMaterial")) {
        mh = i;
        break;
      }
    if (mh == std::string::npos || mh < 4) throw std::runtime_error("no CMaterial class record");
    auto mc = read_u32(data, mh - 4);
    if (mc < 2 || mc > 100000) throw std::runtime_error("invalid material count");
    Archive boot(data, ver);
    boot.next = boot.base = 1 << 20;
    boot.r.p = mh;
    boot.object("CMaterial");
    auto tag = read_u16(data, boot.r.p);
    if (tag == 0xffff || !(tag & 0x8000)) throw std::runtime_error("cannot bootstrap slot base");
    Archive ar(data, ver);
    ar.next = ar.base = tag & 0x7fff;
    ar.r.p = mh;
    std::vector<std::pair<uint64_t, std::shared_ptr<V>>> mats, layers;
    for (uint32_t i = 0; i < mc; ++i) {
      auto q = ar.object("CMaterial");
      mats.push_back({std::get<0>(q), std::get<2>(q)});
    }
    ar.r.u32();
    if (ver >= 17) ar.r.u8();
    auto lc = ar.r.u32();
    if (lc > 100000) throw std::runtime_error("invalid layer count");
    while (lc--) {
      auto q = ar.object("CLayer");
      layers.push_back({std::get<0>(q), std::get<2>(q)});
    }
    auto anchor = ar.object();
    if (std::get<1>(anchor) != "CLayer")
      throw std::runtime_error("definition anchor is not a layer");
    auto dc = ar.r.u32();
    if (dc > 1000000) throw std::runtime_error("invalid definition count");
    while (dc--) ar.object("CComponentDefinition");
    auto cs = ar.class_slot.find("CComponentDefinition");
    while (ar.r.p + 2 <= data.size()) {
      auto t = read_u16(data, ar.r.p);
      bool yes = cs != ar.class_slot.end() && t == (0x8000 | cs->second);
      if (!yes && t == 0xffff && ar.r.p + 26 <= data.size())
        yes = std::equal(data.begin() + ar.r.p + 6, data.begin() + ar.r.p + 26,
                         "CComponentDefinition");
      if (!yes) break;
      ar.object();
    }
    auto root = ar.entity_list(ar.r.u32(), true);
    for (auto& m : mats) {
      auto v = m.second;
      auto x = std::make_shared<RawMaterial>();
      x->name = v->name;
      x->r = v->r;
      x->g = v->g;
      x->b = v->b;
      x->a = v->a;
      x->transparency = std::clamp(1.0 - v->opacity, 0.0, 1.0);
      x->colorized = v->colorized;
      x->colorize_type = v->colorized ? 1 : 0;
      if (v->tex_dib) {
        RawTexture t;
        t.filename = v->tex_file;
        t.x_scale = v->tw;
        t.y_scale = v->th;
        auto di = ar.slots.find(v->tex_dib);
        if (di != ar.slots.end() && di->second.v) t.data = di->second.v->blob;
        if (t.filename.empty())
          t.filename =
              v->name + (t.data && t.data->size() >= 4 && (*t.data)[0] == 0x89 ? ".png" : ".jpg");
        x->texture = std::move(t);
      }
      out.materials[x->name] = x;
      out.material_id_to_name[m.first] = x->name;
    }
    for (auto& l : layers) {
      out.layer_id_to_name[l.first] = l.second->name;
      out.layer_colors[l.second->name] = {uint8_t(l.second->r), uint8_t(l.second->g),
                                          uint8_t(l.second->b)};
      out.layer_hidden[l.second->name] = l.second->hidden != 0;
    }
    if (!out.layer_colors.count("Layer0")) out.layer_colors["Layer0"] = {136, 136, 136};
    if (!out.layer_hidden.count("Layer0")) out.layer_hidden["Layer0"] = false;
    for (auto& s : ar.slots)
      if (!s.second.cls && s.second.name == "CComponentDefinition" && s.second.v) {
        RawDefinition d;
        d.name = s.second.v->name;
        d.guid = s.second.v->guid;
        d.always_faces_camera = s.second.v->faces_camera;
        d.shadows_face_sun = s.second.v->shadows_face_sun;
        fill(d.builder, s.second.v->ents, ar.slots);
        out.definitions[s.first] = std::move(d);
      }
    fill(out.root.builder, root, ar.slots);
    emit_progress(o, ParseStage::legacy_defs, out.definitions.size(), out.definitions.size());
    emit_log(o, LogLevel::information,
             "Parse complete: " + std::to_string(out.definitions.size()) + " defs");
    return out;
  } catch (const SkpParseError&) {
    throw;
  } catch (...) {
    throw SkpParseError("legacy .skp parse failed", ParseStage::legacy_walk, {}, {}, {}, {}, {},
                        std::current_exception());
  }
}
}  // namespace openskp
