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
// Only repair an inexact range. Each enlarged container must have an exact
// child partition and an exact remaining sibling partition. Never scan payload
// bytes for plausible tags. Bound speculative work on adversarial inputs.
class HeaderResolver {
 public:
  const std::function<std::uint32_t(std::size_t)>& length;
  const std::function<bool(std::size_t)>& container;
  // Charge branching, not the linear walk: a wrapped endpoint can land in
  // opaque zero-filled data containing arbitrarily many apparent empty TLVs.
  std::size_t attempts = 256;

  // Cache one intrinsically invalid ordinary suffix, keyed by its parent end.
  // Only boundaries after the last expandable container are safe to reuse:
  // an earlier container might still jump across the invalid suffix.
  std::size_t failed_end = 0;
  std::vector<std::size_t> failed_offsets;

  bool failed_suffix(std::size_t start, std::size_t end) const {
    return end == failed_end &&
           std::binary_search(failed_offsets.begin(), failed_offsets.end(), start);
  }

  void remember_failure(std::size_t end, const std::vector<TlvHeader>& records, std::size_t stop) {
    constexpr std::uint64_t wrap = std::uint64_t{1} << 32;
    std::size_t first = 0;
    for (std::size_t i = 0; i < records.size(); ++i) {
      const auto h = records[i];
      if (end - h.offset - 6 - h.size >= wrap && container(h.offset)) first = i + 1;
    }
    failed_end = end;
    failed_offsets.clear();
    for (std::size_t i = first; i < records.size(); ++i)
      failed_offsets.push_back(records[i].offset);
    failed_offsets.push_back(stop);
  }

  bool exact(std::size_t start, std::size_t end, std::vector<TlvHeader>& out, int depth) {
    if (depth >= kMaxTlvDepth) return false;
    auto p = start;
    while (end - p >= 6) {
      if (failed_suffix(p, end)) return repair(end, out, depth);
      const std::size_t n = length(p);
      if (n > end - p - 6) break;
      out.push_back({p, n});
      p += 6 + n;
    }
    if (p == end) return true;
    remember_failure(end, out, p);
    return repair(end, out, depth);
  }

  // The caller has already established that the ordinary partition is inexact.
  // Reuse it, and try earlier containers before records found inside payloads.
  bool repair(std::size_t end, std::vector<TlvHeader>& out, int depth) {
    if (depth >= kMaxTlvDepth || attempts == 0) return false;
    constexpr std::uint64_t wrap = std::uint64_t{1} << 32;
    for (std::size_t i = 0; i < out.size(); ++i) {
      const auto h = out[i];
      const auto available = end - h.offset - 6;
      if (available - h.size < wrap || !container(h.offset)) continue;
      for (std::uint64_t n = std::uint64_t(h.size) + wrap; n <= available; n += wrap) {
        if (attempts == 0) return false;
        --attempts;
        const auto next = h.offset + 6 + static_cast<std::size_t>(n);
        std::vector<TlvHeader> suffix, children;
        if (exact(next, end, suffix, depth + 1) && exact(h.offset + 6, next, children, depth + 1)) {
          out.resize(i + 1);
          out.back().size = static_cast<std::size_t>(n);
          out.insert(out.end(), suffix.begin(), suffix.end());
          return true;
        }
        if (available - n < wrap) break;
      }
    }
    return false;
  }
};
}  // namespace

std::vector<TlvHeader> tlv_headers(std::size_t start, std::size_t end,
                                   const std::function<std::uint32_t(std::size_t)>& length,
                                   const std::function<bool(std::size_t)>& container) {
  if (start > end) throw std::out_of_range("invalid TLV range");
  std::vector<TlvHeader> out;
  auto p = start;
  while (end - p >= 6) {
    const std::size_t n = length(p);
    if (n > end - p - 6) break;
    out.push_back({p, n});
    p += 6 + n;
  }
  if (p != end && std::uint64_t(end - start) >= (std::uint64_t{1} << 32)) {
    HeaderResolver resolver{length, container};
    resolver.remember_failure(end, out, p);
    resolver.repair(end, out, 0);
  }
  return out;  // Preserve legacy leniency for ordinary nested records.
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
