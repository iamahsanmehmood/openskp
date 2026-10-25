#include <algorithm>
#include <cmath>
#include <gtest/gtest.h>
#include <random>

#include <openskp/openskp.hpp>

#include "test_helpers.hpp"

namespace openskp {
namespace {

// An instance scaled through the matrix's scale term (openskp#444).
//
// SketchUp can keep a uniform scale in the homogeneous term of an instance's
// transformation instead of in the 3x3: the 3x3 stays as it is, the
// translation is in the scaled units, and the 16th value `w` is the divisor.
// SketchUp's own library reports a 100 in cube placed with `w = 100` and a
// translation of 2000 in as a 1 in cube at x = 20 in. The 13-value matrix
// stores `1/w` at index 12, and every consumer used to ignore it, so such an
// instance came out 100x its size and 100x its distance from its parent.
//
// Fixtures: instance_scale_term_2021.skp and instance_scale_term_v6.skp - one
// 100 in cube definition placed twice, once through the 3x3 (scaling 0.01) and
// once through w = 100 with a translation of 2000 in, built and saved through
// the installed SketchUp library. No private content.

constexpr double kInchMm = 25.4;
const char* const kFiles[] = {"instance_scale_term_2021.skp", "instance_scale_term_v6.skp"};

TEST(InstanceScaleTerm, ScalesTheTranslatedPoint) {
  const std::vector<double> m{1, 0, 0, 0, 1, 0, 0, 0, 1, 2000, 0, 0, 0.01};
  const auto p = transform_point(m, {100.0, 100.0, 100.0});
  EXPECT_NEAR(p[0], 21.0, 1e-9);
  EXPECT_NEAR(p[1], 1.0, 1e-9);
  EXPECT_NEAR(p[2], 1.0, 1e-9);
}

TEST(InstanceScaleTerm, AScaleOfOneChangesNothing) {
  const std::vector<double> m{2, 0, 0, 0, 3, 0, 0, 0, 4, 5, 6, 7, 1};
  const auto p = transform_point(m, {1.0, 1.0, 1.0});
  EXPECT_EQ(p[0], 7.0);
  EXPECT_EQ(p[1], 9.0);
  EXPECT_EQ(p[2], 11.0);
  EXPECT_EQ(fold_scale(m), m);
}

TEST(InstanceScaleTerm, ScalingThroughThe3x3AndThroughTheTermAgree) {
  const std::vector<double> via3x3{0.01, 0, 0, 0, 0.01, 0, 0, 0, 0.01, 0, 0, 0, 1};
  const std::vector<double> via_term{1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.01};
  const auto a = transform_point(via3x3, {50.0, 20.0, 10.0});
  const auto b = transform_point(via_term, {50.0, 20.0, 10.0});
  for (int i = 0; i < 3; ++i) EXPECT_NEAR(a[i], b[i], 1e-9);
}

TEST(InstanceScaleTerm, CompositionMatchesApplyingOneTransformAfterTheOther) {
  std::mt19937 rng(12345);
  std::uniform_real_distribution<double> small(-2, 2), shift(-50, 50), pt(-100, 100);
  const double scales[] = {1.0, 1.0, 0.01, 0.5, 2.0, 0.1};
  auto random_matrix = [&]() {
    std::vector<double> m;
    for (int i = 0; i < 9; ++i) m.push_back(small(rng));
    for (int i = 0; i < 3; ++i) m.push_back(shift(rng));
    m.push_back(scales[rng() % 6]);
    return m;
  };
  for (int k = 0; k < 40; ++k) {
    const auto parent = random_matrix();
    const auto child = random_matrix();
    const std::array<double, 3> p{pt(rng), pt(rng), pt(rng)};
    const auto composed = multiply_matrices(parent, child);
    const auto want = transform_point(parent, transform_point(child, p));
    const auto got = transform_point(composed, p);
    for (int i = 0; i < 3; ++i) EXPECT_NEAR(got[i], want[i], 1e-6) << k;
  }
}

TEST(InstanceScaleTerm, AComposedMatrixHasItsScaleFoldedIn) {
  const auto out = multiply_matrices({1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0.01},
                                     {1, 0, 0, 0, 1, 0, 0, 0, 1, 2000, 0, 0, 1});
  EXPECT_EQ(out[12], 1.0);
  EXPECT_NEAR(out[9], 20.0, 1e-9);
}

TEST(InstanceScaleTerm, TheParseStillReportsTheRawMatrix) {
  for (const char* file : kFiles) {
    auto model = SkpFile::open(test::fixture(file)).parse();
    bool found = false;
    for (const auto& i : model.root().instances)
      if (i.matrix.size() > 12 && std::abs(i.matrix[12] - 0.01) < 1e-12 &&
          std::abs(i.matrix[9] - 2000.0) < 1e-9)
        found = true;
    EXPECT_TRUE(found) << file;
  }
}

TEST(InstanceScaleTerm, TheSceneBothCubesWhereSketchUpDoes) {
  for (const char* file : kFiles) {
    auto scene = SkpFile::open(test::fixture(file)).build_scene();
    std::vector<double> xs;
    for (const auto& n : scene.scene_hierarchy.children) xs.push_back(n.position_mm[0]);
    std::sort(xs.begin(), xs.end());
    ASSERT_EQ(xs.size(), 2u) << file;
    EXPECT_NEAR(xs[0], 0.0, 1e-6) << file;
    EXPECT_NEAR(xs[1], 20 * kInchMm, 1e-6) << file;  // 508 mm, not 50,800
  }
}

TEST(InstanceScaleTerm, TheInstancedSceneNodeMatricesCarryTheScaleAndTheTranslation) {
  for (const char* file : kFiles) {
    auto scene = SkpFile::open(test::fixture(file)).build_instanced_scene();
    const auto& placed = scene.scene_hierarchy.children;
    ASSERT_EQ(placed.size(), 2u) << file;
    for (const auto& n : placed) {
      EXPECT_NEAR(n.matrix[0], 0.01, 1e-9) << file;
      EXPECT_NEAR(n.matrix[5], 0.01, 1e-9) << file;
      EXPECT_NEAR(n.matrix[10], 0.01, 1e-9) << file;
    }
    std::vector<double> tx;
    for (const auto& n : placed) tx.push_back(n.matrix[12]);
    std::sort(tx.begin(), tx.end());
    EXPECT_NEAR(tx[0], 0.0, 1e-9) << file;
    EXPECT_NEAR(tx[1], 20 * kInchMm / 1000.0, 1e-9) << file;  // 0.508 m, not 50.8
  }
}

}  // namespace
}  // namespace openskp
