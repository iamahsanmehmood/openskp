using System;
using System.Collections;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Reflection;

namespace OpenSkp
{
    /// <summary>Synchronous image codec used to decode and re-encode colorized JPEG textures
    /// during GLB export (PNG textures are decoded and encoded natively).</summary>
    public interface ISkpImageCodec
    {
        /// <summary>Decodes compressed image bytes into row-major 8-bit RGBA pixels
        /// (<c>Width * Height * 4</c> bytes), or returns <c>null</c> when decoding fails.</summary>
        /// <param name="data">Compressed image bytes (for example, <c>"image/jpeg"</c>).</param>
        /// <param name="mimeType">IANA media type of <paramref name="data"/>, such as <c>"image/jpeg"</c>.</param>
        /// <returns>Decoded dimensions and 8-bit RGBA buffer, or <c>null</c>.</returns>
        (int Width, int Height, byte[] Rgba)? Decode(byte[] data, string mimeType);

        /// <summary>Encodes row-major 8-bit RGBA pixels (<c>width * height * 4</c> bytes) into
        /// compressed image bytes.</summary>
        /// <param name="rgba">Row-major 8-bit RGBA pixel buffer (<c>width * height * 4</c> bytes).</param>
        /// <param name="width">Image width in pixels.</param>
        /// <param name="height">Image height in pixels.</param>
        /// <param name="mimeType">Target IANA media type, such as <c>"image/jpeg"</c>.</param>
        /// <param name="quality">Target encoding quality from 1 to 100 (95 for baked GLB JPEGs).</param>
        /// <returns>Compressed image bytes.</returns>
        byte[] Encode(byte[] rgba, int width, int height, string mimeType, int quality = 95);
    }

    internal sealed class GltfTextureBakeStats
    {
        public int DecodeCalls { get; set; }
        public int ColorizeCalls { get; set; }
    }

    internal static class GlbTextures
    {
        internal const int MaxColorizeBlockPixels = 262144;
        internal const ulong MaxDecodeTexturePixels = 178956970UL;

        private static readonly byte[] PngSignature = { 137, 80, 78, 71, 13, 10, 26, 10 };

        private static readonly (int X0, int Dx, int Y0, int Dy)[] Adam7Passes =
        {
            (0, 8, 0, 8),
            (4, 8, 0, 8),
            (0, 4, 4, 8),
            (2, 4, 0, 4),
            (0, 2, 2, 4),
            (1, 2, 0, 2),
            (0, 1, 1, 2),
        };

        private static readonly float[] HueChannelOffsets = { 1.0f / 3.0f, 0.0f, -1.0f / 3.0f };

        private static readonly uint[] Crc32Table = BuildCrc32Table();

        private static uint[] BuildCrc32Table()
        {
            var table = new uint[256];
            for (uint i = 0; i < 256; i++)
            {
                uint c = i;
                for (int k = 0; k < 8; k++)
                {
                    c = (c & 1u) != 0 ? (0xEDB88320u ^ (c >> 1)) : (c >> 1);
                }
                table[i] = c;
            }
            return table;
        }

        private static uint ComputeCrc32(byte[] buf, int start, int length)
        {
            uint c = 0xFFFFFFFFu;
            int end = start + length;
            for (int i = start; i < end; i++)
            {
                c = Crc32Table[(c ^ buf[i]) & 0xFFu] ^ (c >> 8);
            }
            return c ^ 0xFFFFFFFFu;
        }

        internal static uint ComputeAdler32(byte[] data, int offset, int length)
        {
            const uint ModAdler = 65521u;
            uint s1 = 1u;
            uint s2 = 0u;
            int end = offset + length;
            int idx = offset;
            while (idx < end)
            {
                int chunkEnd = Math.Min(idx + 5552, end);
                while (idx < chunkEnd)
                {
                    s1 += data[idx++];
                    s2 += s1;
                }
                s1 %= ModAdler;
                s2 %= ModAdler;
            }
            return (s2 << 16) | s1;
        }

        private static uint ReadUInt32BE(byte[] buf, int offset) =>
            ((uint)buf[offset] << 24) |
            ((uint)buf[offset + 1] << 16) |
            ((uint)buf[offset + 2] << 8) |
            buf[offset + 3];

        private static void WriteUInt32BE(byte[] buf, int offset, uint value)
        {
            buf[offset] = (byte)(value >> 24);
            buf[offset + 1] = (byte)(value >> 16);
            buf[offset + 2] = (byte)(value >> 8);
            buf[offset + 3] = (byte)value;
        }

        private static int PaethPredictor(int a, int b, int c)
        {
            int p = a + b - c;
            int pa = Math.Abs(p - a);
            int pb = Math.Abs(p - b);
            int pc = Math.Abs(p - c);
            if (pa <= pb && pa <= pc) return a;
            return pb <= pc ? b : c;
        }

        internal static byte[] StripJpegExifApp1(byte[] data)
        {
            int n = data.Length;
            if (n < 4 || data[0] != 0xFF || data[1] != 0xD8) return data;

            List<(int Start, int End)>? dropRanges = null;
            int pos = 2;
            while (pos < n)
            {
                if (data[pos] != 0xFF) break;
                int markerStart = pos;
                while (pos < n && data[pos] == 0xFF) pos++;
                if (pos >= n) break;
                byte marker = data[pos++];
                if (marker == 0x00 || marker == 0xD9) break;
                if (marker == 0x01 || (marker >= 0xD0 && marker <= 0xD8)) continue;
                if (pos + 2 > n) break;
                int segLen = (data[pos] << 8) | data[pos + 1];
                if (segLen < 2 || pos + segLen > n) break;
                int segEnd = pos + segLen;
                if (marker == 0xDA) break; // Start of Scan
                if (marker == 0xE1)
                {
                    dropRanges ??= new List<(int, int)>();
                    dropRanges.Add((markerStart, segEnd));
                }
                pos = segEnd;
            }

            if (dropRanges == null || dropRanges.Count == 0) return data;

            int dropped = 0;
            foreach (var (s, e) in dropRanges) dropped += e - s;
            var output = new byte[n - dropped];
            int cursor = 0;
            int dst = 0;
            foreach (var (start, end) in dropRanges)
            {
                if (start > cursor)
                {
                    int len = start - cursor;
                    Buffer.BlockCopy(data, cursor, output, dst, len);
                    dst += len;
                }
                cursor = end;
            }
            if (cursor < n)
            {
                Buffer.BlockCopy(data, cursor, output, dst, n - cursor);
            }
            return output;
        }

