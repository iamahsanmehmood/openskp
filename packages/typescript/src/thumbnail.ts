import { extractSkpContents } from './vff';
import { isLegacy } from './legacy';
import { SkpParseError } from './errors';
import { ParseOptions, emitLog } from './observability';
import { sniffImageMime } from './model';

/**
 * The preview image SketchUp saves inside the file itself.
 *
 * Every model saved by SketchUp carries a rendered thumbnail, so a catalogue
 * or asset browser can show what a `.skp` contains without parsing its
 * geometry, spinning up a renderer, or generating a preview offline.
 */
export interface SkpThumbnail {
  /** The image file's raw bytes, exactly as stored in the `.skp`. */
  data: Uint8Array;
  /** Sniffed from the bytes rather than the entry name. */
  mimeType: 'image/png' | 'image/jpeg';
  width: number;
  height: number;
  /**
   * Which stored image this came from.
   *
   * `model` is the model on a clean background - the one to show in a
   * catalogue. `preview` is the same view WITH SketchUp's red/green/blue
   * axis lines drawn in, which reads as clutter on a product card. `model`
   * is preferred and `preview` is only a fallback for a file that somehow
   * lacks it.
   */
  source: 'model' | 'preview';
}

/** Entry names SketchUp writes, in preference order. */
const THUMBNAIL_ENTRIES: { name: string; source: SkpThumbnail['source'] }[] = [
  { name: 'meta/model_thumbnail.png', source: 'model' },
  { name: 'meta/preview_thumbnail.png', source: 'preview' },
];

/**
 * Read a PNG's IHDR dimensions. Returns null if the bytes are not a PNG
 * whose header is intact - the caller then reports no usable thumbnail
 * rather than inventing a size.
 */
function readPngSize(data: Uint8Array): { width: number; height: number } | null {
  // 8-byte signature + 4-byte length + "IHDR" + 8 bytes of width/height
  if (data.length < 24) return null;
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const width = view.getUint32(16);
  const height = view.getUint32(20);
  if (width === 0 || height === 0) return null;
  return { width, height };
}

/**
 * Extract the preview image SketchUp stored in a `.skp`.
 *
 * Cheap by design: this reads the container's own metadata entries and
 * never touches geometry, so listing a directory of models costs nothing
 * like {@link parseSkp} or {@link buildScene}.
 *
 * Returns `null` rather than throwing when the file simply has no usable
 * thumbnail, which includes two real cases:
 *
 * - **Legacy (pre-2021 MFC) files.** Those carry embedded PNGs too, but the
 *   container has no entry names, so a thumbnail cannot be told apart from
 *   a material's texture image without guessing. Returning `null` is
 *   honest; handing back a texture and calling it a preview would not be.
 * - **Modern files with no thumbnail entry**, e.g. one written by a tool
 *   other than SketchUp.
 *
 * A malformed container still throws {@link SkpParseError}, matching the
 * rest of the library.
 *
 * @param buffer - The raw file contents
 * @param options - Optional progress/log callbacks
 */
export function extractThumbnail(
  buffer: ArrayBuffer,
  options?: ParseOptions
): SkpThumbnail | null {
  const data = new Uint8Array(buffer);

  if (!(data.length >= 4 && data[0] === 0xff && data[1] === 0xfe && data[2] === 0xff && data[3] === 0x0e)) {
    throw new SkpParseError('Not a valid SketchUp file (bad header magic)', { stage: 'header' });
  }

  if (isLegacy(data)) {
    // See the doc comment: the legacy container stores images without
    // names, so there is no reliable way to identify the thumbnail.
    emitLog(options, 'debug', 'Legacy container: no named thumbnail entry available');
    return null;
  }

  let contents;
  try {
    contents = extractSkpContents(data, options);
  } catch (e) {
    throw new SkpParseError(`Failed to extract SKP contents: ${(e as Error).message}`, {
      stage: 'zip_extract',
      cause: e,
    });
  }

  for (const { name, source } of THUMBNAIL_ENTRIES) {
    const bytes = contents.materialFiles[name];
    if (!bytes || bytes.length === 0) continue;

    const mimeType = sniffImageMime(bytes);
    if (mimeType === null) {
      emitLog(options, 'debug', `${name} is not a PNG or JPEG; skipping`);
      continue;
    }

    const size = mimeType === 'image/png' ? readPngSize(bytes) : null;
    if (!size) {
      emitLog(options, 'debug', `${name} has no readable image header; skipping`);
      continue;
    }

    return { data: bytes, mimeType, width: size.width, height: size.height, source };
  }

  emitLog(options, 'debug', 'No thumbnail entry found in container');
  return null;
}
