include(GoogleTest)

add_executable(
  openskp_tests
  create_test.cpp
  dxf_export_test.cpp
  edit_test.cpp
  face_instance_hidden_test.cpp
  geometry_test.cpp
  glb_test.cpp
  ifc_export_test.cpp
  json_export_test.cpp
  layer_hidden_test.cpp
  legacy_single_material_test.cpp
  legacy_v20_filler_test.cpp
  legacy_v20_test.cpp
  lowlevel_test.cpp
  meta_units_test.cpp
  obj_export_test.cpp
  observability_test.cpp
  parser_test.cpp
  ply_export_test.cpp
  scene_test.cpp
  stl_export_test.cpp
  texture_test.cpp
  triangulator_test.cpp
  zip_entry_size_cap_test.cpp
)

target_link_libraries(openskp_tests PRIVATE OpenSkp::OpenSkp GTest::gtest_main)

target_include_directories(
  openskp_tests
  PRIVATE
    ${PROJECT_SOURCE_DIR}/src
    ${OPENSKP_MINIZ_INCLUDE_DIR}
    ${miniz_SOURCE_DIR}
    ${tinygltf_SOURCE_DIR}
)

target_compile_definitions(
  openskp_tests
  PRIVATE OPENSKP_FIXTURES="${CMAKE_CURRENT_SOURCE_DIR}/fixtures"
)

gtest_discover_tests(openskp_tests)
