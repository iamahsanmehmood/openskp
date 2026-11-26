//! Colorized texture baking, built-in PNG decode/encode (on top of in-tree
//! `inflate` / `deflate` / `zip`), EXIF `APP1` stripping, and header-level
//! pixel-count enforcement for `to_glb` and `to_instanced_glb`.

use std::borrow::Cow;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

use crate::deflate::zlib_compress;
use crate::face_groups::describe_image_format;
use crate::inflate::zlib_decompress_capped;
use crate::observability::{LogFn, LogLevel};
use crate::scene::{GltfMaterial, SceneTexture};
use crate::zip::crc32;

/// Maximum pixels per `f32` colorize row-block (`262,144` pixels).
pub const MAX_COLORIZE_BLOCK_PIXELS: usize = 262_144;

/// Maximum decoded image size (`178,956,970` pixels, matching Python/Pillow,
/// C++, TypeScript, .NET, and Dart).
pub const MAX_DECODE_TEXTURE_PIXELS: u64 = 178_956_970;

/// Decoded 8-bit RGBA image (`width * height * 4` bytes, row-major).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DecodedRgbaImage {
    pub width: u32,
    pub height: u32,
    pub rgba: Vec<u8>,
}

/// Synchronous codec hook used to decode and re-encode colorized JPEG textures
/// during GLB export (PNG textures are decoded and encoded natively in-tree).
pub trait ImageCodec: Send + Sync {
    /// Decodes compressed image bytes (such as `"image/jpeg"`) into row-major
    /// 8-bit RGBA pixels (`width * height * 4` bytes).
    fn decode(&self, data: &[u8], mime_type: &str) -> Result<DecodedRgbaImage, String>;

    /// Encodes row-major 8-bit RGBA pixels (`width * height * 4` bytes) into
    /// compressed image bytes (such as `"image/jpeg"` at `quality`).
    fn encode(
        &self,
        rgba: &[u8],
        width: u32,
        height: u32,
        mime_type: &str,
        quality: u8,
    ) -> Result<Vec<u8>, String>;
}

impl<D, E> ImageCodec for (D, E)
where
    D: Fn(&[u8], &str) -> Result<DecodedRgbaImage, String> + Send + Sync,
    E: Fn(&[u8], u32, u32, &str, u8) -> Result<Vec<u8>, String> + Send + Sync,
{
    fn decode(&self, data: &[u8], mime_type: &str) -> Result<DecodedRgbaImage, String> {
        (self.0)(data, mime_type)
    }

    fn encode(
        &self,
        rgba: &[u8],
        width: u32,
        height: u32,
        mime_type: &str,
        quality: u8,
    ) -> Result<Vec<u8>, String> {
        (self.1)(rgba, width, height, mime_type, quality)
    }
}

/// Internal counters exposed for white-box caching tests.
#[derive(Debug, Default, Clone, Copy, PartialEq, Eq)]
pub struct GltfTextureBakeStats {
    pub decode_calls: usize,
    pub colorize_calls: usize,
}

const PNG_SIG: [u8; 8] = [137, 80, 78, 71, 13, 10, 26, 10];

const ADAM7_PASSES: [(usize, usize, usize, usize); 7] = [
    (0, 8, 0, 8),
    (4, 8, 0, 8),
    (0, 4, 4, 8),
    (2, 4, 0, 4),
    (0, 2, 2, 4),
    (1, 2, 0, 2),
    (0, 1, 1, 2),
];

#[inline]
fn be32(data: &[u8], off: usize) -> u32 {
    u32::from_be_bytes([data[off], data[off + 1], data[off + 2], data[off + 3]])
}

#[inline]
fn paeth_predictor(a: i32, b: i32, c: i32) -> i32 {
    let p = a + b - c;
    let pa = (p - a).abs();
    let pb = (p - b).abs();
    let pc = (p - c).abs();
    if pa <= pb && pa <= pc {
        a
    } else if pb <= pc {
        b
    } else {
        c
    }
}

/// Drops JPEG `APP1` (`0xFFE1`, Exif / XMP orientation) marker segments before
/// `SOS` (`0xFFDA`) without re-encoding. Borrows `data` unchanged when no
/// `APP1` segment is present.
pub fn strip_jpeg_exif_app1(data: &[u8]) -> Cow<'_, [u8]> {
    let n = data.len();
    if n < 4 || data[0] != 0xFF || data[1] != 0xD8 {
        return Cow::Borrowed(data);
    }

    let mut drop_ranges: Vec<(usize, usize)> = Vec::new();
    let mut pos = 2usize;
    while pos < n {
        if data[pos] != 0xFF {
            break;
        }
        let marker_start = pos;
        while pos < n && data[pos] == 0xFF {
            pos += 1;
        }
        if pos >= n {
            break;
        }
        let marker = data[pos];
        pos += 1;
        if marker == 0x00 || marker == 0xD9 {
            break;
        }
        if marker == 0x01 || (0xD0..=0xD8).contains(&marker) {
            continue;
        }
        if pos + 2 > n {
            break;
        }
        let seg_len = (usize::from(data[pos]) << 8) | usize::from(data[pos + 1]);
        if seg_len < 2 || pos + seg_len > n {
            break;
        }
        let seg_end = pos + seg_len;
        if marker == 0xDA {
            break; // Start of Scan
        }
        if marker == 0xE1 {
            drop_ranges.push((marker_start, seg_end));
        }
        pos = seg_end;
    }

    if drop_ranges.is_empty() {
        return Cow::Borrowed(data);
    }

    let dropped: usize = drop_ranges.iter().map(|(s, e)| e - s).sum();
    let mut out = Vec::with_capacity(n - dropped);
    let mut cursor = 0usize;
    for (start, end) in drop_ranges {
        if start > cursor {
            out.extend_from_slice(&data[cursor..start]);
        }
        cursor = end;
    }
    if cursor < n {
        out.extend_from_slice(&data[cursor..]);
    }
    Cow::Owned(out)
}

