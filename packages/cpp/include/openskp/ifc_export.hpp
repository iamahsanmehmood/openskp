#ifndef OPENSKP_IFC_EXPORT_HPP
#define OPENSKP_IFC_EXPORT_HPP

#include <filesystem>
#include <string>

#include "model.hpp"
#include "scene.hpp"

namespace openskp {

// 1 metre = 39.37007874015748 inches (SketchUp native unit)
constexpr double METRES_TO_INCHES = 39.37007874015748;

/**
 * Generate a standard 22-character IFC base64 compressed GUID.
 */
std::string generate_ifc_guid();

/**
 * Classify a geometry name to an IFC entity type (STEP_TYPE, CLASS_NAME).
 */
std::pair<std::string, std::string> classify_element(const std::string& geom_name);

/**
 * Serialize a baked Scene into ISO-10303-21 STEP ASCII IFC4 format.
 *
 * @param scene The baked scene returned by SkpFile::build_scene()
 * @param scale Scale factor for vertex coordinates (default: METRES_TO_INCHES)
 * @param schema IFC schema version (default: "IFC4")
 * @return Formatted ASCII IFC text string.
 */
std::string to_ifc(const Scene& scene, double scale = METRES_TO_INCHES,
                   const std::string& schema = "IFC4");

/**
 * Export a baked Scene directly to an ISO-10303-21 STEP ASCII IFC4 file.
 *
 * @param scene The baked scene returned by SkpFile::build_scene()
 * @param path Destination file path (.ifc)
 * @param scale Scale factor for vertex coordinates (default: METRES_TO_INCHES)
 * @param schema IFC schema version (default: "IFC4")
 */
void export_ifc(const Scene& scene, const std::filesystem::path& path,
                double scale = METRES_TO_INCHES, const std::string& schema = "IFC4");

}  // namespace openskp

#endif  // OPENSKP_IFC_EXPORT_HPP
