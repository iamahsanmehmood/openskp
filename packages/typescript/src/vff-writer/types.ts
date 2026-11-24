/** Attribute-value types and option types shared by the VFF writer. */

export type Point3 = [number, number, number];

/** `SketchUp::AttributeDictionary` value wrappers matching Python's
 * `Timestamp`, `Length`, `Point3d` and `Vector3d` classes. */
export class VffTimestamp {
  constructor(public readonly value: number) {}
}

export class VffLength {
  constructor(public readonly value: number) {}
}

export class VffPoint3d {
  constructor(public readonly x: number, public readonly y: number, public readonly z: number) {}
}

export class VffVector3d {
  constructor(public readonly x: number, public readonly y: number, public readonly z: number) {}
}

export type VffAttributeValue =
  | string
  | number
  | boolean
  | null
  | VffTimestamp
  | VffLength
  | VffPoint3d
  | VffVector3d
  | VffAttributeValue[];

export type VffAttributeDict = Record<string, VffAttributeValue>;
export type VffAttributeDicts = Array<[string, VffAttributeDict]>;

export interface VffWriterOptions {
  /** Seed for the portable PRNG that generates definition/instance GUIDs
   * (and the model GUID when `modelGuid` is not supplied). Omit for random
   * UUIDs, matching Python's default. */
  guidSeed?: number;
  /** 32 hex characters (or a 16-byte array) for meta/meta.dat's model GUID. */
  modelGuid?: string | Uint8Array;
  /** Fixed save time (unix seconds) for meta/meta.dat. */
  saveTime?: number;
  /** Fixed timestamp for new component definitions. */
  definitionTime?: number;
  /** Use the writer's portable sin/cos (matching Python's portable mode)
   * instead of the platform libm, so circle/arc vertices are bit-identical
   * across platforms. */
  portableTrig?: boolean;
}

export interface VffTemplateSource {
  bytes?: Uint8Array;
}

export function resolveAttributeDicts(
  attributes: VffAttributeDict | undefined,
  attributeDictName: string,
  attributeDicts: VffAttributeDicts = [],
): VffAttributeDicts {
  const out: VffAttributeDicts = [];
  if (attributes && Object.keys(attributes).length > 0) out.push([attributeDictName, attributes]);
  for (const item of attributeDicts) out.push(item);
  return out;
}

export function identityMatrix(): number[] {
  return [1, 0, 0, 0, 1, 0, 0, 0, 1];
}
