"""Dump VFF container / model.dat record structure for research."""
import io, struct, sys, zipfile, hashlib

CONTAINERS = {
    0x01f4, 0x01f5, 0x01f6, 0x01f7, 0x01f8, 0x01f9, 0x01fa, 0x01fb,
    0x01fc, 0x01fd, 0x01fe, 0x01ff, 0x0200, 0x0203, 0x0204, 0x0205,
    0x0206, 0x0207, 0x0208, 0x0209, 0x020b, 0x020e, 0x0210, 0x0211,
    0x0215, 0x0218,
    # sections' inner containers
    0x03e8, 0x34bc, 0x733c, 0x6590, 0x4650, 0x4651, 0x4652, 0x4653,
    0x4654, 0x4074, 0x4075, 0x4076, 0x7d00, 0x7d01, 0x7d0a, 0x30d4,
    0x30d5, 0x32c8, 0x30d6, 0x3a98, 0x3a99, 0x3c8c, 0x3a9a, 0x3a9b,
    0x3e80, 0x4e20, 0x4e21, 0x5014, 0x57e4, 0x5fb4, 0x2cec, 0x2ced,
    0x2ee0, 0x1770, 0x1771, 0x157c, 0x6978, 0x6d60, 0x6784, 0x7918,
    0x1b58, 0x05dc, 0x07d0, 0x07d1, 0x07d2, 0x07d3, 0x05dd, 0x05de,
    0x1388, 0x1389, 0x09c4, 0x09c5, 0x138a, 0x0bb8, 0x0bb9, 0x0bba,
    0x0bbb, 0x138b, 0x0dac, 0x0dad, 0x0dae, 0x0daf, 0x138c, 0x1964,
    0x1965, 0x1966, 0x1967, 0x1968, 0x1969, 0x138d, 0x1390, 0x13a0,
    0x0dae, 0x1194, 0x1195, 0x0fa0, 0x0fa1, 0x0fa2, 0x0dae,
    0x138e, 0x139f, 0x139b, 0x639f, 0x63a0, 0x639c, 0x639d, 0x639e,
    0x36b1, 0x36b2, 0x36b3, 0x36b4, 0x36b5, 0x36b6, 0x38a4, 0x38a7,
    0x38a8, 0x38a9, 0x38aa, 0x38ab, 0x38ac, 0x38ad, 0x38ae, 0x61ac,
    0x61ad, 0x32ca, 0x32cb, 0x32cc, 0x32cd, 0x32ce, 0x32cf, 0x3c8d,
    0x3c8e, 0x3c8f, 0x3c90, 0x3c91, 0x251c, 0x251d, 0x251e, 0x2328,
    0x2329, 0x232a, 0x232c, 0x7d82, 0x7d83, 0x7d84, 0x1585, 0x157d,
    0x157e, 0x157f, 0x1580, 0x1581, 0x1582, 0x1583, 0x1588,
}

def hdr(data, pos):
    return struct.unpack_from('<HI', data, pos)

def dump(data, start, end, depth, out, maxdepth=99, tagfilter=None):
    pos = start
    while pos <= end - 6:
        tag, size = hdr(data, pos)
        if pos + 6 + size > end:
            out.append('  '*depth + f'!! truncated at {pos}: tag {tag:04x} size {size} (end {end})')
            return
        payload = data[pos+6:pos+6+size]
        if depth < maxdepth and (tag in CONTAINERS or tagfilter and tagfilter(tag, payload)):
            out.append('  '*depth + f'{tag:04x} [{size}]')
            dump(data, pos+6, pos+6+size, depth+1, out)
        else:
            out.append('  '*depth + f'{tag:04x} [{size}] {payload[:24].hex()}' + (' | ' + repr(payload[:40]) if size and all(32<=b<127 for b in payload[:min(size,40)]) else ''))
        pos += 6 + size
    if pos != end and end-pos >= 6:
        out.append('  '*depth + f'trailing {end-pos} bytes')

def main(path, mode='tree'):
    raw = open(path,'rb').read()
    print(f'file {path} size {len(raw)} sha {hashlib.sha256(raw).hexdigest()[:16]}')
    print('header:', raw[:0x41].hex())
    # version string
    z = raw.find(b'PK\x03\x04')
    print('zip offset', z, 'header len', z)
    zf = zipfile.ZipFile(io.BytesIO(raw[z:]))
    print('entries:')
    for i in zf.infolist():
        print(f'  {i.filename:60s} method={i.compress_type} flag={i.flag_bits:#06x} date={i.date_time} extralen={len(i.extra)} crc={i.CRC:08x} size={i.file_size}/{i.compress_size}')
    print('comment', zf.comment)
    if mode == 'meta':
        m = zf.read('meta/meta.dat')
        print('meta len', len(m), 'last4', m[-4:].hex())
        print('meta hex', m.hex())
        return
    model = zf.read('model.dat')
    print('model.dat len', len(model))
    out=[]
    dump(model, 0, len(model), 0, out, maxdepth=int(sys.argv[3]) if len(sys.argv)>3 else 99)
    print('\n'.join(out))

if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2] if len(sys.argv)>2 else 'tree')
