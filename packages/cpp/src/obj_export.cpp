#include "openskp/obj_export.hpp"

#include <fstream>
#include <iomanip>
#include <sstream>
#include <stdexcept>

namespace openskp {

std::string to_obj(const Scene& scene) {
  std::ostringstream ss;
  ss.imbue(std::locale::classic());
  ss << "# OpenSKP OBJ Export\n";
  ss << "# Primitives: " << scene.glb_primitives.size() << "\n\n";

  std::uint32_t vert_offset = 1;  // OBJ indices are 1-based
  for (const auto& prim : scene.glb_primitives) {
    ss << "o " << prim.geom_name << "\n";

    std::size_t vert_count = prim.positions.size() / 3;
    for (std::size_t i = 0; i < vert_count; ++i) {
      ss << "v " << std::fixed << std::setprecision(6) << prim.positions[i * 3] << " "
         << prim.positions[i * 3 + 1] << " " << prim.positions[i * 3 + 2] << "\n";
    }

    std::size_t tri_count = prim.indices.size() / 3;
    for (std::size_t i = 0; i < tri_count; ++i) {
      std::uint32_t i0 = prim.indices[i * 3] + vert_offset;
      std::uint32_t i1 = prim.indices[i * 3 + 1] + vert_offset;
      std::uint32_t i2 = prim.indices[i * 3 + 2] + vert_offset;
      ss << "f " << i0 << " " << i1 << " " << i2 << "\n";
    }

    vert_offset += static_cast<std::uint32_t>(vert_count);
    ss << "\n";
  }

  return ss.str();
}

void export_obj(const Scene& scene, const std::filesystem::path& path) {
  auto parent = path.parent_path();
  if (!parent.empty() && !std::filesystem::exists(parent)) {
    std::filesystem::create_directories(parent);
  }
  std::ofstream out(path);
  if (!out) {
    throw std::runtime_error("Cannot open OBJ output file: " + path.string());
  }
  out << to_obj(scene);
  if (!out) {
    throw std::runtime_error("Failed to write OBJ output file: " + path.string());
  }
}

}  // namespace openskp