/// Reads JPEG frame `(width, height)` from its `SOF` marker before `SOS`.
pub fn read_jpeg_dimensions(data: &[u8]) -> Option<(u32, u32)> {
    let n = data.len();
    if n < 4 || data[0] != 0xFF || data[1] != 0xD8 {
        return None;
    }

    let mut pos = 2usize;
    while pos < n {
        if data[pos] != 0xFF {
            break;
        }
        while pos < n && data[pos] == 0xFF {
            pos += 1;
        }
        if pos >= n {
            break;
        }
        let marker = data[pos];
        pos += 1;
        if marker == 0x00 || marker == 0xD9 || marker == 0xDA {
            break;
        }
        if marker == 0x01 || (0xD0..=0xD8).contains(&marker) {
            continue;
        }
        if pos + 2 > n {
            break;
        }
        let seg_len = (usize::from(data[pos]) << 8) | usize::from(data[pos + 1]);
        if seg_len < 2 || pos + seg_len > n {
            break;
        }
        if (0xC0..=0xCF).contains(&marker) && marker != 0xC4 && marker != 0xC8 && marker != 0xCC {
            if seg_len < 7 {
                return None;
            }
            let height = (u32::from(data[pos + 3]) << 8) | u32::from(data[pos + 4]);
            let width = (u32::from(data[pos + 5]) << 8) | u32::from(data[pos + 6]);
            return Some((width, height));
        }
        pos += seg_len;
    }
    None
}

/// Reads PNG `IHDR` `(width, height)` before any `IDAT` inflation.
pub fn read_png_dimensions(data: &[u8]) -> Option<(u32, u32)> {
    if data.len() < 33 || data[..8] != PNG_SIG {
        return None;
    }
    let chunk_len = be32(data, 8);
    let chunk_type = &data[12..16];
    if chunk_type != b"IHDR" || chunk_len != 13 {
        return None;
    }
    Some((be32(data, 16), be32(data, 20)))
}

fn check_pixel_limit(width: u32, height: u32) -> Result<(usize, usize), String> {
    if width == 0 || height == 0 {
        return Err(format!("invalid image dimensions {width}x{height}"));
    }
    let total_pixels = u64::from(width) * u64::from(height);
    if total_pixels > MAX_DECODE_TEXTURE_PIXELS {
        return Err(format!(
            "image dimensions {width}x{height} ({total_pixels} pixels) exceed maximum of {MAX_DECODE_TEXTURE_PIXELS} pixels"
        ));
    }
    let w = usize::try_from(width).map_err(|_| "image width overflows usize".to_owned())?;
    let h = usize::try_from(height).map_err(|_| "image height overflows usize".to_owned())?;
    Ok((w, h))
}

