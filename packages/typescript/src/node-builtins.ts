/**
 * Node.js's own modules, for the functions that read or write files.
 *
 * A plain `require` works in the CommonJS build only: in the ES module build
 * (`import { SkpFile } from 'openskp'`) it throws. A static import would break
 * browser bundles. `process.getBuiltinModule` (Node.js 20.16 and 22.3) does
 * neither; `require` stays for CommonJS callers on an older Node.js.
 */

declare const process: { getBuiltinModule?: (id: string) => any };
declare const require: (id: string) => any;

/** @internal */
export function nodeFs(): any {
  return process.getBuiltinModule ? process.getBuiltinModule('fs') : require('fs');
}

/** @internal */
export function nodePath(): any {
  return process.getBuiltinModule ? process.getBuiltinModule('path') : require('path');
}
