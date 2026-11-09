#!/usr/bin/env python3
"""Corrige SQRT.S en código ya generado por PS2Recomp.

En el EE, SQRT.S fd, ft toma el operando del campo ft (bits 16-20); el
recompilador usaba fs (bits 11-15, casi siempre f0). Uso: fix_sqrt.py <carpeta> ...
"""
import re, sys, pathlib

COMMENT = re.compile(r'^\s*// 0x([0-9a-fA-F]+): 0x([0-9a-fA-F]+)\b')
SQRT = re.compile(r'(ctx->f\[(\d+)\] = FPU_SQRT_S\(ctx->f\[)(\d+)(\]\);)')

def fix_file(path):
    lines = path.read_text(encoding='utf-8', errors='surrogateescape').split('\n')
    word = None
    changed = 0
    for i, line in enumerate(lines):
        m = COMMENT.match(line)
        if m:
            word = int(m.group(2), 16)
            continue
        s = SQRT.search(line)
        if s and word is not None and (word >> 26) == 0x11 and (word & 0x3F) == 0x04:
            ft = (word >> 16) & 31
            if int(s.group(3)) != ft:
                lines[i] = line[:s.start()] + f'{s.group(1)}{ft}{s.group(4)}' + line[s.end():]
                changed += 1
    if changed:
        with open(path, 'w', encoding='utf-8', errors='surrogateescape', newline='') as f:
            f.write('\n'.join(lines))
    return changed

total = files = 0
for d in sys.argv[1:]:
    for p in pathlib.Path(d).glob('*.cpp'):
        n = fix_file(p)
        if n:
            files += 1
            total += n
print(f'SQRT.S corregidos: {total} en {files} archivos')
