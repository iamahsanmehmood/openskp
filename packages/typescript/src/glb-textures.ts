import { unzlibSync, zlibSync } from 'fflate';
import { toUint8ArrayView } from './binary-input';
import { describeImageFormat } from './face-groups';
import type { SceneTexture } from './model';
import type { LogLevel } from './observability';

/** Maximum pixels per `float32` colorize row-block (`256k` pixels). */
export const MAX_COLORIZE_BLOCK_PIXELS = 262144;

/** Maximum decoded image size (`178,956,970` pixels, matching C++ and Pillow's bomb refusal ceiling). */
export const MAX_DECODE_TEXTURE_PIXELS = 178956970;
const MAX_DECODE_TEXTURE_PIXELS_BIG = 178956970n;

/** Decoded 8-bit RGBA image (`width * height * 4` bytes, row-major). */
export interface RawRgbaImage {
  width: number;
  height: number;
  data: Uint8Array | Uint8ClampedArray;
}

/**
 * Synchronous codec hook for decoding and encoding colorized JPEG textures
 * during GLB export (PNG textures are decoded and encoded natively via `fflate`).
 */
export interface ImageCodec {
  decode(data: Uint8Array, mimeType: string): RawRgbaImage | null;
  encode(image: RawRgbaImage, mimeType: 'image/jpeg', quality: number): Uint8Array;
}

/** Internal counters exposed for caching unit tests. */
export interface GltfTextureBakeStats {
  decodeCalls: number;
  colorizeCalls: number;
}

const PNG_SIG = [137, 80, 78, 71, 13, 10, 26, 10] as const;

const CRC32_TABLE = (() => {
  const table = new Uint32Array(256);
  for (let i = 0; i < 256; i++) {
    let c = i;
    for (let k = 0; k < 8; k++) {
      c = (c & 1) !== 0 ? (0xedb88320 ^ (c >>> 1)) : (c >>> 1);
    }
    table[i] = c >>> 0;
  }
  return table;
})();

function crc32(buf: Uint8Array, start: number, len: number): number {
  let c = 0xffffffff;
  const end = start + len;
  for (let i = start; i < end; i++) {
    c = CRC32_TABLE[(c ^ buf[i]) & 0xff] ^ (c >>> 8);
  }
  return (c ^ 0xffffffff) >>> 0;
}

function readU32BE(buf: Uint8Array, off: number): number {
  return (
    ((buf[off] << 24) | (buf[off + 1] << 16) | (buf[off + 2] << 8) | buf[off + 3]) >>> 0
  );
}

function writeU32BE(buf: Uint8Array, off: number, val: number): void {
  buf[off] = (val >>> 24) & 0xff;
  buf[off + 1] = (val >>> 16) & 0xff;
  buf[off + 2] = (val >>> 8) & 0xff;
  buf[off + 3] = val & 0xff;
}

function paethPredictor(a: number, b: number, c: number): number {
  const p = a + b - c;
  const pa = Math.abs(p - a);
  const pb = Math.abs(p - b);
  const pc = Math.abs(p - c);
  return pa <= pb && pa <= pc ? a : pb <= pc ? b : c;
}

/**
 * Drop JPEG `APP1` (`0xFFE1`, Exif / XMP orientation) marker segments before
 * `SOS` (`0xFFDA`) without re-encoding.
 */
export function stripJpegExifApp1(input: Uint8Array): Uint8Array {
  const data = toUint8ArrayView(input);
  const n = data.length;
  if (n < 4 || data[0] !== 0xff || data[1] !== 0xd8) return data;

  const dropRanges: [number, number][] = [];
  let pos = 2;
  while (pos < n) {
    if (data[pos] !== 0xff) break;
    const markerStart = pos;
    while (pos < n && data[pos] === 0xff) pos++;
    if (pos >= n) break;
    const marker = data[pos++];
    if (marker === 0x00 || marker === 0xd9) break;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
    if (pos + 2 > n) break;
    const segLen = (data[pos] << 8) | data[pos + 1];
    if (segLen < 2 || pos + segLen > n) break;
    const segEnd = pos + segLen;
    if (marker === 0xda) break; // Start of Scan
    if (marker === 0xe1) {
      dropRanges.push([markerStart, segEnd]);
    }
    pos = segEnd;
  }

  if (dropRanges.length === 0) return data;

  let dropped = 0;
  for (const [s, e] of dropRanges) dropped += e - s;
  const out = new Uint8Array(n - dropped);
  let cursor = 0;
  let dst = 0;
  for (const [start, end] of dropRanges) {
    if (start > cursor) {
      const slice = data.subarray(cursor, start);
      out.set(slice, dst);
      dst += slice.length;
    }
    cursor = end;
  }
  if (cursor < n) {
    out.set(data.subarray(cursor), dst);
  }
  return out;
}

/** Read JPEG frame `(width, height)` from its `SOF` marker before `SOS`. */
export function readJpegDimensions(input: Uint8Array): { width: number; height: number } | null {
  const data = toUint8ArrayView(input);
  const n = data.length;
  if (n < 4 || data[0] !== 0xff || data[1] !== 0xd8) return null;

  let pos = 2;
  while (pos < n) {
    if (data[pos] !== 0xff) break;
    while (pos < n && data[pos] === 0xff) pos++;
    if (pos >= n) break;
    const marker = data[pos++];
    if (marker === 0x00 || marker === 0xd9 || marker === 0xda) break;
    if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd8)) continue;
    if (pos + 2 > n) break;
    const segLen = (data[pos] << 8) | data[pos + 1];
    if (segLen < 2 || pos + segLen > n) break;
    if (
      marker >= 0xc0 &&
      marker <= 0xcf &&
      marker !== 0xc4 &&
      marker !== 0xc8 &&
      marker !== 0xcc
    ) {
      if (segLen < 7) return null;
      const height = (data[pos + 3] << 8) | data[pos + 4];
      const width = (data[pos + 5] << 8) | data[pos + 6];
      return { width, height };
    }
    pos += segLen;
  }
  return null;
}