/// Decodes PNG bytes into 8-bit RGBA pixels using in-tree `zlib_decompress_capped`.
/// Enforces [`MAX_DECODE_TEXTURE_PIXELS`] on `IHDR` before decompressing `IDAT`.
pub fn decode_png_rgba(data: &[u8]) -> Result<DecodedRgbaImage, String> {
    if data.len() < 33 {
        return Err("truncated PNG header".into());
    }
    if data[..8] != PNG_SIG {
        return Err("invalid PNG signature".into());
    }

    let mut width_u32 = 0u32;
    let mut height_u32 = 0u32;
    let mut width = 0usize;
    let mut height = 0usize;
    let mut bit_depth = 0usize;
    let mut color_type = 0u8;
    let mut interlace = 0u8;
    let mut channels = 0usize;
    let mut seen_ihdr = false;
    let mut palette: Option<&[u8]> = None;
    let mut trns: Option<&[u8]> = None;
    let mut idat_parts: Vec<&[u8]> = Vec::new();
    let mut total_idat_len = 0usize;

    let mut pos = 8usize;
    while pos + 8 <= data.len() {
        let len_u32 = be32(data, pos);
        let chunk_type = &data[pos + 4..pos + 8];
        let len = usize::try_from(len_u32).map_err(|_| "PNG chunk length overflows usize")?;
        if len > data.len() - pos - 12 {
            return Err("truncated PNG chunk".into());
        }
        let payload_start = pos + 8;
        let payload_end = payload_start + len;
        let payload = &data[payload_start..payload_end];

        if !seen_ihdr {
            if chunk_type != b"IHDR" || len != 13 {
                return Err("missing or invalid PNG IHDR chunk".into());
            }
            width_u32 = be32(payload, 0);
            height_u32 = be32(payload, 4);
            (width, height) = check_pixel_limit(width_u32, height_u32)?;

            bit_depth = usize::from(payload[8]);
            color_type = payload[9];
            let comp_method = payload[10];
            let filter_method = payload[11];
            interlace = payload[12];
            if comp_method != 0 || filter_method != 0 || (interlace != 0 && interlace != 1) {
                return Err("unsupported PNG compression/filter/interlace mode".into());
            }

            channels = match color_type {
                0 => {
                    if !matches!(bit_depth, 1 | 2 | 4 | 8 | 16) {
                        return Err(format!("unsupported greyscale PNG bit depth {bit_depth}"));
                    }
                    1
                }
                2 => {
                    if !matches!(bit_depth, 8 | 16) {
                        return Err(format!("unsupported RGB PNG bit depth {bit_depth}"));
                    }
                    3
                }
                3 => {
                    if !matches!(bit_depth, 1 | 2 | 4 | 8) {
                        return Err(format!("unsupported indexed PNG bit depth {bit_depth}"));
                    }
                    1
                }
                4 => {
                    if !matches!(bit_depth, 8 | 16) {
                        return Err(format!("unsupported grey+alpha PNG bit depth {bit_depth}"));
                    }
                    2
                }
                6 => {
                    if !matches!(bit_depth, 8 | 16) {
                        return Err(format!("unsupported RGBA PNG bit depth {bit_depth}"));
                    }
                    4
                }
                _ => return Err(format!("unsupported PNG color type {color_type}")),
            };
            seen_ihdr = true;
        } else if chunk_type == b"PLTE" {
            if !(3..=768).contains(&len) || !len.is_multiple_of(3) {
                return Err("invalid PNG PLTE chunk".into());
            }
            palette = Some(payload);
        } else if chunk_type == b"tRNS" {
            trns = Some(payload);
        } else if chunk_type == b"IDAT" {
            if len > 0 {
                total_idat_len = total_idat_len
                    .checked_add(len)
                    .ok_or_else(|| "PNG IDAT size overflow".to_owned())?;
                idat_parts.push(payload);
            }
        } else if chunk_type == b"IEND" {
            break;
        }

        pos = payload_end + 4;
    }

    if !seen_ihdr || total_idat_len == 0 {
        return Err("PNG contains no IDAT image data".into());
    }
    if color_type == 3 && palette.is_none() {
        return Err("indexed PNG missing PLTE chunk".into());
    }

    let mut pass_specs: Vec<(usize, usize, usize, usize, usize, usize)> = Vec::with_capacity(7);
    let mut expected_raw_bytes = 0usize;
    if interlace == 0 {
        let row_bytes = width
            .checked_mul(channels * bit_depth)
            .ok_or_else(|| "PNG row size overflow".to_owned())?
            .div_ceil(8);
        expected_raw_bytes = height
            .checked_mul(1 + row_bytes)
            .ok_or_else(|| "PNG scanline buffer overflow".to_owned())?;
        pass_specs.push((0, 1, 0, 1, width, height));
    } else {
        for (x0, dx, y0, dy) in ADAM7_PASSES {
            let pw = if width > x0 {
                (width - x0).div_ceil(dx)
            } else {
                0
            };
            let ph = if height > y0 {
                (height - y0).div_ceil(dy)
            } else {
                0
            };
            if pw > 0 && ph > 0 {
                let row_bytes = (pw * channels * bit_depth).div_ceil(8);
                expected_raw_bytes = expected_raw_bytes
                    .checked_add(ph * (1 + row_bytes))
                    .ok_or_else(|| "PNG Adam7 scanline buffer overflow".to_owned())?;
                pass_specs.push((x0, dx, y0, dy, pw, ph));
            }
        }
    }

    let idat: Cow<'_, [u8]> = if idat_parts.len() == 1 {
        Cow::Borrowed(idat_parts[0])
    } else {
        let mut combined = Vec::with_capacity(total_idat_len);
        for part in idat_parts {
            combined.extend_from_slice(part);
        }
        Cow::Owned(combined)
    };

    if idat.len() < 6 {
        return Err("truncated PNG zlib stream".into());
    }
    if (idat[0] >> 4) > 7 {
        return Err("invalid PNG zlib window size".into());
    }

    let inflated =
        zlib_decompress_capped(&idat, expected_raw_bytes).map_err(|e| format!("PNG IDAT: {e}"))?;
    if inflated.len() < expected_raw_bytes {
        return Err("truncated PNG IDAT deflate stream".into());
    }

    let rgba_len = width
        .checked_mul(height)
        .and_then(|px| px.checked_mul(4))
        .ok_or_else(|| "RGBA buffer size overflow".to_owned())?;
    let mut rgba = vec![0u8; rgba_len];
    let bpp = (channels * bit_depth).div_ceil(8).max(1);

    let grey_trns: Option<u16> = if color_type == 0 {
        trns.filter(|t| t.len() >= 2).map(|t| {
            let raw = u16::from_be_bytes([t[0], t[1]]);
            if bit_depth == 16 {
                raw
            } else {
                raw & ((1u16 << bit_depth) - 1)
            }
        })
    } else {
        None
    };
    let rgb_trns: Option<(u16, u16, u16)> = if color_type == 2 {
        trns.filter(|t| t.len() >= 6).map(|t| {
            let mask = if bit_depth == 16 { 0xFFFF } else { 0x00FF };
            (
                u16::from_be_bytes([t[0], t[1]]) & mask,
                u16::from_be_bytes([t[2], t[3]]) & mask,
                u16::from_be_bytes([t[4], t[5]]) & mask,
            )
        })
    } else {
        None
    };
    let pal_entries = palette.map_or(0, |p| p.len() / 3);

    let mut raw_cursor = 0usize;
    for (x0, dx, y0, dy, pw, ph) in pass_specs {
        let row_bytes = (pw * channels * bit_depth).div_ceil(8);
        let mut prev_row = vec![0u8; row_bytes];
        let mut curr_row = vec![0u8; row_bytes];

        for py in 0..ph {
            let filter_type = inflated[raw_cursor];
            raw_cursor += 1;
            let src_row = &inflated[raw_cursor..raw_cursor + row_bytes];
            raw_cursor += row_bytes;

            match filter_type {
                0 => curr_row.copy_from_slice(src_row),
                1 => {
                    for i in 0..row_bytes {
                        let left = if i >= bpp { curr_row[i - bpp] } else { 0 };
                        curr_row[i] = src_row[i].wrapping_add(left);
                    }
                }
                2 => {
                    for ((dst, &src), &up) in
                        curr_row.iter_mut().zip(src_row.iter()).zip(prev_row.iter())
                    {
                        *dst = src.wrapping_add(up);
                    }
                }
                3 => {
                    for i in 0..row_bytes {
                        let left = if i >= bpp {
                            u16::from(curr_row[i - bpp])
                        } else {
                            0
                        };
                        let avg = ((left + u16::from(prev_row[i])) >> 1) as u8;
                        curr_row[i] = src_row[i].wrapping_add(avg);
                    }
                }
                4 => {
                    for i in 0..row_bytes {
                        let left = if i >= bpp {
                            i32::from(curr_row[i - bpp])
                        } else {
                            0
                        };
                        let up_left = if i >= bpp {
                            i32::from(prev_row[i - bpp])
                        } else {
                            0
                        };
                        let pred = paeth_predictor(left, i32::from(prev_row[i]), up_left) as u8;
                        curr_row[i] = src_row[i].wrapping_add(pred);
                    }
                }
                _ => return Err(format!("invalid PNG filter type {filter_type}")),
            }

            let dst_y = y0 + py * dy;
            if interlace == 0 && bit_depth == 8 && color_type == 6 {
                let dst_off = dst_y * width * 4;
                rgba[dst_off..dst_off + row_bytes].copy_from_slice(&curr_row);
            } else if interlace == 0 && bit_depth == 8 && color_type == 2 && rgb_trns.is_none() {
                let dst_row = &mut rgba[dst_y * width * 4..(dst_y + 1) * width * 4];
                for (dst_px, src_px) in dst_row
                    .as_chunks_mut::<4>()
                    .0
                    .iter_mut()
                    .zip(curr_row.as_chunks::<3>().0)
                {
                    dst_px[0] = src_px[0];
                    dst_px[1] = src_px[1];
                    dst_px[2] = src_px[2];
                    dst_px[3] = 255;
                }
            } else {
                for px in 0..pw {
                    let dst_idx = (dst_y * width + (x0 + px * dx)) * 4;
                    match color_type {
                        6 => {
                            if bit_depth == 8 {
                                let s = px * 4;
                                rgba[dst_idx..dst_idx + 4].copy_from_slice(&curr_row[s..s + 4]);
                            } else {
                                let s = px * 8;
                                for c in 0..4 {
                                    let v16 = u32::from(u16::from_be_bytes([
                                        curr_row[s + c * 2],
                                        curr_row[s + c * 2 + 1],
                                    ]));
                                    rgba[dst_idx + c] = ((v16 * 255 + 32895) >> 16) as u8;
                                }
                            }
                        }
                        2 => {
                            if bit_depth == 8 {
                                let s = px * 3;
                                let (r, g, b) = (curr_row[s], curr_row[s + 1], curr_row[s + 2]);
                                rgba[dst_idx] = r;
                                rgba[dst_idx + 1] = g;
                                rgba[dst_idx + 2] = b;
                                rgba[dst_idx + 3] = if rgb_trns
                                    == Some((u16::from(r), u16::from(g), u16::from(b)))
                                {
                                    0
                                } else {
                                    255
                                };
                            } else {
                                let s = px * 6;
                                let r16 = u16::from_be_bytes([curr_row[s], curr_row[s + 1]]);
                                let g16 = u16::from_be_bytes([curr_row[s + 2], curr_row[s + 3]]);
                                let b16 = u16::from_be_bytes([curr_row[s + 4], curr_row[s + 5]]);
                                rgba[dst_idx] = ((u32::from(r16) * 255 + 32895) >> 16) as u8;
                                rgba[dst_idx + 1] = ((u32::from(g16) * 255 + 32895) >> 16) as u8;
                                rgba[dst_idx + 2] = ((u32::from(b16) * 255 + 32895) >> 16) as u8;
                                rgba[dst_idx + 3] = if rgb_trns == Some((r16, g16, b16)) {
                                    0
                                } else {
                                    255
                                };
                            }
                        }
                        4 => {
                            if bit_depth == 8 {
                                let g = curr_row[px * 2];
                                rgba[dst_idx] = g;
                                rgba[dst_idx + 1] = g;
                                rgba[dst_idx + 2] = g;
                                rgba[dst_idx + 3] = curr_row[px * 2 + 1];
                            } else {
                                let s = px * 4;
                                let g16 =
                                    u32::from(u16::from_be_bytes([curr_row[s], curr_row[s + 1]]));
                                let a16 = u32::from(u16::from_be_bytes([
                                    curr_row[s + 2],
                                    curr_row[s + 3],
                                ]));
                                let g = ((g16 * 255 + 32895) >> 16) as u8;
                                rgba[dst_idx] = g;
                                rgba[dst_idx + 1] = g;
                                rgba[dst_idx + 2] = g;
                                rgba[dst_idx + 3] = ((a16 * 255 + 32895) >> 16) as u8;
                            }
                        }
                        0 => {
                            if bit_depth == 8 {
                                let g = curr_row[px];
                                rgba[dst_idx] = g;
                                rgba[dst_idx + 1] = g;
                                rgba[dst_idx + 2] = g;
                                rgba[dst_idx + 3] = if grey_trns == Some(u16::from(g)) {
                                    0
                                } else {
                                    255
                                };
                            } else if bit_depth == 16 {
                                let g16 =
                                    u16::from_be_bytes([curr_row[px * 2], curr_row[px * 2 + 1]]);
                                let g = ((u32::from(g16) * 255 + 32895) >> 16) as u8;
                                rgba[dst_idx] = g;
                                rgba[dst_idx + 1] = g;
                                rgba[dst_idx + 2] = g;
                                rgba[dst_idx + 3] = if grey_trns == Some(g16) { 0 } else { 255 };
                            } else {
                                let bit_pos = px * bit_depth;
                                let byte_idx = bit_pos >> 3;
                                let shift = 8 - bit_depth - (bit_pos & 7);
                                let mask = (1u16 << bit_depth) - 1;
                                let sample = (u16::from(curr_row[byte_idx]) >> shift) & mask;
                                let g = ((u32::from(sample) * 255 + u32::from(mask >> 1))
                                    / u32::from(mask))
                                    as u8;
                                rgba[dst_idx] = g;
                                rgba[dst_idx + 1] = g;
                                rgba[dst_idx + 2] = g;
                                rgba[dst_idx + 3] = if grey_trns == Some(sample) { 0 } else { 255 };
                            }
                        }
                        _ => {
                            // Indexed (color_type == 3)
                            let idx = if bit_depth == 8 {
                                usize::from(curr_row[px])
                            } else {
                                let bit_pos = px * bit_depth;
                                let byte_idx = bit_pos >> 3;
                                let shift = 8 - bit_depth - (bit_pos & 7);
                                usize::from(curr_row[byte_idx] >> shift)
                                    & ((1usize << bit_depth) - 1)
                            };
                            if idx >= pal_entries {
                                return Err(format!(
                                    "PNG palette index {idx} out of range ({pal_entries})"
                                ));
                            }
                            let pal = palette.unwrap();
                            rgba[dst_idx] = pal[idx * 3];
                            rgba[dst_idx + 1] = pal[idx * 3 + 1];
                            rgba[dst_idx + 2] = pal[idx * 3 + 2];
                            rgba[dst_idx + 3] =
                                trns.and_then(|t| t.get(idx).copied()).unwrap_or(255);
                        }
                    }
                }
            }

            std::mem::swap(&mut prev_row, &mut curr_row);
        }
    }

    Ok(DecodedRgbaImage {
        width: width_u32,
        height: height_u32,
        rgba,
    })
}

