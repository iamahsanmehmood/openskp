#include <exception>
#include <iostream>

#include <openskp/openskp.hpp>

int main(int argc, char* argv[]) {
  if (argc != 3) {
    std::cerr << "usage: openskp_export_glb input.skp output.glb\n";
    return 2;
  }

  try {
    const auto scene = openskp::SkpFile::open(argv[1]).build_scene();
    openskp::export_glb(scene, argv[2]);
  } catch (const std::exception& error) {
    std::cerr << "GLB export failed: " << error.what() << '\n';
    return 1;
  }
}