/** Read PNG `IHDR` `(width, height)` before any `IDAT` inflation. */
export function readPngDimensions(input: Uint8Array): { width: number; height: number } | null {
  const data = toUint8ArrayView(input);
  if (data.length < 33) return null;
  for (let i = 0; i < 8; i++) {
    if (data[i] !== PNG_SIG[i]) return null;
  }
  const chunkLen = readU32BE(data, 8);
  const chunkType = readU32BE(data, 12);
  if (chunkType !== 0x49484452 || chunkLen !== 13) return null; // 'IHDR'
  return {
    width: readU32BE(data, 16),
    height: readU32BE(data, 20),
  };
}

function checkPixelLimit(width: number, height: number): void {
  if (width <= 0 || height <= 0) {
    throw new Error(`invalid image dimensions ${width}x${height}`);
  }
  const totalPixels = BigInt(width) * BigInt(height);
  if (totalPixels > MAX_DECODE_TEXTURE_PIXELS_BIG) {
    throw new Error(
      `image dimensions ${width}x${height} (${totalPixels.toString()} pixels) exceed maximum of ${MAX_DECODE_TEXTURE_PIXELS} pixels`
    );
  }
}

const ADAM7_PASSES: readonly [number, number, number, number][] = [
  [0, 8, 0, 8],
  [4, 8, 0, 8],
  [0, 4, 4, 8],
  [2, 4, 0, 4],
  [0, 2, 2, 4],
  [1, 2, 0, 2],
  [0, 1, 1, 2],
];

/**
 * Decode PNG bytes into 8-bit RGBA pixels using `fflate.unzlibSync`.
 * Enforces `MAX_DECODE_TEXTURE_PIXELS` on `IHDR` before decompressing `IDAT`.
 */
