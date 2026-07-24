/**
 * Structured parse errors.
 *
 * {@link SkpParseError} carries *where* a parse failed - which stage, which
 * top-level record (and how many total), which TLV tag, which definition -
 * so a stuck or failed model in a production pipeline can be traced back to
 * an exact location instead of a bare stack trace.
 *
 * The original error is always preserved as `.cause`, so inspecting the
 * failure never loses information, it only adds context.
 */

export type SkpParseStage =
  | 'header'
  | 'zip_extract'
  | 'materials'
  | 'tlv_walk'
  | 'legacy_walk'
  | 'legacy_defs'
  | 'build_scene';

export interface SkpParseErrorContext {
  stage?: SkpParseStage;
  recordIndex?: number;
  totalRecords?: number;
  tag?: string;
  offset?: number;
  definitionId?: number | string;
  cause?: unknown;
}

export class SkpParseError extends Error {
  readonly stage?: SkpParseStage;
  readonly recordIndex?: number;
  readonly totalRecords?: number;
  readonly tag?: string;
  readonly offset?: number;
  readonly definitionId?: number | string;
  /** The original error that triggered this one, if any. */
  readonly cause?: unknown;

  constructor(message: string, context: SkpParseErrorContext = {}) {
    const parts = [message];
    if (context.stage !== undefined) parts.push(`stage=${context.stage}`);
    if (context.recordIndex !== undefined && context.totalRecords !== undefined) {
      parts.push(`record=${context.recordIndex}/${context.totalRecords}`);
    }
    if (context.tag !== undefined) parts.push(`tag=${context.tag}`);
    if (context.offset !== undefined) parts.push(`offset=0x${context.offset.toString(16).toUpperCase()}`);
    if (context.definitionId !== undefined) parts.push(`definitionId=${context.definitionId}`);
    super(parts.join(' | '));
    this.name = 'SkpParseError';
    this.stage = context.stage;
    this.recordIndex = context.recordIndex;
    this.totalRecords = context.totalRecords;
    this.tag = context.tag;
    this.offset = context.offset;
    this.definitionId = context.definitionId;
    this.cause = context.cause;
    // Restore the prototype chain (needed when targeting down-level JS).
    Object.setPrototypeOf(this, SkpParseError.prototype);
  }
}
