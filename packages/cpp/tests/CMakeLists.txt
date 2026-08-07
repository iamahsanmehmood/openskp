include(GoogleTest)

add_executable(
  openskp_tests
  geometry_test.cpp
  glb_test.cpp
  lowlevel_test.cpp
  observability_test.cpp
  parser_test.cpp
  scene_test.cpp
  texture_test.cpp
  triangulator_test.cpp
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
