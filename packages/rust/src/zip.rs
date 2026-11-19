//! Minimal in-memory ZIP reader replacing the miniz `mz_zip_reader_*` calls
//! core.cpp makes: init from memory, file count/stat, locate by name, and
//! extract to memory (stored or deflated, CRC-checked).
//!
//! The central-directory validation, the case-insensitive heap-sorted name
//! index used by `locate`, and the extraction checks mirror miniz so the same
//! archives are accepted and rejected.

use std::fmt;

use crate::inflate;

/// Why an archive or entry was rejected (miniz's `mz_zip_error`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ZipError {
    NotAnArchive,
    FailedFindingCentralDir,
    InvalidHeaderOrCorrupted,
    UnsupportedMultidisk,
    TooManyFiles,
    UnsupportedCdirSize,
    UnsupportedEncryption,
    UnsupportedMethod,
    FileReadFailed,
    InvalidParameter,
    DecompressionFailed,
    UnexpectedDecompressedSize,
    CrcCheckFailed,
}

impl fmt::Display for ZipError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            ZipError::NotAnArchive => "not a ZIP archive",
            ZipError::FailedFindingCentralDir => "failed finding the central directory",
            ZipError::InvalidHeaderOrCorrupted => "invalid header or archive is corrupted",
            ZipError::UnsupportedMultidisk => "multidisk archives are not supported",
            ZipError::TooManyFiles => "too many files",
            ZipError::UnsupportedCdirSize => "unsupported central directory size",
            ZipError::UnsupportedEncryption => "encryption is not supported",
            ZipError::UnsupportedMethod => "unsupported compression method",
            ZipError::FileReadFailed => "file read failed",
            ZipError::InvalidParameter => "invalid parameter",
            ZipError::DecompressionFailed => "decompression failed",
            ZipError::UnexpectedDecompressedSize => "unexpected decompressed size",
            ZipError::CrcCheckFailed => "CRC-32 check failed",
        })
    }
}

impl std::error::Error for ZipError {}

// ── CRC-32 (slice-by-8) ─────────────────────────────────────────────────

const fn crc_tables() -> [[u32; 256]; 8] {
    let mut t = [[0u32; 256]; 8];
    let mut i = 0;
    while i < 256 {
        let mut c = i as u32;
        let mut k = 0;
        while k < 8 {
            c = if c & 1 != 0 {
                0xEDB8_8320 ^ (c >> 1)
            } else {
                c >> 1
            };
            k += 1;
        }
        t[0][i] = c;
        i += 1;
    }
    let mut i = 0;
    while i < 256 {
        let mut s = 1;
        while s < 8 {
            let prev = t[s - 1][i];
            t[s][i] = (prev >> 8) ^ t[0][(prev & 0xFF) as usize];
            s += 1;
        }
        i += 1;
    }
    t
}

static CRC_TABLES: [[u32; 256]; 8] = crc_tables();

/// Continues a CRC-32 (IEEE) from `crc` (0 to start).
pub fn crc32_update(crc: u32, data: &[u8]) -> u32 {
    let t = &CRC_TABLES;
    let mut c = !crc;
    let (chunks, rest) = data.as_chunks::<8>();
    for b in chunks {
        let lo = c ^ u32::from_le_bytes([b[0], b[1], b[2], b[3]]);
        let hi = u32::from_le_bytes([b[4], b[5], b[6], b[7]]);
        c = t[7][(lo & 0xFF) as usize]
            ^ t[6][((lo >> 8) & 0xFF) as usize]
            ^ t[5][((lo >> 16) & 0xFF) as usize]
            ^ t[4][(lo >> 24) as usize]
            ^ t[3][(hi & 0xFF) as usize]
            ^ t[2][((hi >> 8) & 0xFF) as usize]
            ^ t[1][((hi >> 16) & 0xFF) as usize]
            ^ t[0][(hi >> 24) as usize];
    }
    for &b in rest {
        c = (c >> 8) ^ t[0][((c ^ u32::from(b)) & 0xFF) as usize];
    }
    !c
}

/// CRC-32 (IEEE) of `data`.
pub fn crc32(data: &[u8]) -> u32 {
    crc32_update(0, data)
}

