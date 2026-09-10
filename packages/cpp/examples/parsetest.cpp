#include <iostream>

#include <openskp/openskp.hpp>

int main(int argc, char** argv) {
  if (argc != 2) {
    std::cerr << "usage: openskp_parsetest model.skp\n";
    return 2;
  }
  try {
    auto file = openskp::SkpFile::open(argv[1]);
    auto model = file.parse();
    std::cout << "ok: " << model.definitions.size() << " definitions\n";
  } catch (const std::exception& e) {
    std::cerr << "EXC: " << e.what() << '\n';
    return 1;
  }
}
