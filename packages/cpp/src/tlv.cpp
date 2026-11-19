#include <algorithm>
#include <iomanip>
#include <sstream>

#include "internal.hpp"

namespace openskp {
static void require_range(const ByteBuffer& d, std::size_t o, std::size_t n) {
  if (o > d.size() || n > d.size() - o) throw std::out_of_range("binary read exceeds buffer");
}

std::string bytes_to_upper_hex(const ByteBuffer& bytes) {
  constexpr char hex[] = "0123456789ABCDEF";
  std::string result;
  result.reserve(bytes.size() * 2);
  for (const auto byte : bytes) {
    result += hex[byte >> 4];
    result += hex[byte & 15];
  }
  return result;
}

// Classic CArchive and VFF both store UUID Data1/Data2/Data3 in Windows order.
std::string guid_from_windows_bytes(const ByteBuffer& bytes) {
  if (bytes.size() != 16) throw std::invalid_argument("GUID must contain exactly 16 bytes");
  constexpr std::size_t order[]{3, 2, 1, 0, 5, 4, 7, 6, 8, 9, 10, 11, 12, 13, 14, 15};
  constexpr char hex[] = "0123456789ABCDEF";
  std::string result;
  result.reserve(32);
  for (const auto i : order) {
    result += hex[bytes[i] >> 4];
    result += hex[bytes[i] & 15];
  }
  return result;
}

std::uint16_t read_u16(const ByteBuffer& d, std::size_t o) {
  require_range(d, o, 2);
  return std::uint16_t(d[o]) | std::uint16_t(d[o + 1]) << 8;
}

std::uint32_t read_u32(const ByteBuffer& d, std::size_t o) {
  require_range(d, o, 4);
  return std::uint32_t(d[o]) | std::uint32_t(d[o + 1]) << 8 | std::uint32_t(d[o + 2]) << 16 |
         std::uint32_t(d[o + 3]) << 24;
}

std::int32_t read_i32(const ByteBuffer& d, std::size_t o) {
  return static_cast<std::int32_t>(read_u32(d, o));
}

double read_f64(const ByteBuffer& d, std::size_t o) {
  require_range(d, o, 8);
  std::uint64_t bits = 0;
  for (int i = 0; i < 8; ++i) bits |= std::uint64_t(d[o + i]) << (8 * i);
  double v;
  std::memcpy(&v, &bits, 8);
  return v;
}

std::uint64_t parse_varint(const ByteBuffer& d, std::size_t o, std::size_t n) {
  require_range(d, o, n);
  if (n > 8) throw std::overflow_error("varint exceeds 64 bits");
  std::uint64_t v = 0;
  for (std::size_t i = 0; i < n; ++i) v |= std::uint64_t(d[o + i]) << (8 * i);
  return v;
}

std::string tag_at(const ByteBuffer& d, std::size_t o) {
  require_range(d, o, 2);
  static const char h[] = "0123456789ABCDEF";
  std::string s(4, '0');
  s[0] = h[d[o] >> 4];
  s[1] = h[d[o] & 15];
  s[2] = h[d[o + 1] >> 4];
  s[3] = h[d[o + 1] & 15];
  return s;
}

// VFF (2021+) model.dat binary structure uses Type-Length-Value (TLV) records.
// Most TLV tags carry raw leaf binary/string payloads, but container tags contain
// nested sub-TLV nodes (e.g. definitions, drawing elements, component instances).
// containers lists every tag hex ID that the TLV parser must recursively traverse.
static const std::set<std::string> containers = {
    "F401", "F701", "D430", "D530", "C832", "7C15", "8813", "8913", "8A13", "8B13", "8C13",
    "8D13", "4C1D", "6419", "F901", "7017", "7117", "D007", "C409", "9411", "9511", "0F01",
    "384A", "B80B", "9713", "2C4C", "AC0D", "AE0D", "F601", "F801", "983A", "993A", "8C3C",
    "8D3C", "9013", "401F", "9113", "9213", "6942", "6C42", "6842"};

namespace {
constexpr std::uint64_t kTlvWrap = std::uint64_t{1} << 32;

// Recovery operates on views of ordinary record chains, not copied header
// lists. The same chain answers both successful and failed range queries,
// including queries with different parent ends. Equal-sized records form runs
// so zero-filled opaque data does not allocate a node for every six bytes.
class HeaderResolver {
  struct Run {
    std::size_t offset, size, count;

    std::size_t step() const { return size + 6; }

    std::size_t end() const { return offset + count * step(); }
  };

  struct Chain {
    std::vector<Run> runs;
    std::vector<TlvHeader> candidates;
    bool indexed = false;

    std::size_t end() const { return runs.back().end(); }

