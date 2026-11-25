import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

String _localFixture(String name) => 'test/fixtures/$name';
String _cppFixture(String name) => '../cpp/tests/fixtures/$name';

const _years = [2015, 2016, 2017, 2018, 2019, 2020];

const _hiddenEntities = [
  'scene_hidden_entities_2017.skp',
  'scene_hidden_entities_2019.skp',
  'scene_hidden_entities_2020.skp',
  'scene_hidden_entities_2024.skp',
];

const _sceneOrder = [
  'Camera',
  'RenderingOptions',
  'ShadowInfo',
  'Axes',
  'Hidden',
  'HiddenGeometry',
  'HiddenObjects',
  'HiddenLayers',
  'SectionPlanes',
  'ShadowsCamera',
  'AxesCamera',
];

final List<(String, bool? Function(Page))> _allUseProps = [
  ('use_camera', (p) => p.useCamera),
  ('use_rendering_options', (p) => p.useRenderingOptions),
  ('use_shadow_info', (p) => p.useShadowInfo),
  ('use_axes', (p) => p.useAxes),
  ('use_hidden_layers', (p) => p.useHiddenLayers),
  ('use_section_planes', (p) => p.useSectionPlanes),
  ('use_hidden_geometry', (p) => p.useHiddenGeometry),
  ('use_hidden_objects', (p) => p.useHiddenObjects),
];

Map<String, Page> _pagesByName(String path) => {
      for (final p in SkpFile.open(path).parse().pages) p.name: p,
    };

