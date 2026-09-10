#!/usr/bin/env bash
set -e

echo "[OpenSKP] Building WebAssembly (WASM) module with Emscripten..."

mkdir -p build-wasm
cd build-wasm

emcmake cmake .. \
  -DCMAKE_BUILD_TYPE=Release \
  -DOPENSKP_BUILD_TESTS=OFF \
  -DOPENSKP_BUILD_EXAMPLES=OFF \
  -DOPENSKP_BUILD_WASM=ON

emmake cmake --build . --config Release

echo "[OpenSKP] WASM build successful! Output files in build-wasm/"
