/**
 * Optional progress/log hooks for long-running parses.
 *
 * openskp never logs or prints on its own - callers that want visibility
 * into a parse (progress through a large file, which stage is running, when
 * it completes) pass an {@link ParseOptions} with `onProgress`/`onLog`
 * callbacks that plug into whatever logging/monitoring the host application
 * already uses. Silent by default, matching {@link SkpParseError}'s "always
 * add context, never hide it" philosophy from the other side: this is how a
 * caller finds out *how far* a parse got before it got stuck, not just
 * *where*.
 */

export type LogLevel = 'debug' | 'info';

export interface ProgressInfo {
  /** Which pipeline stage is reporting progress, e.g. "tlv_walk",
   * "legacy_defs", "build_scene". */
  stage: string;
  /** Units completed so far (records, definitions, or instances,
   * depending on `stage`). */
  current: number;
  /** Total units expected for this stage. */
  total: number;
}

export interface ParseOptions {
  /** Called periodically (every {@link PROGRESS_INTERVAL} units) during a
   * long walk, so a caller can report "N of M processed" without any extra
   * pass over the data. */
  onProgress?: (info: ProgressInfo) => void;
  /** Called for start/stage/completion messages. `level` is "debug" for
   * fine-grained detail, "info" for start/complete summaries - mirrors the
   * Python port's `logging.DEBUG`/`logging.INFO` split. */
  onLog?: (level: LogLevel, message: string) => void;
}

/** How often (in records/definitions/instances) to call `onProgress` during
 * a long walk - coarse enough that it costs nothing on a 300k-definition
 * file. Mirrors the Python port's `_PROGRESS_INTERVAL`. */
export const PROGRESS_INTERVAL = 500;

export function emitLog(options: ParseOptions | undefined, level: LogLevel, message: string): void {
  options?.onLog?.(level, message);
}

export function emitProgress(options: ParseOptions | undefined, stage: string, current: number, total: number): void {
  options?.onProgress?.({ stage, current, total });
}