fn append_png_chunk(out: &mut Vec<u8>, tag: &[u8; 4], payload: &[u8]) {
    out.extend_from_slice(&(payload.len() as u32).to_be_bytes());
    let crc_start = out.len();
    out.extend_from_slice(tag);
    out.extend_from_slice(payload);
    let c = crc32(&out[crc_start..]);
    out.extend_from_slice(&c.to_be_bytes());
}

/// Encodes 8-bit RGB (`channels == 3`) or RGBA (`channels == 4`) pixels to PNG
/// using adaptive scanline filtering and in-tree `zlib_compress`.
pub fn encode_png_u8(
    pixels: &[u8],
    width: u32,
    height: u32,
    channels: usize,
) -> Result<Vec<u8>, String> {
    if width == 0 || height == 0 || (channels != 3 && channels != 4) {
        return Err("encode_png_u8 requires positive dimensions and 3 or 4 channels".into());
    }
    let w = usize::try_from(width).map_err(|_| "PNG width overflows usize".to_owned())?;
    let h = usize::try_from(height).map_err(|_| "PNG height overflows usize".to_owned())?;
    let row_bytes = w
        .checked_mul(channels)
        .ok_or_else(|| "PNG row size overflow".to_owned())?;
    let needed = row_bytes
        .checked_mul(h)
        .ok_or_else(|| "PNG pixel size overflow".to_owned())?;
    if pixels.len() < needed {
        return Err("encode_png_u8 pixel buffer shorter than width * height * channels".into());
    }

    let raw_len = h
        .checked_mul(1 + row_bytes)
        .ok_or_else(|| "PNG scanline buffer overflow".to_owned())?;
    let mut raw_scanlines = vec![0u8; raw_len];
    let mut cand = vec![0u8; row_bytes];
    let bpp = channels;

    for y in 0..h {
        let row = &pixels[y * row_bytes..(y + 1) * row_bytes];
        let prev_row = if y > 0 {
            Some(&pixels[(y - 1) * row_bytes..y * row_bytes])
        } else {
            None
        };
        let dst_scan_off = y * (1 + row_bytes);
        let mut best_score = u64::MAX;

        for f in 0u8..5 {
            if y == 0 && f == 2 {
                continue;
            }
            let mut score = 0u64;
            match f {
                0 => {
                    for (dst, &v) in cand.iter_mut().zip(row.iter()) {
                        *dst = v;
                        score += u64::from(if v < 128 { v } else { 255 - v + 1 });
                    }
                }
                1 => {
                    for i in 0..row_bytes {
                        let left = if i >= bpp { row[i - bpp] } else { 0 };
                        let v = row[i].wrapping_sub(left);
                        cand[i] = v;
                        score += u64::from(if v < 128 { v } else { 255 - v + 1 });
                    }
                }
                2 => {
                    let prev = prev_row.unwrap();
                    for ((dst, &cur), &up) in cand.iter_mut().zip(row.iter()).zip(prev.iter()) {
                        let v = cur.wrapping_sub(up);
                        *dst = v;
                        score += u64::from(if v < 128 { v } else { 255 - v + 1 });
                    }
                }
                3 => {
                    for i in 0..row_bytes {
                        let left = if i >= bpp { u16::from(row[i - bpp]) } else { 0 };
                        let up = prev_row.map_or(0, |p| u16::from(p[i]));
                        let avg = ((left + up) >> 1) as u8;
                        let v = row[i].wrapping_sub(avg);
                        cand[i] = v;
                        score += u64::from(if v < 128 { v } else { 255 - v + 1 });
                    }
                }
                _ => {
                    for i in 0..row_bytes {
                        let left = if i >= bpp { i32::from(row[i - bpp]) } else { 0 };
                        let up = prev_row.map_or(0, |p| i32::from(p[i]));
                        let up_left = if i >= bpp {
                            prev_row.map_or(0, |p| i32::from(p[i - bpp]))
                        } else {
                            0
                        };
                        let pred = paeth_predictor(left, up, up_left) as u8;
                        let v = row[i].wrapping_sub(pred);
                        cand[i] = v;
                        score += u64::from(if v < 128 { v } else { 255 - v + 1 });
                    }
                }
            }

            if score < best_score {
                best_score = score;
                raw_scanlines[dst_scan_off] = f;
                raw_scanlines[dst_scan_off + 1..dst_scan_off + 1 + row_bytes]
                    .copy_from_slice(&cand);
            }
        }
    }

    let compressed = zlib_compress(&raw_scanlines, 6);
    let mut ihdr = [0u8; 13];
    ihdr[0..4].copy_from_slice(&width.to_be_bytes());
    ihdr[4..8].copy_from_slice(&height.to_be_bytes());
    ihdr[8] = 8;
    ihdr[9] = if channels == 4 { 6 } else { 2 };

    let mut out = Vec::with_capacity(8 + 25 + 12 + compressed.len() + 12);
    out.extend_from_slice(&PNG_SIG);
    append_png_chunk(&mut out, b"IHDR", &ihdr);
    append_png_chunk(&mut out, b"IDAT", &compressed);
    append_png_chunk(&mut out, b"IEND", &[]);
    Ok(out)
}