// ── reader ──────────────────────────────────────────────────────────────

const EOCD_SIZE: usize = 22;
const CDH_SIZE: usize = 46;
const LDH_SIZE: usize = 30;
const ZIP64_EOCD_SIZE: usize = 56;
const ZIP64_LOCATOR_SIZE: usize = 20;
const EOCD_SIG: u32 = 0x0605_4b50;
const CDH_SIG: u32 = 0x0201_4b50;
const LDH_SIG: u32 = 0x0403_4b50;
const ZIP64_LOCATOR_SIG: u32 = 0x0706_4b50;
const ZIP64_EOCD_SIG: u32 = 0x0606_4b50;
const ZIP64_EXTRA_ID: u16 = 0x0001;
/// miniz's `MZ_ZIP_MAX_ARCHIVE_FILENAME_SIZE`: stat names are cut to 511 bytes.
const MAX_FILENAME: usize = 512;

fn le16(d: &[u8], o: usize) -> u16 {
    u16::from_le_bytes([d[o], d[o + 1]])
}

fn le32(d: &[u8], o: usize) -> u32 {
    u32::from_le_bytes([d[o], d[o + 1], d[o + 2], d[o + 3]])
}

fn le64(d: &[u8], o: usize) -> u64 {
    let mut b = [0u8; 8];
    b.copy_from_slice(&d[o..o + 8]);
    u64::from_le_bytes(b)
}

fn lower(c: u8) -> u8 {
    c.to_ascii_lowercase()
}

/// One entry's central-directory metadata (`mz_zip_archive_file_stat`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ZipFileStat {
    pub index: u32,
    /// Name bytes as miniz exposes them: at most 511 bytes, cut at the first
    /// NUL (it is a C string).
    pub filename: Vec<u8>,
    pub comp_size: u64,
    pub uncomp_size: u64,
    pub crc32: u32,
    pub method: u16,
    pub bit_flag: u16,
    pub local_header_ofs: u64,
    pub external_attr: u32,
    pub is_directory: bool,
}

impl ZipFileStat {
    /// The name as (lossy) UTF-8.
    pub fn filename_str(&self) -> String {
        String::from_utf8_lossy(&self.filename).into_owned()
    }
}

/// A ZIP archive held in memory (`mz_zip_reader_init_mem`).
pub struct ZipArchive<'a> {
    data: &'a [u8],
    cdir_ofs: usize,
    cdir_size: usize,
    /// Offset of each entry's header inside the central directory.
    offsets: Vec<u32>,
    /// Entry indices sorted case-insensitively by name.
    sorted: Vec<u32>,
}