export function decodePngRgba(input: Uint8Array): { width: number; height: number; rgba: Uint8Array } {
  const data = toUint8ArrayView(input);
  if (data.length < 33) throw new Error('truncated PNG header');
  for (let i = 0; i < 8; i++) {
    if (data[i] !== PNG_SIG[i]) throw new Error('invalid PNG signature');
  }

  let width = 0;
  let height = 0;
  let bitDepth = 0;
  let colorType = 0;
  let interlace = 0;
  let channels = 0;
  let seenIhdr = false;
  let palette: Uint8Array | null = null;
  let trns: Uint8Array | null = null;
  const idatParts: Uint8Array[] = [];
  let totalIdatLen = 0;

  let pos = 8;
  while (pos + 8 <= data.length) {
    const len = readU32BE(data, pos);
    const type = readU32BE(data, pos + 4);
    if (len > data.length - pos - 12) {
      throw new Error('truncated PNG chunk');
    }
    const payloadStart = pos + 8;
    const payloadEnd = payloadStart + len;

    if (!seenIhdr) {
      if (type !== 0x49484452 || len !== 13) {
        throw new Error('missing or invalid PNG IHDR chunk');
      }
      width = readU32BE(data, payloadStart);
      height = readU32BE(data, payloadStart + 4);
      checkPixelLimit(width, height);

      bitDepth = data[payloadStart + 8];
      colorType = data[payloadStart + 9];
      const compMethod = data[payloadStart + 10];
      const filterMethod = data[payloadStart + 11];
      interlace = data[payloadStart + 12];
      if (compMethod !== 0 || filterMethod !== 0 || (interlace !== 0 && interlace !== 1)) {
        throw new Error('unsupported PNG compression/filter/interlace mode');
      }

      if (colorType === 0) {
        if (bitDepth !== 1 && bitDepth !== 2 && bitDepth !== 4 && bitDepth !== 8 && bitDepth !== 16) {
          throw new Error(`unsupported greyscale PNG bit depth ${bitDepth}`);
        }
        channels = 1;
      } else if (colorType === 2) {
        if (bitDepth !== 8 && bitDepth !== 16) {
          throw new Error(`unsupported RGB PNG bit depth ${bitDepth}`);
        }
        channels = 3;
      } else if (colorType === 3) {
        if (bitDepth !== 1 && bitDepth !== 2 && bitDepth !== 4 && bitDepth !== 8) {
          throw new Error(`unsupported indexed PNG bit depth ${bitDepth}`);
        }
        channels = 1;
      } else if (colorType === 4) {
        if (bitDepth !== 8 && bitDepth !== 16) {
          throw new Error(`unsupported grey+alpha PNG bit depth ${bitDepth}`);
        }
        channels = 2;
      } else if (colorType === 6) {
        if (bitDepth !== 8 && bitDepth !== 16) {
          throw new Error(`unsupported RGBA PNG bit depth ${bitDepth}`);
        }
        channels = 4;
      } else {
        throw new Error(`unsupported PNG color type ${colorType}`);
      }
      seenIhdr = true;
    } else if (type === 0x504c5445) {
      // PLTE
      if (len < 3 || len > 768 || len % 3 !== 0) {
        throw new Error('invalid PNG PLTE chunk');
      }
      palette = data.subarray(payloadStart, payloadEnd);
    } else if (type === 0x74524e53) {
      // tRNS
      trns = data.subarray(payloadStart, payloadEnd);
    } else if (type === 0x49444154) {
      // IDAT
      if (len > 0) {
        idatParts.push(data.subarray(payloadStart, payloadEnd));
        totalIdatLen += len;
      }
    } else if (type === 0x49454e44) {
      // IEND
      break;
    }

    pos = payloadEnd + 4;
  }

  if (!seenIhdr || totalIdatLen === 0) {
    throw new Error('PNG contains no IDAT image data');
  }
  if (colorType === 3 && !palette) {
    throw new Error('indexed PNG missing PLTE chunk');
  }

  const passSpecs: [number, number, number, number, number, number][] = [];
  let expectedRawBytes = 0;
  if (interlace === 0) {
    const rowBytes = Math.floor((width * channels * bitDepth + 7) / 8);
    expectedRawBytes = height * (1 + rowBytes);
    passSpecs.push([0, 1, 0, 1, width, height]);
  } else {
    for (const [x0, dx, y0, dy] of ADAM7_PASSES) {
      const pw = width > x0 ? Math.floor((width - x0 + dx - 1) / dx) : 0;
      const ph = height > y0 ? Math.floor((height - y0 + dy - 1) / dy) : 0;
      if (pw > 0 && ph > 0) {
        const rowBytes = Math.floor((pw * channels * bitDepth + 7) / 8);
        expectedRawBytes += ph * (1 + rowBytes);
        passSpecs.push([x0, dx, y0, dy, pw, ph]);
      }
    }
  }

  let idat: Uint8Array;
  if (idatParts.length === 1) {
    idat = idatParts[0];
  } else {
    idat = new Uint8Array(totalIdatLen);
    let off = 0;
    for (const part of idatParts) {
      idat.set(part, off);
      off += part.length;
    }
  }

  const inflated = unzlibSync(idat, { out: new Uint8Array(expectedRawBytes) });
  if (inflated.length < expectedRawBytes) {
    throw new Error('truncated PNG IDAT deflate stream');
  }

  const rgba = new Uint8Array(width * height * 4);
  const bpp = Math.max(1, (channels * bitDepth + 7) >> 3);
  const hasGreyTrns = colorType === 0 && trns !== null && trns.length >= 2;
  const greyTrnsVal = hasGreyTrns
    ? ((trns![0] << 8) | trns![1]) & (bitDepth === 16 ? 0xffff : (1 << bitDepth) - 1)
    : -1;
  const hasRgbTrns = colorType === 2 && trns !== null && trns.length >= 6;
  const rgbTrnsR = hasRgbTrns ? ((trns![0] << 8) | trns![1]) & (bitDepth === 16 ? 0xffff : 0xff) : -1;
  const rgbTrnsG = hasRgbTrns ? ((trns![2] << 8) | trns![3]) & (bitDepth === 16 ? 0xffff : 0xff) : -1;
  const rgbTrnsB = hasRgbTrns ? ((trns![4] << 8) | trns![5]) & (bitDepth === 16 ? 0xffff : 0xff) : -1;
  const palEntries = palette ? palette.length / 3 : 0;

  let rawCursor = 0;
  for (const [x0, dx, y0, dy, pw, ph] of passSpecs) {
    const rowBytes = Math.floor((pw * channels * bitDepth + 7) / 8);
    let prevRow = new Uint8Array(rowBytes);
    let currRow = new Uint8Array(rowBytes);

    for (let py = 0; py < ph; py++) {
      const filterType = inflated[rawCursor++];
      const srcBase = rawCursor;
      rawCursor += rowBytes;

      if (filterType === 0) {
        currRow.set(inflated.subarray(srcBase, srcBase + rowBytes));
      } else if (filterType === 1) {
        for (let i = 0; i < rowBytes; i++) {
          const left = i >= bpp ? currRow[i - bpp] : 0;
          currRow[i] = (inflated[srcBase + i] + left) & 0xff;
        }
      } else if (filterType === 2) {
        for (let i = 0; i < rowBytes; i++) {
          currRow[i] = (inflated[srcBase + i] + prevRow[i]) & 0xff;
        }
      } else if (filterType === 3) {
        for (let i = 0; i < rowBytes; i++) {
          const left = i >= bpp ? currRow[i - bpp] : 0;
          currRow[i] = (inflated[srcBase + i] + ((left + prevRow[i]) >>> 1)) & 0xff;
        }
      } else if (filterType === 4) {
        for (let i = 0; i < rowBytes; i++) {
          const left = i >= bpp ? currRow[i - bpp] : 0;
          const upLeft = i >= bpp ? prevRow[i - bpp] : 0;
          currRow[i] = (inflated[srcBase + i] + paethPredictor(left, prevRow[i], upLeft)) & 0xff;
        }
      } else {
        throw new Error(`invalid PNG filter type ${filterType}`);
      }

      const dstY = y0 + py * dy;
      if (interlace === 0 && bitDepth === 8 && colorType === 6) {
        rgba.set(currRow, dstY * width * 4);
      } else if (interlace === 0 && bitDepth === 8 && colorType === 2 && !hasRgbTrns) {
        let sOff = 0;
        let dOff = dstY * width * 4;
        for (let px = 0; px < pw; px++) {
          rgba[dOff] = currRow[sOff];
          rgba[dOff + 1] = currRow[sOff + 1];
          rgba[dOff + 2] = currRow[sOff + 2];
          rgba[dOff + 3] = 255;
          sOff += 3;
          dOff += 4;
        }
      } else {
        for (let px = 0; px < pw; px++) {
          const dstIdx = (dstY * width + (x0 + px * dx)) * 4;
          if (colorType === 6) {
            if (bitDepth === 8) {
              const s = px * 4;
              rgba[dstIdx] = currRow[s];
              rgba[dstIdx + 1] = currRow[s + 1];
              rgba[dstIdx + 2] = currRow[s + 2];
              rgba[dstIdx + 3] = currRow[s + 3];
            } else {
              const s = px * 8;
              rgba[dstIdx] = (((currRow[s] << 8) | currRow[s + 1]) * 255 + 32895) >>> 16;
              rgba[dstIdx + 1] = (((currRow[s + 2] << 8) | currRow[s + 3]) * 255 + 32895) >>> 16;
              rgba[dstIdx + 2] = (((currRow[s + 4] << 8) | currRow[s + 5]) * 255 + 32895) >>> 16;
              rgba[dstIdx + 3] = (((currRow[s + 6] << 8) | currRow[s + 7]) * 255 + 32895) >>> 16;
            }
          } else if (colorType === 2) {
            if (bitDepth === 8) {
              const s = px * 3;
              const r = currRow[s];
              const g = currRow[s + 1];
              const b = currRow[s + 2];
              rgba[dstIdx] = r;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = b;
              rgba[dstIdx + 3] =
                hasRgbTrns && r === rgbTrnsR && g === rgbTrnsG && b === rgbTrnsB ? 0 : 255;
            } else {
              const s = px * 6;
              const r16 = (currRow[s] << 8) | currRow[s + 1];
              const g16 = (currRow[s + 2] << 8) | currRow[s + 3];
              const b16 = (currRow[s + 4] << 8) | currRow[s + 5];
              rgba[dstIdx] = (r16 * 255 + 32895) >>> 16;
              rgba[dstIdx + 1] = (g16 * 255 + 32895) >>> 16;
              rgba[dstIdx + 2] = (b16 * 255 + 32895) >>> 16;
              rgba[dstIdx + 3] =
                hasRgbTrns && r16 === rgbTrnsR && g16 === rgbTrnsG && b16 === rgbTrnsB ? 0 : 255;
            }
          } else if (colorType === 4) {
            if (bitDepth === 8) {
              const g = currRow[px * 2];
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = currRow[px * 2 + 1];
            } else {
              const s = px * 4;
              const g = (((currRow[s] << 8) | currRow[s + 1]) * 255 + 32895) >>> 16;
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = (((currRow[s + 2] << 8) | currRow[s + 3]) * 255 + 32895) >>> 16;
            }
          } else if (colorType === 0) {
            if (bitDepth === 8) {
              const g = currRow[px];
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = hasGreyTrns && g === greyTrnsVal ? 0 : 255;
            } else if (bitDepth === 16) {
              const g16 = (currRow[px * 2] << 8) | currRow[px * 2 + 1];
              const g = (g16 * 255 + 32895) >>> 16;
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = hasGreyTrns && g16 === greyTrnsVal ? 0 : 255;
            } else {
              const bitPos = px * bitDepth;
              const byteIdx = bitPos >>> 3;
              const shift = 8 - bitDepth - (bitPos & 7);
              const mask = (1 << bitDepth) - 1;
              const sample = (currRow[byteIdx] >>> shift) & mask;
              const g = Math.round((sample * 255) / mask);
              rgba[dstIdx] = g;
              rgba[dstIdx + 1] = g;
              rgba[dstIdx + 2] = g;
              rgba[dstIdx + 3] = hasGreyTrns && sample === greyTrnsVal ? 0 : 255;
            }
          } else {
            // Indexed (colorType === 3)
            let idx: number;
            if (bitDepth === 8) {
              idx = currRow[px];
            } else {
              const bitPos = px * bitDepth;
              const byteIdx = bitPos >>> 3;
              const shift = 8 - bitDepth - (bitPos & 7);
              idx = (currRow[byteIdx] >>> shift) & ((1 << bitDepth) - 1);
            }
            if (idx >= palEntries) {
              throw new Error(`PNG palette index ${idx} out of range (${palEntries})`);
            }
            rgba[dstIdx] = palette![idx * 3];
            rgba[dstIdx + 1] = palette![idx * 3 + 1];
            rgba[dstIdx + 2] = palette![idx * 3 + 2];
            rgba[dstIdx + 3] = trns && idx < trns.length ? trns[idx] : 255;
          }
        }
      }

      const tmp = prevRow;
      prevRow = currRow;
      currRow = tmp;
    }
  }

  return { width, height, rgba };
}