        internal static bool TryReadJpegDimensions(byte[] data, out int width, out int height)
        {
            width = 0;
            height = 0;
            int n = data.Length;
            if (n < 4 || data[0] != 0xFF || data[1] != 0xD8) return false;

            int pos = 2;
            while (pos < n)
            {
                if (data[pos] != 0xFF) break;
                while (pos < n && data[pos] == 0xFF) pos++;
                if (pos >= n) break;
                byte marker = data[pos++];
                if (marker == 0x00 || marker == 0xD9 || marker == 0xDA) break;
                if (marker == 0x01 || (marker >= 0xD0 && marker <= 0xD8)) continue;
                if (pos + 2 > n) break;
                int segLen = (data[pos] << 8) | data[pos + 1];
                if (segLen < 2 || pos + segLen > n) break;
                if (marker >= 0xC0 && marker <= 0xCF &&
                    marker != 0xC4 && marker != 0xC8 && marker != 0xCC)
                {
                    if (segLen < 7) return false;
                    height = (data[pos + 3] << 8) | data[pos + 4];
                    width = (data[pos + 5] << 8) | data[pos + 6];
                    return true;
                }
                pos += segLen;
            }
            return false;
        }

        internal static bool TryReadPngDimensions(byte[] data, out uint width, out uint height)
        {
            width = 0;
            height = 0;
            if (data.Length < 33) return false;
            for (int i = 0; i < 8; i++)
            {
                if (data[i] != PngSignature[i]) return false;
            }
            uint chunkLen = ReadUInt32BE(data, 8);
            uint chunkType = ReadUInt32BE(data, 12);
            if (chunkType != 0x49484452u || chunkLen != 13u) return false; // IHDR
            width = ReadUInt32BE(data, 16);
            height = ReadUInt32BE(data, 20);
            return true;
        }

        private static void CheckPixelLimit(ulong width, ulong height)
        {
            if (width == 0 || height == 0)
            {
                throw new InvalidDataException(
                    string.Format(CultureInfo.InvariantCulture, "invalid image dimensions {0}x{1}", width, height));
            }
            ulong totalPixels = width * height;
            if (width > ulong.MaxValue / height || totalPixels > MaxDecodeTexturePixels)
            {
                throw new InvalidDataException(
                    string.Format(
                        CultureInfo.InvariantCulture,
                        "image dimensions {0}x{1} ({2} pixels) exceed maximum of {3} pixels",
                        width,
                        height,
                        totalPixels,
                        MaxDecodeTexturePixels));
            }
        }

        private static byte[] ZlibInflateExact(byte[] zlibData, int expectedRawBytes)
        {
            if (zlibData.Length < 6)
            {
                throw new InvalidDataException("truncated PNG zlib stream");
            }
            byte cmf = zlibData[0];
            byte flg = zlibData[1];
            if ((cmf & 0x0F) != 8 || (cmf >> 4) > 7 || (((cmf << 8) | flg) % 31) != 0 || (flg & 0x20) != 0)
            {
                throw new InvalidDataException("invalid PNG zlib header");
            }

            var inflated = new byte[expectedRawBytes];
            using (var ms = new MemoryStream(zlibData, 2, zlibData.Length - 2, writable: false))
            using (var deflate = new DeflateStream(ms, CompressionMode.Decompress))
            {
                int offset = 0;
                while (offset < expectedRawBytes)
                {
                    int read = deflate.Read(inflated, offset, expectedRawBytes - offset);
                    if (read <= 0)
                    {
                        throw new InvalidDataException("truncated PNG IDAT deflate stream");
                    }
                    offset += read;
                }
            }
            return inflated;
        }

        private static byte[] ZlibDeflate(byte[] rawData)
        {
            using var ms = new MemoryStream();
            ms.WriteByte(0x78);
            ms.WriteByte(0x9C);
            using (var deflate = new DeflateStream(ms, CompressionLevel.Optimal, leaveOpen: true))
            {
                deflate.Write(rawData, 0, rawData.Length);
            }
            uint adler = ComputeAdler32(rawData, 0, rawData.Length);
            ms.WriteByte((byte)(adler >> 24));
            ms.WriteByte((byte)(adler >> 16));
            ms.WriteByte((byte)(adler >> 8));
            ms.WriteByte((byte)adler);
            return ms.ToArray();
        }

