#!/usr/bin/env python3
"""Checks that generated/output_fixed matches the code this port was built and tested with.

The fingerprint ignores line endings (the recompiler writes CRLF on Windows and LF on
Linux) and the order the OS lists files in.

Usage: python verify_generated.py <output_fixed folder>
"""
import hashlib, os, sys

EXPECTED_FILES = 10126
EXPECTED_SHA1 = "8b4334d1b7e30022771e2e29f430041cfa604a36"

HERE = os.path.dirname(os.path.abspath(__file__))

def obsolete():
    # files left over from older generations are ignored (recomp/obsolete_files.txt)
    try:
        with open(os.path.join(HERE, '..', 'recomp', 'obsolete_files.txt')) as f:
            return {l.strip() for l in f if l.strip()}
    except OSError:
        return set()

def fingerprint(folder):
    h = hashlib.sha1()
    skip = obsolete()
    names = sorted(n for n in os.listdir(folder) if n.endswith(('.cpp', '.h')) and n not in skip)
    for n in names:
        h.update(n.encode() + b'\0')
        with open(os.path.join(folder, n), 'rb') as f:
            h.update(f.read().replace(b'\r\n', b'\n'))
        h.update(b'\0')
    return len(names), h.hexdigest()

if __name__ == '__main__':
    folder = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, '..', 'generated', 'output_fixed')
    count, digest = fingerprint(folder)
    print('%d files, fingerprint %s' % (count, digest))
    if count == EXPECTED_FILES and digest == EXPECTED_SHA1:
        print('OK: identical to the reference build.')
        sys.exit(0)
    print('DIFFERENT from the reference build (expected %d files, %s).' % (EXPECTED_FILES, EXPECTED_SHA1))
    print('It may still work, but bugs you find may not be reproducible by others.')
    sys.exit(1)
