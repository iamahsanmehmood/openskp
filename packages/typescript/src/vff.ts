import { unzipSync, UnzipFileInfo } from 'fflate';
import { ParseOptions, emitLog } from './observability';

export interface SkpContents {
  version: string;
  modelData: Uint8Array;
  materialFiles: Record<string, Uint8Array>;
  /** Raw bytes of meta/meta.dat, or null when the entry is absent. */
  metaData: Uint8Array | null;
}

const VFF_MAGIC = [0xFF, 0xFE, 0xFF, 0x0E];
const ZIP_LOCAL_HEADER = [0x50, 0x4B, 0x03, 0x04]; // PK\x03\x04

function findSequence(data: Uint8Array, sequence: number[], startOffset: number = 0): number {
  const seqLen = sequence.length;
  if (seqLen === 0) return -1;
  const limit = data.length - seqLen;
  for (let i = startOffset; i <= limit; i++) {
    let match = true;
    for (let j = 0; j < seqLen; j++) {
      if (data[i + j] !== sequence[j]) {
        match = false;
        break;
      }
    }
    if (match) return i;
  }
  return -1;
}

/**
 * Recover a ZIP entry name that was decoded as Latin-1.
 *
 * SketchUp writes entry names as UTF-8 but does not set the language encoding
 * flag (general purpose bit 11), so a spec-compliant reader falls back to
 * CP437/Latin-1: a material folder named "ДСП Egger" arrives as "ÐÐ¡Ð Egger".
 * That mangled name is later compared against the material's real name and
 * never matches, so the material silently loses its id and every face using it
 * falls back to the layer colour.
 *
 * The Latin-1 mapping is lossless, so the original bytes can be recovered and
 * decoded again as UTF-8. Names that are not valid UTF-8 are kept as they are,
 * and a pure ASCII name survives the round trip unchanged.
 */
function decodeEntryName(entry: string): string {
  if (!/[\u0080-\u00ff]/.test(entry)) return entry;
  const bytes = new Uint8Array(entry.length);
  for (let i = 0; i < entry.length; i++) {
    const code = entry.charCodeAt(i);
    if (code > 0xff) return entry;
    bytes[i] = code;
  }
  try {
    return new TextDecoder('utf-8', { fatal: true }).decode(bytes);
  } catch {
    return entry;
  }
}

export function validateHeader(data: Uint8Array): boolean {
  if (data.length < 4) return false;
  return (
    data[0] === VFF_MAGIC[0] &&
    data[1] === VFF_MAGIC[1] &&
    data[2] === VFF_MAGIC[2] &&
    data[3] === VFF_MAGIC[3]
  );
}

export function readVersion(data: Uint8Array, options?: ParseOptions): string {
  if (data.length < 16) return 'unknown';

  // Find second FF FE FF marker after the initial one at offset 0
  const secondMarker = findSequence(data, [0xFF, 0xFE, 0xFF], 4);
  if (secondMarker > 0) {
    const verStart = secondMarker + 4;
    const verBytes = data.subarray(verStart, Math.min(verStart + 200, data.length));
    try {
      const decoder = new TextDecoder('utf-16le');
      const verText = decoder.decode(verBytes);
      const braceStart = verText.indexOf('{');
      if (braceStart >= 0) {
        const braceEnd = verText.indexOf('}', braceStart);
        if (braceEnd > braceStart) {
          return verText.slice(braceStart, braceEnd + 1);
        }
      }
    } catch (e) {
      emitLog(options, 'debug', `Failed to decode version string: ${(e as Error).message}`);
    }
  }

  return 'unknown';
}

function findZipOffset(data: Uint8Array): number {
  const offset = findSequence(data, ZIP_LOCAL_HEADER);
  if (offset < 0) {
    throw new Error('No embedded ZIP archive found in the file');
  }
  return offset;
}

// meta/meta.dat uses the same low-level TLV framing as model.dat (2-byte
// tag + 4-byte little-endian length + payload), but as one flat
// (non-recursive) record list wrapped in a single outer record. Confirmed
// against a real fixture: the outer wrapper is tag 0x6400; among its
// direct children, tag 0x6D00 carries the model's unit-system string as
// plain text ("Millimeter" in the fixture) - siblings carry the SketchUp
// version, save path, and thumbnail references, none of which any parser
// surfaces either.
const META_WRAPPER_TAG = [0x64, 0x00];
const META_UNITS_TAG = [0x6d, 0x00];

/** Extract the model's unit-system string from meta/meta.dat's raw bytes,
 * or null if the expected tags aren't found. */
export function readMetaUnits(metaBytes: Uint8Array): string | null {
  const view = new DataView(metaBytes.buffer, metaBytes.byteOffset, metaBytes.byteLength);
  let pos = 0;
  while (pos + 6 <= metaBytes.length) {
    const tag = [metaBytes[pos], metaBytes[pos + 1]];
    const size = view.getUint32(pos + 2, true);
    if (pos + 6 + size > metaBytes.length) break;
    if (tag[0] === META_WRAPPER_TAG[0] && tag[1] === META_WRAPPER_TAG[1]) {
      return readMetaUnits(metaBytes.subarray(pos + 6, pos + 6 + size));
    }
    if (tag[0] === META_UNITS_TAG[0] && tag[1] === META_UNITS_TAG[1]) {
      return new TextDecoder('utf-8').decode(metaBytes.subarray(pos + 6, pos + 6 + size));
    }
    pos += 6 + size;
  }
  return null;
}

