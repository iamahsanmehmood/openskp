#!/usr/bin/env sh
# Builds the viewer: compiles openskp-viewer to wasm32 and embeds it (base64)
# into a single self-contained dist/openskp-viewer.html.
set -e
cd "$(dirname "$0")"

cargo build --release --target wasm32-unknown-unknown

mkdir -p dist
b64=$(base64 < target/wasm32-unknown-unknown/release/openskp_viewer.wasm | tr -d '\n')
awk -v b64="$b64" '{ i = index($0, "__WASM_BASE64__"); if (i) $0 = substr($0, 1, i - 1) b64 substr($0, i + 15); print }' \
  web/index.html > dist/openskp-viewer.html
echo "dist/openskp-viewer.html ($(($(wc -c < dist/openskp-viewer.html) / 1024)) KB)"
