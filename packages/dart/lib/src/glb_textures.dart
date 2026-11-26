import 'dart:math';
import 'dart:typed_data';

import 'package:archive/archive.dart' show ZLibDecoder, ZLibEncoder, getCrc32;

import 'observability.dart' show SkpLogLevel;
import 'scene.dart' show SceneTexture, describeImageFormat;

/// Maximum pixels per `float32` colorize row-block (`262,144` pixels).
const int maxColorizeBlockPixels = 262144;

/// Maximum decoded image size (`178,956,970` pixels, matching Python/Pillow,
/// C++, TypeScript, and .NET).
const int maxDecodeTexturePixels = 178956970;
final BigInt _maxDecodeTexturePixelsBig = BigInt.from(maxDecodeTexturePixels);

/// Synchronous image codec used to decode and re-encode colorized JPEG textures
/// during GLB export (PNG textures are decoded and encoded natively via
/// `package:archive`).
class SkpImageCodec {
  /// Decodes compressed image bytes (such as `'image/jpeg'`) into row-major
  /// 8-bit RGBA pixels (`width * height * 4` bytes), or returns `null` when
  /// decoding fails.
  final ({int width, int height, Uint8List rgba})? Function(
    Uint8List data,
    String mimeType,
  ) decode;

  /// Encodes row-major 8-bit RGBA pixels (`width * height * 4` bytes) into
  /// compressed image bytes (such as `'image/jpeg'` at [quality]).
  final Uint8List Function(
    Uint8List rgba,
    int width,
    int height,
    String mimeType, {
    int quality,
  }) encode;

  /// Creates a synchronous image codec for colorized GLB texture baking.
  const SkpImageCodec({required this.decode, required this.encode});
}

/// Internal counters exposed for caching unit tests.
class GltfTextureBakeStats {
  int decodeCalls = 0;
  int colorizeCalls = 0;
}

const List<int> _pngSignature = <int>[137, 80, 78, 71, 13, 10, 26, 10];

const List<(int, int, int, int)> _adam7Passes = <(int, int, int, int)>[
  (0, 8, 0, 8),
  (4, 8, 0, 8),
  (0, 4, 4, 8),
  (2, 4, 0, 4),
  (0, 2, 2, 4),
  (1, 2, 0, 2),
  (0, 1, 1, 2),
];

final Float32List _f32Scratch = Float32List(1);

@pragma('vm:prefer-inline')
double _fr(double v) {
  _f32Scratch[0] = v;
  return _f32Scratch[0];
}

Uint8List _asUint8List(List<int> bytes) =>
    bytes is Uint8List ? bytes : Uint8List.fromList(bytes);

int _paethPredictor(int a, int b, int c) {
  final p = a + b - c;
  final pa = (p - a).abs();
  final pb = (p - b).abs();
  final pc = (p - c).abs();
  if (pa <= pb && pa <= pc) return a;
  return pb <= pc ? b : c;
}

