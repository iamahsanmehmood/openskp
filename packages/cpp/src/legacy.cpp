#include <algorithm>
#include <cmath>
#include <regex>
#include <unordered_map>

#include "internal.hpp"

namespace openskp {
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
  std::string guid;
  Vec3 xyz{};
  std::vector<double> plane;
  std::vector<double> xf;
  std::vector<double> uvf;
  std::vector<double> uvb;
  std::uint64_t v1{};
  std::uint64_t v2{};
  std::uint64_t edge{};
  std::uint64_t def{};
  std::uint64_t attrs{};
  std::uint64_t tex_dib{};
  bool sense{};
  bool faces_camera{};
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

  void preamble() {
    object("CAttributeContainer");
    if (pid) {
      auto mask = r.u8();
      for (int i = 0; i < 8; ++i)
        if (mask & (1 << i)) r.u8();
    }
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
      preamble();
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
      while (r.p + 2 <= r.d.size() && read_u16(r.d, r.p)) object("CAttributeNamed");
      r.u16();
    } else if (n == "CAttributeNamed") {
      preamble();
      v->k = "dict";
      r.raw(4);
      v->name = r.utf16();
      while (true) {
        auto key = r.utf16();
        if (key.empty()) break;
        typed(r.u8());
      }
      r.u32();
    } else if (n == "CLayer") {
      preamble();
      v->k = "layer";
      v->name = r.utf16();
      ByteBuffer mid;
      while (mid.size() < 8 && !r.marker()) mid.push_back(r.u8());
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
      r.u32();
      r.u32();
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
      draw(*v);
      auto first = read_f64(r.d, r.p);
      if (std::abs(first) > 1.0001) object();
      r.f64s(4);
      if (r.marker()) {
        r.utf16();
        r.utf16();
      }
    } else if (n == "CSkFont") {
      object("CAttributeContainer");
      if (pid) r.u8();
      r.utf16();
      r.raw(15);
    } else if (n == "CDimensionLinear") {
      preamble();
      draw(*v);
      r.utf16();
      object("CSkFont");
      r.raw(165);
    } else if (n == "CText") {
      preamble();
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
      r.utf16();
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
      for (size_t off = 0; off < 96 && r.p + off + 26 <= r.d.size(); ++off) {
        auto p = r.p + off;
        if (r.d[p] == 255 && r.d[p + 1] == 255 && r.d[p + 4] == 10 && r.d[p + 5] == 0 &&
            std::equal(r.d.begin() + p + 6, r.d.begin() + p + 16, "CThumbnail")) {
          tpos = p;
          break;
        }
        auto cs = class_slot.find("CThumbnail");
        if (cs != class_slot.end() && read_u16(r.d, p) == (0x8000 | cs->second)) {
          tpos = p;
          break;
        }
      }
      if (tpos == std::string::npos) throw std::runtime_error("definition thumbnail not found");
      auto gap = r.raw(tpos - r.p);
      v->faces_camera = gap.size() >= 9 && (gap[gap.size() - 9] & 1);
      object("CThumbnail");
    } else if (n == "CComponentInstance" || n == "CGroup") {
      preamble();
      v->k = "instance";
      draw(*v);
      auto q = object("CComponentDefinition");
      v->def = std::get<0>(q);
      v->xf = r.f64s(13);
      v->name = r.utf16();
      r.raw(16);
    } else
      throw std::runtime_error("no legacy reader for " + n);
    return v;
  }

  void typed(uint8_t t) {
    switch (t) {
      case 0:
        return;
      case 4:
        r.raw(4);
        return;
      case 6:
        r.f64();
        return;
      case 7:
        r.u8();
        return;
      case 9:
        r.u32();
        return;
      case 10:
        r.utf16();
        return;
      case 11: {
        auto n = r.u32();
        if (n > 100000) throw std::runtime_error("attr array too large");
        while (n--) typed(r.u8());
        return;
      }
      case 18:
        r.f64s(3);
        return;
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
      b.faces[s] = std::move(f);
    } else if (v->k == "instance") {
      RawInstance i;
      i.name = v->name;
      i.ref_idx = v->def;
      i.matrix = v->xf;
      if (v->mat) i.material_id = v->mat;
      if (v->layer) i.layer = std::to_string(v->layer);
      b.instances.push_back(std::move(i));
    }
  }
}
}  // namespace

RawParsed parse_legacy(const ByteBuffer& data, const ParseOptions& o) {
  emit_log(o, LogLevel::information,
           "Parsing legacy MFC container (" + std::to_string(data.size()) + " bytes)");
  RawParsed out;
  out.version = extract_version(data);
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
    }
    if (!out.layer_colors.count("Layer0")) out.layer_colors["Layer0"] = {136, 136, 136};
    for (auto& s : ar.slots)
      if (!s.second.cls && s.second.name == "CComponentDefinition" && s.second.v) {
        RawDefinition d;
        d.name = s.second.v->name;
        d.guid = s.second.v->guid;
        d.always_faces_camera = s.second.v->faces_camera;
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