        internal static (int Width, int Height, byte[] Rgba) DecodePngRgba(byte[] data)
        {
            if (data.Length < 33)
            {
                throw new InvalidDataException("truncated PNG header");
            }
            for (int i = 0; i < 8; i++)
            {
                if (data[i] != PngSignature[i])
                {
                    throw new InvalidDataException("invalid PNG signature");
                }
            }

            int width = 0;
            int height = 0;
            int bitDepth = 0;
            int colorType = 0;
            int interlace = 0;
            int channels = 0;
            bool seenIhdr = false;
            byte[]? palette = null;
            byte[]? trns = null;
            var idatSegments = new List<(int Offset, int Length)>();
            long totalIdatLen = 0;

            int pos = 8;
            while (pos + 8 <= data.Length)
            {
                uint uLen = ReadUInt32BE(data, pos);
                uint type = ReadUInt32BE(data, pos + 4);
                if (uLen > (uint)(data.Length - pos - 12))
                {
                    throw new InvalidDataException("truncated PNG chunk");
                }
                int len = (int)uLen;
                int payloadStart = pos + 8;

                if (!seenIhdr)
                {
                    if (type != 0x49484452u || len != 13)
                    {
                        throw new InvalidDataException("missing or invalid PNG IHDR chunk");
                    }
                    uint uWidth = ReadUInt32BE(data, payloadStart);
                    uint uHeight = ReadUInt32BE(data, payloadStart + 4);
                    CheckPixelLimit(uWidth, uHeight);
                    width = (int)uWidth;
                    height = (int)uHeight;

                    bitDepth = data[payloadStart + 8];
                    colorType = data[payloadStart + 9];
                    int compMethod = data[payloadStart + 10];
                    int filterMethod = data[payloadStart + 11];
                    interlace = data[payloadStart + 12];
                    if (compMethod != 0 || filterMethod != 0 || (interlace != 0 && interlace != 1))
                    {
                        throw new InvalidDataException("unsupported PNG compression/filter/interlace mode");
                    }

                    if (colorType == 0)
                    {
                        if (bitDepth != 1 && bitDepth != 2 && bitDepth != 4 && bitDepth != 8 && bitDepth != 16)
                        {
                            throw new InvalidDataException($"unsupported greyscale PNG bit depth {bitDepth}");
                        }
                        channels = 1;
                    }
                    else if (colorType == 2)
                    {
                        if (bitDepth != 8 && bitDepth != 16)
                        {
                            throw new InvalidDataException($"unsupported RGB PNG bit depth {bitDepth}");
                        }
                        channels = 3;
                    }
                    else if (colorType == 3)
                    {
                        if (bitDepth != 1 && bitDepth != 2 && bitDepth != 4 && bitDepth != 8)
                        {
                            throw new InvalidDataException($"unsupported indexed PNG bit depth {bitDepth}");
                        }
                        channels = 1;
                    }
                    else if (colorType == 4)
                    {
                        if (bitDepth != 8 && bitDepth != 16)
                        {
                            throw new InvalidDataException($"unsupported grey+alpha PNG bit depth {bitDepth}");
                        }
                        channels = 2;
                    }
                    else if (colorType == 6)
                    {
                        if (bitDepth != 8 && bitDepth != 16)
                        {
                            throw new InvalidDataException($"unsupported RGBA PNG bit depth {bitDepth}");
                        }
                        channels = 4;
                    }
                    else
                    {
                        throw new InvalidDataException($"unsupported PNG color type {colorType}");
                    }
                    seenIhdr = true;
                }
                else if (type == 0x504C5445u)
                {
                    // PLTE
                    if (len < 3 || len > 768 || len % 3 != 0)
                    {
                        throw new InvalidDataException("invalid PNG PLTE chunk");
                    }
                    palette = new byte[len];
                    Buffer.BlockCopy(data, payloadStart, palette, 0, len);
                }
                else if (type == 0x74524E53u)
                {
                    // tRNS
                    trns = new byte[len];
                    Buffer.BlockCopy(data, payloadStart, trns, 0, len);
                }
                else if (type == 0x49444154u)
                {
                    // IDAT
                    if (len > 0)
                    {
                        idatSegments.Add((payloadStart, len));
                        totalIdatLen += len;
                    }
                }
                else if (type == 0x49454E44u)
                {
                    // IEND
                    break;
                }

                pos = payloadStart + len + 4;
            }

            if (!seenIhdr || totalIdatLen <= 0 || totalIdatLen > int.MaxValue)
            {
                throw new InvalidDataException("PNG contains no IDAT image data");
            }
            if (colorType == 3 && palette == null)
            {
                throw new InvalidDataException("indexed PNG missing PLTE chunk");
            }

            var passSpecs = new List<(int X0, int Dx, int Y0, int Dy, int Pw, int Ph)>(7);
            long expectedRawBytesLong = 0;
            if (interlace == 0)
            {
                long rowBytes = ((long)width * channels * bitDepth + 7) / 8;
                expectedRawBytesLong = (long)height * (1 + rowBytes);
                passSpecs.Add((0, 1, 0, 1, width, height));
            }
            else
            {
                foreach (var (x0, dx, y0, dy) in Adam7Passes)
                {
                    int pw = width > x0 ? (width - x0 + dx - 1) / dx : 0;
                    int ph = height > y0 ? (height - y0 + dy - 1) / dy : 0;
                    if (pw > 0 && ph > 0)
                    {
                        long rowBytes = ((long)pw * channels * bitDepth + 7) / 8;
                        expectedRawBytesLong += (long)ph * (1 + rowBytes);
                        passSpecs.Add((x0, dx, y0, dy, pw, ph));
                    }
                }
            }

            if (expectedRawBytesLong <= 0 || expectedRawBytesLong > int.MaxValue)
            {
                throw new InvalidDataException("PNG raw scanline buffer exceeds addressable limit");
            }

            var idat = new byte[(int)totalIdatLen];
            int idatOff = 0;
            foreach (var (segOff, segLen) in idatSegments)
            {
                Buffer.BlockCopy(data, segOff, idat, idatOff, segLen);
                idatOff += segLen;
            }

            byte[] inflated = ZlibInflateExact(idat, (int)expectedRawBytesLong);
            var rgba = new byte[width * height * 4];
            int bpp = Math.Max(1, (channels * bitDepth + 7) >> 3);

            bool hasGreyTrns = colorType == 0 && trns != null && trns.Length >= 2;
            int greyTrnsVal = hasGreyTrns
                ? ((trns![0] << 8) | trns[1]) & (bitDepth == 16 ? 0xFFFF : (1 << bitDepth) - 1)
                : -1;
            bool hasRgbTrns = colorType == 2 && trns != null && trns.Length >= 6;
            int rgbTrnsMask = bitDepth == 16 ? 0xFFFF : 0xFF;
            int rgbTrnsR = hasRgbTrns ? ((trns![0] << 8) | trns[1]) & rgbTrnsMask : -1;
            int rgbTrnsG = hasRgbTrns ? ((trns![2] << 8) | trns[3]) & rgbTrnsMask : -1;
            int rgbTrnsB = hasRgbTrns ? ((trns![4] << 8) | trns[5]) & rgbTrnsMask : -1;
            int palEntries = palette != null ? palette.Length / 3 : 0;

            int rawCursor = 0;
            foreach (var (x0, dx, y0, dy, pw, ph) in passSpecs)
            {
                int rowBytes = (int)(((long)pw * channels * bitDepth + 7) / 8);
                var prevRow = new byte[rowBytes];
                var currRow = new byte[rowBytes];

                for (int py = 0; py < ph; py++)
                {
                    int filterType = inflated[rawCursor++];
                    int srcBase = rawCursor;
                    rawCursor += rowBytes;

                    if (filterType == 0)
                    {
                        Buffer.BlockCopy(inflated, srcBase, currRow, 0, rowBytes);
                    }
                    else if (filterType == 1)
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            int left = i >= bpp ? currRow[i - bpp] : 0;
                            currRow[i] = (byte)(inflated[srcBase + i] + left);
                        }
                    }
                    else if (filterType == 2)
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            currRow[i] = (byte)(inflated[srcBase + i] + prevRow[i]);
                        }
                    }
                    else if (filterType == 3)
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            int left = i >= bpp ? currRow[i - bpp] : 0;
                            currRow[i] = (byte)(inflated[srcBase + i] + ((left + prevRow[i]) >> 1));
                        }
                    }
                    else if (filterType == 4)
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            int left = i >= bpp ? currRow[i - bpp] : 0;
                            int upLeft = i >= bpp ? prevRow[i - bpp] : 0;
                            currRow[i] = (byte)(inflated[srcBase + i] + PaethPredictor(left, prevRow[i], upLeft));
                        }
                    }
                    else
                    {
                        throw new InvalidDataException($"invalid PNG filter type {filterType}");
                    }

                    int dstY = y0 + py * dy;
                    if (interlace == 0 && bitDepth == 8 && colorType == 6)
                    {
                        Buffer.BlockCopy(currRow, 0, rgba, dstY * width * 4, rowBytes);
                    }
                    else if (interlace == 0 && bitDepth == 8 && colorType == 2 && !hasRgbTrns)
                    {
                        int sOff = 0;
                        int dOff = dstY * width * 4;
                        for (int px = 0; px < pw; px++)
                        {
                            rgba[dOff] = currRow[sOff];
                            rgba[dOff + 1] = currRow[sOff + 1];
                            rgba[dOff + 2] = currRow[sOff + 2];
                            rgba[dOff + 3] = 255;
                            sOff += 3;
                            dOff += 4;
                        }
                    }
                    else
                    {
                        for (int px = 0; px < pw; px++)
                        {
                            int dstIdx = (dstY * width + (x0 + px * dx)) * 4;
                            if (colorType == 6)
                            {
                                if (bitDepth == 8)
                                {
                                    int s = px * 4;
                                    rgba[dstIdx] = currRow[s];
                                    rgba[dstIdx + 1] = currRow[s + 1];
                                    rgba[dstIdx + 2] = currRow[s + 2];
                                    rgba[dstIdx + 3] = currRow[s + 3];
                                }
                                else
                                {
                                    int s = px * 8;
                                    rgba[dstIdx] = (byte)((((currRow[s] << 8) | currRow[s + 1]) * 255 + 32895) >> 16);
                                    rgba[dstIdx + 1] = (byte)((((currRow[s + 2] << 8) | currRow[s + 3]) * 255 + 32895) >> 16);
                                    rgba[dstIdx + 2] = (byte)((((currRow[s + 4] << 8) | currRow[s + 5]) * 255 + 32895) >> 16);
                                    rgba[dstIdx + 3] = (byte)((((currRow[s + 6] << 8) | currRow[s + 7]) * 255 + 32895) >> 16);
                                }
                            }
                            else if (colorType == 2)
                            {
                                if (bitDepth == 8)
                                {
                                    int s = px * 3;
                                    byte r = currRow[s];
                                    byte g = currRow[s + 1];
                                    byte b = currRow[s + 2];
                                    rgba[dstIdx] = r;
                                    rgba[dstIdx + 1] = g;
                                    rgba[dstIdx + 2] = b;
                                    rgba[dstIdx + 3] =
                                        hasRgbTrns && r == rgbTrnsR && g == rgbTrnsG && b == rgbTrnsB ? (byte)0 : (byte)255;
                                }
                                else
                                {
                                    int s = px * 6;
                                    int r16 = (currRow[s] << 8) | currRow[s + 1];
                                    int g16 = (currRow[s + 2] << 8) | currRow[s + 3];
                                    int b16 = (currRow[s + 4] << 8) | currRow[s + 5];
                                    rgba[dstIdx] = (byte)((r16 * 255 + 32895) >> 16);
                                    rgba[dstIdx + 1] = (byte)((g16 * 255 + 32895) >> 16);
                                    rgba[dstIdx + 2] = (byte)((b16 * 255 + 32895) >> 16);
                                    rgba[dstIdx + 3] =
                                        hasRgbTrns && r16 == rgbTrnsR && g16 == rgbTrnsG && b16 == rgbTrnsB ? (byte)0 : (byte)255;
                                }
                            }
                            else if (colorType == 4)
                            {
                                if (bitDepth == 8)
                                {
                                    byte g = currRow[px * 2];
                                    rgba[dstIdx] = g;
                                    rgba[dstIdx + 1] = g;
                                    rgba[dstIdx + 2] = g;
                                    rgba[dstIdx + 3] = currRow[px * 2 + 1];
                                }
                                else
                                {
                                    int s = px * 4;
                                    byte g = (byte)((((currRow[s] << 8) | currRow[s + 1]) * 255 + 32895) >> 16);
                                    rgba[dstIdx] = g;
                                    rgba[dstIdx + 1] = g;
                                    rgba[dstIdx + 2] = g;
                                    rgba[dstIdx + 3] = (byte)((((currRow[s + 2] << 8) | currRow[s + 3]) * 255 + 32895) >> 16);
                                }
                            }
                            else if (colorType == 0)
                            {
                                if (bitDepth == 8)
                                {
                                    byte g = currRow[px];
                                    rgba[dstIdx] = g;
                                    rgba[dstIdx + 1] = g;
                                    rgba[dstIdx + 2] = g;
                                    rgba[dstIdx + 3] = hasGreyTrns && g == greyTrnsVal ? (byte)0 : (byte)255;
                                }
                                else if (bitDepth == 16)
                                {
                                    int g16 = (currRow[px * 2] << 8) | currRow[px * 2 + 1];
                                    byte g = (byte)((g16 * 255 + 32895) >> 16);
                                    rgba[dstIdx] = g;
                                    rgba[dstIdx + 1] = g;
                                    rgba[dstIdx + 2] = g;
                                    rgba[dstIdx + 3] = hasGreyTrns && g16 == greyTrnsVal ? (byte)0 : (byte)255;
                                }
                                else
                                {
                                    int bitPos = px * bitDepth;
                                    int byteIdx = bitPos >> 3;
                                    int shift = 8 - bitDepth - (bitPos & 7);
                                    int mask = (1 << bitDepth) - 1;
                                    int sample = (currRow[byteIdx] >> shift) & mask;
                                    byte g = (byte)((sample * 255 + (mask >> 1)) / mask);
                                    rgba[dstIdx] = g;
                                    rgba[dstIdx + 1] = g;
                                    rgba[dstIdx + 2] = g;
                                    rgba[dstIdx + 3] = hasGreyTrns && sample == greyTrnsVal ? (byte)0 : (byte)255;
                                }
                            }
                            else
                            {
                                // Indexed (colorType == 3)
                                int idx;
                                if (bitDepth == 8)
                                {
                                    idx = currRow[px];
                                }
                                else
                                {
                                    int bitPos = px * bitDepth;
                                    int byteIdx = bitPos >> 3;
                                    int shift = 8 - bitDepth - (bitPos & 7);
                                    idx = (currRow[byteIdx] >> shift) & ((1 << bitDepth) - 1);
                                }
                                if (idx >= palEntries)
                                {
                                    throw new InvalidDataException($"PNG palette index {idx} out of range ({palEntries})");
                                }
                                rgba[dstIdx] = palette![idx * 3];
                                rgba[dstIdx + 1] = palette[idx * 3 + 1];
                                rgba[dstIdx + 2] = palette[idx * 3 + 2];
                                rgba[dstIdx + 3] = trns != null && idx < trns.Length ? trns[idx] : (byte)255;
                            }
                        }
                    }

                    (prevRow, currRow) = (currRow, prevRow);
                }
            }

            return (width, height, rgba);
        }

        private static int WritePngChunk(byte[] output, int offset, string typeAscii, byte[] payload)
        {
            int len = payload.Length;
            WriteUInt32BE(output, offset, (uint)len);
            int crcStart = offset + 4;
            output[crcStart] = (byte)typeAscii[0];
            output[crcStart + 1] = (byte)typeAscii[1];
            output[crcStart + 2] = (byte)typeAscii[2];
            output[crcStart + 3] = (byte)typeAscii[3];
            if (len > 0)
            {
                Buffer.BlockCopy(payload, 0, output, crcStart + 4, len);
            }
            uint crc = ComputeCrc32(output, crcStart, 4 + len);
            WriteUInt32BE(output, crcStart + 4 + len, crc);
            return crcStart + 8 + len;
        }

        internal static byte[] EncodePngU8(byte[] pixels, int width, int height, int channels)
        {
            if (width <= 0 || height <= 0 || (channels != 3 && channels != 4))
            {
                throw new ArgumentException("EncodePngU8 requires positive dimensions and 3 or 4 channels.");
            }
            int rowBytes = width * channels;
            var rawScanlines = new byte[height * (1 + rowBytes)];
            var cand = new byte[rowBytes];
            int bpp = channels;

            for (int y = 0; y < height; y++)
            {
                int rowOff = y * rowBytes;
                int prevOff = (y - 1) * rowBytes;
                int dstScanOff = y * (1 + rowBytes);
                long bestScore = long.MaxValue;

                for (int f = 0; f < 5; f++)
                {
                    if (y == 0 && f == 2) continue;
                    long score = 0;
                    if (f == 0)
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            byte v = pixels[rowOff + i];
                            cand[i] = v;
                            score += v < 128 ? v : 256 - v;
                        }
                    }
                    else if (f == 1)
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            int left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
                            byte v = (byte)(pixels[rowOff + i] - left);
                            cand[i] = v;
                            score += v < 128 ? v : 256 - v;
                        }
                    }
                    else if (f == 2)
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            byte v = (byte)(pixels[rowOff + i] - pixels[prevOff + i]);
                            cand[i] = v;
                            score += v < 128 ? v : 256 - v;
                        }
                    }
                    else if (f == 3)
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            int left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
                            int up = y > 0 ? pixels[prevOff + i] : 0;
                            byte v = (byte)(pixels[rowOff + i] - ((left + up) >> 1));
                            cand[i] = v;
                            score += v < 128 ? v : 256 - v;
                        }
                    }
                    else
                    {
                        for (int i = 0; i < rowBytes; i++)
                        {
                            int left = i >= bpp ? pixels[rowOff + i - bpp] : 0;
                            int up = y > 0 ? pixels[prevOff + i] : 0;
                            int upLeft = y > 0 && i >= bpp ? pixels[prevOff + i - bpp] : 0;
                            byte v = (byte)(pixels[rowOff + i] - PaethPredictor(left, up, upLeft));
                            cand[i] = v;
                            score += v < 128 ? v : 256 - v;
                        }
                    }

                    if (score < bestScore)
                    {
                        bestScore = score;
                        rawScanlines[dstScanOff] = (byte)f;
                        Buffer.BlockCopy(cand, 0, rawScanlines, dstScanOff + 1, rowBytes);
                    }
                }
            }

            byte[] compressed = ZlibDeflate(rawScanlines);
            var ihdr = new byte[13];
            WriteUInt32BE(ihdr, 0, (uint)width);
            WriteUInt32BE(ihdr, 4, (uint)height);
            ihdr[8] = 8;
            ihdr[9] = (byte)(channels == 4 ? 6 : 2);
            ihdr[10] = 0;
            ihdr[11] = 0;
            ihdr[12] = 0;

            var output = new byte[8 + 25 + 12 + compressed.Length + 12];
            Buffer.BlockCopy(PngSignature, 0, output, 0, 8);
            int cursor = 8;
            cursor = WritePngChunk(output, cursor, "IHDR", ihdr);
            cursor = WritePngChunk(output, cursor, "IDAT", compressed);
            WritePngChunk(output, cursor, "IEND", Array.Empty<byte>());
            return output;
        }

        private static (double H, double L, double S) RgbToHlsF64(double r, double g, double b)
        {
            double maxc = Math.Max(r, Math.Max(g, b));
            double minc = Math.Min(r, Math.Min(g, b));
            double sumc = maxc + minc;
            double rangec = maxc - minc;
            double l = sumc / 2.0;
            if (minc == maxc) return (0.0, l, 0.0);
            double s = l <= 0.5 ? rangec / sumc : rangec / (2.0 - maxc - minc);
            double rc = (maxc - r) / rangec;
            double gc = (maxc - g) / rangec;
            double bc = (maxc - b) / rangec;
            double h;
            if (r == maxc)
            {
                h = bc - gc;
            }
            else if (g == maxc)
            {
                h = 2.0 + rc - bc;
            }
            else
            {
                h = 4.0 + gc - rc;
            }
            h = (h / 6.0) % 1.0;
            if (h < 0.0) h += 1.0;
            return (h, l, s);
        }

        internal static void ColorizePixelsImpl(
            byte[] rgba,
            byte[] dst,
            int width,
            int height,
            (int R, int G, int B) targetRgb,
            int colorizeType,
            bool keepAlpha,
            int maxBlockPixels = MaxColorizeBlockPixels)
        {
            if (width <= 0 || height <= 0) return;
            int nPx = width * height;
            int outChannels = keepAlpha ? 4 : 3;

            ulong sumR = 0;
            ulong sumG = 0;
            ulong sumB = 0;
            for (int i = 0; i < nPx; i++)
            {
                int p4 = i * 4;
                sumR += rgba[p4];
                sumG += rgba[p4 + 1];
                sumB += rgba[p4 + 2];
            }
            ulong uPx = (ulong)nPx;
            double avgR = (double)(sumR / uPx) / 255.0;
            double avgG = (double)(sumG / uPx) / 255.0;
            double avgB = (double)(sumB / uPx) / 255.0;
            var (avgH, avgL, avgS) = RgbToHlsF64(avgR, avgG, avgB);
            var (tgtH, tgtL, tgtS) = RgbToHlsF64(
                targetRgb.R / 255.0,
                targetRgb.G / 255.0,
                targetRgb.B / 255.0);

            bool isShift = colorizeType == 0;
            float deltaH = (float)(tgtH - avgH);
            float deltaL = (float)(tgtL - avgL);
            float deltaS = (float)(tgtS == 0.0 && avgS > 0.0 ? -1.0 : tgtS - avgS);
            float tgtHF = (float)tgtH;

            const float OneSixth = 1.0f / 6.0f;
            const float Half = 0.5f;
            const float One = 1.0f;
            const float Two = 2.0f;
            const float Four = 4.0f;
            const float Six = 6.0f;
            const float MinL = 0.01f;
            const float UndefH = -1.0f / 360.0f;
            const float Eps = 1e-6f;
            const float Scale255 = 255.0f;
            const float Zero = 0.0f;

            int effBlock = Math.Max(1, maxBlockPixels);
            int blockRows = Math.Min(height, Math.Max(1, effBlock / width));

            for (int y0 = 0; y0 < height; y0 += blockRows)
            {
                int y1 = Math.Min(height, y0 + blockRows);
                int pStart = y0 * width;
                int pEnd = y1 * width;
                for (int p = pStart; p < pEnd; p++)
                {
                    int p4 = p * 4;
                    byte srcR = rgba[p4];
                    byte srcG = rgba[p4 + 1];
                    byte srcB = rgba[p4 + 2];
                    byte srcA = rgba[p4 + 3];

                    float r = srcR / Scale255;
                    float g = srcG / Scale255;
                    float b = srcB / Scale255;

                    float cm = Math.Max(r, Math.Max(g, b));
                    float cn = Math.Min(r, Math.Min(g, b));
                    float rg = cm - cn;
                    float sum = cm + cn;
                    float lt = sum * Half;
                    bool nz = rg > Eps;
                    float st = Zero;
                    if (nz)
                    {
                        float denom = lt <= Half ? sum : (Two - sum);
                        st = rg / denom;
                    }

                    float nh = tgtHF;
                    if (isShift)
                    {
                        if (!nz)
                        {
                            nh = UndefH;
                        }
                        else
                        {
                            if (r == cm)
                            {
                                nh = (g - b) / rg;
                            }
                            else if (g == cm)
                            {
                                nh = ((b - r) / rg) + Two;
                            }
                            else
                            {
                                nh = ((r - g) / rg) + Four;
                            }
                            nh *= OneSixth;
                            nh -= (float)Math.Floor(nh);
                        }
                        nh += deltaH;
                        nh -= (float)Math.Floor(nh);
                    }

                    lt += deltaL;
                    if (lt < MinL) lt = MinL;
                    else if (lt > One) lt = One;

                    st += deltaS;
                    if (st < Zero) st = Zero;
                    else if (st > One) st = One;

                    float m2 = lt > Half ? (lt + (st * (One - lt))) : (lt + (lt * st));
                    float m1 = (lt * Two) - m2;
                    float span = m2 - m1;
                    bool achromatic = st <= Eps;

                    int outOff = p * outChannels;
                    for (int ch = 0; ch < 3; ch++)
                    {
                        float t1 = nh;
                        float off = HueChannelOffsets[ch];
                        if (off != Zero)
                        {
                            t1 += off;
                            t1 -= (float)Math.Floor(t1);
                        }
                        t1 = (t1 * Six) - Two;
                        t1 = Two - Math.Abs(t1);
                        if (t1 < Zero) t1 = Zero;
                        else if (t1 > One) t1 = One;

                        float val = lt;
                        if (!achromatic)
                        {
                            val = t1 == One ? m2 : ((t1 * span) + m1);
                        }
                        val *= Scale255;
                        if (val < Zero) val = Zero;
                        else if (val > Scale255) val = Scale255;
                        dst[outOff + ch] = (byte)(int)val;
                    }
                    if (keepAlpha)
                    {
                        dst[outOff + 3] = srcA;
                    }
                }
            }
        }

        internal static byte[] ColorizeImageU8(
            byte[] rgba,
            int width,
            int height,
            (int R, int G, int B) targetRgb,
            int colorizeType,
            bool keepAlpha = true,
            int maxBlockPixels = MaxColorizeBlockPixels)
        {
            if (width <= 0 || height <= 0) return Array.Empty<byte>();
            int outChannels = keepAlpha ? 4 : 3;
            var output = new byte[width * height * outChannels];
            ColorizePixelsImpl(rgba, output, width, height, targetRgb, colorizeType, keepAlpha, maxBlockPixels);
            return output;
        }

        private static double SrgbToLinear(double c)
        {
            if (c <= 0.0) return 0.0;
            if (c >= 1.0) return 1.0;
            return c <= 0.04045 ? c / 12.92 : Math.Pow((c + 0.055) / 1.055, 2.4);
        }

        private static Dictionary<string, object>? ToObjectDictionary(object? value)
        {
            if (value == null) return null;
            if (value is IDictionary<string, object> dict)
            {
                return new Dictionary<string, object>(dict);
            }
            var reflected = new Dictionary<string, object>();
            foreach (var prop in value.GetType().GetProperties(BindingFlags.Public | BindingFlags.Instance))
            {
                var propVal = prop.GetValue(value);
                if (propVal != null) reflected[prop.Name] = propVal;
            }
            return reflected.Count > 0 ? reflected : null;
        }

        private static bool TryReadFactorComponents(object? factorObj, out double r, out double g, out double b, out double a)
        {
            r = 1.0;
            g = 1.0;
            b = 1.0;
            a = 1.0;
            if (factorObj is double[] arr && arr.Length >= 3)
            {
                r = arr[0];
                g = arr[1];
                b = arr[2];
                a = arr.Length > 3 ? arr[3] : 1.0;
                return true;
            }
            if (factorObj is IEnumerable enumerable && factorObj is not string)
            {
                var vals = new List<double>(4);
                foreach (var item in enumerable)
                {
                    vals.Add(Convert.ToDouble(item, CultureInfo.InvariantCulture));
                }
                if (vals.Count >= 3)
                {
                    r = vals[0];
                    g = vals[1];
                    b = vals[2];
                    a = vals.Count > 3 ? vals[3] : 1.0;
                    return true;
                }
            }
            return false;
        }

        private static double[]? TryLinearizeFactor(object? factorObj)
        {
            if (!TryReadFactorComponents(factorObj, out double r, out double g, out double b, out double a))
            {
                return null;
            }
            return new[]
            {
                SrgbToLinear(r),
                SrgbToLinear(g),
                SrgbToLinear(b),
                a,
            };
        }

        private static bool TryGetColorizeInfo(IDictionary<string, object> mat, out int colorizeType)
        {
            colorizeType = 0;
            if (!mat.TryGetValue("extras", out var extrasObj)) return false;
            var extras = ToObjectDictionary(extrasObj);
            if (extras == null || !extras.TryGetValue("sketchup", out var skpObj)) return false;
            var skp = ToObjectDictionary(skpObj);
            if (skp == null || !skp.TryGetValue("colorized", out var colObj) || colObj is not bool isCol || !isCol)
            {
                return false;
            }
            if (skp.TryGetValue("colorize_type", out var ctObj) && ctObj != null)
            {
                colorizeType = Convert.ToInt32(ctObj, CultureInfo.InvariantCulture);
            }
            return true;
        }

        private static int ClampByte(int value)
        {
            if (value < 0) return 0;
            return value > 255 ? 255 : value;
        }

        private static (int R, int G, int B) QuantizeTargetRgb(IDictionary<string, object> pbr)
        {
            if (pbr.TryGetValue("baseColorFactor", out var bcfObj) &&
                TryReadFactorComponents(bcfObj, out double fr, out double fg, out double fb, out _))
            {
                return (
                    ClampByte((int)Math.Round(fr * 255.0, MidpointRounding.AwayFromZero)),
                    ClampByte((int)Math.Round(fg * 255.0, MidpointRounding.AwayFromZero)),
                    ClampByte((int)Math.Round(fb * 255.0, MidpointRounding.AwayFromZero)));
            }
            return (255, 255, 255);
        }

        private static object LinearizeMaterialWithoutTexture(object mat, bool stripTexture, bool stripMask)
        {
            var newMat = ToObjectDictionary(mat);
            if (newMat == null || !newMat.TryGetValue("pbrMetallicRoughness", out var pbrObj))
            {
                return mat;
            }
            var newPbr = ToObjectDictionary(pbrObj);
            if (newPbr == null) return mat;
            if (!stripTexture && newPbr.ContainsKey("baseColorTexture")) return mat;

            if (stripTexture) newPbr.Remove("baseColorTexture");
            if (newPbr.TryGetValue("baseColorFactor", out var bcfObj))
            {
                var linearized = TryLinearizeFactor(bcfObj);
                if (linearized != null) newPbr["baseColorFactor"] = linearized;
            }
            newMat["pbrMetallicRoughness"] = newPbr;
            if (stripMask && newMat.TryGetValue("alphaMode", out var am) && am as string == "MASK")
            {
                newMat.Remove("alphaMode");
            }
            return newMat;
        }

        private sealed class DecodedEntry
        {
            public int Width { get; set; }
            public int Height { get; set; }
            public byte[] Rgba { get; set; } = Array.Empty<byte>();
            public bool OwnedBuffer { get; set; }
        }

        internal static (List<SceneTexture> Textures, List<object> Materials) ResolveGltfTextures(
            List<SceneTexture> rawTextures,
            List<object> rawMaterials,
            bool embed,
            Action<SkpLogLevel, string>? onLog,
            ISkpImageCodec? imageCodec = null,
            GltfTextureBakeStats? stats = null)
        {
            if (!embed || rawTextures.Count == 0)
            {
                var stripped = new List<object>(rawMaterials.Count);
                foreach (var m in rawMaterials)
                {
                    stripped.Add(LinearizeMaterialWithoutTexture(m, stripTexture: true, stripMask: false));
                }
                return (new List<SceneTexture>(), stripped);
            }

            var warnedUnsupported = new HashSet<(string BaseName, string Format)>();
            for (int i = 0; i < rawTextures.Count; i++)
            {
                var tex = rawTextures[i];
                if (tex.MimeType != "image/png" && tex.MimeType != "image/jpeg")
                {
                    string filename = tex.Filename ?? "";
                    string normalized = filename.Replace('\\', '/');
                    int slash = normalized.LastIndexOf('/');
                    string baseName = slash >= 0 ? normalized.Substring(slash + 1) : normalized;
                    string fmt = FaceGroups.DescribeImageFormat(tex.Data, filename);
                    if (warnedUnsupported.Add((baseName, fmt)))
                    {
                        string label = baseName.Length > 0 ? $" '{baseName}'" : "";
                        onLog?.Invoke(
                            SkpLogLevel.Warning,
                            $"Texture{label} is {fmt}, which glTF 2.0 cannot carry (only PNG and JPEG): leaving it out of the GLB");
                    }
                }
            }

            var uniqueColorizeKeys = new HashSet<(int TexIdx, int ColorizeType, int R, int G, int B)>();
            var remainingVariants = new Dictionary<int, int>();
            foreach (var rawMat in rawMaterials)
            {
                var dict = rawMat as IDictionary<string, object> ?? ToObjectDictionary(rawMat);
                if (dict == null || !dict.TryGetValue("pbrMetallicRoughness", out var pbrObj)) continue;
                var pbr = pbrObj as IDictionary<string, object> ?? ToObjectDictionary(pbrObj);
                if (pbr == null || !pbr.TryGetValue("baseColorTexture", out var texRefObj)) continue;
                var texRef = texRefObj as IDictionary<string, object> ?? ToObjectDictionary(texRefObj);
                if (texRef == null || !texRef.TryGetValue("index", out var idxObj) || idxObj is not int texIdx) continue;
                if (texIdx < 0 || texIdx >= rawTextures.Count) continue;
                var tex = rawTextures[texIdx];
                if (tex.MimeType != "image/png" && tex.MimeType != "image/jpeg") continue;
                if (tex.MimeType == "image/jpeg" && imageCodec == null) continue;
                if (!TryGetColorizeInfo(dict, out int ctype)) continue;
                var (r, g, b) = QuantizeTargetRgb(pbr);
                if (uniqueColorizeKeys.Add((texIdx, ctype, r, g, b)))
                {
                    remainingVariants[texIdx] = remainingVariants.TryGetValue(texIdx, out int cur) ? cur + 1 : 1;
                }
            }

            var outTextures = new List<SceneTexture>(rawTextures.Count);
            var plainByTex = new Dictionary<int, int>();
            var bakedByKey = new Dictionary<(int TexIdx, int ColorizeType, int R, int G, int B), (int Index, bool Baked)>();
            var decodedByTex = new Dictionary<int, DecodedEntry?>();
            bool warnedNoJpegCodec = false;

            int GetPlainTextureIndex(int texIdx)
            {
                if (plainByTex.TryGetValue(texIdx, out int cachedIdx)) return cachedIdx;
                var tex = rawTextures[texIdx];
                byte[] bytes = tex.MimeType == "image/jpeg" ? StripJpegExifApp1(tex.Data) : tex.Data;
                int gltfIdx = outTextures.Count;
                outTextures.Add(new SceneTexture
                {
                    Data = bytes,
                    MimeType = tex.MimeType,
                    Filename = tex.Filename,
                });
                plainByTex[texIdx] = gltfIdx;
                return gltfIdx;
            }

            DecodedEntry? GetDecoded(int texIdx)
            {
                if (decodedByTex.TryGetValue(texIdx, out var existing)) return existing;
                if (stats != null) stats.DecodeCalls++;
                var tex = rawTextures[texIdx];
                DecodedEntry? entry;
                try
                {
                    if (tex.MimeType == "image/png")
                    {
                        var (w, h, rgba) = DecodePngRgba(tex.Data);
                        entry = new DecodedEntry { Width = w, Height = h, Rgba = rgba, OwnedBuffer = true };
                    }
                    else
                    {
                        if (!TryReadJpegDimensions(tex.Data, out int hdrW, out int hdrH))
                        {
                            throw new InvalidDataException("missing or corrupt JPEG SOF header");
                        }
                        CheckPixelLimit((ulong)hdrW, (ulong)hdrH);
                        var dec = imageCodec!.Decode(tex.Data, tex.MimeType);
                        if (!dec.HasValue)
                        {
                            throw new InvalidDataException("ImageCodec.Decode returned null");
                        }
                        var (dw, dh, drgba) = dec.Value;
                        if (dw <= 0 || dh <= 0)
                        {
                            throw new InvalidDataException(
                                string.Format(CultureInfo.InvariantCulture, "invalid image dimensions {0}x{1}", dw, dh));
                        }
                        CheckPixelLimit((ulong)dw, (ulong)dh);
                        int expectedLen = dw * dh * 4;
                        if (drgba == null || drgba.Length < expectedLen)
                        {
                            int actualLen = drgba?.Length ?? 0;
                            throw new InvalidDataException(
                                string.Format(
                                    CultureInfo.InvariantCulture,
                                    "ImageCodec.Decode returned {0} bytes for {1}x{2} RGBA (expected {3})",
                                    actualLen,
                                    dw,
                                    dh,
                                    expectedLen));
                        }
                        entry = new DecodedEntry { Width = dw, Height = dh, Rgba = drgba, OwnedBuffer = false };
                    }
                }
                catch (Exception ex)
                {
                    string namePart = !string.IsNullOrEmpty(tex.Filename) ? $" ('{tex.Filename}')" : "";
                    onLog?.Invoke(
                        SkpLogLevel.Warning,
                        $"Failed to decode texture {texIdx}{namePart} for GLB export: {ex.Message}");
                    entry = null;
                }
                decodedByTex[texIdx] = entry;
                return entry;
            }

            var materials = new List<object>(rawMaterials.Count);
            foreach (var rawMat in rawMaterials)
            {
                var dict = rawMat as IDictionary<string, object> ?? ToObjectDictionary(rawMat);
                if (dict == null || !dict.TryGetValue("pbrMetallicRoughness", out var pbrObj))
                {
                    materials.Add(rawMat);
                    continue;
                }
                var pbr = pbrObj as IDictionary<string, object> ?? ToObjectDictionary(pbrObj);
                if (pbr == null)
                {
                    materials.Add(rawMat);
                    continue;
                }
                if (!pbr.TryGetValue("baseColorTexture", out var texRefObj))
                {
                    materials.Add(LinearizeMaterialWithoutTexture(rawMat, stripTexture: false, stripMask: false));
                    continue;
                }
                var texRef = texRefObj as IDictionary<string, object> ?? ToObjectDictionary(texRefObj);
                if (texRef == null || !texRef.TryGetValue("index", out var idxObj) || idxObj is not int texIdx)
                {
                    materials.Add(LinearizeMaterialWithoutTexture(rawMat, stripTexture: false, stripMask: false));
                    continue;
                }

                var tex = texIdx >= 0 && texIdx < rawTextures.Count ? rawTextures[texIdx] : null;
                if (tex == null || (tex.MimeType != "image/png" && tex.MimeType != "image/jpeg"))
                {
                    materials.Add(LinearizeMaterialWithoutTexture(rawMat, stripTexture: true, stripMask: true));
                    continue;
                }

                double alpha = 1.0;
                if (pbr.TryGetValue("baseColorFactor", out var bcfObj) &&
                    TryReadFactorComponents(bcfObj, out _, out _, out _, out double parsedAlpha))
                {
                    alpha = parsedAlpha;
                }

                if (!TryGetColorizeInfo(dict, out int ctype))
                {
                    int mapped = GetPlainTextureIndex(texIdx);
                    var newPbr = new Dictionary<string, object>(pbr)
                    {
                        ["baseColorFactor"] = new[] { 1.0, 1.0, 1.0, alpha },
                    };
                    if (mapped != texIdx)
                    {
                        newPbr["baseColorTexture"] = new Dictionary<string, object>(texRef) { ["index"] = mapped };
                    }
                    materials.Add(new Dictionary<string, object>(dict) { ["pbrMetallicRoughness"] = newPbr });
                    continue;
                }

                if (tex.MimeType == "image/jpeg" && imageCodec == null)
                {
                    if (!warnedNoJpegCodec)
                    {
                        warnedNoJpegCodec = true;
                        onLog?.Invoke(
                            SkpLogLevel.Warning,
                            "No ImageCodec provided; colorized JPEG textures in GLB export stay untinted with their material factor (pass ImageCodec in GlbOptions to bake colorized JPEGs).");
                    }
                    int mapped = GetPlainTextureIndex(texIdx);
                    if (mapped == texIdx)
                    {
                        materials.Add(rawMat);
                    }
                    else
                    {
                        var newPbr = new Dictionary<string, object>(pbr)
                        {
                            ["baseColorTexture"] = new Dictionary<string, object>(texRef) { ["index"] = mapped },
                        };
                        materials.Add(new Dictionary<string, object>(dict) { ["pbrMetallicRoughness"] = newPbr });
                    }
                    continue;
                }

                var (r, g, b) = QuantizeTargetRgb(pbr);
                var ckey = (texIdx, ctype, r, g, b);
                if (!bakedByKey.TryGetValue(ckey, out var resolved))
                {
                    int rem = (remainingVariants.TryGetValue(texIdx, out int curRem) ? curRem : 1) - 1;
                    remainingVariants[texIdx] = Math.Max(0, rem);

                    var decoded = GetDecoded(texIdx);
                    if (decoded == null)
                    {
                        resolved = (GetPlainTextureIndex(texIdx), false);
                        bakedByKey[ckey] = resolved;
                    }
                    else
                    {
                        try
                        {
                            if (stats != null) stats.ColorizeCalls++;
                            int nPx = decoded.Width * decoded.Height;
                            byte[] encoded;
                            if (tex.MimeType == "image/jpeg")
                            {
                                byte[] bakedRgba = ColorizeImageU8(
                                    decoded.Rgba,
                                    decoded.Width,
                                    decoded.Height,
                                    (r, g, b),
                                    ctype,
                                    keepAlpha: true);
                                if (rem <= 0) decodedByTex.Remove(texIdx);
                                encoded = imageCodec!.Encode(bakedRgba, decoded.Width, decoded.Height, "image/jpeg", 95);
                                if (encoded == null || encoded.Length == 0)
                                {
                                    throw new InvalidDataException("ImageCodec.Encode returned empty output");
                                }
                            }
                            else
                            {
                                bool keepAlpha = false;
                                byte[] rawRgba = decoded.Rgba;
                                for (int p = 0; p < nPx; p++)
                                {
                                    if (rawRgba[p * 4 + 3] < 255)
                                    {
                                        keepAlpha = true;
                                        break;
                                    }
                                }
                                int outCh = keepAlpha ? 4 : 3;
                                if (rem <= 0 && decoded.OwnedBuffer)
                                {
                                    ColorizePixelsImpl(
                                        rawRgba,
                                        rawRgba,
                                        decoded.Width,
                                        decoded.Height,
                                        (r, g, b),
                                        ctype,
                                        keepAlpha);
                                    encoded = EncodePngU8(rawRgba, decoded.Width, decoded.Height, outCh);
                                    decodedByTex.Remove(texIdx);
                                }
                                else
                                {
                                    byte[] baked = ColorizeImageU8(
                                        rawRgba,
                                        decoded.Width,
                                        decoded.Height,
                                        (r, g, b),
                                        ctype,
                                        keepAlpha);
                                    encoded = EncodePngU8(baked, decoded.Width, decoded.Height, outCh);
                                }
                            }

                            int gltfIdx = outTextures.Count;
                            outTextures.Add(new SceneTexture
                            {
                                Data = encoded,
                                MimeType = tex.MimeType,
                                Filename = tex.Filename,
                            });
                            resolved = (gltfIdx, true);
                            bakedByKey[ckey] = resolved;
                        }
                        catch (Exception ex)
                        {
                            string namePart = !string.IsNullOrEmpty(tex.Filename) ? $" ('{tex.Filename}')" : "";
                            onLog?.Invoke(
                                SkpLogLevel.Warning,
                                $"Failed to encode baked texture {texIdx}{namePart} for GLB export: {ex.Message}");
                            resolved = (GetPlainTextureIndex(texIdx), false);
                            bakedByKey[ckey] = resolved;
                        }
                    }
                }

                if (!resolved.Baked)
                {
                    if (resolved.Index == texIdx)
                    {
                        materials.Add(rawMat);
                    }
                    else
                    {
                        var fallbackPbr = new Dictionary<string, object>(pbr)
                        {
                            ["baseColorTexture"] = new Dictionary<string, object>(texRef) { ["index"] = resolved.Index },
                        };
                        materials.Add(new Dictionary<string, object>(dict) { ["pbrMetallicRoughness"] = fallbackPbr });
                    }
                    continue;
                }

                var bakedPbr = new Dictionary<string, object>(pbr)
                {
                    ["baseColorFactor"] = new[] { 1.0, 1.0, 1.0, alpha },
                };
                if (resolved.Index != texIdx)
                {
                    bakedPbr["baseColorTexture"] = new Dictionary<string, object>(texRef) { ["index"] = resolved.Index };
                }
                materials.Add(new Dictionary<string, object>(dict) { ["pbrMetallicRoughness"] = bakedPbr });
            }

            return (outTextures, materials);
        }
    }
}