fn rgb_to_hls_f64(r: f64, g: f64, b: f64) -> (f64, f64, f64) {
    let maxc = r.max(g).max(b);
    let minc = r.min(g).min(b);
    let sumc = maxc + minc;
    let rangec = maxc - minc;
    let l = sumc / 2.0;
    if minc == maxc {
        return (0.0, l, 0.0);
    }
    let s = if l <= 0.5 {
        rangec / sumc
    } else {
        rangec / (2.0 - maxc - minc)
    };
    let rc = (maxc - r) / rangec;
    let gc = (maxc - g) / rangec;
    let bc = (maxc - b) / rangec;
    let mut h = if r == maxc {
        bc - gc
    } else if g == maxc {
        2.0 + rc - bc
    } else {
        4.0 + gc - rc
    };
    h = (h / 6.0) % 1.0;
    if h < 0.0 {
        h += 1.0;
    }
    (h, l, s)
}

#[derive(Clone, Copy)]
struct ColorizeParams {
    is_shift: bool,
    delta_h: f32,
    delta_l: f32,
    delta_s: f32,
    tgt_h_f: f32,
}

fn compute_colorize_params(
    rgba: &[u8],
    n_px: usize,
    target_rgb: [u8; 3],
    colorize_type: i32,
) -> ColorizeParams {
    let mut sum_r = 0u64;
    let mut sum_g = 0u64;
    let mut sum_b = 0u64;
    for px in rgba[..n_px * 4].as_chunks::<4>().0 {
        sum_r += u64::from(px[0]);
        sum_g += u64::from(px[1]);
        sum_b += u64::from(px[2]);
    }
    let u_px = n_px as u64;
    let avg_r = (sum_r / u_px) as f64 / 255.0;
    let avg_g = (sum_g / u_px) as f64 / 255.0;
    let avg_b = (sum_b / u_px) as f64 / 255.0;
    let (avg_h, avg_l, avg_s) = rgb_to_hls_f64(avg_r, avg_g, avg_b);
    let (tgt_h, tgt_l, tgt_s) = rgb_to_hls_f64(
        f64::from(target_rgb[0]) / 255.0,
        f64::from(target_rgb[1]) / 255.0,
        f64::from(target_rgb[2]) / 255.0,
    );

    ColorizeParams {
        is_shift: colorize_type == 0,
        delta_h: (tgt_h - avg_h) as f32,
        delta_l: (tgt_l - avg_l) as f32,
        delta_s: if tgt_s == 0.0 && avg_s > 0.0 {
            -1.0f32
        } else {
            (tgt_s - avg_s) as f32
        },
        tgt_h_f: tgt_h as f32,
    }
}

