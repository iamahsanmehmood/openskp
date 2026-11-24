import 'dart:io';
import 'dart:typed_data';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

void main() {
  const fixturePath = 'test/fixtures/sdk_saved_leaderless_text_edges_2020.skp';

  group('legacy leaderless CText and CEdge vertex validation (#592)', () {
    test('reads a leaderless text before shared-vertex loose edges with SDK parity', () {
      final warnings = <String>[];
      final options = ParseOptions(
        onLog: (level, message) {
          if (level == SkpLogLevel.warning) warnings.add(message);
        },
      );
      final skp = SkpFile.open(fixturePath);
      final model = skp.parse(options);
      final scene = skp.buildScene(options);
      final instanced = skp.buildInstancedScene(options);

      expect(model.definitions.length, equals(1));
      final post = model.definitions.values.single;
      expect(post.name, equals('Post'));
      expect(post.isGroup, isFalse);
      expect(model.root.faces, isEmpty);
      expect(model.root.edges.length, equals(3));
      expect(model.root.instances.length, equals(1));
      expect(
        model.root.texts.map((t) => t.text).toList(),
        equals(['Screen title', 'Callout']),
      );
      expect(model.unreadRootEntities, equals(0));
      expect(scene.glbPrimitives.length, equals(1));
      expect(instanced.meshResources.length, equals(1));
      expect(warnings, isEmpty);
    });

    test('reports a root edge whose vertex ref is not a vertex as a partial read', () {
      final raw = Uint8List.fromList(File(fixturePath).readAsBytesSync());
      expect(raw[5323], equals(0x31));
      expect(raw[5324], equals(0x00));
      raw[5323] = 0x2F; // point 2nd root edge v1 at 1st root CEdge

      final warnings = <String>[];
      final options = ParseOptions(
        onLog: (level, message) {
          if (level == SkpLogLevel.warning) warnings.add(message);
        },
      );
      final skp = SkpFile.fromBuffer(raw);
      final model = skp.parse(options);
      final scene = skp.buildScene();
      final instanced = skp.buildInstancedScene();

      expect(model.root.instances.length, equals(1));
      expect(
        model.root.texts.map((t) => t.text).toList(),
        equals(['Screen title', 'Callout']),
      );
      expect(model.root.edges.length, equals(1));
      expect(model.unreadRootEntities, equals(2));
      expect(scene.glbPrimitives.length, equals(1));
      expect(instanced.meshResources.length, equals(1));
      expect(warnings.any((w) => w.contains('2 unread')), isTrue);
    });

    test('throws SkpParseException when a definition edge vertex ref is not a vertex', () {
      final raw = Uint8List.fromList(File(fixturePath).readAsBytesSync());
      expect(raw[4039], equals(0x14));
      expect(raw[4040], equals(0x00));
      raw[4039] = 0x11; // point 2nd definition edge v1 at 1st definition CEdge

      final skp = SkpFile.fromBuffer(raw);
      expect(() => skp.parse(), throwsA(isA<SkpParseException>()));
      expect(() => skp.buildScene(), throwsA(isA<SkpParseException>()));
      expect(() => skp.buildInstancedScene(), throwsA(isA<SkpParseException>()));
    });
  });
}
