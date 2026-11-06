"""SD card image for the virtual Play: a FAT32 image file served sector by sector."""
import os
import shutil
import subprocess

import pyfat

# mkfs.fat + mtools when installed (what the cloud runs used); otherwise the pure-Python pyfat
HAVE_MTOOLS = bool(shutil.which('mkfs.fat') and shutil.which('mcopy'))

SECTOR = 512


def make_image(path, size_mb=256, files=None, dirs=('Projects', 'Firmware')):
    """Create a fresh FAT32 image. files: {'/dir/name': bytes}."""
    with open(path, 'wb') as f:
        f.truncate(size_mb * 1024 * 1024)
    if not HAVE_MTOOLS:
        pyfat.mkfs(path)
        fs = pyfat.Fat32(path)
        for d in dirs:
            fs.mkdir('/' + d)
        for p, data in (files or {}).items():
            fs.write_file(p, data)
        fs.close()
        return
    subprocess.run(['mkfs.fat', '-F', '32', '-n', 'PLAYSD', path], check=True, capture_output=True)
    env = dict(os.environ, MTOOLS_SKIP_CHECK='1')
    for d in dirs:
        subprocess.run(['mmd', '-i', path, f'::/{d}'], check=True, env=env)
    for p, data in (files or {}).items():
        parts = p.strip('/').split('/')
        for k in range(1, len(parts)):
            subprocess.run(['mmd', '-i', path, '::/' + '/'.join(parts[:k])], env=env, capture_output=True)
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(data)
        subprocess.run(['mcopy', '-o', '-i', path, tmp, '::' + p], check=True, env=env)
        os.remove(tmp)


def ls(path, d='/'):
    if not HAVE_MTOOLS:
        fs = pyfat.Fat32(path)
        try:
            return '\n'.join(f"{n}{'/' if a & pyfat.ATTR_DIR else ''} {sz}" for n, a, sz in fs.listdir(d))
        finally:
            fs.close()
    env = dict(os.environ, MTOOLS_SKIP_CHECK='1')
    return subprocess.run(['mdir', '-/', '-i', path, '::' + d], capture_output=True, text=True, env=env).stdout


def read_file(path, p):
    """Bytes of file p on the card image, or None."""
    fs = pyfat.Fat32(path)
    try:
        return fs.read_file(p)
    finally:
        fs.close()


class SDCard:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'r+b')
        self.f.seek(0, 2)
        self.sectors = self.f.tell() // SECTOR
        self.reads = self.writes = 0

    def read(self, lba, count):
        self.reads += count
        self.f.seek(lba * SECTOR)
        return self.f.read(count * SECTOR)

    def write(self, lba, data):
        self.writes += len(data) // SECTOR
        self.f.seek(lba * SECTOR)
        self.f.write(data)
        self.f.flush()
