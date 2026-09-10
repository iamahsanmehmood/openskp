#include <chrono>
#include <iostream>

#include <openskp/fragments_export.hpp>
#include <openskp/openskp.hpp>

int main(int argc, char** argv) {
  if (argc != 3) {
    std::cerr << "usage: openskp_fragtest model.skp output.frag\n";
    return 2;
  }
  try {
    auto t0 = std::chrono::steady_clock::now();
    auto file = openskp::SkpFile::open(argv[1]);
    auto scene = file.build_instanced_scene();
    auto t1 = std::chrono::steady_clock::now();
    openskp::export_fragments(scene, argv[2]);
    auto t2 = std::chrono::steady_clock::now();

    auto build_ms = std::chrono::duration<double, std::milli>(t1 - t0).count();
    auto export_ms = std::chrono::duration<double, std::milli>(t2 - t1).count();
    auto total_ms = std::chrono::duration<double, std::milli>(t2 - t0).count();

    std::cout << "mesh_resources=" << scene.mesh_resources.size() << "\n"
              << "build_instanced_scene_ms=" << build_ms << "\n"
              << "export_fragments_ms=" << export_ms << "\n"
              << "total_ms=" << total_ms << "\n";
  } catch (const std::exception& e) {
    std::cerr << e.what() << '\n';
    return 1;
  }
}
