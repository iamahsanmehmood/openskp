#include <chrono>
#include <iostream>

#include <openskp/openskp.hpp>

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: openskp_bench model.skp\n";
    return 2;
  }
  try {
    auto t0 = std::chrono::steady_clock::now();
    auto file = openskp::SkpFile::open(argv[1]);
    auto model = file.parse();
    auto t1 = std::chrono::steady_clock::now();
    auto scene = file.build_scene();
    auto t2 = std::chrono::steady_clock::now();

    auto parse_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    auto scene_ms = std::chrono::duration<double, std::milli>(t2 - t1).count();
    auto total_ms = std::chrono::duration<double, std::milli>(t2 - t0).count();

    std::cout << "definitions=" << model.definitions.size()
              << " primitives=" << scene.glb_primitives.size()
              << " meshes=" << scene.mesh_index.size() << "\n"
              << "parse_ms=" << parse_ms << "\n"
              << "build_scene_ms=" << scene_ms << "\n"
              << "total_ms=" << total_ms << "\n";
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