    std::size_t run_at(std::size_t p) const {
      auto it = std::upper_bound(runs.begin(), runs.end(), p,
                                 [](std::size_t v, const Run& r) { return v < r.offset; });
      return static_cast<std::size_t>(it - runs.begin() - 1);
    }

    bool contains(std::size_t p) const {
      if (p < runs.front().offset || p >= end()) return false;
      const auto& r = runs[run_at(p)];
      return (p - r.offset) % r.step() == 0;
    }

    std::size_t clip(std::size_t limit) const {
      if (limit >= end()) return end();
      const auto& r = runs[run_at(limit)];
      return r.offset + (limit - r.offset) / r.step() * r.step();
    }

    void append(std::size_t p, std::size_t size) {
      if (!runs.empty() && runs.back().size == size && end() == p)
        ++runs.back().count;
      else
        runs.push_back({p, size, 1});
    }
  };

  struct View {
    std::shared_ptr<Chain> chain;
    std::size_t begin, end;
  };

  struct Plan {
    std::vector<View> views;
    std::size_t stop;
  };

  const std::function<std::uint32_t(std::size_t)>& length_;
  const std::function<bool(std::size_t)>& container_;
  const std::size_t limit_;
  std::size_t attempts_ = 256;
  std::vector<std::shared_ptr<Chain>> chains_;
  // Only completed decisions are cached. Resource-limit errors propagate;
  // they are never converted into an ordinary invalid partition.
  std::map<std::tuple<std::size_t, std::size_t, int>, std::optional<Plan>> decisions_;

  std::shared_ptr<Chain> chain_at(std::size_t p) const {
    for (auto it = chains_.rbegin(); it != chains_.rend(); ++it)
      if ((*it)->contains(p)) return *it;
    return {};
  }

  Plan ordinary(std::size_t start, std::size_t end) {
    Plan plan{{}, start};
    auto& p = plan.stop;
    while (end - p >= 6) {
      if (auto chain = chain_at(p)) {
        const auto next = chain->clip(end);
        if (next > p) plan.views.push_back({chain, p, next});
        p = next;
        if (p < chain->end()) return plan;  // A record crosses this parent end.
        continue;
      }
      auto chain = std::make_shared<Chain>();
      const auto begin = p;
      std::size_t probes = 0;
      bool joined = false;
      while (end - p >= 6) {
        // A new alignment can converge on a cached chain. Check periodically
        // to bound duplicate reads without searching every chain per record.
        if (probes && probes % 1024 == 0 && chain_at(p)) {
          joined = true;
          break;
        }
        const std::size_t size = length_(p);
        if (size > end - p - 6) break;
        chain->append(p, size);
        p += 6 + size;
        ++probes;
      }
      if (p > begin) {
        chains_.push_back(chain);
        plan.views.push_back({chain, begin, p});
      }
      if (!joined) return plan;
    }
    return plan;
  }

  void index(Chain& chain) {
    if (chain.indexed) return;
    chain.indexed = true;
    for (const auto& r : chain.runs) {
      for (auto p = r.offset; p < r.end(); p += r.step()) {
        if (limit_ - p - r.step() < kTlvWrap) return;
        if (container_(p)) chain.candidates.push_back({p, r.size});
      }
    }
  }

  static Plan replace(const Plan& original, const TlvHeader& h, std::size_t size,
                      const Plan& suffix, std::size_t end) {
    Plan result{{}, end};
    for (const auto& v : original.views) {
      if (v.begin >= h.offset) break;
      result.views.push_back({v.chain, v.begin, std::min(v.end, h.offset)});
      if (v.end >= h.offset) break;
    }
    auto extended = std::make_shared<Chain>();
    extended->append(h.offset, size);
    // A corrected header belongs only to the selected plan, never to the raw
    // chain cache (its stored 32-bit length still has the original value).
    result.views.push_back({extended, h.offset, h.offset + 6 + size});
    result.views.insert(result.views.end(), suffix.views.begin(), suffix.views.end());
    return result;
  }

