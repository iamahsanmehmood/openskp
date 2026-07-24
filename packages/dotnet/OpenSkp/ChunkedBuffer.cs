using System;
using System.IO;

namespace OpenSkp
{
    /// <summary>A byte buffer backed by multiple fixed-size byte[] segments
    /// instead of one contiguous array.
    ///
    /// .NET caps a single array at ~2.1 GB (Int32-indexed), and
    /// SketchUp's model.dat routinely decompresses to several times the
    /// compressed file size (real production files observed at ~10x) - a
    /// 300 MB+ .skp file's model.dat can exceed that cap outright, which a
    /// single byte[] or MemoryStream simply cannot hold regardless of GC
    /// settings ("Stream was too long" / OutOfMemoryException). This class
    /// lets the TLV parser address arbitrarily large decompressed data by
    /// long offset while each individual backing segment stays within the
    /// CLR's per-array limit.</summary>
    internal sealed class ChunkedBuffer
    {
        private readonly byte[][] _chunks;
        private readonly int _chunkSize;
        public long Length { get; }

        private ChunkedBuffer(byte[][] chunks, int chunkSize, long length)
        {
            _chunks = chunks;
            _chunkSize = chunkSize;
            Length = length;
        }

        public byte this[long index]
        {
            get
            {
                int chunkIdx = (int)(index / _chunkSize);
                int offsetInChunk = (int)(index % _chunkSize);
                return _chunks[chunkIdx][offsetInChunk];
            }
        }

        /// <summary>Copy `count` bytes starting at `offset` into a freshly
        /// allocated array. Payload slices are always small (leaf TLV
        /// records - vertex coordinates, flags, entity IDs), so this never
        /// needs to be more than a few KB even for multi-GB buffers.</summary>
        public byte[] Slice(long offset, int count)
        {
            var result = new byte[count];
            CopyTo(offset, result, 0, count);
            return result;
        }

        public void CopyTo(long offset, byte[] dest, int destOffset, int count)
        {
            long remaining = count;
            long pos = offset;
            int destPos = destOffset;
            while (remaining > 0)
            {
                int chunkIdx = (int)(pos / _chunkSize);
                int offsetInChunk = (int)(pos % _chunkSize);
                int available = _chunkSize - offsetInChunk;
                int take = (int)Math.Min(available, remaining);
                Array.Copy(_chunks[chunkIdx], offsetInChunk, dest, destPos, take);
                pos += take;
                destPos += take;
                remaining -= take;
            }
        }

        private readonly byte[] _scratch8 = new byte[8];

        public ushort ReadU16(long offset)
        {
            CopyTo(offset, _scratch8, 0, 2);
            return (ushort)(_scratch8[0] | (_scratch8[1] << 8));
        }

        public uint ReadU32(long offset)
        {
            CopyTo(offset, _scratch8, 0, 4);
            return (uint)(_scratch8[0] | (_scratch8[1] << 8) | (_scratch8[2] << 16) | (_scratch8[3] << 24));
        }

        /// <summary>Read the whole buffer, chunk by chunk, from a stream
        /// whose total decompressed length is already known (from the ZIP
        /// entry's recorded uncompressed size) - so each chunk can be
        /// allocated at its exact final size, no growth/reallocation.</summary>
        public static ChunkedBuffer FromStream(Stream source, long totalLength, int chunkSize = 512 * 1024 * 1024)
        {
            if (totalLength == 0)
            {
                return new ChunkedBuffer(Array.Empty<byte[]>(), chunkSize, 0);
            }
            int chunkCount = (int)((totalLength + chunkSize - 1) / chunkSize);
            var chunks = new byte[chunkCount][];
            long remaining = totalLength;
            for (int i = 0; i < chunkCount; i++)
            {
                int thisSize = (int)Math.Min(chunkSize, remaining);
                var buf = new byte[thisSize];
                int readTotal = 0;
                while (readTotal < thisSize)
                {
                    int n = source.Read(buf, readTotal, thisSize - readTotal);
                    if (n <= 0)
                    {
                        throw new EndOfStreamException("ZIP entry ended before its declared length");
                    }
                    readTotal += n;
                }
                chunks[i] = buf;
                remaining -= thisSize;
            }
            return new ChunkedBuffer(chunks, chunkSize, totalLength);
        }

        /// <summary>Wrap a single already-in-memory array (small XML/style
        /// entries don't need chunking).</summary>
        public static ChunkedBuffer FromArray(byte[] data)
        {
            return new ChunkedBuffer(data.Length == 0 ? Array.Empty<byte[]>() : new[] { data }, Math.Max(data.Length, 1), data.Length);
        }
    }
}
