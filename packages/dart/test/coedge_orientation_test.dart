import 'dart:io';

import 'package:openskp/openskp.dart';
import 'package:test/test.dart';

/// Regression coverage for the CoEdge orientation contract (+1 = same
/// direction as the edge's own v1Id->v2Id, -1 = reversed). Both the legacy
/// (pre-2021 MFC) and modern (VFF) readers used to leak SketchUp's raw
/// storage bit (0 = forward, 1 = reversed) straight through instead of
/// normalizing it - counting coedges or checking their type does not catch
/// this, since a face with every coedge reversed still has the same edge
/// count and loop length, just the wrong winding. The only thing that
/// catches it is checking that consecutive coedges in a loop actually
/// connect head-to-tail.
void main() {
  String fixture(String name) =>
      '${Directory.current.path}/test/fixtures/$name';

  void assertConnectedNormalizedLoops(Iterable<Definition> definitions) {
    for (final def in definitions) {
      for (final face in def.faces.values) {
        for (final loop in face.loops) {
          expect(loop, isNotEmpty, reason: 'empty loop');
          final n = loop.length;
          for (var i = 0; i < n; i++) {
            final (edgeId, orientation) = loop[i];
            final (nextEdgeId, nextOrientation) = loop[(i + 1) % n];
            expect(orientation, anyOf(1, -1));
            final edge = def.edges[edgeId];
            final nextEdge = def.edges[nextEdgeId];
            expect(edge, isNotNull);
            expect(nextEdge, isNotNull);
            final end = orientation == 1 ? edge!.v2Id : edge!.v1Id;
            final nextStart =
                nextOrientation == 1 ? nextEdge!.v1Id : nextEdge!.v2Id;
            expect(end, nextStart);
          }
        }
      }
    }
  }

  test('legacy MFC reader (capilla_quiroz_v17.skp)', () {
    final model = SkpFile.open(fixture('capilla_quiroz_v17.skp')).parse();
    assertConnectedNormalizedLoops([...model.definitions.values, model.root]);
  });

  test('modern VFF reader (Untitled.skp)', () {
    final model = SkpFile.open(fixture('Untitled.skp')).parse();
    assertConnectedNormalizedLoops([...model.definitions.values, model.root]);
  });
}