/// Drops JPEG `APP1` (`0xFFE1`, Exif / XMP orientation) marker segments before
/// `SOS` (`0xFFDA`) without re-encoding.
Uint8List stripJpegExifApp1(List<int> input) {
  final data = _asUint8List(input);
  final n = data.length;
  if (n < 4 || data[0] != 0xff || data[1] != 0xd8) return data;

  List<(int, int)>? dropRanges;
  var pos = 2;
  while (pos < n) {
    if (data[pos] != 0xff) break;
    final markerStart = pos;
    while (pos < n && data[pos] == 0xff) {
      pos++;
    }
    if (pos >= n) break;
    final marker = data[pos++];
    if (marker == 0x00 || marker == 0xd9) break;
    if (marker == 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
    if (pos + 2 > n) break;
    final segLen = (data[pos] << 8) | data[pos + 1];
    if (segLen < 2 || pos + segLen > n) break;
    final segEnd = pos + segLen;
    if (marker == 0xda) break; // Start of Scan
    if (marker == 0xe1) {
      (dropRanges ??= <(int, int)>[]).add((markerStart, segEnd));
    }
    pos = segEnd;
  }

  if (dropRanges == null || dropRanges.isEmpty) return data;

  var dropped = 0;
  for (final (s, e) in dropRanges) {
    dropped += e - s;
  }
  final out = Uint8List(n - dropped);
  var cursor = 0;
  var dst = 0;
  for (final (start, end) in dropRanges) {
    if (start > cursor) {
      final len = start - cursor;
      out.setRange(dst, dst + len, data, cursor);
      dst += len;
    }
    cursor = end;
  }
  if (cursor < n) {
    out.setRange(dst, dst + (n - cursor), data, cursor);
  }
  return out;
}

/// Reads JPEG frame `(width, height)` from its `SOF` marker before `SOS`.
({int width, int height})? readJpegDimensions(List<int> input) {
  final data = _asUint8List(input);
  final n = data.length;
  if (n < 4 || data[0] != 0xff || data[1] != 0xd8) return null;

  var pos = 2;
  while (pos < n) {
    if (data[pos] != 0xff) break;
    while (pos < n && data[pos] == 0xff) {
      pos++;
    }
    if (pos >= n) break;
    final marker = data[pos++];
    if (marker == 0x00 || marker == 0xd9 || marker == 0xda) break;
    if (marker == 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
    if (pos + 2 > n) break;
    final segLen = (data[pos] << 8) | data[pos + 1];
    if (segLen < 2 || pos + segLen > n) break;
    if (marker >= 0xc0 &&
        marker <= 0xcf &&
        marker != 0xc4 &&
        marker != 0xc8 &&
        marker != 0xcc) {
      if (segLen < 7) return null;
      final height = (data[pos + 3] << 8) | data[pos + 4];
      final width = (data[pos + 5] << 8) | data[pos + 6];
      return (width: width, height: height);
    }
    pos += segLen;
  }
  return null;
}

/// Reads PNG `IHDR` `(width, height)` before any `IDAT` inflation.
({int width, int height})? readPngDimensions(List<int> input) {
  final data = _asUint8List(input);
  if (data.length < 33) return null;
  for (var i = 0; i < 8; i++) {
    if (data[i] != _pngSignature[i]) return null;
  }
  final view = ByteData.sublistView(data);
  final chunkLen = view.getUint32(8, Endian.big);
  final chunkType = view.getUint32(12, Endian.big);
  if (chunkType != 0x49484452 || chunkLen != 13) return null; // IHDR
  return (
    width: view.getUint32(16, Endian.big),
    height: view.getUint32(20, Endian.big),
  );
}

void _checkPixelLimit(int width, int height) {
  if (width <= 0 || height <= 0) {
    throw FormatException('invalid image dimensions ${width}x$height');
  }
  final totalPixels = BigInt.from(width) * BigInt.from(height);
  if (totalPixels > _maxDecodeTexturePixelsBig) {
    throw FormatException(
      'image dimensions ${width}x$height ($totalPixels pixels) exceed maximum of $maxDecodeTexturePixels pixels',
    );
  }
}

/// Decodes PNG bytes into 8-bit RGBA pixels using `package:archive` `ZLibDecoder`.
/// Enforces `maxDecodeTexturePixels` on `IHDR` before decompressing `IDAT`.
({int width, int height, Uint8List rgba}) decodePngRgba(List<int> input) {
  final data = _asUint8List(input);
  if (data.length < 33) {
    throw const FormatException('truncated PNG header');
  }
  for (var i = 0; i < 8; i++) {
    if (data[i] != _pngSignature[i]) {
      throw const FormatException('invalid PNG signature');
    }
  }

  final view = ByteData.sublistView(data);
  var width = 0;
  var height = 0;
  var bitDepth = 0;
  var colorType = 0;
  var interlace = 0;
  var channels = 0;
  var seenIhdr = false;
  Uint8List? palette;
  Uint8List? trns;
  final idatParts = <Uint8List>[];
  var totalIdatLen = 0;

  var pos = 8;
  while (pos + 8 <= data.length) {
    final len = view.getUint32(pos, Endian.big);
    final type = view.getUint32(pos + 4, Endian.big);
    if (len > data.length - pos - 12) {
      throw const FormatException('truncated PNG chunk');
    }
    final payloadStart = pos + 8;
    final payloadEnd = payloadStart + len;

    if (!seenIhdr) {
      if (type != 0x49484452 || len != 13) {
        throw const FormatException('missing or invalid PNG IHDR chunk');
      }
      width = view.getUint32(payloadStart, Endian.big);
      height = view.getUint32(payloadStart + 4, Endian.big);
      _checkPixelLimit(width, height);

      bitDepth = data[payloadStart + 8];
      colorType = data[payloadStart + 9];
      final compMethod = data[payloadStart + 10];
      final filterMethod = data[payloadStart + 11];
      interlace = data[payloadStart + 12];
      if (compMethod != 0 ||
          filterMethod != 0 ||
          (interlace != 0 && interlace != 1)) {
        throw const FormatException(
          'unsupported PNG compression/filter/interlace mode',
        );
      }

      if (colorType == 0) {
        if (bitDepth != 1 &&
            bitDepth != 2 &&
            bitDepth != 4 &&
            bitDepth != 8 &&
            bitDepth != 16) {
          throw FormatException(
              'unsupported greyscale PNG bit depth $bitDepth');
        }
        channels = 1;
      } else if (colorType == 2) {
        if (bitDepth != 8 && bitDepth != 16) {
          throw FormatException('unsupported RGB PNG bit depth $bitDepth');
        }
        channels = 3;
      } else if (colorType == 3) {
        if (bitDepth != 1 && bitDepth != 2 && bitDepth != 4 && bitDepth != 8) {
          throw FormatException('unsupported indexed PNG bit depth $bitDepth');
        }
        channels = 1;
      } else if (colorType == 4) {
        if (bitDepth != 8 && bitDepth != 16) {
          throw FormatException(
            'unsupported grey+alpha PNG bit depth $bitDepth',
          );
        }
        channels = 2;
      } else if (colorType == 6) {
        if (bitDepth != 8 && bitDepth != 16) {
          throw FormatException('unsupported RGBA PNG bit depth $bitDepth');
        }
        channels = 4;
      } else {
        throw FormatException('unsupported PNG color type $colorType');
      }
      seenIhdr = true;
    } else if (type == 0x504c5445) {
      // PLTE
      if (len < 3 || len > 768 || len % 3 != 0) {
        throw const FormatException('invalid PNG PLTE chunk');
      }
      palette = Uint8List.sublistView(data, payloadStart, payloadEnd);
    } else if (type == 0x74524e53) {
      // tRNS
      trns = Uint8List.sublistView(data, payloadStart, payloadEnd);
    } else if (type == 0x49444154) {
      // IDAT
      if (len > 0) {
        idatParts.add(Uint8List.sublistView(data, payloadStart, payloadEnd));
        totalIdatLen += len;
      }
    } else if (type == 0x49454e44) {
      // IEND
      break;
    }

    pos = payloadEnd + 4;
  }

  if (!seenIhdr || totalIdatLen <= 0) {
    throw const FormatException('PNG contains no IDAT image data');
  }
  if (colorType == 3 && palette == null) {
    throw const FormatException('indexed PNG missing PLTE chunk');
  }

  final passSpecs = <(int, int, int, int, int, int)>[];
  var expectedRawBytes = 0;
  if (interlace == 0) {
    final rowBytes = (width * channels * bitDepth + 7) ~/ 8;
    expectedRawBytes = height * (1 + rowBytes);
    passSpecs.add((0, 1, 0, 1, width, height));
  } else {
    for (final (x0, dx, y0, dy) in _adam7Passes) {
      final pw = width > x0 ? (width - x0 + dx - 1) ~/ dx : 0;
      final ph = height > y0 ? (height - y0 + dy - 1) ~/ dy : 0;
      if (pw > 0 && ph > 0) {
        final rowBytes = (pw * channels * bitDepth + 7) ~/ 8;
        expectedRawBytes += ph * (1 + rowBytes);
        passSpecs.add((x0, dx, y0, dy, pw, ph));
      }
    }
  }

  // Bound compressed IDAT length against expected scanlines before inflating.
  final maxSafeIdatBytes = max(65536, expectedRawBytes * 2 + 65536);
  if (totalIdatLen > maxSafeIdatBytes) {
    throw const FormatException(
        'PNG IDAT stream exceeds expected scanline bound');
  }

  final Uint8List idat;
  if (idatParts.length == 1) {
    idat = idatParts[0];
  } else {
    idat = Uint8List(totalIdatLen);
    var off = 0;
    for (final part in idatParts) {
      idat.setRange(off, off + part.length, part);
      off += part.length;
    }
  }

  if (idat.length < 6) {
    throw const FormatException('truncated PNG zlib stream');
  }
  final cmf = idat[0];
  final flg = idat[1];
  if ((cmf & 0x0f) != 8 ||
      (cmf >> 4) > 7 ||
      (((cmf << 8) | flg) % 31) != 0 ||
      (flg & 0x20) != 0) {
    throw const FormatException('invalid PNG zlib header');
  }

  final inflated = const ZLibDecoder().decodeBytes(idat, verify: true);
  if (inflated.length < expectedRawBytes) {
    throw const FormatException('truncated PNG IDAT deflate stream');
  }

  final rgba = Uint8List(width * height * 4);
  final bpp = max(1, (channels * bitDepth + 7) >> 3);
  final hasGreyTrns = colorType == 0 && trns != null && trns.length >= 2;
  final greyTrnsVal = hasGreyTrns
      ? ((trns[0] << 8) | trns[1]) &
          (bitDepth == 16 ? 0xffff : (1 << bitDepth) - 1)
      : -1;
  final hasRgbTrns = colorType == 2 && trns != null && trns.length >= 6;
  final rgbTrnsMask = bitDepth == 16 ? 0xffff : 0xff;
  final rgbTrnsR = hasRgbTrns ? ((trns[0] << 8) | trns[1]) & rgbTrnsMask : -1;
  final rgbTrnsG = hasRgbTrns ? ((trns[2] << 8) | trns[3]) & rgbTrnsMask : -1;
  final rgbTrnsB = hasRgbTrns ? ((trns[4] << 8) | trns[5]) & rgbTrnsMask : -1;
  final palEntries = palette != null ? palette.length ~/ 3 : 0;

  var rawCursor = 0;
  for (final (x0, dx, y0, dy, pw, ph) in passSpecs) {
    final rowBytes = (pw * channels * bitDepth + 7) ~/ 8;
    var prevRow = Uint8List(rowBytes);
    var currRow = Uint8List(rowBytes);

    for (var py = 0; py < ph; py++) {
      final filterType = inflated[rawCursor++];
      final srcBase = rawCursor;
      rawCursor += rowBytes;

      if (filterType == 0) {
        currRow.setRange(0, rowBytes, inflated, srcBase);
      } else if (filterType == 1) {
        for (var i = 0; i < rowBytes; i++) {
          final left = i >= bpp ? currRow[i - bpp] : 0;
          currRow[i] = (inflated[srcBase + i] + left) & 0xff;
        }
      } else if (filterType == 2) {
        for (var i = 0; i < rowBytes; i++) {
          currRow[i] = (inflated[srcBase + i] + prevRow[i]) & 0xff;
        }
      } else if (filterType == 3) {
        for (var i = 0; i < rowBytes; i++) {
          final left = i >= bpp ? currRow[i - bpp] : 0;
          currRow[i] =
              (inflated[srcBase + i] + ((left + prevRow[i]) >> 1)) & 0xff;
        }
      } else if (filterType == 4) {
        for (var i = 0; i < rowBytes; i++) {
          final left = i >= bpp ? currRow[i - bpp] : 0;
          final upLeft = i >= bpp ? prevRow[i - bpp] : 0;
          currRow[i] = (inflated[srcBase + i] +
                  _paethPredictor(left, prevRow[i], upLeft)) &
              0xff;
        }
      } else {
        throw FormatException('invalid PNG filter type $filterType');
      }

      final dstY = y0 + py * dy;
      if (interlace == 0 && bitDepth == 8 && colorType == 6) {
        final dstStart = dstY * width * 4;
        rgba.setRange(dstStart, dstStart + rowBytes, currRow);
      } else if (interlace == 0 &&
          bitDepth == 8 &&
          colorType == 2 &&
          !hasRgbTrns) {
        var sOff = 0;
        var dOff = dstY * width * 4;
        for (var px = 0; px < pw; px++) {
          rgba[dOff] = currRow[sOff];
          rgba[dOff + 1] = currRow[sOff + 1];
          rgba[dOff + 2] = currRow[sOff + 2];
          rgba[dOff + 3] = 255;
          sOff += 3;
          dOff += 4;
        }
      } else {
        for (var px = 0; px < pw; px++) {
          final dstIdx = (dstY * width + (x0 + px * dx)) * 4;
          if (colorType == 6) {
            if (bitDepth == 8) {
              final s = px * 4;
              rgba[dstIdx] = currRow[s];
              rgba[dstIdx + 1] = currRow[s + 1];
              rgba[dstIdx + 2] = currRow[s + 2];
              rgba[dstIdx + 3] = currRow[s + 3];
            } else {
              final s = px * 8;
              rgba[dstIdx] =
                  (((currRow[s] << 8) | currRow[s + 1]) * 255 + 32895) >> 16;
              rgba[dstIdx + 1] =
                  (((currRow[s + 2] << 8) | currRow[s + 3]) * 255 + 32895) >>
                      16;
              rgba[dstIdx + 2] =
                  (((currRow[s + 4] << 8) | currRow[s + 5]) * 255 + 32895) >>
                      16;
              rgba[dstIdx + 3] =
                  (((currRow[s + 6] << 8) | currRow[s + 7]) * 255 + 32895) >>
                      16;
            }
          } else if (colorType == 2) {
            if (bitDepth == 8) {
              final s = px * 3;
              final r = currRow[s];
              final g = currRow[s + 1];
              final b = currRow[s + 2];
              rgba[dstIdx] = r;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = b;
              rgba[dstIdx + 3] =
                  hasRgbTrns && r == rgbTrnsR && g == rgbTrnsG && b == rgbTrnsB
                      ? 0
                      : 255;
            } else {
              final s = px * 6;
              final r16 = (currRow[s] << 8) | currRow[s + 1];
              final g16 = (currRow[s + 2] << 8) | currRow[s + 3];
              final b16 = (currRow[s + 4] << 8) | currRow[s + 5];
              rgba[dstIdx] = (r16 * 255 + 32895) >> 16;
              rgba[dstIdx + 1] = (g16 * 255 + 32895) >> 16;
              rgba[dstIdx + 2] = (b16 * 255 + 32895) >> 16;
              rgba[dstIdx + 3] = hasRgbTrns &&
                      r16 == rgbTrnsR &&
                      g16 == rgbTrnsG &&
                      b16 == rgbTrnsB
                  ? 0
                  : 255;
            }
          } else if (colorType == 4) {
            if (bitDepth == 8) {
              final g = currRow[px * 2];
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = currRow[px * 2 + 1];
            } else {
              final s = px * 4;
              final g =
                  (((currRow[s] << 8) | currRow[s + 1]) * 255 + 32895) >> 16;
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] =
                  (((currRow[s + 2] << 8) | currRow[s + 3]) * 255 + 32895) >>
                      16;
            }
          } else if (colorType == 0) {
            if (bitDepth == 8) {
              final g = currRow[px];
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = hasGreyTrns && g == greyTrnsVal ? 0 : 255;
            } else if (bitDepth == 16) {
              final g16 = (currRow[px * 2] << 8) | currRow[px * 2 + 1];
              final g = (g16 * 255 + 32895) >> 16;
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = hasGreyTrns && g16 == greyTrnsVal ? 0 : 255;
            } else {
              final bitPos = px * bitDepth;
              final byteIdx = bitPos >> 3;
              final shift = 8 - bitDepth - (bitPos & 7);
              final mask = (1 << bitDepth) - 1;
              final sample = (currRow[byteIdx] >> shift) & mask;
              final g = (sample * 255 + (mask >> 1)) ~/ mask;
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = hasGreyTrns && sample == greyTrnsVal ? 0 : 255;
            }
          } else {
            // Indexed (colorType == 3)
            final int idx;
            if (bitDepth == 8) {
              idx = currRow[px];
            } else {
              final bitPos = px * bitDepth;
              final byteIdx = bitPos >> 3;
              final shift = 8 - bitDepth - (bitPos & 7);
              idx = (currRow[byteIdx] >> shift) & ((1 << bitDepth) - 1);
            }
            if (idx >= palEntries) {
              throw FormatException(
                'PNG palette index $idx out of range ($palEntries)',
              );
            }
            final pal = palette!;
            rgba[dstIdx] = pal[idx * 3];
            rgba[dstIdx + 1] = pal[idx * 3 + 1];
            rgba[dstIdx + 2] = pal[idx * 3 + 2];
            rgba[dstIdx + 3] =
                trns != null && idx < trns.length ? trns[idx] : 255;
          }
        }
      }

      final tmp = prevRow;
      prevRow = currRow;
      currRow = tmp;
    }
  }

  return (width: width, height: height, rgba: rgba);
}

int _writePngChunk(
  Uint8List out,
  ByteData outView,
  int offset,
  String typeAscii,
  Uint8List payload,
) {
  final len = payload.length;
  outView.setUint32(offset, len, Endian.big);
  final crcStart = offset + 4;
  out[crcStart] = typeAscii.codeUnitAt(0);
  out[crcStart + 1] = typeAscii.codeUnitAt(1);
  out[crcStart + 2] = typeAscii.codeUnitAt(2);
  out[crcStart + 3] = typeAscii.codeUnitAt(3);
  if (len > 0) {
    out.setRange(crcStart + 4, crcStart + 4 + len, payload);
  }
  final crc =
      getCrc32(Uint8List.sublistView(out, crcStart, crcStart + 4 + len));
  outView.setUint32(crcStart + 4 + len, crc, Endian.big);
  return crcStart + 8 + len;
}

/// Encodes 8-bit RGB (`channels == 3`) or RGBA (`channels == 4`) pixels to PNG
/// using adaptive scanline filtering and `package:archive` `ZLibEncoder`.
Uint8List encodePngU8(Uint8List pixels, int width, int height, int channels) {
  if (width <= 0 || height <= 0 || (channels != 3 && channels != 4)) {
    throw ArgumentError(
      'encodePngU8 requires positive dimensions and 3 or 4 channels',
    );
  }
  final rowBytes = width * channels;
  final rawScanlines = Uint8List(height * (1 + rowBytes));
  final cand = Uint8List(rowBytes);
  final bpp = channels;

  for (var y = 0; y < height; y++) {
    final rowOff = y * rowBytes;
    final prevOff = (y - 1) * rowBytes;
    final dstScanOff = y * (1 + rowBytes);
    var bestScore = 0x7fffffffffffffff;

    for (var f = 0; f < 5; f++) {
      if (y == 0 && f == 2) continue;
      var score = 0;
      if (f == 0) {
        for (var i = 0; i < rowBytes; i++) {
          final v = pixels[rowOff + i];
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      } else if (f == 1) {
        for (var i = 0; i < rowBytes; i++) {
          final left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
          final v = (pixels[rowOff + i] - left) & 0xff;
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      } else if (f == 2) {
        for (var i = 0; i < rowBytes; i++) {
          final v = (pixels[rowOff + i] - pixels[prevOff + i]) & 0xff;
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      } else if (f == 3) {
        for (var i = 0; i < rowBytes; i++) {
          final left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
          final up = y > 0 ? pixels[prevOff + i] : 0;
          final v = (pixels[rowOff + i] - ((left + up) >> 1)) & 0xff;
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      } else {
        for (var i = 0; i < rowBytes; i++) {
          final left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
          final up = y > 0 ? pixels[prevOff + i] : 0;
          final upLeft = y > 0 && i >= bpp ? pixels[prevOff + i - bpp] : 0;
          final v =
              (pixels[rowOff + i] - _paethPredictor(left, up, upLeft)) & 0xff;
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      }

      if (score < bestScore) {
        bestScore = score;
        rawScanlines[dstScanOff] = f;
        rawScanlines.setRange(dstScanOff + 1, dstScanOff + 1 + rowBytes, cand);
      }
    }
  }

  final compressedList = const ZLibEncoder().encode(rawScanlines, level: 6);
  final compressed = _asUint8List(compressedList);
  final ihdr = Uint8List(13);
  final ihdrView = ByteData.sublistView(ihdr);
  ihdrView.setUint32(0, width, Endian.big);
  ihdrView.setUint32(4, height, Endian.big);
  ihdr[8] = 8;
  ihdr[9] = channels == 4 ? 6 : 2;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  final out = Uint8List(8 + 25 + 12 + compressed.length + 12);
  final outView = ByteData.sublistView(out);
  out.setRange(0, 8, _pngSignature);
  var cursor = 8;
  cursor = _writePngChunk(out, outView, cursor, 'IHDR', ihdr);
  cursor = _writePngChunk(out, outView, cursor, 'IDAT', compressed);
  _writePngChunk(out, outView, cursor, 'IEND', Uint8List(0));
  return out;
}

(double, double, double) _rgbToHlsF64(double r, double g, double b) {
  final maxc = max(r, max(g, b));
  final minc = min(r, min(g, b));
  final sumc = maxc + minc;
  final rangec = maxc - minc;
  final l = sumc / 2.0;
  if (minc == maxc) return (0.0, l, 0.0);
  final s = l <= 0.5 ? rangec / sumc : rangec / (2.0 - maxc - minc);
  final rc = (maxc - r) / rangec;
  final gc = (maxc - g) / rangec;
  final bc = (maxc - b) / rangec;
  double h;
  if (r == maxc) {
    h = bc - gc;
  } else if (g == maxc) {
    h = 2.0 + rc - bc;
  } else {
    h = 4.0 + gc - rc;
  }
  h = (h / 6.0) % 1.0;
  if (h < 0.0) h += 1.0;
  return (h, l, s);
}

void colorizePixelsImpl(
  Uint8List rgba,
  Uint8List dst,
  int width,
  int height,
  (int, int, int) targetRgb,
  int colorizeType,
  bool keepAlpha, [
  int maxBlockPixels = maxColorizeBlockPixels,
]) {
  if (width <= 0 || height <= 0) return;
  final nPx = width * height;
  final outChannels = keepAlpha ? 4 : 3;

  var sumR = 0;
  var sumG = 0;
  var sumB = 0;
  for (var i = 0; i < nPx; i++) {
    final p4 = i * 4;
    sumR += rgba[p4];
    sumG += rgba[p4 + 1];
    sumB += rgba[p4 + 2];
  }
  final avgR = (sumR ~/ nPx) / 255.0;
  final avgG = (sumG ~/ nPx) / 255.0;
  final avgB = (sumB ~/ nPx) / 255.0;
  final (avgH, avgL, avgS) = _rgbToHlsF64(avgR, avgG, avgB);
  final (tgtH, tgtL, tgtS) = _rgbToHlsF64(
    targetRgb.$1 / 255.0,
    targetRgb.$2 / 255.0,
    targetRgb.$3 / 255.0,
  );

  final isShift = colorizeType == 0;
  final deltaH = _fr(tgtH - avgH);
  final deltaL = _fr(tgtL - avgL);
  final deltaS = _fr(tgtS == 0.0 && avgS > 0.0 ? -1.0 : tgtS - avgS);
  final tgtHF = _fr(tgtH);

  final oneSixth = _fr(1.0 / 6.0);
  final oneThird = _fr(1.0 / 3.0);
  final negOneThird = _fr(-1.0 / 3.0);
  const half = 0.5;
  const one = 1.0;
  const two = 2.0;
  const four = 4.0;
  const six = 6.0;
  final minL = _fr(0.01);
  final undefH = _fr(-1.0 / 360.0);
  final eps = _fr(1e-6);
  const scale255 = 255.0;
  const zero = 0.0;
  final offsets = <double>[oneThird, zero, negOneThird];

  final effBlock = max(1, maxBlockPixels);
  final blockRows = min(height, max(1, effBlock ~/ width));

  for (var y0 = 0; y0 < height; y0 += blockRows) {
    final y1 = min(height, y0 + blockRows);
    final pStart = y0 * width;
    final pEnd = y1 * width;
    for (var p = pStart; p < pEnd; p++) {
      final p4 = p * 4;
      final srcR = rgba[p4];
      final srcG = rgba[p4 + 1];
      final srcB = rgba[p4 + 2];
      final srcA = rgba[p4 + 3];

      final r = _fr(srcR / scale255);
      final g = _fr(srcG / scale255);
      final b = _fr(srcB / scale255);

      final cm = max(r, max(g, b));
      final cn = min(r, min(g, b));
      final rg = _fr(cm - cn);
      final sum = _fr(cm + cn);
      var lt = _fr(sum * half);
      final nz = rg > eps;
      var st = zero;
      if (nz) {
        final denom = lt <= half ? sum : _fr(two - sum);
        st = _fr(rg / denom);
      }

      var nh = tgtHF;
      if (isShift) {
        if (!nz) {
          nh = undefH;
        } else {
          if (r == cm) {
            nh = _fr(_fr(g - b) / rg);
          } else if (g == cm) {
            nh = _fr(_fr(_fr(b - r) / rg) + two);
          } else {
            nh = _fr(_fr(_fr(r - g) / rg) + four);
          }
          nh = _fr(nh * oneSixth);
          nh = _fr(nh - _fr(nh.floorToDouble()));
        }
        nh = _fr(nh + deltaH);
        nh = _fr(nh - _fr(nh.floorToDouble()));
      }

      lt = _fr(lt + deltaL);
      if (lt < minL) {
        lt = minL;
      } else if (lt > one) {
        lt = one;
      }

      st = _fr(st + deltaS);
      if (st < zero) {
        st = zero;
      } else if (st > one) {
        st = one;
      }

      final m2 = lt > half
          ? _fr(lt + _fr(st * _fr(one - lt)))
          : _fr(lt + _fr(lt * st));
      final m1 = _fr(_fr(lt * two) - m2);
      final span = _fr(m2 - m1);
      final achromatic = st <= eps;

      final outOff = p * outChannels;
      for (var ch = 0; ch < 3; ch++) {
        var t1 = nh;
        final off = offsets[ch];
        if (off != zero) {
          t1 = _fr(t1 + off);
          t1 = _fr(t1 - _fr(t1.floorToDouble()));
        }
        t1 = _fr(_fr(t1 * six) - two);
        t1 = _fr(two - t1.abs());
        if (t1 < zero) {
          t1 = zero;
        } else if (t1 > one) {
          t1 = one;
        }

        var val = lt;
        if (!achromatic) {
          val = t1 == one ? m2 : _fr(_fr(t1 * span) + m1);
        }
        val = _fr(val * scale255);
        if (val < zero) {
          val = zero;
        } else if (val > scale255) {
          val = scale255;
        }
        dst[outOff + ch] = val.toInt();
      }
      if (keepAlpha) {
        dst[outOff + 3] = srcA;
      }
    }
  }
}

/// Applies SketchUp's HLS colorize rules (`colorizeType` `0` = shift, `1` = tint)
/// in `float32` row blocks, matching `SUTextureGetColorizedImageRep` within `1/255`.
Uint8List colorizeImageU8(
  Uint8List rgba,
  int width,
  int height,
  (int, int, int) targetRgb,
  int colorizeType, {
  bool keepAlpha = true,
  int maxBlockPixels = maxColorizeBlockPixels,
}) {
  if (width <= 0 || height <= 0) return Uint8List(0);
  final outChannels = keepAlpha ? 4 : 3;
  final out = Uint8List(width * height * outChannels);
  colorizePixelsImpl(
    rgba,
    out,
    width,
    height,
    targetRgb,
    colorizeType,
    keepAlpha,
    maxBlockPixels,
  );
  return out;
}

double _srgbToLinear(double c) {
  if (c <= 0.0) return 0.0;
  if (c >= 1.0) return 1.0;
  return c <= 0.04045 ? c / 12.92 : pow((c + 0.055) / 1.055, 2.4).toDouble();
}

Map<String, dynamic> _linearizePbr(
  Map<String, dynamic> pbr, {
  bool stripTexture = false,
}) {
  final newPbr = Map<String, dynamic>.from(pbr);
  if (stripTexture) newPbr.remove('baseColorTexture');
  final bcf = newPbr['baseColorFactor'];
  if (bcf is List && bcf.length >= 3) {
    newPbr['baseColorFactor'] = <double>[
      _srgbToLinear((bcf[0] as num).toDouble()),
      _srgbToLinear((bcf[1] as num).toDouble()),
      _srgbToLinear((bcf[2] as num).toDouble()),
      bcf.length > 3 ? (bcf[3] as num).toDouble() : 1.0,
    ];
  }
  return newPbr;
}

(bool, int) _readColorizeInfo(Map<String, dynamic> mat) {
  final extras = mat['extras'];
  final sketchup = extras is Map ? extras['sketchup'] : null;
  if (sketchup is! Map || sketchup['colorized'] != true) {
    return (false, 0);
  }
  final ct = sketchup['colorize_type'];
  return (true, ct is num ? ct.toInt() : 0);
}

int _clampByte(int v) => v < 0 ? 0 : (v > 255 ? 255 : v);

(int, int, int) _quantizeTargetRgb(Map<String, dynamic> pbr) {
  final bcf = pbr['baseColorFactor'];
  if (bcf is List && bcf.length >= 3) {
    return (
      _clampByte(((bcf[0] as num).toDouble() * 255.0).round()),
      _clampByte(((bcf[1] as num).toDouble() * 255.0).round()),
      _clampByte(((bcf[2] as num).toDouble() * 255.0).round()),
    );
  }
  return (255, 255, 255);
}

class _DecodedEntry {
  final int width;
  final int height;
  final Uint8List rgba;
  final bool ownedBuffer;

  const _DecodedEntry({
    required this.width,
    required this.height,
    required this.rgba,
    required this.ownedBuffer,
  });
}

/// Resolves embedded GLB textures and per-material PBR properties for [toGlb]
/// and [toInstancedGlb] without mutating the caller's scene.
(List<SceneTexture>, List<Map<String, dynamic>>) resolveGltfTextures(
  List<SceneTexture> rawTextures,
  List<Map<String, dynamic>> rawMaterials,
  bool embed,
  void Function(SkpLogLevel level, String message)? onLog, [
  SkpImageCodec? imageCodec,
  GltfTextureBakeStats? stats,
]) {
  if (!embed || rawTextures.isEmpty) {
    final stripped = rawMaterials.map((m) {
      final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>?;
      if (pbr == null) return m;
      final newPbr = _linearizePbr(
        pbr,
        stripTexture: pbr.containsKey('baseColorTexture'),
      );
      return Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = newPbr;
    }).toList();
    return (<SceneTexture>[], stripped);
  }

  final warnedUnsupported = <(String, String)>{};
  for (var i = 0; i < rawTextures.length; i++) {
    final tex = rawTextures[i];
    if (tex.mimeType != 'image/png' && tex.mimeType != 'image/jpeg') {
      final baseName = tex.filename.replaceAll('\\', '/').split('/').last;
      final fmt = describeImageFormat(tex.data, tex.filename);
      if (warnedUnsupported.add((baseName, fmt))) {
        final label = baseName.isNotEmpty ? " '$baseName'" : '';
        onLog?.call(
          SkpLogLevel.warning,
          'Texture$label is $fmt, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB',
        );
      }
    }
  }

  final uniqueColorizeKeys = <(int, int, int, int, int)>{};
  final remainingVariants = <int, int>{};
  for (final rawMat in rawMaterials) {
    final pbr = rawMat['pbrMetallicRoughness'] as Map<String, dynamic>?;
    if (pbr == null) continue;
    final texRef = pbr['baseColorTexture'] as Map<String, dynamic>?;
    final texIdx = texRef?['index'];
    if (texRef == null ||
        texIdx is! int ||
        texIdx < 0 ||
        texIdx >= rawTextures.length) {
      continue;
    }
    final tex = rawTextures[texIdx];
    if (tex.mimeType != 'image/png' && tex.mimeType != 'image/jpeg') continue;
    if (tex.mimeType == 'image/jpeg' && imageCodec == null) continue;
    final (isCol, ctype) = _readColorizeInfo(rawMat);
    if (!isCol) continue;
    final (r, g, b) = _quantizeTargetRgb(pbr);
    if (uniqueColorizeKeys.add((texIdx, ctype, r, g, b))) {
      remainingVariants[texIdx] = (remainingVariants[texIdx] ?? 0) + 1;
    }
  }

  final outTextures = <SceneTexture>[];
  final plainByTex = <int, int>{};
  final bakedByKey = <(int, int, int, int, int), (int, bool)>{};
  final decodedByTex = <int, _DecodedEntry?>{};
  var warnedNoJpegCodec = false;

  int getPlainTextureIndex(int texIdx) {
    final cached = plainByTex[texIdx];
    if (cached != null) return cached;
    final tex = rawTextures[texIdx];
    final bytes = tex.mimeType == 'image/jpeg'
        ? stripJpegExifApp1(tex.data)
        : _asUint8List(tex.data);
    final gltfIdx = outTextures.length;
    outTextures.add(
      SceneTexture(data: bytes, mimeType: tex.mimeType, filename: tex.filename),
    );
    plainByTex[texIdx] = gltfIdx;
    return gltfIdx;
  }

  _DecodedEntry? getDecoded(int texIdx) {
    if (decodedByTex.containsKey(texIdx)) {
      return decodedByTex[texIdx];
    }
    if (stats != null) stats.decodeCalls++;
    final tex = rawTextures[texIdx];
    _DecodedEntry? entry;
    try {
      if (tex.mimeType == 'image/png') {
        final dec = decodePngRgba(tex.data);
        entry = _DecodedEntry(
          width: dec.width,
          height: dec.height,
          rgba: dec.rgba,
          ownedBuffer: true,
        );
      } else {
        final dims = readJpegDimensions(tex.data);
        if (dims == null) {
          throw const FormatException('missing or corrupt JPEG SOF header');
        }
        _checkPixelLimit(dims.width, dims.height);
        final dec = imageCodec!.decode(_asUint8List(tex.data), tex.mimeType);
        if (dec == null) {
          throw const FormatException('imageCodec.decode returned null');
        }
        _checkPixelLimit(dec.width, dec.height);
        final expectedLen = dec.width * dec.height * 4;
        if (dec.rgba.length < expectedLen) {
          throw FormatException(
            'imageCodec.decode returned ${dec.rgba.length} bytes for ${dec.width}x${dec.height} RGBA (expected $expectedLen)',
          );
        }
        entry = _DecodedEntry(
          width: dec.width,
          height: dec.height,
          rgba: dec.rgba.length == expectedLen
              ? dec.rgba
              : Uint8List.sublistView(dec.rgba, 0, expectedLen),
          ownedBuffer: false,
        );
      }
    } catch (err) {
      final namePart = tex.filename.isNotEmpty ? " ('${tex.filename}')" : '';
      onLog?.call(
        SkpLogLevel.warning,
        'Failed to decode texture $texIdx$namePart for GLB export: $err',
      );
      entry = null;
    }
    decodedByTex[texIdx] = entry;
    return entry;
  }

  final materials = rawMaterials.map((m) {
    final pbr = m['pbrMetallicRoughness'] as Map<String, dynamic>?;
    if (pbr == null) return m;
    final texRef = pbr['baseColorTexture'] as Map<String, dynamic>?;
    final texIdx = texRef?['index'];
    if (texRef == null || texIdx is! int) {
      return Map<String, dynamic>.from(m)
        ..['pbrMetallicRoughness'] = _linearizePbr(pbr);
    }
    final tex =
        texIdx >= 0 && texIdx < rawTextures.length ? rawTextures[texIdx] : null;
    if (tex == null ||
        (tex.mimeType != 'image/png' && tex.mimeType != 'image/jpeg')) {
      final newPbr = _linearizePbr(pbr, stripTexture: true);
      final newMat = Map<String, dynamic>.from(m)
        ..['pbrMetallicRoughness'] = newPbr;
      if (newMat['alphaMode'] == 'MASK') newMat.remove('alphaMode');
      return newMat;
    }

    final bcf = pbr['baseColorFactor'];
    final alpha =
        (bcf is List && bcf.length > 3) ? (bcf[3] as num).toDouble() : 1.0;

    final (isCol, ctype) = _readColorizeInfo(m);
    if (!isCol) {
      final mapped = getPlainTextureIndex(texIdx);
      final newPbr = Map<String, dynamic>.from(pbr)
        ..['baseColorFactor'] = <double>[1.0, 1.0, 1.0, alpha];
      if (mapped != texIdx) {
        newPbr['baseColorTexture'] = Map<String, dynamic>.from(texRef)
          ..['index'] = mapped;
      }
      return Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = newPbr;
    }

    if (tex.mimeType == 'image/jpeg' && imageCodec == null) {
      if (!warnedNoJpegCodec) {
        warnedNoJpegCodec = true;
        onLog?.call(
          SkpLogLevel.warning,
          'No imageCodec provided; colorized JPEG textures in GLB export stay untinted with their material factor (pass imageCodec to bake colorized JPEGs).',
        );
      }
      final mapped = getPlainTextureIndex(texIdx);
      if (mapped == texIdx) return m;
      final newPbr = Map<String, dynamic>.from(pbr)
        ..['baseColorTexture'] =
            (Map<String, dynamic>.from(texRef)..['index'] = mapped);
      return Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = newPbr;
    }

    final (r, g, b) = _quantizeTargetRgb(pbr);
    final ckey = (texIdx, ctype, r, g, b);
    var resolved = bakedByKey[ckey];
    if (resolved == null) {
      final rem = (remainingVariants[texIdx] ?? 1) - 1;
      remainingVariants[texIdx] = max(0, rem);

      final decoded = getDecoded(texIdx);
      if (decoded == null) {
        resolved = (getPlainTextureIndex(texIdx), false);
        bakedByKey[ckey] = resolved;
      } else {
        try {
          if (stats != null) stats.colorizeCalls++;
          final nPx = decoded.width * decoded.height;
          final Uint8List encoded;
          if (tex.mimeType == 'image/jpeg') {
            final bakedRgba = colorizeImageU8(
              decoded.rgba,
              decoded.width,
              decoded.height,
              (r, g, b),
              ctype,
              keepAlpha: true,
            );
            if (rem <= 0) decodedByTex.remove(texIdx);
            encoded = imageCodec!.encode(
              bakedRgba,
              decoded.width,
              decoded.height,
              'image/jpeg',
              quality: 95,
            );
            if (encoded.isEmpty) {
              throw const FormatException(
                'imageCodec.encode returned empty output',
              );
            }
          } else {
            var keepAlpha = false;
            final rawRgba = decoded.rgba;
            for (var p = 0; p < nPx; p++) {
              if (rawRgba[p * 4 + 3] < 255) {
                keepAlpha = true;
                break;
              }
            }
            final outCh = keepAlpha ? 4 : 3;
            if (rem <= 0 && decoded.ownedBuffer) {
              colorizePixelsImpl(
                rawRgba,
                rawRgba,
                decoded.width,
                decoded.height,
                (r, g, b),
                ctype,
                keepAlpha,
              );
              encoded = encodePngU8(
                Uint8List.sublistView(rawRgba, 0, nPx * outCh),
                decoded.width,
                decoded.height,
                outCh,
              );
              decodedByTex.remove(texIdx);
            } else {
              final baked = colorizeImageU8(
                rawRgba,
                decoded.width,
                decoded.height,
                (r, g, b),
                ctype,
                keepAlpha: keepAlpha,
              );
              encoded =
                  encodePngU8(baked, decoded.width, decoded.height, outCh);
            }
          }

          final gltfIdx = outTextures.length;
          outTextures.add(
            SceneTexture(
              data: encoded,
              mimeType: tex.mimeType,
              filename: tex.filename,
            ),
          );
          resolved = (gltfIdx, true);
          bakedByKey[ckey] = resolved;
        } catch (err) {
          final namePart =
              tex.filename.isNotEmpty ? " ('${tex.filename}')" : '';
          onLog?.call(
            SkpLogLevel.warning,
            'Failed to encode baked texture $texIdx$namePart for GLB export: $err',
          );
          resolved = (getPlainTextureIndex(texIdx), false);
          bakedByKey[ckey] = resolved;
        }
      }
    }

    final (mappedIdx, isBaked) = resolved;
    if (!isBaked) {
      if (mappedIdx == texIdx) return m;
      final fallbackPbr = Map<String, dynamic>.from(pbr)
        ..['baseColorTexture'] =
            (Map<String, dynamic>.from(texRef)..['index'] = mappedIdx);
      return Map<String, dynamic>.from(m)
        ..['pbrMetallicRoughness'] = fallbackPbr;
    }

    final bakedPbr = Map<String, dynamic>.from(pbr)
      ..['baseColorFactor'] = <double>[1.0, 1.0, 1.0, alpha];
    if (mappedIdx != texIdx) {
      bakedPbr['baseColorTexture'] = Map<String, dynamic>.from(texRef)
        ..['index'] = mappedIdx;
    }
    return Map<String, dynamic>.from(m)..['pbrMetallicRoughness'] = bakedPbr;
  }).toList();

  return (outTextures, materials);
}