void main() {
  group('Scene capture flags (#660, #661)', () {
    test('exposes the SketchUp capture-flag bit constants', () {
      expect(pageUseCamera, 0x001);
      expect(pageUseRenderingOptions, 0x002);
      expect(pageUseShadowInfo, 0x004);
      expect(pageUseAxes, 0x008);
      expect(pageUseHidden, 0x010);
      expect(pageUseHiddenLayers, 0x020);
      expect(pageUseSectionPlanes, 0x040);
      expect(pageUseHiddenGeometry, 0x080);
      expect(pageUseHiddenObjects, 0x100);
    });

    for (final name in _hiddenEntities) {
      test('tells an empty hidden list apart ($name)', () {
        final pages = _pagesByName(_cppFixture(name));
        expect(pages['All']!.hiddenEntityIds, isEmpty);
        expect(pages['Not saved']!.hiddenEntityIds, isEmpty);
        expect(pages['All']!.captureFlags, 0x191);
        expect(pages['All']!.useHiddenObjects, isTrue);
        expect(pages['All']!.useHiddenGeometry, isTrue);
        expect(pages['Not saved']!.captureFlags, 0x001);
        expect(pages['Not saved']!.useHiddenObjects, isFalse);
        expect(pages['Not saved']!.useHiddenGeometry, isFalse);
        expect(pages['Not saved']!.useCamera, isTrue);
      });

      test('every scene that hides something saves it ($name)', () {
        for (final page in _pagesByName(_cppFixture(name)).values) {
          if (page.hiddenEntityIds.isNotEmpty) {
            expect(
                page.useHiddenObjects == true || page.useHiddenGeometry == true,
                isTrue);
          }
        }
      });
    }

    test('reads flags on VFF scenes that save no camera', () {
      final path = _localFixture('scene_without_camera_2021.skp');
      final pages = _pagesByName(path);
      expect(pages['With camera']!.captureFlags, 0x001);
      expect(pages['With camera']!.useCamera, isTrue);
      expect(pages['With camera']!.useHiddenLayers, isFalse);
      expect(pages['No camera']!.captureFlags, 0x020);
      expect(pages['No camera']!.useCamera, isFalse);
      expect(pages['No camera']!.useHiddenLayers, isTrue);
      expect(pages['No camera']!.eye, isNull);
      expect(pages['Plain']!.captureFlags, 0x000);
      expect(pages['Plain']!.useCamera, isFalse);
      expect(pages['Plain']!.useHiddenLayers, isFalse);

      final skp = SkpFile.open(path);
      expect([for (final p in skp.buildScene().pages) p.captureFlags],
          [0x001, 0x020, 0x000]);
      expect([for (final p in skp.buildInstancedScene().pages) p.captureFlags],
          [0x001, 0x020, 0x000]);
    });

    for (final name in [
      'sdk_saved_sections_2019.skp',
      'sdk_saved_sections_2020.skp',
    ]) {
      test('all or nothing ($name)', () {
        final pages = _pagesByName(_localFixture(name));
        final everything = pages['Everything']!;
        final plain = pages['Plain']!;
        expect(everything.captureFlags, 0x1FF);
        expect(plain.captureFlags, 0x000);
        for (final (propName, getter) in _allUseProps) {
          expect(getter(everything), isTrue, reason: propName);
          expect(getter(plain), isFalse, reason: propName);
        }
      });
    }

    test('reads an application-saved scene (gondola_v20.skp)', () {
      final page = _pagesByName(_localFixture('gondola_v20.skp')).values.single;
      expect(page.captureFlags, 0x1FF);
      expect(page.useHiddenGeometry, isTrue);
    });

    test('older combined bit covers both until split bits are set', () {
      const old = Page(captureFlags: 0x011);
      expect(old.useHiddenGeometry, isTrue);
      expect(old.useHiddenObjects, isTrue);

      const split = Page(captureFlags: 0x111);
      expect(split.useHiddenObjects, isTrue);
      expect(split.useHiddenGeometry, isFalse);
    });

    test('no flags means unknown', () {
      const page = Page();
      expect(page.captureFlags, isNull);
      for (final (propName, getter) in _allUseProps) {
        expect(getter(page), isNull, reason: propName);
      }
    });

    for (final year in _years) {
      test(
          'reads all 11 scenes with capture flags, cameras and hidden layers ($year)',
          () {
        final model =
            SkpFile.open(_cppFixture('scene_capture_flags_$year.skp')).parse();
        expect(model.pages.map((p) => p.name).toList(), equals(_sceneOrder));
        final byName = {for (final p in model.pages) p.name: p};

        const expected = <String, (int, Set<String>)>{
          'Camera': (0x001, {'use_camera'}),
          'RenderingOptions': (0x002, {'use_rendering_options'}),
          'ShadowInfo': (0x004, {'use_shadow_info'}),
          'Axes': (0x008, {'use_axes'}),
          'Hidden': (0x190, {'use_hidden_geometry', 'use_hidden_objects'}),
          'HiddenGeometry': (0x090, {'use_hidden_geometry'}),
          'HiddenObjects': (0x110, {'use_hidden_objects'}),
          'HiddenLayers': (0x020, {'use_hidden_layers'}),
          'SectionPlanes': (0x040, {'use_section_planes'}),
          'ShadowsCamera': (0x005, {'use_shadow_info', 'use_camera'}),
          'AxesCamera': (0x009, {'use_axes', 'use_camera'}),
        };
        for (final entry in expected.entries) {
          final p = byName[entry.key]!;
          expect(p.captureFlags, entry.value.$1, reason: entry.key);
          for (final (propName, getter) in _allUseProps) {
            expect(getter(p), entry.value.$2.contains(propName),
                reason: '${entry.key}.$propName');
          }
        }

        final cam = byName['Camera']!;
        expect(cam.eye, equals((120.0, -240.0, 90.0)));
        expect(cam.target, equals((5.0, 5.0, 0.0)));
        expect(cam.up!.$1, closeTo(-0.13407807210149128, 1e-9));
        expect(cam.up!.$2, closeTo(0.28564458839013357, 1e-9));
        expect(cam.up!.$3, closeTo(0.9489100271917137, 1e-9));
        expect(cam.parallel, isFalse);
        expect(cam.fov, closeTo(42.0, 1e-9));

        final shadowsCam = byName['ShadowsCamera']!;
        expect(shadowsCam.eye, equals((0.0, -300.0, 150.0)));
        expect(shadowsCam.target, equals((0.0, 0.0, 0.0)));
        expect(shadowsCam.up!.$1, closeTo(0.0, 1e-9));
        expect(shadowsCam.up!.$2, closeTo(0.44721359549995804, 1e-9));
        expect(shadowsCam.up!.$3, closeTo(0.8944271909999161, 1e-9));
        expect(shadowsCam.parallel, isTrue);
        expect(shadowsCam.orthoHeight, closeTo(80.0, 1e-9));

        final axesCam = byName['AxesCamera']!;
        expect(axesCam.eye, equals((-180.0, -180.0, 110.0)));
        expect(axesCam.target, equals((10.0, 10.0, 0.0)));
        expect(axesCam.up!.$1, closeTo(0.2678945632789157, 1e-9));
        expect(axesCam.up!.$2, closeTo(0.2678945632789157, 1e-9));
        expect(axesCam.up!.$3, closeTo(0.9254539458726179, 1e-9));
        expect(axesCam.parallel, isFalse);
        expect(axesCam.fov, closeTo(55.0, 1e-9));

        for (final name in _sceneOrder) {
          final p = byName[name]!;
          if (name != 'Camera' &&
              name != 'ShadowsCamera' &&
              name != 'AxesCamera') {
            expect(p.eye, isNull, reason: name);
            expect(p.target, isNull, reason: name);
          }
          if (name == 'HiddenLayers') {
            expect(p.hiddenLayers, equals(['HiddenTag']));
          } else {
            expect(p.hiddenLayers, isEmpty, reason: name);
          }
        }
      });

      test(
          'resolves hidden entities in 2017+ and drops slot refs in 2015–2016 ($year)',
          () {
        final model =
            SkpFile.open(_cppFixture('scene_capture_flags_$year.skp')).parse();
        final byName = {for (final p in model.pages) p.name: p};
        if (year < 2017) {
          for (final p in model.pages) {
            expect(p.hiddenEntityIds, isEmpty, reason: p.name);
          }
        } else {
          final crate =
              model.root.instances.singleWhere((i) => i.name == 'Crate');
          expect(crate.id, equals(30));
          expect(
              model.root.edges.values.any((e) => e.persistentId == 21), isTrue);
          expect(byName['Hidden']!.hiddenEntityIds.toSet(), equals({21, 30}));
          expect(byName['HiddenGeometry']!.hiddenEntityIds, equals([21]));
          expect(byName['HiddenObjects']!.hiddenEntityIds, equals([30]));
          for (final name in _sceneOrder) {
            if (name != 'Hidden' &&
                name != 'HiddenGeometry' &&
                name != 'HiddenObjects') {
              expect(byName[name]!.hiddenEntityIds, isEmpty, reason: name);
            }
          }
        }
      });
    }
  });
}
