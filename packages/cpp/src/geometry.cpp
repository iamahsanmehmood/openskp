#include <algorithm>
#include <sstream>

#include "internal.hpp"

namespace openskp {
static const TlvNode* find_node(const std::vector<TlvNode>& ns, const std::string& t) {
  for (auto& n : ns) {
    if (n.tag == t) return &n;
    if (auto* r = find_node(n.children, t)) return r;
  }
  return nullptr;
}

static void find_all(const std::vector<TlvNode>& ns, const std::string& t,
                     std::vector<const TlvNode*>& out) {
  for (auto& n : ns) {
    if (n.tag == t) out.push_back(&n);
    find_all(n.children, t, out);
  }
}

static std::optional<EntityId> entity_id(const TlvNode& n) {
  for (auto& c : n.children) {
    if (c.tag == "DE05" && !c.payload.empty())
      return static_cast<EntityId>(parse_varint(c.payload, 0, c.payload.size()));
    if (c.tag == "DC05" && c.payload.size() >= 6 && c.payload[0] == 0xde && c.payload[1] == 5) {
      auto z = read_u32(c.payload, 2);
      if (z <= c.payload.size() - 6) return static_cast<EntityId>(parse_varint(c.payload, 6, z));
    }
  }
  for (auto& c : n.children)
    if (auto v = entity_id(c)) return v;
  return {};
}

static EntityId dc_id(const ByteBuffer& p) {
  if (p.size() >= 6 && p[0] == 0xde && p[1] == 5) {
    auto z = read_u32(p, 2);
    if (z <= p.size() - 6) return parse_varint(p, 6, z);
  }
  return parse_varint(p, 0, p.size());
}

static std::string text(const ByteBuffer& p) {
  return {reinterpret_cast<const char*>(p.data()), p.size()};
}

static std::string hex(const ByteBuffer& p) {
  static char h[] = "0123456789ABCDEF";
  std::string s;
  s.reserve(p.size() * 2);
  for (auto b : p) {
    s += h[b >> 4];
    s += h[b & 15];
  }
  return s;
}

static const ByteBuffer* flat_find(const std::vector<std::pair<std::string, ByteBuffer>>& s,
                                   const std::string& t) {
  for (auto& v : s)
    if (v.first == t) return &v.second;
  return nullptr;
}

static std::pair<std::optional<std::array<double, 9>>, std::optional<std::array<double, 9>>> uv(
    const ByteBuffer& p) {
  auto a = parse_flat(p);
  auto q = flat_find(a, "DD05");
  if (!q) return {};
  auto b = parse_flat(*q);
  q = flat_find(b, "B136");
  if (!q) return {};
  auto c = parse_flat(*q);
  q = flat_find(c, "B236");
  if (!q) return {};
  auto d = parse_flat(*q);
  q = flat_find(d, "1027");
  if (!q) return {};
  auto sides = parse_flat(*q);
  auto side = [&](const char* t) -> std::optional<std::array<double, 9>> {
    auto x = flat_find(sides, t);
    if (!x) return {};
    auto s1 = parse_flat(*x);
    x = flat_find(s1, "1327");
    if (!x) return {};
    auto s2 = parse_flat(*x);
    x = flat_find(s2, "1527");
    if (!x || x->size() != 72) return {};
    std::array<double, 9> m{};
    for (int i = 0; i < 9; ++i) m[i] = read_f64(*x, i * 8);
    return m;
  };
  return {side("1127"), side("1227")};
}

static void scan_properties(const ByteBuffer& p, std::map<std::string, std::string>& out) {
  std::string key;
  std::function<void(size_t, size_t)> walk = [&](size_t a, size_t z) {
    while (a + 6 <= z) {
      auto n = read_u32(p, a + 2);
      if (n > z - a - 6) break;
      std::string t;
      static char h[] = "0123456789ABCDEF";
      t += h[p[a] >> 4];
      t += h[p[a] & 15];
      t += h[p[a + 1] >> 4];
      t += h[p[a + 1] & 15];
      if (t == "B636")
        key = std::string(reinterpret_cast<const char*>(p.data() + a + 6), n);
      else if (t == "AD38" && !key.empty()) {
        out[key] = std::string(reinterpret_cast<const char*>(p.data() + a + 6), n);
        key.clear();
      } else if (t == "DD05" || t == "B536" || t == "B136" || t == "B236" || t == "B336" ||
                 t == "B036" || t == "A438")
        walk(a + 6, a + 6 + n);
      a += 6 + n;
    }
  };
  walk(0, p.size());
}

void collect_geometry(const std::vector<TlvNode>& es, GeometryBuilder& b) {
  for (auto& e : es) {
    if (e.tag == "C409") {
      auto id = entity_id(e);
      auto* p = find_node(e.children, "C509");
      if (id && p && p->payload.size() >= 24)
        b.vertices[*id] = {read_f64(p->payload, 0), read_f64(p->payload, 8),
                           read_f64(p->payload, 16)};
    } else if (e.tag == "B80B") {
      auto id = entity_id(e);
      if (id) {
        auto *x = find_node(e.children, "B90B"), *y = find_node(e.children, "BA0B");
        std::optional<EntityId> a, c;
        if (x && !x->payload.empty()) a = parse_varint(x->payload, 0, x->payload.size());
        if (y && !y->payload.empty()) c = parse_varint(y->payload, 0, y->payload.size());
        b.edges[*id] = {a, c};
        for (auto& z : e.children)
          if (z.tag == "D007")
            for (auto& w : z.children)
              if (w.tag == "D307" && !w.payload.empty()) b.edge_flags[*id] = w.payload[0];
      }
    } else if (e.tag == "AC0D") {
      auto id = entity_id(e);
      if (id) {
        RawFace f;
        auto* n = find_node(e.children, "AD0D");
        if (n && n->payload.size() >= 24)
          f.normal = {read_f64(n->payload, 0), read_f64(n->payload, 8), read_f64(n->payload, 16)};
        auto* l = find_node(e.children, "AE0D");
        if (l) {
          std::vector<const TlvNode*> loops;
          find_all(l->children, "9411", loops);
          for (auto* ln : loops) {
            std::vector<CoEdge> co;
            std::vector<const TlvNode*> cs;
            find_all(ln->children, "A00F", cs);
            for (auto* cn : cs) {
              std::optional<EntityId> eid;
              std::optional<std::int64_t> ori;
              for (auto& v : parse_flat(cn->payload)) {
                if (v.first == "A10F")
                  eid = parse_varint(v.second, 0, v.second.size());
                else if (v.first == "A20F")
                  ori = parse_varint(v.second, 0, v.second.size());
              }
              if (eid && ori) co.push_back({*eid, *ori});
            }
            if (!co.empty()) f.loops.push_back(std::move(co));
          }
        }
        for (auto& d : e.children)
          if (d.tag == "D007")
            for (auto& x : d.children) {
              if (x.tag == "D107" && !x.payload.empty())
                f.material_id = parse_varint(x.payload, 0, x.payload.size());
              else if (x.tag == "DC05")
                std::tie(f.uv_transform, f.uv_transform_back) = uv(x.payload);
            }
        for (auto& x : e.children)
          if (x.tag == "AF0D" && !x.payload.empty())
            f.back_material_id = parse_varint(x.payload, 0, x.payload.size());
        b.faces[*id] = std::move(f);
      }
    } else if (e.tag == "6419") {
      RawInstance i;
      i.offset = e.offset;
      i.children = e.children;
      auto* g = find_node(e.children, "6819");
      if (g && g->payload.size() == 16) i.ref_guid = hex(g->payload);
      auto* r = find_node(e.children, "6719");
      if (r && !r->payload.empty()) i.ref_idx = parse_varint(r->payload, 0, r->payload.size());
      auto* n = find_node(e.children, "6519");
      if (n) i.name = text(n->payload);
      auto* m = find_node(e.children, "6619");
      if (m && m->payload.size() >= 104)
        for (int q = 0; q < 13; ++q) i.matrix.push_back(read_f64(m->payload, q * 8));
      for (auto& d : e.children)
        if (d.tag == "D007")
          for (auto& x : d.children) {
            if (x.tag == "D107" && !x.payload.empty())
              i.material_id = parse_varint(x.payload, 0, x.payload.size());
            else if (x.tag == "D207" && !x.payload.empty())
              i.layer = std::to_string(parse_varint(x.payload, 0, x.payload.size()));
            else if (x.tag == "DC05")
              scan_properties(x.payload, i.properties);
          }
      b.instances.push_back(std::move(i));
    } else if (!e.children.empty())
      collect_geometry(e.children, b);
  }
}

void collect_layers(const std::vector<TlvNode>& ns, std::map<EntityId, std::string>& out) {
  for (auto& e : ns) {
    if (e.tag == "993A")
      for (auto& c : e.children)
        if (c.tag == "8C3C") {
          auto *d = find_node(c.children, "DC05"), *n = find_node(c.children, "8D3C");
          if (d && n && !d->payload.empty()) out[dc_id(d->payload)] = text(n->payload);
        }
    collect_layers(e.children, out);
  }
}

void collect_material_ids(const std::vector<TlvNode>& ns, std::map<EntityId, std::string>& out) {
  for (auto& e : ns) {
    if (e.tag == "C832") {
      auto *d = find_node(e.children, "DC05"), *n = find_node(e.children, "CC32");
      if (d && n && !d->payload.empty()) out[dc_id(d->payload)] = text(n->payload);
    }
    collect_material_ids(e.children, out);
  }
}

void collect_definitions(const std::vector<TlvNode>& ns, std::map<EntityId, RawDefinition>& out) {
  for (auto& e : ns) {
    if (e.tag == "7C15") {
      RawDefinition d;
      for (auto& c : e.children) {
        if (c.tag == "7D15" && c.payload.size() == 16)
          d.guid = hex(c.payload);
        else if (c.tag == "7E15")
          d.name = text(c.payload);
        else if (c.tag == "8315" && !c.payload.empty())
          d.is_image = parse_varint(c.payload, 0, c.payload.size()) == 2;
        else if (c.tag == "581B")
          for (auto& v : parse_flat(c.payload))
            if (v.first == "5D1B" && !v.second.empty())
              d.always_faces_camera = parse_varint(v.second, 0, v.second.size()) == 1;
      }
      collect_geometry(e.children, d.builder);
      if (auto id = entity_id(e)) out[*id] = std::move(d);
    }
    collect_definitions(e.children, out);
  }
}
}  // namespace openskp
