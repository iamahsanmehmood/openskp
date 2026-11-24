"""Extract and recursively dump selected top-level model.dat sections."""
import io, struct, sys, zipfile

CONTAINERS = {
 0x01f4,0x01f5,0x01f6,0x01f7,0x01f8,0x01f9,0x01fa,0x01fb,0x01fc,0x01fd,
 0x01fe,0x01ff,0x0200,0x0203,0x0204,0x0205,0x0206,0x0207,0x0208,0x0209,
 0x020e,0x0210,0x0211,0x0215,0x0218,0x03e8,0x34bc,0x733c,0x6590,0x4650,
 0x4074,0x4075,0x4076,0x7d00,0x7d01,0x7d0a,0x30d4,0x30d5,0x32c8,0x3a98,
 0x3a99,0x3c8c,0x3a9b,0x3e80,0x4e20,0x4e21,0x5014,0x57e4,0x5fb4,0x2cec,
 0x2ced,0x2ee0,0x1770,0x1771,0x157c,0x6978,0x6d60,0x6784,0x7918,0x1b58,
 0x05dc,0x07d0,0x05dd,0x1388,0x1389,0x09c4,0x138a,0x0bb8,0x138b,0x0dac,
 0x0dae,0x1194,0x1195,0x0fa0,0x138c,0x1964,0x138d,0x1390,0x1391,0x1392,
 0x1393,0x1395,0x1396,0x1397,0x1398,0x1399,0x139a,0x639f,0x63a0,0x639c,
 0x36b1,0x36b2,0x36b3,0x36b5,0x38a4,0x61ac,0x61ad,0x251c,0x251d,0x251e,
 0x2328,0x1585,0x7d82,0x0217,0x1396,0x1397,0x4a38,0x4c2c,0x4c2d,0x1d4c,0x2710,0x2711,0x2712,0x2713,0x2714,0x2715,0x2716,0x2717,0x2718,0x2719,0x271a,0x36b0,0x36b4,0x36b6,0x36b7,0x38ae,0x38aa,0x38a9,0x38a7,0x38ad,0x38af,0x38b4,0x38b5,0x38a7,0x38a9,0x38aa,0x38ad,0x38ae,
}

def recs(data, start, end):
    pos = start
    while pos <= end - 6:
        tag, size = struct.unpack_from('<HI', data, pos)
        if pos + 6 + size > end:
            raise ValueError(f'truncated at {pos}: {tag:04x} {size} end {end}')
        yield tag, data[pos+6:pos+6+size], pos
        pos += 6 + size

def dump(data, start, end, depth, out, skip=()):
    for tag, payload, pos in recs(data, start, end):
        if tag in skip:
            out.append('  '*depth + f'{tag:04x} [{len(payload)}] (skipped)')
            continue
        if tag in CONTAINERS:
            out.append('  '*depth + f'{tag:04x} [{len(payload)}] @{pos}')
            try:
                dump(payload, 0, len(payload), depth+1, out)
            except ValueError as e:
                out.append('  '*(depth+1) + f'!! not records: {e}; hex={payload[:32].hex()}')
        else:
            asc = payload.decode('utf-8','replace') if payload and all(32<=b<127 for b in payload) else ''
            out.append('  '*depth + f'{tag:04x} [{len(payload)}] {payload[:32].hex()}' + (f'  "{asc}"' if asc else ''))

def model_sections(path):
    raw = open(path,'rb').read()
    z = raw.find(b'PK\x03\x04')
    zf = zipfile.ZipFile(io.BytesIO(raw[z:]))
    md = zf.read('model.dat')
    out = {}
    for tag, payload, pos in recs(md, 0, len(md)):
        out.setdefault(tag, []).append(payload)
    # top-level F401 wrapper
    f401 = out[0x01f4][0]
    secs = {}
    for tag, payload, pos in recs(f401, 0, len(f401)):
        secs.setdefault(tag, []).append(payload)
    return raw, zf, secs

if __name__ == '__main__':
    path = sys.argv[1]
    tags = [int(t,16) for t in sys.argv[2].split(',')]
    maxdepth = int(sys.argv[3]) if len(sys.argv)>3 else 20
    raw, zf, secs = model_sections(path)
    for tag in tags:
        for payload in secs.get(tag, []):
            out=[]
            dump(payload, 0, len(payload), 0, out)
            print(f'=== {tag:04x} [{len(payload)}]')
            print('\n'.join(out))