impl<'a> ZipArchive<'a> {
    /// Reads and validates the central directory.
    pub fn new(data: &'a [u8]) -> Result<Self, ZipError> {
        if data.len() < EOCD_SIZE {
            return Err(ZipError::NotAnArchive);
        }
        let eocd_ofs = locate_eocd(data).ok_or(ZipError::FailedFindingCentralDir)?;
        let size = data.len() as u64;
        let eocd = &data[eocd_ofs..eocd_ofs + EOCD_SIZE];
        if le32(eocd, 0) != EOCD_SIG {
            return Err(ZipError::NotAnArchive);
        }

        let mut zip64 = None;
        if eocd_ofs >= ZIP64_LOCATOR_SIZE + ZIP64_EOCD_SIZE {
            let loc = &data[eocd_ofs - ZIP64_LOCATOR_SIZE..eocd_ofs];
            if le32(loc, 0) == ZIP64_LOCATOR_SIG {
                let z64_ofs = le64(loc, 8);
                if z64_ofs > size - ZIP64_EOCD_SIZE as u64 {
                    return Err(ZipError::NotAnArchive);
                }
                let z = &data[z64_ofs as usize..z64_ofs as usize + ZIP64_EOCD_SIZE];
                if le32(z, 0) == ZIP64_EOCD_SIG {
                    zip64 = Some((loc, z));
                }
            }
        }

        let mut total_files = u32::from(le16(eocd, 10));
        let mut entries_on_disk = u32::from(le16(eocd, 8));
        let mut num_this_disk = u32::from(le16(eocd, 4));
        let mut cdir_disk_index = u32::from(le16(eocd, 6));
        let mut cdir_size = u64::from(le32(eocd, 12));
        let mut cdir_ofs = u64::from(le32(eocd, 16));

        if let Some((loc, z)) = zip64 {
            let total_disks = le32(loc, 16);
            let z_total = le64(z, 32);
            let z_on_disk = le64(z, 24);
            let z_record_size = le64(z, 4);
            let z_cdir_size = le64(z, 40);
            if z_record_size < (ZIP64_EOCD_SIZE - 12) as u64 {
                return Err(ZipError::InvalidHeaderOrCorrupted);
            }
            if total_disks != 1 {
                return Err(ZipError::UnsupportedMultidisk);
            }
            if z_total > u64::from(u32::MAX) || z_on_disk > u64::from(u32::MAX) {
                return Err(ZipError::TooManyFiles);
            }
            total_files = z_total as u32;
            entries_on_disk = z_on_disk as u32;
            if z_cdir_size > u64::from(u32::MAX) {
                return Err(ZipError::UnsupportedCdirSize);
            }
            cdir_size = z_cdir_size;
            num_this_disk = le32(z, 16);
            cdir_disk_index = le32(z, 20);
            cdir_ofs = le64(z, 48);
        }

        if total_files != entries_on_disk {
            return Err(ZipError::UnsupportedMultidisk);
        }
        if (num_this_disk | cdir_disk_index) != 0 && (num_this_disk != 1 || cdir_disk_index != 1) {
            return Err(ZipError::UnsupportedMultidisk);
        }
        if cdir_size < u64::from(total_files) * CDH_SIZE as u64 {
            return Err(ZipError::InvalidHeaderOrCorrupted);
        }
        if cdir_ofs.checked_add(cdir_size).is_none_or(|end| end > size) {
            return Err(ZipError::InvalidHeaderOrCorrupted);
        }
        if (eocd_ofs as u64) < cdir_ofs + cdir_size {
            return Err(ZipError::InvalidHeaderOrCorrupted);
        }
        let archive_ofs = eocd_ofs as u64 - (cdir_ofs + cdir_size);
        if zip64.is_some() && archive_ofs < (ZIP64_EOCD_SIZE + ZIP64_LOCATOR_SIZE) as u64 {
            return Err(ZipError::InvalidHeaderOrCorrupted);
        }

        let mut archive = ZipArchive {
            data,
            cdir_ofs: cdir_ofs as usize,
            cdir_size: cdir_size as usize,
            offsets: Vec::with_capacity(total_files as usize),
            sorted: Vec::new(),
        };
        if total_files > 0 {
            archive.index_entries(total_files, num_this_disk)?;
        }
        archive.sorted = (0..total_files).collect();
        archive.sort_by_name();
        Ok(archive)
    }