  std::optional<Plan> solve(std::size_t start, std::size_t end, int depth) {
    auto normal = ordinary(start, end);
    if (normal.stop == end) return normal;  // Prefer an exact stored partition.
    const auto key = std::make_tuple(start, end, depth);
    if (auto it = decisions_.find(key); it != decisions_.end()) return it->second;
    for (const auto& v : normal.views) {
      index(*v.chain);
      const auto& candidates = v.chain->candidates;
      auto it = std::lower_bound(candidates.begin(), candidates.end(), v.begin,
                                 [](const TlvHeader& h, std::size_t p) { return h.offset < p; });
      for (; it != candidates.end() && it->offset < v.end; ++it) {
        const auto h = *it;
        const auto available = end - h.offset - 6;
        if (available - h.size < kTlvWrap) break;
        for (std::uint64_t n = std::uint64_t(h.size) + kTlvWrap; n <= available; n += kTlvWrap) {
          if (depth >= kMaxTlvDepth || attempts_ == 0)
            throw SkpParseError("TLV length recovery exceeded its depth or attempt limit",
                                ParseStage::tlv_walk, {}, {}, {}, h.offset);
          --attempts_;
          const auto next = h.offset + 6 + static_cast<std::size_t>(n);
          auto suffix = solve(next, end, depth + 1);
          if (suffix && solve(h.offset + 6, next, depth + 1)) {
            auto result = replace(normal, h, static_cast<std::size_t>(n), *suffix, end);
            decisions_.emplace(key, result);
            return result;
          }
          if (available - n < kTlvWrap) break;
        }
      }
    }
    decisions_.emplace(key, std::nullopt);
    return std::nullopt;
  }

  static std::vector<TlvHeader> materialize(const Plan& plan) {
    std::vector<TlvHeader> out;
    for (const auto& v : plan.views) {
      auto i = v.chain->run_at(v.begin);
      while (i < v.chain->runs.size() && v.chain->runs[i].offset < v.end) {
        const auto& r = v.chain->runs[i++];
        const auto end = std::min(v.end, r.end());
        for (auto p = std::max(v.begin, r.offset); p < end; p += r.step())
          out.push_back({p, r.size});
      }
    }
    return out;
  }

 public:
  HeaderResolver(const std::function<std::uint32_t(std::size_t)>& length,
                 const std::function<bool(std::size_t)>& container, std::size_t end)
      : length_(length), container_(container), limit_(end) {}

  std::vector<TlvHeader> headers(std::size_t start, std::size_t end) {
    if (auto plan = solve(start, end, 0)) return materialize(*plan);
    return materialize(ordinary(start, end));  // Preserve nested leniency.
  }
};
}  // namespace

std::vector<TlvHeader> tlv_headers(std::size_t start, std::size_t end,
                                   const std::function<std::uint32_t(std::size_t)>& length,
                                   const std::function<bool(std::size_t)>& container) {
  if (start > end) throw std::out_of_range("invalid TLV range");
  if (std::uint64_t(end - start) >= kTlvWrap)
    return HeaderResolver(length, container, end).headers(start, end);
  std::vector<TlvHeader> out;
  auto p = start;
  while (end - p >= 6) {
    const std::size_t n = length(p);
    if (n > end - p - 6) break;
    out.push_back({p, n});
    p += 6 + n;
  }
  return out;
}

std::vector<TlvHeader> tlv_headers(const ByteBuffer& d, std::size_t start, std::size_t end) {
  if (end > d.size()) throw std::out_of_range("invalid TLV range");
  return tlv_headers(
      start, end, [&](std::size_t p) { return read_u32(d, p + 2); },
      [&](std::size_t p) { return containers.count(tag_at(d, p)) != 0; });
}

void throw_tlv_too_deep(const std::string& tag, std::size_t offset) {
  throw SkpParseError("TLV records nest deeper than " + std::to_string(kMaxTlvDepth) + " levels",
                      ParseStage::tlv_walk, {}, {}, tag, offset);
}

std::vector<TlvNode> parse_tlv_recursive(const ByteBuffer& d, std::size_t start, std::size_t end,
                                         int depth) {
  if (end > d.size() || start > end) throw std::out_of_range("invalid TLV range");
  std::vector<TlvNode> out;
  for (const auto& header : tlv_headers(d, start, end)) {
    const auto p = header.offset;
    const auto size = header.size;
    auto tag = tag_at(d, p);
    TlvNode n;
    n.offset = p;
    n.size = size;
    n.tag = tag;
    if (size && containers.count(tag)) {
      if (depth >= kMaxTlvDepth) throw_tlv_too_deep(tag, p);
      n.children = parse_tlv_recursive(d, p + 6, p + 6 + size, depth + 1);
    }
    if (n.children.empty() && size)
      n.payload.assign(d.begin() + static_cast<std::ptrdiff_t>(p + 6),
                       d.begin() + static_cast<std::ptrdiff_t>(p + 6 + size));
    out.push_back(std::move(n));
  }
  return out;
}

std::vector<std::pair<std::string, ByteBuffer>> parse_flat(const ByteBuffer& d) {
  std::vector<std::pair<std::string, ByteBuffer>> out;
  std::size_t p = 0;
  while (d.size() - std::min(p, d.size()) >= 6) {
    const std::size_t n = read_u32(d, p + 2);
    if (n > d.size() - p - 6) break;
    out.push_back({tag_at(d, p), ByteBuffer(d.begin() + p + 6, d.begin() + p + 6 + n)});
    p += 6 + n;
  }
  return out;
}
}  // namespace openskp
