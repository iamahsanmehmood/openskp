#ifndef OPENSKP_IFC_EXPORT_HPP
#define OPENSKP_IFC_EXPORT_HPP

#include <filesystem>
#include <functional>
#include <string>

#include "model.hpp"
#include "scene.hpp"

namespace openskp {

// 1 metre = 39.37007874015748 inches (SketchUp native unit)
constexpr double METRES_TO_INCHES = 39.37007874015748;

/// A (STEP_ENTITY_TYPE, IFC_CLASS_NAME) classification, and the signature
/// a custom classifier passed to to_ifc()/export_ifc() must match.
using IfcClassifier =
    std::function<std::pair<std::string, std::string>(const std::string&, const std::string&)>;

/**
 * Generate a standard 22-character IFC base64 compressed GUID.
 */
std::string generate_ifc_guid();

/**
 * Classify a geometry/component name to an IFC entity type (STEP_TYPE, CLASS_NAME).
 *
 * Tries geom_name first, then falls back to layer_name (many
 * SketchUp-for-BIM workflows organize by tag/layer - "Walls", "Doors" -
 * even when individual components are never renamed away from
 * SketchUp's own defaults like "Component#109415"), then falls back to a
 * generic, untyped element if neither matches.
 */
std::pair<std::string, std::string> classify_element(const std::string& geom_name,
                                                     const std::string& layer_name = "");

/**
 * Serialize a baked Scene into ISO-10303-21 STEP ASCII IFC4 format.
 *
 * @param scene The baked scene returned by SkpFile::build_scene()
 * @param scale Scale factor for vertex coordinates (default: METRES_TO_INCHES)
 * @param schema IFC schema version (default: "IFC4")
 * @param classifier Optional override for classify_element() - supply your
 *   own naming convention or metadata-driven typing instead of the
 *   built-in keyword/layer heuristic.
 * @return Formatted ASCII IFC text string.
 */
std::string to_ifc(const Scene& scene, double scale = METRES_TO_INCHES,
                   const std::string& schema = "IFC4", const IfcClassifier& classifier = nullptr);

/**
 * Export a baked Scene directly to an ISO-10303-21 STEP ASCII IFC4 file.
 *
 * @param scene The baked scene returned by SkpFile::build_scene()
 * @param path Destination file path (.ifc)
 * @param scale Scale factor for vertex coordinates (default: METRES_TO_INCHES)
 * @param schema IFC schema version (default: "IFC4")
 * @param classifier Optional override for classify_element() - see to_ifc().
 */
void export_ifc(const Scene& scene, const std::filesystem::path& path,
                double scale = METRES_TO_INCHES, const std::string& schema = "IFC4",
                const IfcClassifier& classifier = nullptr);

}  // namespace openskp

#endif  // OPENSKP_IFC_EXPORT_HPP
