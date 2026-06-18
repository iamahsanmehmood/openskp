import { unzipSync } from 'fflate';

export interface SkpContents {
  version: string;
  modelData: Uint8Array;
  materialFiles: Record<string, Uint8Array>;
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

export function validateHeader(data: Uint8Array): boolean {
  if (data.length < 4) return false;
  return (
    data[0] === VFF_MAGIC[0] &&
    data[1] === VFF_MAGIC[1] &&
    data[2] === VFF_MAGIC[2] &&
    data[3] === VFF_MAGIC[3]
  );
}

export function readVersion(data: Uint8Array): string {
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
      // Ignore decoder errors
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

export function extractSkpContents(data: Uint8Array): SkpContents {
  // Allow both VFF-wrapped and bare ZIP (some exporters omit the header)
  if (!validateHeader(data)) {
    const zipInHeader = findSequence(data.subarray(0, Math.min(64, data.length)), ZIP_LOCAL_HEADER) >= 0;
    if (!zipInHeader) {
      throw new Error('Not a valid SketchUp (.skp) file');
    }
  }

  const version = readVersion(data);
  const zipOffset = findZipOffset(data);
  const zipBytes = data.subarray(zipOffset);

  let unzipped: Record<string, Uint8Array>;
  try {
    unzipped = unzipSync(zipBytes);
  } catch (e) {
    throw new Error('Failed to decompress ZIP archive: ' + (e as Error).message);
  }

  let modelData: Uint8Array | null = null;
  const materialFiles: Record<string, Uint8Array> = {};

  for (const entry of Object.keys(unzipped)) {
    const lower = entry.toLowerCase();
    if (lower === 'model.dat' || lower.endsWith('/model.dat')) {
      modelData = unzipped[entry];
    } else if (
      lower.endsWith('.xml') ||
      lower.endsWith('.png') ||
      lower.endsWith('.jpg') ||
      lower.endsWith('.jpeg') ||
      lower.includes('material')
    ) {
      materialFiles[entry] = unzipped[entry];
    }
  }

  if (!modelData) {
    throw new Error('ZIP archive found but does not contain a model.dat entry');
  }

  return {
    version,
    modelData,
    materialFiles,
  };
}