// A ZIP entry's declared uncompressed size (UnzipFileInfo.originalSize) is
// untrusted central-directory metadata - it can be set independently of
// what the compressed stream actually decompresses to, and even when
// genuine, DEFLATE can expand highly compressible data by three orders of
// magnitude. fflate's unzipSync decompresses (and so allocates) up to that
// declared size with no ceiling of its own. Real production model.dat
// entries are observed at ~10x compression, so both limits below leave
// generous headroom for legitimate files while rejecting the kind of
// declared-size lie or extreme ratio a genuine file would never need.
const MAX_UNCOMPRESSED_ENTRY_BYTES = 16 * 1024 * 1024 * 1024; // 16 GB
const MAX_COMPRESSION_RATIO = 1000;
const RATIO_CHECK_THRESHOLD_BYTES = 1024 * 1024; // 1 MB

/** Reject a ZIP entry whose declared uncompressed size is implausible,
 * before unzipSync decompresses (and so allocates for) it. */
function validateEntrySize(file: UnzipFileInfo): void {
  const declared = file.originalSize;
  if (declared <= 0) return;

  if (declared > MAX_UNCOMPRESSED_ENTRY_BYTES) {
    throw new Error(
      `ZIP entry '${file.name}' declares ${declared} bytes uncompressed, exceeding the ${MAX_UNCOMPRESSED_ENTRY_BYTES}-byte safety ceiling`
    );
  }

  if (declared >= RATIO_CHECK_THRESHOLD_BYTES) {
    const compressed = file.size;
    if (compressed <= 0 || declared / compressed > MAX_COMPRESSION_RATIO) {
      throw new Error(
        `ZIP entry '${file.name}' declares an implausible compression ratio (${declared} bytes from ${compressed} bytes compressed) - likely a decompression bomb`
      );
    }
  }
}

export function extractSkpContents(data: Uint8Array, options?: ParseOptions): SkpContents {
  // Allow both VFF-wrapped and bare ZIP (some exporters omit the header)
  if (!validateHeader(data)) {
    const zipInHeader = findSequence(data.subarray(0, Math.min(64, data.length)), ZIP_LOCAL_HEADER) >= 0;
    if (!zipInHeader) {
      throw new Error('Not a valid SketchUp (.skp) file');
    }
  }

  const version = readVersion(data, options);
  const zipOffset = findZipOffset(data);
  const zipBytes = data.subarray(zipOffset);

  // Only decompress entries we actually consume - a ZIP full of unrelated
  // large assets (the file's own thumbnails, etc.) would otherwise get
  // fully inflated into memory alongside model.dat for nothing.
  const wanted = (file: UnzipFileInfo): boolean => {
    const lower = file.name.toLowerCase();
    const isWanted = (
      lower === 'model.dat' ||
      lower.endsWith('/model.dat') ||
      lower === 'meta/meta.dat' ||
      lower.endsWith('.xml') ||
      lower.endsWith('.png') ||
      lower.endsWith('.jpg') ||
      lower.endsWith('.jpeg') ||
      lower.includes('material')
    );
    if (isWanted) validateEntrySize(file);
    return isWanted;
  };

  let unzipped: Record<string, Uint8Array>;
  try {
    unzipped = unzipSync(zipBytes, { filter: wanted });
  } catch (e) {
    // The Error constructor's `cause` option is an ES2022 addition not covered
    // by this package's ES2020 lib target; set it manually instead - it's a
    // real runtime feature regardless (Node 16.9+ and all current browsers).
    const wrapped = new Error('Failed to decompress ZIP archive: ' + (e as Error).message);
    (wrapped as Error & { cause?: unknown }).cause = e;
    throw wrapped;
  }

  let modelData: Uint8Array | null = null;
  let metaData: Uint8Array | null = null;
  const materialFiles: Record<string, Uint8Array> = {};

  for (const rawEntry of Object.keys(unzipped)) {
    const entry = decodeEntryName(rawEntry);
    const lower = entry.toLowerCase();
    if (lower === 'model.dat' || lower.endsWith('/model.dat')) {
      modelData = unzipped[rawEntry];
    } else if (lower === 'meta/meta.dat') {
      metaData = unzipped[rawEntry];
    } else if (
      lower.endsWith('.xml') ||
      lower.endsWith('.png') ||
      lower.endsWith('.jpg') ||
      lower.endsWith('.jpeg') ||
      lower.includes('material')
    ) {
      materialFiles[entry] = unzipped[rawEntry];
    }
  }

  if (!modelData) {
    throw new Error('ZIP archive found but does not contain a model.dat entry');
  }

  return {
    version,
    modelData,
    materialFiles,
    metaData,
  };
}
