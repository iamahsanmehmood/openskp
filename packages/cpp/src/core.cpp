#include <algorithm>
#include <cctype>
#include <miniz.h>
#include <regex>

#include "internal.hpp"

namespace openskp {
namespace {
struct Zip {
  mz_zip_archive z{};
  std::vector<std::string> names;

  Zip(const ByteBuffer& d, std::size_t off) {
    if (!mz_zip_reader_init_mem(&z, d.data() + off, d.size() - off, 0))
      throw SkpParseError("Invalid ZIP container", ParseStage::zip_extract);
    auto n = mz_zip_reader_get_num_files(&z);
    for (mz_uint i = 0; i < n; ++i) {
      mz_zip_archive_file_stat s{};
      if (mz_zip_reader_file_stat(&z, i, &s) && !s.m_is_directory) names.emplace_back(s.m_filename);
    }
  }

  ~Zip() { mz_zip_reader_end(&z); }

  Zip(const Zip&) = delete;

  std::optional<ByteBuffer> get(const std::string& name) {
    int i = mz_zip_reader_locate_file(&z, name.c_str(), nullptr, 0);
    if (i < 0) return {};
    size_t n = 0;
    void* p = mz_zip_reader_extract_to_heap(&z, i, &n, 0);
    if (!p) return {};
    ByteBuffer b(static_cast<std::uint8_t*>(p), static_cast<std::uint8_t*>(p) + n);
    mz_free(p);
    return b;
  }
};

std::size_t zip_offset(const ByteBuffer& d) {
  for (std::size_t i = 0; i + 4 <= d.size() && i < 4096; ++i)
    if (d[i] == 'P' && d[i + 1] == 'K' && d[i + 2] == 3 && d[i + 3] == 4) return i;
  return std::string::npos;
}

struct Header {
  std::size_t offset;
  std::size_t size;
};

std::vector<Header> headers(const ByteBuffer& d, std::size_t start, std::size_t end) {
  std::vector<Header> h;
  for (auto p = start; p + 6 <= end;) {
    auto n = read_u32(d, p + 2);
    if (n > end - p - 6) break;
    h.push_back({p, n});
    p += 6 + n;
  }
  return h;
}

std::string str(const ByteBuffer& b) { return {reinterpret_cast<const char*>(b.data()), b.size()}; }

std::string attr(const std::string& s, const std::string& key) {
  std::regex r("(?:^|\\s)" + key + "\\s*=\\s*[\\\"']([^\\\"']*)[\\\"']", std::regex::icase);
  std::smatch m;
  return std::regex_search(s, m, r) ? m[1].str() : "";
}

int integer(const std::string& s, int fallback) {
  try {
    return s.empty() ? fallback : std::stoi(s);
  } catch (...) {
    return fallback;
  }
}

double number(const std::string& s, double fallback) {
  try {
    return s.empty() ? fallback : std::stod(s);
  } catch (...) {
    return fallback;
  }
}

std::string basename(const std::string& s) {
  auto p = s.find_last_of('/');
  return p == std::string::npos ? s : s.substr(p + 1);
}

std::shared_ptr<RawMaterial> material_xml(Zip& zip, const std::string& path,
                                          const ByteBuffer& bytes) {
  auto xml = str(bytes);
  std::regex tag("<(?:[A-Za-z_][\\w.-]*:)?material\\b([^>]*)>", std::regex::icase);
  std::smatch m;
  if (!std::regex_search(xml, m, tag)) return {};
  auto a = m[1].str();
  auto out = std::make_shared<RawMaterial>();
  out->name = attr(a, "name");
  if (out->name.empty()) out->name = "unknown";
  out->r = integer(attr(a, "colorRed"), 128);
  out->g = integer(attr(a, "colorGreen"), 128);
  out->b = integer(attr(a, "colorBlue"), 128);
  if (attr(a, "useTrans") == "1")
    out->transparency = std::clamp(1.0 - number(attr(a, "trans"), 0), 0.0, 1.0);
  out->colorized = attr(a, "type") == "2";
  out->colorize_type = integer(attr(a, "colorizeType"), 0);
  std::regex tr("<(?:[A-Za-z_][\\w.-]*:)?texture\\b([^>]*)>", std::regex::icase);
  if (std::regex_search(xml, m, tr)) {
    auto ta = m[1].str();
    RawTexture t;
    t.filename = attr(ta, "textureFilename");
    t.x_scale = number(attr(ta, "xScale"), 0);
    t.y_scale = number(attr(ta, "yScale"), 0);
    auto slash = path.find_last_of('/');
    auto folder = slash == std::string::npos ? std::string{} : path.substr(0, slash);
    auto candidate = folder + "/" + t.filename;
    if (!t.filename.empty()) t.data = zip.get(candidate);
    if (!t.data)
      for (auto& n : zip.names)
        if (n.rfind(folder + "/", 0) == 0 && n != path && n.size() > 4 &&
            n.substr(n.size() - 4) != ".xml") {
          t.data = zip.get(n);
          if (t.filename.empty()) t.filename = basename(n);
          break;
        }
    if (!t.data) {
      std::regex ir("<(?:[A-Za-z_][\\w.-]*:)?image\\b([^>]*)", std::regex::icase);
      if (std::regex_search(xml, m, ir)) {
        auto ip = attr(m[1].str(), "path");
        while (!ip.empty() && (ip[0] == '.' || ip[0] == '/')) ip.erase(ip.begin());
        for (auto& c : {ip, folder + "/" + ip})
          if (!c.empty() && (t.data = zip.get(c))) {
            if (t.filename.empty()) t.filename = basename(c);
            break;
          }
      }
    }
    out->texture = std::move(t);
  }
  return out;
}

std::optional<RawStyle> style_xml(const ByteBuffer& bytes) {
  auto xml = str(bytes);
  std::regex st("<(?:[A-Za-z_][\\w.-]*:)?style\\b([^>]*)>", std::regex::icase);
  std::smatch m;
  if (!std::regex_search(xml, m, st)) return {};
  RawStyle o;
  o.name = attr(m[1].str(), "name");
  std::regex item(
      "<(?:[A-Za-z_][\\w.-]*:)?item\\b([^>]*)>([\\s\\S]*?)</(?:[A-Za-z_][\\w.-]*:)?item>",
      std::regex::icase);
  for (auto i = std::sregex_iterator(xml.begin(), xml.end(), item); i != std::sregex_iterator();
       ++i) {
    auto id = attr((*i)[1].str(), "id");
    if (id != "4000" && id != "4001") continue;
    std::regex vr("<(?:[A-Za-z_][\\w.-]*:)?variant[^>]*>\\s*(-?\\d+)", std::regex::icase);
    std::smatch v;
    auto body = (*i)[2].str();
    if (std::regex_search(body, v, vr)) {
      auto n = static_cast<std::uint32_t>(std::stoll(v[1].str()));
      Color3 c{std::uint8_t(n >> 16), std::uint8_t(n >> 8), std::uint8_t(n)};
      if (id == "4000")
        o.front_color = c;
      else
        o.back_color = c;
    }
  }
  return o;
}
}  // namespace

RawParsed full_parse(const ByteBuffer& data, const ParseOptions& o) {
  emit_log(o, LogLevel::information, "Parsing buffer (" + std::to_string(data.size()) + " bytes)");
  if (!valid_header(data))
    throw SkpParseError("Not a valid SketchUp file (bad header magic)", ParseStage::header);
  if (is_legacy(data)) {
    emit_log(o, LogLevel::debug, "Detected legacy MFC container; routing to legacy walker");
    return parse_legacy(data, o);
  }
  RawParsed p;
  p.version = extract_version(data);
  auto off = zip_offset(data);
  if (off == std::string::npos)
    throw SkpParseError("No ZIP container found", ParseStage::zip_extract);
  Zip zip(data, off);
  for (auto& n : zip.names)
    if (n.rfind("materials/", 0) == 0 && n.size() >= 12 &&
        n.substr(n.size() - 12) == "material.xml") {
      if (auto b = zip.get(n))
        if (auto m = material_xml(zip, n, *b)) {
          auto slash = n.find('/', 10);
          auto folder = n.substr(10, slash == std::string::npos ? std::string::npos : slash - 10);
          p.materials[m->name] = m;
          p.materials_by_folder[folder] = m;
          if (m->name.rfind("Layer_", 0) == 0)
            p.layer_colors[m->name.substr(6)] = {std::uint8_t(m->r), std::uint8_t(m->g),
                                                 std::uint8_t(m->b)};
        }
    }
  for (auto& n : zip.names)
    if (n.rfind("styles/", 0) == 0 && n.size() >= 9 && n.substr(n.size() - 9) == "style.xml")
      if (auto b = zip.get(n))
        if (auto s = style_xml(*b)) p.styles.push_back(*s);
  auto model = zip.get("model.dat");
  if (!model) throw SkpParseError("model.dat not found in ZIP container", ParseStage::zip_extract);
  auto hs = headers(*model, 0, model->size());
  if (hs.size() == 1 && model->at(hs[0].offset) == 0xf4 && model->at(hs[0].offset + 1) == 1)
    hs = headers(*model, hs[0].offset + 6, hs[0].offset + 6 + hs[0].size);
  auto total = hs.size();
  for (std::size_t i = 0; i < total; ++i) {
    std::string tag;
    try {
      auto one = parse_tlv_recursive(*model, hs[i].offset, hs[i].offset + 6 + hs[i].size);
      if (one.empty()) continue;
      tag = one[0].tag;
      collect_layers(one, p.layer_id_to_name);
      collect_material_ids(one, p.material_id_to_name);
      collect_definitions(one, p.definitions);
      if (one[0].tag == "F601") collect_geometry(one[0].children, p.root.builder);
    } catch (const SkpParseError&) {
      throw;
    } catch (...) {
      throw SkpParseError("Failed while processing top-level record", ParseStage::tlv_walk, i,
                          total, tag, hs[i].offset, {}, std::current_exception());
    }
    if (i % progress_interval == 0 || i + 1 == total)
      emit_progress(o, ParseStage::tlv_walk, i + 1, total);
  }
  if (!p.layer_id_to_name.count(1)) p.layer_id_to_name[1] = "Layer0";
  if (!p.layer_colors.count("Layer0")) p.layer_colors["Layer0"] = {136, 136, 136};
  emit_log(o, LogLevel::information,
           "Parse complete: " + std::to_string(p.definitions.size()) + " defs");
  return p;
}
}  // namespace openskp