    fn central_dir(&self) -> &'a [u8] {
        &self.data[self.cdir_ofs..self.cdir_ofs + self.cdir_size]
    }

    fn index_entries(&mut self, total_files: u32, num_this_disk: u32) -> Result<(), ZipError> {
        let cdir = self.central_dir();
        let archive_size = self.data.len() as u64;
        let mut has_zip64_extra = false;
        let mut p = 0usize;
        let mut n = cdir.len();
        for _ in 0..total_files {
            if n < CDH_SIZE || le32(cdir, p) != CDH_SIG {
                return Err(ZipError::InvalidHeaderOrCorrupted);
            }
            self.offsets.push(p as u32);
            let h = &cdir[p..];
            let comp_size = u64::from(le32(h, 20));
            let decomp_size = u64::from(le32(h, 24));
            let local_header_ofs = u64::from(le32(h, 42));
            let filename_size = le16(h, 28) as usize;
            let ext_data_size = le16(h, 30) as usize;
            let max32 = u64::from(u32::MAX);

            if !has_zip64_extra
                && ext_data_size > 0
                && comp_size.max(decomp_size).max(local_header_ofs) == max32
            {
                // Look for a zip64 extended information field in the extra data.
                let start = CDH_SIZE + filename_size;
                let extra: &[u8] = if start + ext_data_size > n {
                    // miniz re-reads from the archive in this case (relative to
                    // the central directory start).
                    let at = self.cdir_ofs + start;
                    self.data
                        .get(at..at + ext_data_size)
                        .ok_or(ZipError::FileReadFailed)?
                } else {
                    &h[start..start + ext_data_size]
                };
                let mut q = 0usize;
                let mut remaining = ext_data_size;
                while remaining > 0 {
                    if remaining < 4 {
                        return Err(ZipError::InvalidHeaderOrCorrupted);
                    }
                    let id = le16(extra, q);
                    let field = le16(extra, q + 2) as usize;
                    if field + 4 > remaining {
                        return Err(ZipError::InvalidHeaderOrCorrupted);
                    }
                    if id == ZIP64_EXTRA_ID {
                        has_zip64_extra = true;
                        break;
                    }
                    q += 4 + field;
                    remaining -= 4 + field;
                }
            }

            // miniz reads the method as a 32-bit value here, so it takes in the
            // modification time too.
            if comp_size != max32
                && decomp_size != max32
                && ((le32(h, 10) == 0 && decomp_size != comp_size)
                    || (decomp_size != 0 && comp_size == 0))
            {
                return Err(ZipError::InvalidHeaderOrCorrupted);
            }
            let disk_index = u32::from(le16(h, 34));
            if disk_index == u32::from(u16::MAX) || (disk_index != num_this_disk && disk_index != 1)
            {
                return Err(ZipError::UnsupportedMultidisk);
            }
            if comp_size != max32 && local_header_ofs + LDH_SIZE as u64 + comp_size > archive_size {
                return Err(ZipError::InvalidHeaderOrCorrupted);
            }
            if le16(h, 8) & 0x2000 != 0 {
                return Err(ZipError::UnsupportedEncryption);
            }
            let total_header = CDH_SIZE + filename_size + ext_data_size + le16(h, 32) as usize;
            if total_header > n {
                return Err(ZipError::InvalidHeaderOrCorrupted);
            }
            n -= total_header;
            p += total_header;
        }
        Ok(())
    }

    fn name_of(&self, index: u32) -> &'a [u8] {
        let cdir = self.central_dir();
        let p = self.offsets[index as usize] as usize;
        let len = le16(cdir, p + 28) as usize;
        &cdir[p + CDH_SIZE..p + CDH_SIZE + len]
    }

    /// miniz's `mz_zip_reader_filename_less`: ASCII case-insensitive.
    fn name_less(&self, l: u32, r: u32) -> bool {
        let (a, b) = (self.name_of(l), self.name_of(r));
        let n = a.len().min(b.len());
        for i in 0..n {
            let (x, y) = (lower(a[i]), lower(b[i]));
            if x != y {
                return x < y;
            }
        }
        a.len() < b.len()
    }

    /// miniz's heap sort of the name index (kept identical so duplicate names
    /// resolve to the same entry).
    fn sort_by_name(&mut self) {
        let size = self.sorted.len();
        if size <= 1 {
            return;
        }
        let mut idx = std::mem::take(&mut self.sorted);
        let mut start = (size - 2) >> 1;
        loop {
            let mut root = start;
            loop {
                let mut child = (root << 1) + 1;
                if child >= size {
                    break;
                }
                if child + 1 < size && self.name_less(idx[child], idx[child + 1]) {
                    child += 1;
                }
                if !self.name_less(idx[root], idx[child]) {
                    break;
                }
                idx.swap(root, child);
                root = child;
            }
            if start == 0 {
                break;
            }
            start -= 1;
        }
        let mut end = size - 1;
        while end > 0 {
            idx.swap(end, 0);
            let mut root = 0;
            loop {
                let mut child = (root << 1) + 1;
                if child >= end {
                    break;
                }
                if child + 1 < end && self.name_less(idx[child], idx[child + 1]) {
                    child += 1;
                }
                if !self.name_less(idx[root], idx[child]) {
                    break;
                }
                idx.swap(root, child);
                root = child;
            }
            end -= 1;
        }
        self.sorted = idx;
    }

    /// Number of entries (`mz_zip_reader_get_num_files`).
    pub fn len(&self) -> usize {
        self.offsets.len()
    }

    pub fn is_empty(&self) -> bool {
        self.offsets.is_empty()
    }

    fn header(&self, index: u32) -> Option<&'a [u8]> {
        let p = *self.offsets.get(index as usize)? as usize;
        Some(&self.central_dir()[p..])
    }

    /// Entry metadata (`mz_zip_reader_file_stat`), resolving zip64 extended
    /// sizes/offsets.
    pub fn stat(&self, index: u32) -> Result<ZipFileStat, ZipError> {
        let h = self.header(index).ok_or(ZipError::InvalidParameter)?;
        let filename_len = le16(h, 28) as usize;
        let extra_len = le16(h, 30) as usize;
        let full_name = &h[CDH_SIZE..CDH_SIZE + filename_len];
        let mut filename = full_name[..filename_len.min(MAX_FILENAME - 1)].to_vec();
        if let Some(nul) = filename.iter().position(|&b| b == 0) {
            filename.truncate(nul);
        }
        let external_attr = le32(h, 38);
        let is_directory = full_name.last() == Some(&b'/') || external_attr & 0x10 != 0;
        let mut st = ZipFileStat {
            index,
            filename,
            comp_size: u64::from(le32(h, 20)),
            uncomp_size: u64::from(le32(h, 24)),
            crc32: le32(h, 16),
            method: le16(h, 10),
            bit_flag: le16(h, 8),
            local_header_ofs: u64::from(le32(h, 42)),
            external_attr,
            is_directory,
        };
        let max32 = u64::from(u32::MAX);
        if st.comp_size.max(st.uncomp_size).max(st.local_header_ofs) == max32 && extra_len > 0 {
            let extra = &h[CDH_SIZE + filename_len..CDH_SIZE + filename_len + extra_len];
            let mut q = 0usize;
            let mut remaining = extra_len;
            while remaining > 0 {
                if remaining < 4 {
                    return Err(ZipError::InvalidHeaderOrCorrupted);
                }
                let id = le16(extra, q);
                let field = le16(extra, q + 2) as usize;
                if field + 4 > remaining {
                    return Err(ZipError::InvalidHeaderOrCorrupted);
                }
                if id == ZIP64_EXTRA_ID {
                    let mut f = q + 4;
                    let mut left = field;
                    for slot in [
                        &mut st.uncomp_size,
                        &mut st.comp_size,
                        &mut st.local_header_ofs,
                    ] {
                        if *slot == max32 {
                            if left < 8 {
                                return Err(ZipError::InvalidHeaderOrCorrupted);
                            }
                            *slot = le64(extra, f);
                            f += 8;
                            left -= 8;
                        }
                    }
                    break;
                }
                q += 4 + field;
                remaining -= 4 + field;
            }
        }
        Ok(st)
    }

    /// Index of the entry named `name` (`mz_zip_reader_locate_file` with no
    /// flags: an ASCII case-insensitive binary search). The name is a C
    /// string, so it ends at its first NUL.
    pub fn locate(&self, name: &[u8]) -> Option<u32> {
        let name = match name.iter().position(|&b| b == 0) {
            Some(n) => &name[..n],
            None => name,
        };
        if self.sorted.is_empty() {
            return None;
        }
        let (mut l, mut h) = (0i64, self.sorted.len() as i64 - 1);
        while l <= h {
            let m = l + ((h - l) >> 1);
            let file_index = self.sorted[m as usize];
            let entry = self.name_of(file_index);
            let n = entry.len().min(name.len());
            let mut comp = 0i64;
            let mut matched = true;
            for i in 0..n {
                let (x, y) = (lower(entry[i]), lower(name[i]));
                if x != y {
                    comp = i64::from(x) - i64::from(y);
                    matched = false;
                    break;
                }
            }
            if matched {
                comp = entry.len() as i64 - name.len() as i64;
            }
            match comp.cmp(&0) {
                std::cmp::Ordering::Equal => return Some(file_index),
                std::cmp::Ordering::Less => l = m + 1,
                std::cmp::Ordering::Greater => h = m - 1,
            }
        }
        None
    }

    /// Extracts an entry into a buffer of exactly its declared uncompressed
    /// size (`mz_zip_reader_extract_to_mem` with a buffer that size).
    pub fn extract(&self, index: u32) -> Result<Vec<u8>, ZipError> {
        let st = self.stat(index)?;
        let uncomp = usize::try_from(st.uncomp_size).map_err(|_| ZipError::InvalidParameter)?;
        // A directory or zero-length entry: only valid when uncompressed size is 0.
        if st.is_directory || st.comp_size == 0 {
            if uncomp != 0 {
                return Err(ZipError::InvalidHeaderOrCorrupted);
            }
            return Ok(Vec::new());
        }
        // Encrypted (bit 0), strong encryption (bit 6), patch data (bit 5).
        if st.bit_flag & (1 | 0x40 | 0x20) != 0 {
            return Err(ZipError::UnsupportedEncryption);
        }
        if st.method != 0 && st.method != 8 {
            return Err(ZipError::UnsupportedMethod);
        }
        let lh_ofs = usize::try_from(st.local_header_ofs).map_err(|_| ZipError::FileReadFailed)?;
        let lh = self
            .data
            .get(lh_ofs..lh_ofs.saturating_add(LDH_SIZE))
            .ok_or(ZipError::FileReadFailed)?;
        if lh.len() != LDH_SIZE {
            return Err(ZipError::FileReadFailed);
        }
        if le32(lh, 0) != LDH_SIG {
            return Err(ZipError::InvalidHeaderOrCorrupted);
        }
        let data_ofs = st
            .local_header_ofs
            .checked_add(LDH_SIZE as u64 + u64::from(le16(lh, 26)) + u64::from(le16(lh, 28)))
            .ok_or(ZipError::InvalidHeaderOrCorrupted)?;
        let data_end = data_ofs
            .checked_add(st.comp_size)
            .ok_or(ZipError::InvalidHeaderOrCorrupted)?;
        if data_end > self.data.len() as u64 {
            return Err(ZipError::InvalidHeaderOrCorrupted);
        }
        let data_ofs = data_ofs as usize;
        let data_end = data_end as usize;
        let out = if st.method == 0 {
            // Stored: miniz copies the declared uncompressed size.
            let src = self
                .data
                .get(data_ofs..data_ofs.saturating_add(uncomp))
                .ok_or(ZipError::FileReadFailed)?;
            if src.len() != uncomp {
                return Err(ZipError::FileReadFailed);
            }
            src.to_vec()
        } else {
            let src = &self.data[data_ofs..data_end];
            let out =
                inflate::inflate_raw(src, uncomp).map_err(|_| ZipError::DecompressionFailed)?;
            if out.len() != uncomp {
                return Err(ZipError::UnexpectedDecompressedSize);
            }
            out
        };
        if crc32(&out) != st.crc32 {
            return Err(ZipError::CrcCheckFailed);
        }
        Ok(out)
    }
}

/// Finds the end-of-central-directory record by scanning backwards from the
/// end in 4 KiB windows, giving up ~64 KiB from the end
/// (`mz_zip_reader_locate_header_sig`).
fn locate_eocd(data: &[u8]) -> Option<usize> {
    const BUF: usize = 4096;
    let size = data.len();
    if size < EOCD_SIZE {
        return None;
    }
    let mut cur = size.saturating_sub(BUF);
    loop {
        let n = BUF.min(size - cur);
        let window = &data[cur..cur + n];
        if n >= 4 {
            let mut i = n as isize - 4;
            while i >= 0 {
                let iu = i as usize;
                if le32(window, iu) == EOCD_SIG && size - (cur + iu) >= EOCD_SIZE {
                    return Some(cur + iu);
                }
                i -= 1;
            }
        }
        if cur == 0 || size - cur >= u16::MAX as usize + EOCD_SIZE {
            return None;
        }
        cur = cur.saturating_sub(BUF - 3);
    }
}
