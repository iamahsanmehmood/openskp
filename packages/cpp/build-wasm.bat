@echo off
setlocal enabledelayedexpansion

echo [OpenSKP] Building WebAssembly (WASM) module with Emscripten...

if not exist build-wasm mkdir build-wasm
cd build-wasm

call emcmake cmake .. -DCMAKE_BUILD_TYPE=Release -DOPENSKP_BUILD_TESTS=OFF -DOPENSKP_BUILD_EXAMPLES=OFF -DOPENSKP_BUILD_WASM=ON
if %ERRORLEVEL% neq 0 (
    echo [OpenSKP] CMake configuration failed. Ensure emsdk is activated (e.g. emsdk_env.bat).
    exit /b %ERRORLEVEL%
)

call emmake cmake --build . --config Release
if %ERRORLEVEL% neq 0 (
    echo [OpenSKP] Build failed.
    exit /b %ERRORLEVEL%
)

echo [OpenSKP] WASM build successful! Output files in build-wasm/
cd ..