#[inline]
fn colorize_single_pixel(src: [u8; 4], params: ColorizeParams) -> [u8; 4] {
    const ONE_SIXTH: f32 = 1.0 / 6.0;
    const ONE_THIRD: f32 = 1.0 / 3.0;
    const NEG_ONE_THIRD: f32 = -1.0 / 3.0;
    const MIN_L: f32 = 0.01;
    const UNDEF_H: f32 = -1.0 / 360.0;
    const EPS: f32 = 1e-6;
    const OFFSETS: [f32; 3] = [ONE_THIRD, 0.0, NEG_ONE_THIRD];

    let r = f32::from(src[0]) / 255.0;
    let g = f32::from(src[1]) / 255.0;
    let b = f32::from(src[2]) / 255.0;

    let cm = r.max(g).max(b);
    let cn = r.min(g).min(b);
    let rg = cm - cn;
    let sum = cm + cn;
    let mut lt = sum * 0.5;
    let nz = rg > EPS;
    let mut st = if nz {
        let denom = if lt <= 0.5 { sum } else { 2.0 - sum };
        rg / denom
    } else {
        0.0
    };

    let mut nh = params.tgt_h_f;
    if params.is_shift {
        if !nz {
            nh = UNDEF_H;
        } else {
            nh = if r == cm {
                (g - b) / rg
            } else if g == cm {
                ((b - r) / rg) + 2.0
            } else {
                ((r - g) / rg) + 4.0
            };
            nh *= ONE_SIXTH;
            nh -= nh.floor();
        }
        nh += params.delta_h;
        nh -= nh.floor();
    }

    lt = (lt + params.delta_l).clamp(MIN_L, 1.0);
    st = (st + params.delta_s).clamp(0.0, 1.0);

    let m2 = if lt > 0.5 {
        lt + st * (1.0 - lt)
    } else {
        lt + lt * st
    };
    let m1 = lt * 2.0 - m2;
    let span = m2 - m1;
    let achromatic = st <= EPS;

    let mut out = [0u8, 0u8, 0u8, src[3]];
    for (ch, &off) in OFFSETS.iter().enumerate() {
        let mut t1 = nh;
        if off != 0.0 {
            t1 += off;
            t1 -= t1.floor();
        }
        t1 = t1 * 6.0 - 2.0;
        t1 = (2.0 - t1.abs()).clamp(0.0, 1.0);

        let mut val = if achromatic {
            lt
        } else if t1 == 1.0 {
            m2
        } else {
            t1 * span + m1
        };
        val = (val * 255.0).clamp(0.0, 255.0);
        out[ch] = val as u8;
    }
    out
}

#[allow(clippy::too_many_arguments)]
pub fn colorize_pixels_impl(
    rgba: &[u8],
    dst: &mut [u8],
    width: u32,
    height: u32,
    target_rgb: [u8; 3],
    colorize_type: i32,
    keep_alpha: bool,
    max_block_pixels: usize,
) {
    if width == 0 || height == 0 {
        return;
    }
    let w = width as usize;
    let h = height as usize;
    let n_px = w * h;
    let out_channels = if keep_alpha { 4 } else { 3 };
    let params = compute_colorize_params(rgba, n_px, target_rgb, colorize_type);

    let eff_block = max_block_pixels.max(1);
    let block_rows = h.min((eff_block / w).max(1));

    let mut y0 = 0usize;
    while y0 < h {
        let y1 = h.min(y0 + block_rows);
        for p in (y0 * w)..(y1 * w) {
            let p4 = p * 4;
            let px =
                colorize_single_pixel([rgba[p4], rgba[p4 + 1], rgba[p4 + 2], rgba[p4 + 3]], params);
            let out_off = p * out_channels;
            dst[out_off..out_off + out_channels].copy_from_slice(&px[..out_channels]);
        }
        y0 = y1;
    }
}

fn colorize_pixels_in_place(
    rgba: &mut [u8],
    width: u32,
    height: u32,
    target_rgb: [u8; 3],
    colorize_type: i32,
    keep_alpha: bool,
    max_block_pixels: usize,
) {
    if width == 0 || height == 0 {
        return;
    }
    let w = width as usize;
    let h = height as usize;
    let n_px = w * h;
    let out_channels = if keep_alpha { 4 } else { 3 };
    let params = compute_colorize_params(rgba, n_px, target_rgb, colorize_type);

    let eff_block = max_block_pixels.max(1);
    let block_rows = h.min((eff_block / w).max(1));

    let mut y0 = 0usize;
    while y0 < h {
        let y1 = h.min(y0 + block_rows);
        for p in (y0 * w)..(y1 * w) {
            let p4 = p * 4;
            let px =
                colorize_single_pixel([rgba[p4], rgba[p4 + 1], rgba[p4 + 2], rgba[p4 + 3]], params);
            let out_off = p * out_channels;
            rgba[out_off..out_off + out_channels].copy_from_slice(&px[..out_channels]);
        }
        y0 = y1;
    }
}

/// Applies SketchUp's HLS colorize rules (`colorize_type` `0` = shift, `1` = tint)
/// in `f32` row blocks, matching `SUTextureGetColorizedImageRep` within `1/255`.
pub fn colorize_image_u8(
    rgba: &[u8],
    width: u32,
    height: u32,
    target_rgb: [u8; 3],
    colorize_type: i32,
    keep_alpha: bool,
    max_block_pixels: usize,
) -> Vec<u8> {
    if width == 0 || height == 0 {
        return Vec::new();
    }
    let out_channels = if keep_alpha { 4 } else { 3 };
    let mut out = vec![0u8; (width as usize) * (height as usize) * out_channels];
    colorize_pixels_impl(
        rgba,
        &mut out,
        width,
        height,
        target_rgb,
        colorize_type,
        keep_alpha,
        max_block_pixels,
    );
    out
}

#[derive(Clone, Copy, PartialEq, Eq, Hash)]
struct BakeKey {
    tex_idx: usize,
    colorize_type: i32,
    target_rgb: [u8; 3],
}

