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
    std::cout << "ok: " << model.definitions.size() << " definitions, " << model.pages.size()
              << " pages\n";
    for (auto& pg : model.pages) {
      std::cout << "  page '" << pg.name << "' hidden_layers=" << pg.hidden_layers.size() << "\n";
    }
  } catch (const std::exception& e) {
    std::cerr << "EXC: " << e.what() << '\n';
    return 1;
  }
}
