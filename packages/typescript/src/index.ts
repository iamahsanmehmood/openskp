/**
 * OpenSKP — TypeScript Implementation
 *
 * Open-source SketchUp (.skp) binary file parser.
 * Works in both browser and Node.js environments.
 *
 * @packageDocumentation
 */

// 🚧 TypeScript implementation is under active development.
// The Python package is fully functional — see packages/python/

export interface SkpModel {
  version: string;
  definitions: Map<number, Definition>;
  layers: Layer[];
  materials: Material[];
  sceneHierarchy: InstanceNode;
  meshIndex: Record<string, MeshMetadata>;
}

export interface Definition {
  id: number;
  guid: string;
  name: string;
  vertices: Vertex[];
  edges: Edge[];
  faces: Face[];
}

export interface Vertex {
  id: number;
  x: number;
  y: number;
  z: number;
}

export interface Edge {
  id: number;
  v1Id: number;
  v2Id: number;
}

export interface Face {
  id: number;
  loops: CoEdge[][];
  normal: [number, number, number];
}

export interface CoEdge {
  edgeId: number;
  orientation: number;
}

export interface Layer {
  name: string;
  color: { r: number; g: number; b: number };
}

export interface Material {
  name: string;
  color: { r: number; g: number; b: number };
  transparency: number;
}

export interface InstanceNode {
  name: string;
  definitionName: string;
  layer: string;
  positionMm: [number, number, number];
  properties: Record<string, string>;
  children: InstanceNode[];
}

export interface MeshMetadata {
  name: string;
  definitionName: string;
  layer: string;
  positionMm: [number, number, number];
  properties: Record<string, string>;
  path: string;
}

/**
 * Parse a SketchUp (.skp) file from an ArrayBuffer.
 *
 * @param buffer - The raw file contents as an ArrayBuffer
 * @returns Parsed SkpModel with full geometry and metadata
 *
 * @example
 * ```typescript
 * import { parseSkp } from 'openskp';
 *
 * const buffer = await fetch('model.skp').then(r => r.arrayBuffer());
 * const model = parseSkp(buffer);
 *
 * console.log(model.layers);
 * console.log(model.definitions);
 * ```
 */
export function parseSkp(_buffer: ArrayBuffer): SkpModel {
  // 🚧 Implementation coming soon — see packages/python/ for the working reference
  throw new Error(
    'TypeScript implementation is under development. ' +
    'Use the Python package (pip install openskp) in the meantime.'
  );
}

/**
 * Export a parsed SkpModel to GLB (binary glTF 2.0) format.
 *
 * @param model - Parsed SkpModel
 * @returns GLB file as Uint8Array
 */
export function toGLB(_model: SkpModel): Uint8Array {
  throw new Error('TypeScript GLB export is under development.');
}

/**
 * Export a parsed SkpModel to a metadata JSON object.
 *
 * @param model - Parsed SkpModel
 * @returns Metadata object
 */
export function toJSON(_model: SkpModel): Record<string, unknown> {
  throw new Error('TypeScript JSON export is under development.');
}