pub(crate) struct ResolvedGltfImage<'a> {
    pub bytes: Cow<'a, [u8]>,
    pub mime_type: &'a str,
}

#[derive(Clone, Copy)]
pub(crate) struct ResolvedMaterialTexture {
    pub image_index: usize,
    pub baked: bool,
}

pub(crate) struct ResolvedGltfTextures<'a> {
    pub images: Vec<ResolvedGltfImage<'a>>,
    pub by_material: Vec<Option<ResolvedMaterialTexture>>,
}

struct DecodedEntry {
    width: u32,
    height: u32,
    rgba: Vec<u8>,
}

fn quantize_target_rgb(factor: &[f64; 4]) -> [u8; 3] {
    [
        ((factor[0] * 255.0).round() as i32).clamp(0, 255) as u8,
        ((factor[1] * 255.0).round() as i32).clamp(0, 255) as u8,
        ((factor[2] * 255.0).round() as i32).clamp(0, 255) as u8,
    ]
}

pub(crate) fn resolve_gltf_textures<'a>(
    raw_textures: &'a [SceneTexture],
    materials: &[GltfMaterial],
    embed: bool,
    log: Option<&Arc<LogFn>>,
    image_codec: Option<&Arc<dyn ImageCodec>>,
    mut stats: Option<&mut GltfTextureBakeStats>,
) -> ResolvedGltfTextures<'a> {
    let mut out = ResolvedGltfTextures {
        images: Vec::new(),
        by_material: vec![None; materials.len()],
    };
    if !embed || raw_textures.is_empty() {
        return out;
    }

    let mut warned_unsupported: HashSet<(String, String)> = HashSet::new();
    for tex in raw_textures {
        if tex.mime_type != "image/png" && tex.mime_type != "image/jpeg" {
            if let Some(cb) = log {
                let base = tex.filename.rsplit(['/', '\\']).next().unwrap_or("");
                let fmt = describe_image_format(&tex.data, &tex.filename);
                if warned_unsupported.insert((base.to_owned(), fmt.clone())) {
                    let label = if base.is_empty() {
                        String::new()
                    } else {
                        format!(" '{base}'")
                    };
                    let msg = format!(
                        "Texture{label} is {fmt}, which glTF 2.0 cannot carry \
                         (only PNG and JPEG): leaving it out of the GLB"
                    );
                    cb(LogLevel::Warning, &msg);
                }
            }
        }
    }

    let mut unique_keys: HashSet<BakeKey> = HashSet::new();
    let mut remaining_variants: HashMap<usize, usize> = HashMap::new();
    for mat in materials {
        let Some(tex_idx) = mat.pbr_metallic_roughness.base_color_texture else {
            continue;
        };
        let Some(tex) = raw_textures.get(tex_idx) else {
            continue;
        };
        if tex.mime_type != "image/png" && tex.mime_type != "image/jpeg" {
            continue;
        }
        if tex.mime_type == "image/jpeg" && image_codec.is_none() {
            continue;
        }
        if !mat.colorized {
            continue;
        }
        let key = BakeKey {
            tex_idx,
            colorize_type: mat.colorize_type,
            target_rgb: quantize_target_rgb(&mat.pbr_metallic_roughness.base_color_factor),
        };
        if unique_keys.insert(key) {
            *remaining_variants.entry(tex_idx).or_insert(0) += 1;
        }
    }

    let mut plain_by_tex: HashMap<usize, usize> = HashMap::new();
    let mut baked_by_key: HashMap<BakeKey, ResolvedMaterialTexture> = HashMap::new();
    let mut decoded_by_tex: HashMap<usize, Option<DecodedEntry>> = HashMap::new();
    let mut warned_no_jpeg_codec = false;

    let get_plain_image = |tex_idx: usize,
                           images: &mut Vec<ResolvedGltfImage<'a>>,
                           plain_map: &mut HashMap<usize, usize>|
     -> usize {
        if let Some(&idx) = plain_map.get(&tex_idx) {
            return idx;
        }
        let tex = &raw_textures[tex_idx];
        let bytes = if tex.mime_type == "image/jpeg" {
            strip_jpeg_exif_app1(&tex.data)
        } else {
            Cow::Borrowed(tex.data.as_slice())
        };
        let idx = images.len();
        images.push(ResolvedGltfImage {
            bytes,
            mime_type: &tex.mime_type,
        });
        plain_map.insert(tex_idx, idx);
        idx
    };

    for (mat_i, mat) in materials.iter().enumerate() {
        let Some(tex_idx) = mat.pbr_metallic_roughness.base_color_texture else {
            continue;
        };
        let Some(tex) = raw_textures.get(tex_idx) else {
            continue;
        };
        if tex.mime_type != "image/png" && tex.mime_type != "image/jpeg" {
            continue;
        }

        if !mat.colorized {
            let image_index = get_plain_image(tex_idx, &mut out.images, &mut plain_by_tex);
            out.by_material[mat_i] = Some(ResolvedMaterialTexture {
                image_index,
                baked: false,
            });
            continue;
        }

        if tex.mime_type == "image/jpeg" && image_codec.is_none() {
            if !warned_no_jpeg_codec {
                warned_no_jpeg_codec = true;
                if let Some(cb) = log {
                    cb(
                        LogLevel::Warning,
                        "No image_codec provided; colorized JPEG textures in GLB export stay untinted with their material factor (pass GlbOptions::with_image_codec to bake colorized JPEGs).",
                    );
                }
            }
            let image_index = get_plain_image(tex_idx, &mut out.images, &mut plain_by_tex);
            out.by_material[mat_i] = Some(ResolvedMaterialTexture {
                image_index,
                baked: false,
            });
            continue;
        }

        let target_rgb = quantize_target_rgb(&mat.pbr_metallic_roughness.base_color_factor);
        let ckey = BakeKey {
            tex_idx,
            colorize_type: mat.colorize_type,
            target_rgb,
        };
        if let Some(&hit) = baked_by_key.get(&ckey) {
            out.by_material[mat_i] = Some(hit);
            continue;
        }

        let rem = remaining_variants.get_mut(&tex_idx).map_or(0, |r| {
            *r = r.saturating_sub(1);
            *r
        });

        if let std::collections::hash_map::Entry::Vacant(vacant) = decoded_by_tex.entry(tex_idx) {
            if let Some(s) = stats.as_deref_mut() {
                s.decode_calls += 1;
            }
            let decoded_res: Result<DecodedEntry, String> = if tex.mime_type == "image/png" {
                decode_png_rgba(&tex.data).map(|d| DecodedEntry {
                    width: d.width,
                    height: d.height,
                    rgba: d.rgba,
                })
            } else {
                (|| {
                    let (hdr_w, hdr_h) = read_jpeg_dimensions(&tex.data)
                        .ok_or_else(|| "missing or corrupt JPEG SOF header".to_owned())?;
                    check_pixel_limit(hdr_w, hdr_h)?;
                    let dec = image_codec.unwrap().decode(&tex.data, &tex.mime_type)?;
                    let (dw, dh) = check_pixel_limit(dec.width, dec.height)?;
                    let expected_len = dw
                        .checked_mul(dh)
                        .and_then(|px| px.checked_mul(4))
                        .ok_or_else(|| "RGBA buffer size overflow".to_owned())?;
                    if dec.rgba.len() < expected_len {
                        return Err(format!(
                            "ImageCodec::decode returned {} bytes for {}x{} RGBA (expected {expected_len})",
                            dec.rgba.len(),
                            dec.width,
                            dec.height
                        ));
                    }
                    let mut rgba = dec.rgba;
                    rgba.truncate(expected_len);
                    Ok(DecodedEntry {
                        width: dec.width,
                        height: dec.height,
                        rgba,
                    })
                })()
            };

            match decoded_res {
                Ok(entry) => {
                    vacant.insert(Some(entry));
                }
                Err(reason) => {
                    if let Some(cb) = log {
                        let name_part = if tex.filename.is_empty() {
                            String::new()
                        } else {
                            format!(" ('{}')", tex.filename)
                        };
                        let msg =
                            format!("Failed to decode texture {tex_idx}{name_part} for GLB export: {reason}");
                        cb(LogLevel::Warning, &msg);
                    }
                    vacant.insert(None);
                }
            }
        }

        let has_decoded = decoded_by_tex
            .get(&tex_idx)
            .and_then(Option::as_ref)
            .is_some();
        if !has_decoded {
            let fallback = ResolvedMaterialTexture {
                image_index: get_plain_image(tex_idx, &mut out.images, &mut plain_by_tex),
                baked: false,
            };
            baked_by_key.insert(ckey, fallback);
            out.by_material[mat_i] = Some(fallback);
            continue;
        }

        if let Some(s) = stats.as_deref_mut() {
            s.colorize_calls += 1;
        }

        let encode_res: Result<Vec<u8>, String> = if tex.mime_type == "image/jpeg" {
            let (width, height, baked_rgba) = {
                let dec = decoded_by_tex[&tex_idx].as_ref().unwrap();
                let baked = colorize_image_u8(
                    &dec.rgba,
                    dec.width,
                    dec.height,
                    target_rgb,
                    mat.colorize_type,
                    true,
                    MAX_COLORIZE_BLOCK_PIXELS,
                );
                (dec.width, dec.height, baked)
            };
            if rem == 0 {
                decoded_by_tex.remove(&tex_idx);
            }
            image_codec
                .unwrap()
                .encode(&baked_rgba, width, height, "image/jpeg", 95)
                .and_then(|enc| {
                    if enc.is_empty() {
                        Err("ImageCodec::encode returned empty output".into())
                    } else {
                        Ok(enc)
                    }
                })
        } else if rem == 0 {
            let mut owned = decoded_by_tex.remove(&tex_idx).flatten().unwrap();
            let n_px = (owned.width as usize) * (owned.height as usize);
            let keep_alpha = owned.rgba[..n_px * 4]
                .as_chunks::<4>()
                .0
                .iter()
                .any(|px| px[3] < 255);
            let out_ch = if keep_alpha { 4 } else { 3 };
            colorize_pixels_in_place(
                &mut owned.rgba,
                owned.width,
                owned.height,
                target_rgb,
                mat.colorize_type,
                keep_alpha,
                MAX_COLORIZE_BLOCK_PIXELS,
            );
            encode_png_u8(
                &owned.rgba[..n_px * out_ch],
                owned.width,
                owned.height,
                out_ch,
            )
        } else {
            let dec = decoded_by_tex[&tex_idx].as_ref().unwrap();
            let n_px = (dec.width as usize) * (dec.height as usize);
            let keep_alpha = dec.rgba[..n_px * 4]
                .as_chunks::<4>()
                .0
                .iter()
                .any(|px| px[3] < 255);
            let out_ch = if keep_alpha { 4 } else { 3 };
            let baked = colorize_image_u8(
                &dec.rgba,
                dec.width,
                dec.height,
                target_rgb,
                mat.colorize_type,
                keep_alpha,
                MAX_COLORIZE_BLOCK_PIXELS,
            );
            encode_png_u8(&baked, dec.width, dec.height, out_ch)
        };

        match encode_res {
            Ok(encoded) => {
                let image_index = out.images.len();
                out.images.push(ResolvedGltfImage {
                    bytes: Cow::Owned(encoded),
                    mime_type: &tex.mime_type,
                });
                let res = ResolvedMaterialTexture {
                    image_index,
                    baked: true,
                };
                baked_by_key.insert(ckey, res);
                out.by_material[mat_i] = Some(res);
            }
            Err(reason) => {
                if let Some(cb) = log {
                    let name_part = if tex.filename.is_empty() {
                        String::new()
                    } else {
                        format!(" ('{}')", tex.filename)
                    };
                    let msg = format!(
                        "Failed to encode baked texture {tex_idx}{name_part} for GLB export: {reason}"
                    );
                    cb(LogLevel::Warning, &msg);
                }
                let fallback = ResolvedMaterialTexture {
                    image_index: get_plain_image(tex_idx, &mut out.images, &mut plain_by_tex),
                    baked: false,
                };
                baked_by_key.insert(ckey, fallback);
                out.by_material[mat_i] = Some(fallback);
            }
        }
    }

    out
}

#[doc(hidden)]
pub fn count_texture_bake_stats(
    scene: &crate::scene::Scene,
    embed: bool,
    image_codec: Option<&Arc<dyn ImageCodec>>,
) -> (usize, GltfTextureBakeStats) {
    let mut stats = GltfTextureBakeStats::default();
    let res = resolve_gltf_textures(
        &scene.textures,
        &scene.gltf_materials,
        embed,
        None,
        image_codec,
        Some(&mut stats),
    );
    (res.images.len(), stats)
}