function writePngChunk(
  out: Uint8Array,
  offset: number,
  typeAscii: string,
  payload: Uint8Array
): number {
  const len = payload.length;
  writeU32BE(out, offset, len);
  const crcStart = offset + 4;
  out[crcStart] = typeAscii.charCodeAt(0);
  out[crcStart + 1] = typeAscii.charCodeAt(1);
  out[crcStart + 2] = typeAscii.charCodeAt(2);
  out[crcStart + 3] = typeAscii.charCodeAt(3);
  if (len > 0) {
    out.set(payload, crcStart + 4);
  }
  const c = crc32(out, crcStart, 4 + len);
  writeU32BE(out, crcStart + 4 + len, c);
  return crcStart + 8 + len;
}

/**
 * Encode 8-bit RGB (`channels === 3`) or RGBA (`channels === 4`) pixels to PNG
 * using adaptive scanline filtering and `fflate.zlibSync`.
 */
export function encodePngU8(
  pixels: Uint8Array,
  width: number,
  height: number,
  channels: 3 | 4
): Uint8Array {
  if (width <= 0 || height <= 0 || (channels !== 3 && channels !== 4)) {
    throw new Error('encodePngU8 requires positive dimensions and 3 or 4 channels');
  }
  const rowBytes = width * channels;
  const rawScanlines = new Uint8Array(height * (1 + rowBytes));
  const cand = new Uint8Array(rowBytes);
  const bpp = channels;

  for (let y = 0; y < height; y++) {
    const rowOff = y * rowBytes;
    const prevOff = (y - 1) * rowBytes;
    const dstScanOff = y * (1 + rowBytes);
    let bestFilter = 0;
    let bestScore = Infinity;

    for (let f = 0; f < 5; f++) {
      // On row 0, Up (2) equals None (0), so skip duplicate evaluation.
      if (y === 0 && f === 2) continue;
      let score = 0;
      if (f === 0) {
        for (let i = 0; i < rowBytes; i++) {
          const v = pixels[rowOff + i];
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      } else if (f === 1) {
        for (let i = 0; i < rowBytes; i++) {
          const left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
          const v = (pixels[rowOff + i] - left) & 0xff;
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      } else if (f === 2) {
        for (let i = 0; i < rowBytes; i++) {
          const v = (pixels[rowOff + i] - pixels[prevOff + i]) & 0xff;
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      } else if (f === 3) {
        for (let i = 0; i < rowBytes; i++) {
          const left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
          const up = y > 0 ? pixels[prevOff + i] : 0;
          const v = (pixels[rowOff + i] - ((left + up) >>> 1)) & 0xff;
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      } else {
        for (let i = 0; i < rowBytes; i++) {
          const left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
          const up = y > 0 ? pixels[prevOff + i] : 0;
          const upLeft = y > 0 && i >= bpp ? pixels[prevOff + i - bpp] : 0;
          const v = (pixels[rowOff + i] - paethPredictor(left, up, upLeft)) & 0xff;
          cand[i] = v;
          score += v < 128 ? v : 256 - v;
        }
      }
      if (score < bestScore) {
        bestScore = score;
        bestFilter = f;
        rawScanlines[dstScanOff] = f;
        rawScanlines.set(cand, dstScanOff + 1);
      }
    }
    void bestFilter;
  }

  const compressed = zlibSync(rawScanlines, { level: 6 });
  const ihdr = new Uint8Array(13);
  writeU32BE(ihdr, 0, width);
  writeU32BE(ihdr, 4, height);
  ihdr[8] = 8;
  ihdr[9] = channels === 4 ? 6 : 2;
  ihdr[10] = 0;
  ihdr[11] = 0;
  ihdr[12] = 0;

  const out = new Uint8Array(8 + 25 + 12 + compressed.length + 12);
  out.set(PNG_SIG, 0);
  let cursor = 8;
  cursor = writePngChunk(out, cursor, 'IHDR', ihdr);
  cursor = writePngChunk(out, cursor, 'IDAT', compressed);
  writePngChunk(out, cursor, 'IEND', new Uint8Array(0));
  return out;
}

function rgbToHlsF64(r: number, g: number, b: number): [number, number, number] {
  const maxc = Math.max(r, g, b);
  const minc = Math.min(r, g, b);
  const sumc = maxc + minc;
  const rangec = maxc - minc;
  const l = sumc / 2.0;
  if (minc === maxc) return [0.0, l, 0.0];
  const s = l <= 0.5 ? rangec / sumc : rangec / (2.0 - maxc - minc);
  const rc = (maxc - r) / rangec;
  const gc = (maxc - g) / rangec;
  const bc = (maxc - b) / rangec;
  let h: number;
  if (r === maxc) {
    h = bc - gc;
  } else if (g === maxc) {
    h = 2.0 + rc - bc;
  } else {
    h = 4.0 + gc - rc;
  }
  h = (h / 6.0) % 1.0;
  if (h < 0.0) h += 1.0;
  return [h, l, s];
}

export function colorizePixelsImpl(
  rgba: Uint8Array | Uint8ClampedArray,
  dst: Uint8Array,
  width: number,
  height: number,
  targetRgb: readonly [number, number, number],
  colorizeType: number,
  keepAlpha: boolean,
  maxBlockPixels: number = MAX_COLORIZE_BLOCK_PIXELS
): void {
  if (width <= 0 || height <= 0) return;
  const nPx = width * height;
  const outChannels = keepAlpha ? 4 : 3;

  // SUTextureGetAverageColor truncates each channel's mean over all pixels to uint8.
  let sumR = 0;
  let sumG = 0;
  let sumB = 0;
  for (let i = 0; i < nPx; i++) {
    const p4 = i * 4;
    sumR += rgba[p4];
    sumG += rgba[p4 + 1];
    sumB += rgba[p4 + 2];
  }
  const avgR = Math.floor(sumR / nPx) / 255.0;
  const avgG = Math.floor(sumG / nPx) / 255.0;
  const avgB = Math.floor(sumB / nPx) / 255.0;
  const [avgH, avgL, avgS] = rgbToHlsF64(avgR, avgG, avgB);
  const [tgtH, tgtL, tgtS] = rgbToHlsF64(
    targetRgb[0] / 255.0,
    targetRgb[1] / 255.0,
    targetRgb[2] / 255.0
  );

  const fr = Math.fround;
  const isShift = (colorizeType | 0) === 0;
  const deltaH = fr(tgtH - avgH);
  const deltaL = fr(tgtL - avgL);
  // SUMaterialGetColorizeDeltas sets delta_s = -1.0 when desaturating a chromatic
  // texture toward a greyscale target (tgt_s == 0 and avg_s > 0).
  const deltaS = fr(tgtS === 0.0 && avgS > 0.0 ? -1.0 : tgtS - avgS);
  const tgtHF = fr(tgtH);

  const ONE_SIXTH = fr(1.0 / 6.0);
  const ONE_THIRD = fr(1.0 / 3.0);
  const NEG_ONE_THIRD = fr(-1.0 / 3.0);
  const HALF = fr(0.5);
  const ONE = fr(1.0);
  const TWO = fr(2.0);
  const FOUR = fr(4.0);
  const SIX = fr(6.0);
  const MIN_L = fr(0.01);
  const UNDEF_H = fr(-1.0 / 360.0);
  const EPS = fr(1e-6);
  const SCALE_255 = fr(255.0);
  const ZERO = fr(0.0);
  const OFFSETS = [ONE_THIRD, ZERO, NEG_ONE_THIRD] as const;

  const effBlock = Math.max(1, Math.floor(maxBlockPixels));
  const blockRows = Math.min(height, Math.max(1, Math.floor(effBlock / width)));

  for (let y0 = 0; y0 < height; y0 += blockRows) {
    const y1 = Math.min(height, y0 + blockRows);
    const pStart = y0 * width;
    const pEnd = y1 * width;
    for (let p = pStart; p < pEnd; p++) {
      const p4 = p * 4;
      const srcR = rgba[p4];
      const srcG = rgba[p4 + 1];
      const srcB = rgba[p4 + 2];
      const srcA = rgba[p4 + 3];

      const r = fr(srcR / SCALE_255);
      const g = fr(srcG / SCALE_255);
      const b = fr(srcB / SCALE_255);

      const cm = Math.max(r, g, b);
      const cn = Math.min(r, g, b);
      const rg = fr(cm - cn);
      const sum = fr(cm + cn);
      let lt = fr(sum * HALF);
      const nz = rg > EPS;
      let st = ZERO;
      if (nz) {
        const denom = lt <= HALF ? sum : fr(TWO - sum);
        st = fr(rg / denom);
      }

      let nh = tgtHF;
      if (isShift) {
        // SketchUp's RGB_to_HLS assigns H = -1 deg (-1/360 turn) to achromatic pixels.
        if (!nz) {
          nh = UNDEF_H;
        } else {
          if (r === cm) {
            nh = fr(fr(g - b) / rg);
          } else if (g === cm) {
            nh = fr(fr(fr(b - r) / rg) + TWO);
          } else {
            nh = fr(fr(fr(r - g) / rg) + FOUR);
          }
          nh = fr(nh * ONE_SIXTH);
          nh = fr(nh - fr(Math.floor(nh)));
        }
        nh = fr(nh + deltaH);
        nh = fr(nh - fr(Math.floor(nh)));
      }

      lt = fr(lt + deltaL);
      if (lt < MIN_L) lt = MIN_L;
      else if (lt > ONE) lt = ONE;

      st = fr(st + deltaS);
      if (st < ZERO) st = ZERO;
      else if (st > ONE) st = ONE;

      // Use lt + st * (1 - lt) for lt > 0.5 so 1 - lt is exact in float32 (Sterbenz's lemma).
      const m2 = lt > HALF ? fr(lt + fr(st * fr(ONE - lt))) : fr(lt + fr(lt * st));
      const m1 = fr(fr(lt * TWO) - m2);
      const span = fr(m2 - m1);
      const achromatic = st <= EPS;

      const outOff = p * outChannels;
      for (let ch = 0; ch < 3; ch++) {
        let t1 = nh;
        const off = OFFSETS[ch];
        if (off !== ZERO) {
          t1 = fr(t1 + off);
          t1 = fr(t1 - fr(Math.floor(t1)));
        }
        t1 = fr(fr(t1 * SIX) - TWO);
        t1 = fr(TWO - Math.abs(t1));
        if (t1 < ZERO) t1 = ZERO;
        else if (t1 > ONE) t1 = ONE;

        let val = lt;
        if (!achromatic) {
          val = t1 === ONE ? m2 : fr(fr(t1 * span) + m1);
        }
        val = fr(val * SCALE_255);
        if (val < ZERO) val = ZERO;
        else if (val > SCALE_255) val = SCALE_255;
        dst[outOff + ch] = val | 0;
      }
      if (keepAlpha) {
        dst[outOff + 3] = srcA;
      }
    }
  }
}

/**
 * Apply SketchUp's HLS colorize rules (`colorizeType` `0` = shift, `1` = tint)
 * in `float32` row blocks, matching `SUTextureGetColorizedImageRep` within `1/255`.
 */
export function colorizeImageU8(
  rgba: Uint8Array | Uint8ClampedArray,
  width: number,
  height: number,
  targetRgb: readonly [number, number, number],
  colorizeType: number,
  keepAlpha: boolean = true,
  maxBlockPixels: number = MAX_COLORIZE_BLOCK_PIXELS
): Uint8Array {
  if (width <= 0 || height <= 0) return new Uint8Array(0);
  const outChannels = keepAlpha ? 4 : 3;
  const out = new Uint8Array(width * height * outChannels);
  colorizePixelsImpl(rgba, out, width, height, targetRgb, colorizeType, keepAlpha, maxBlockPixels);
  return out;
}

function srgbToLinear(c: number): number {
  if (c <= 0) return 0;
  if (c >= 1) return 1;
  return c <= 0.04045 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
}

function linearizeBaseColorFactor(pbr: any): any {
  const bcf = pbr?.baseColorFactor;
  if (!Array.isArray(bcf) || bcf.length < 3) return pbr;
  return {
    ...pbr,
    baseColorFactor: [
      srgbToLinear(bcf[0]),
      srgbToLinear(bcf[1]),
      srgbToLinear(bcf[2]),
      bcf.length > 3 ? bcf[3] : 1.0,
    ],
  };
}

function isColorizedMaterial(mat: any): boolean {
  return Boolean(mat?.extras?.sketchup?.colorized);
}

interface DecodedEntry {
  width: number;
  height: number;
  rgba: Uint8Array | Uint8ClampedArray;
  ownedBuffer: boolean;
}

/**
 * Resolve embedded GLB textures and per-material PBR properties for `toGLB()`
 * and `toInstancedGLB()` without mutating the caller's scene:
 * - Bakes colorized PNGs out of the box via `fflate`, and colorized JPEGs at
 *   `quality = 95` when `imageCodec` is provided, once per unique
 *   `(textureIndex, colorizeType, targetRgb)`.
 * - Strips JPEG `APP1` EXIF orientation segments without re-encoding.
 * - Enforces `MAX_DECODE_TEXTURE_PIXELS` (`178,956,970`) from the image header
 *   before decoding, keeping source bytes and the tinted factor with one warning
 *   when exceeded, corrupt, or when a colorized JPEG has no `imageCodec`.
 */
export function resolveGltfTextures(
  rawTextures: SceneTexture[],
  rawMaterials: unknown[],
  embed: boolean,
  onLog?: (level: LogLevel, message: string) => void,
  imageCodec?: ImageCodec,
  stats?: GltfTextureBakeStats
): { textures: SceneTexture[]; materials: unknown[] } {
  if (!embed || rawTextures.length === 0) {
    const stripped = rawMaterials.map((mat) => {
      const m = mat as any;
      const pbr = m?.pbrMetallicRoughness;
      if (!pbr) return mat;
      const nextPbr = linearizeBaseColorFactor(pbr);
      if (nextPbr.baseColorTexture) {
        const restPbr = { ...nextPbr };
        delete restPbr.baseColorTexture;
        return { ...m, pbrMetallicRoughness: restPbr };
      }
      return nextPbr === pbr ? mat : { ...m, pbrMetallicRoughness: nextPbr };
    });
    return { textures: [], materials: stripped };
  }

  // Warn once per distinct unsupported format (preserving #635 exact messages).
  const warnedUnsupported = new Set<string>();
  for (let i = 0; i < rawTextures.length; i++) {
    const tex = rawTextures[i];
    if (tex.mimeType !== 'image/png' && tex.mimeType !== 'image/jpeg') {
      const filename = tex.filename ?? '';
      const base = filename.replace(/\\/g, '/').split('/').pop() ?? '';
      const fmt = describeImageFormat(tex.data, filename);
      const warnKey = `${base}\0${fmt}`;
      if (!warnedUnsupported.has(warnKey)) {
        warnedUnsupported.add(warnKey);
        const label = base ? ` '${base}'` : '';
        onLog?.(
          'warn',
          `Texture${label} is ${fmt}, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB`
        );
      }
    }
  }

  // Pre-count distinct colorized variants per PNG texture so the final variant
  // can colorize our freshly inflated RGBA buffer in place without allocating
  // a second full-image pixel buffer.
  const uniqueColorizeKeys = new Set<string>();
  const remainingVariants = new Map<number, number>();
  for (const rawMat of rawMaterials) {
    const m = rawMat as any;
    const pbr = m?.pbrMetallicRoughness;
    const texRef = pbr?.baseColorTexture;
    if (!texRef || typeof texRef.index !== 'number') continue;
    const texIdx = texRef.index;
    if (texIdx < 0 || texIdx >= rawTextures.length) continue;
    const tex = rawTextures[texIdx];
    if (tex.mimeType !== 'image/png' && tex.mimeType !== 'image/jpeg') continue;
    if (!isColorizedMaterial(m)) continue;
    if (tex.mimeType === 'image/jpeg' && !imageCodec) continue;
    const bcf = Array.isArray(pbr.baseColorFactor) ? pbr.baseColorFactor : [1, 1, 1, 1];
    const r = Math.max(0, Math.min(255, Math.round((bcf[0] ?? 1) * 255)));
    const g = Math.max(0, Math.min(255, Math.round((bcf[1] ?? 1) * 255)));
    const b = Math.max(0, Math.min(255, Math.round((bcf[2] ?? 1) * 255)));
    const ctype = Number(m?.extras?.sketchup?.colorize_type ?? 0) | 0;
    const ckey = `${texIdx}:1:${ctype}:${r},${g},${b}`;
    if (!uniqueColorizeKeys.has(ckey)) {
      uniqueColorizeKeys.add(ckey);
      remainingVariants.set(texIdx, (remainingVariants.get(texIdx) ?? 0) + 1);
    }
  }

  const outTextures: SceneTexture[] = [];
  const resolvedByKey = new Map<string, { index: number; baked: boolean }>();
  const decodedByTex = new Map<number, DecodedEntry | null>();
  let warnedNoJpegCodec = false;

  const getPlainTextureIndex = (texIdx: number): number => {
    const key = `${texIdx}:0`;
    const cached = resolvedByKey.get(key);
    if (cached !== undefined) return cached.index;
    const tex = rawTextures[texIdx];
    const bytes =
      tex.mimeType === 'image/jpeg'
        ? stripJpegExifApp1(tex.data)
        : toUint8ArrayView(tex.data);
    const gltfIdx = outTextures.length;
    outTextures.push({ data: bytes, mimeType: tex.mimeType, filename: tex.filename });
    resolvedByKey.set(key, { index: gltfIdx, baked: false });
    return gltfIdx;
  };

  const getDecoded = (texIdx: number): DecodedEntry | null => {
    if (decodedByTex.has(texIdx)) {
      return decodedByTex.get(texIdx)!;
    }
    if (stats) stats.decodeCalls++;
    const tex = rawTextures[texIdx];
    let entry: DecodedEntry | null;
    try {
      if (tex.mimeType === 'image/png') {
        const dec = decodePngRgba(tex.data);
        entry = { width: dec.width, height: dec.height, rgba: dec.rgba, ownedBuffer: true };
      } else {
        const dims = readJpegDimensions(tex.data);
        if (!dims) {
          throw new Error('missing or corrupt JPEG SOF header');
        }
        checkPixelLimit(dims.width, dims.height);
        const dec = imageCodec!.decode(toUint8ArrayView(tex.data), tex.mimeType);
        if (!dec) {
          throw new Error('imageCodec.decode returned null');
        }
        checkPixelLimit(dec.width, dec.height);
        const expectedLen = dec.width * dec.height * 4;
        const rgbaView =
          dec.data instanceof Uint8ClampedArray
            ? dec.data
            : toUint8ArrayView(dec.data);
        if (rgbaView.length < expectedLen) {
          throw new Error(
            `imageCodec.decode returned ${rgbaView.length} bytes for ${dec.width}x${dec.height} RGBA (expected ${expectedLen})`
          );
        }
        entry = {
          width: dec.width,
          height: dec.height,
          rgba: rgbaView.length === expectedLen ? rgbaView : rgbaView.subarray(0, expectedLen),
          ownedBuffer: false,
        };
      }
    } catch (err) {
      const namePart = tex.filename ? ` ('${tex.filename}')` : '';
      const reason = err instanceof Error ? err.message : String(err);
      onLog?.('warn', `Failed to decode texture ${texIdx}${namePart} for GLB export: ${reason}`);
      entry = null;
    }
    decodedByTex.set(texIdx, entry);
    return entry;
  };

  const materials = rawMaterials.map((mat) => {
    const m = mat as any;
    const pbr = m?.pbrMetallicRoughness;
    if (!pbr) return mat;
    const texRef = pbr.baseColorTexture;
    if (!texRef || typeof texRef.index !== 'number') {
      const nextPbr = linearizeBaseColorFactor(pbr);
      return nextPbr === pbr ? mat : { ...m, pbrMetallicRoughness: nextPbr };
    }
    const texIdx = texRef.index;
    const tex = texIdx >= 0 && texIdx < rawTextures.length ? rawTextures[texIdx] : null;
    if (!tex || (tex.mimeType !== 'image/png' && tex.mimeType !== 'image/jpeg')) {
      const restPbr = linearizeBaseColorFactor(pbr);
      delete restPbr.baseColorTexture;
      const nextMat = { ...m, pbrMetallicRoughness: restPbr };
      if (nextMat.alphaMode === 'MASK') delete nextMat.alphaMode;
      return nextMat;
    }

    const bcf = Array.isArray(pbr.baseColorFactor) ? pbr.baseColorFactor : [1, 1, 1, 1];
    const alpha = bcf.length > 3 ? bcf[3] : 1.0;

    if (!isColorizedMaterial(m)) {
      const mapped = getPlainTextureIndex(texIdx);
      return {
        ...m,
        pbrMetallicRoughness: {
          ...pbr,
          baseColorFactor: [1.0, 1.0, 1.0, alpha],
          baseColorTexture: mapped === texIdx ? texRef : { ...texRef, index: mapped },
        },
      };
    }

    if (tex.mimeType === 'image/jpeg' && !imageCodec) {
      if (!warnedNoJpegCodec) {
        warnedNoJpegCodec = true;
        onLog?.(
          'warn',
          'No imageCodec provided; colorized JPEG textures in GLB export stay untinted with their material factor (pass imageCodec in GlbOptions to bake colorized JPEGs).'
        );
      }
      const mapped = getPlainTextureIndex(texIdx);
      if (mapped === texIdx) return mat;
      return {
        ...m,
        pbrMetallicRoughness: {
          ...pbr,
          baseColorTexture: { ...texRef, index: mapped },
        },
      };
    }

    const r = Math.max(0, Math.min(255, Math.round((bcf[0] ?? 1) * 255)));
    const g = Math.max(0, Math.min(255, Math.round((bcf[1] ?? 1) * 255)));
    const b = Math.max(0, Math.min(255, Math.round((bcf[2] ?? 1) * 255)));
    const ctype = Number(m?.extras?.sketchup?.colorize_type ?? 0) | 0;
    const ckey = `${texIdx}:1:${ctype}:${r},${g},${b}`;

    let resolved = resolvedByKey.get(ckey);
    if (resolved === undefined) {
      const rem = (remainingVariants.get(texIdx) ?? 1) - 1;
      remainingVariants.set(texIdx, Math.max(0, rem));

      const decoded = getDecoded(texIdx);
      if (!decoded) {
        const plainIdx = getPlainTextureIndex(texIdx);
        resolved = { index: plainIdx, baked: false };
        resolvedByKey.set(ckey, resolved);
      } else {
        try {
          if (stats) stats.colorizeCalls++;
          const nPx = decoded.width * decoded.height;
          let encoded: Uint8Array;
          if (tex.mimeType === 'image/jpeg') {
            const bakedRgba = colorizeImageU8(
              decoded.rgba,
              decoded.width,
              decoded.height,
              [r, g, b],
              ctype,
              true
            );
            if (rem <= 0) decodedByTex.delete(texIdx);
            encoded = toUint8ArrayView(
              imageCodec!.encode(
                { width: decoded.width, height: decoded.height, data: bakedRgba },
                'image/jpeg',
                95
              )
            );
            if (encoded.length === 0) {
              throw new Error('imageCodec.encode returned empty output');
            }
          } else {
            let keepAlpha = false;
            const rawRgba = decoded.rgba;
            for (let p = 0; p < nPx; p++) {
              if (rawRgba[p * 4 + 3] < 255) {
                keepAlpha = true;
                break;
              }
            }
            const outCh: 3 | 4 = keepAlpha ? 4 : 3;
            if (rem <= 0 && decoded.ownedBuffer && rawRgba instanceof Uint8Array) {
              colorizePixelsImpl(
                rawRgba,
                rawRgba,
                decoded.width,
                decoded.height,
                [r, g, b],
                ctype,
                keepAlpha
              );
              encoded = encodePngU8(
                rawRgba.subarray(0, nPx * outCh),
                decoded.width,
                decoded.height,
                outCh
              );
              decodedByTex.delete(texIdx);
            } else {
              const baked = colorizeImageU8(
                rawRgba,
                decoded.width,
                decoded.height,
                [r, g, b],
                ctype,
                keepAlpha
              );
              encoded = encodePngU8(baked, decoded.width, decoded.height, outCh);
            }
          }
          const gltfIdx = outTextures.length;
          outTextures.push({ data: encoded, mimeType: tex.mimeType, filename: tex.filename });
          resolved = { index: gltfIdx, baked: true };
          resolvedByKey.set(ckey, resolved);
        } catch (err) {
          const namePart = tex.filename ? ` ('${tex.filename}')` : '';
          const reason = err instanceof Error ? err.message : String(err);
          onLog?.('warn', `Failed to encode baked texture ${texIdx}${namePart} for GLB export: ${reason}`);
          const plainIdx = getPlainTextureIndex(texIdx);
          resolved = { index: plainIdx, baked: false };
          resolvedByKey.set(ckey, resolved);
        }
      }
    }

    if (!resolved.baked) {
      if (resolved.index === texIdx) return mat;
      return {
        ...m,
        pbrMetallicRoughness: {
          ...pbr,
          baseColorTexture: { ...texRef, index: resolved.index },
        },
      };
    }

    return {
      ...m,
      pbrMetallicRoughness: {
        ...pbr,
        baseColorFactor: [1.0, 1.0, 1.0, alpha],
        baseColorTexture: resolved.index === texIdx ? texRef : { ...texRef, index: resolved.index },
      },
    };
  });

  return { textures: outTextures, materials };
}
