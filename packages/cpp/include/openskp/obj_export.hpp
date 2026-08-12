#ifndef OPENSKP_OBJ_EXPORT_HPP
#define OPENSKP_OBJ_EXPORT_HPP

#include <filesystem>
#include <string>

#include "model.hpp"
#include "scene.hpp"

namespace openskp {

/// Serialize a baked \ref Scene to Wavefront OBJ text representation.
///
/// \param scene The baked scene returned by \ref SkpFile::build_scene.
/// \return The formatted OBJ text string.
std::string to_obj(const Scene& scene);

/// Export a baked \ref Scene to a Wavefront OBJ text file at \p path.
///
/// \param scene The baked scene returned by \ref SkpFile::build_scene.
/// \param path Destination file path (.obj).
void export_obj(const Scene& scene, const std::filesystem::path& path);

}  // namespace openskp

#endif  // OPENSKP_OBJ_EXPORT_HPP
