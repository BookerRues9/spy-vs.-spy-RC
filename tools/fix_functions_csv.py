#!/usr/bin/env python3
"""
Genera functions_fixed.csv para Spy vs. Spy a partir del functions.csv de Ghidra.

El CSV exportado de Ghidra tiene cientos de funciones con tamaño 4 (inicio bien,
fin mal) y le faltan funciones que solo se llaman por puntero. Este script toma
como inicios de función:
  * los inicios del CSV de Ghidra dentro de .text,
  * todos los destinos de instrucciones `jal`,
  * punteros a código guardados en datos (tablas de funciones, vtables),
  * direcciones de código armadas con lui+addiu/ori (punteros a función en el código),
filtrando los punteros para quedarse solo con los que parecen inicio de función
(justo después de un `jr $ra` + delay slot, después de dos o más nops de relleno, o que
empiezan reservando pila con `addiu $sp, $sp, -N`).
Cada función termina donde empieza la siguiente.

Uso: python fix_functions_csv.py SLES_530.78 functions.csv functions_fixed.csv
"""
import csv, struct, sys

elf, src, dst = sys.argv[1:4]
data = open(elf, "rb").read()
TEXT_START, TEXT_END, TEXT_OFF = 0x100000, 0x3D4370, 0x1000   # .text de SLES_530.78
SEG_START, SEG_END = 0x100000, 0x100000 + 0x319B94             # segmento cargado (texto + datos)
words = struct.unpack_from("<%dI" % ((TEXT_END - TEXT_START) // 4), data, TEXT_OFF)
seg = struct.unpack_from("<%dI" % ((SEG_END - SEG_START) // 4), data, TEXT_OFF)

def word(addr):
    return words[(addr - TEXT_START) // 4]

def in_text(a):
    return TEXT_START <= a < TEXT_END and a % 4 == 0

def after_return(a):
    """True si a está justo después de `jr $ra; delay` más cero o más nops de relleno."""
    b = a - 4
    while b >= TEXT_START and word(b) == 0 and b > a - 64:
        b -= 4
    # b apunta a la última instrucción no-nop: puede ser el delay slot (no-nop) o el jr mismo
    for jr_at in (b - 4, b):
        if jr_at >= TEXT_START and word(jr_at) == 0x03E00008:
            return True
    return False

def looks_like_entry(a):
    if not in_text(a):
        return False
    first = word(a)
    if first == 0x03E00008:                                   # función de 1 instrucción: jr $ra + delay slot
        delay = word(a + 4)
        if (delay >> 16) == 0x27BD and not (delay & 0x8000):  # ...salvo epílogos (addiu $sp,$sp,+N)
            return False
        return True
    if first != 0 and after_return(a):
        return True
    if (first >> 16) == 0x27BD and (first & 0x8000):          # addiu $sp,$sp,-N
        return True
    if a - 8 >= TEXT_START and word(a - 8) == 0x03E00008:     # jr $ra ; delay ; <aquí>
        return True
    if a - 8 >= TEXT_START and word(a - 4) == 0 and word(a - 8) == 0 and first != 0:  # tras relleno de nops
        return True
    return False

starts, why = set(), {}
def add(a, reason):
    if a not in starts:
        starts.add(a)
        why[reason] = why.get(reason, 0) + 1

for row in csv.DictReader(open(src)):
    a = int(row["Start"], 16)
    if in_text(a):
        add(a, "ghidra")
for i, w in enumerate(words):
    if w >> 26 == 3:   # jal
        t = ((TEXT_START + i * 4) & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
        if in_text(t):
            add(t, "jal")
# punteros en la zona de datos
for i in range((TEXT_END - SEG_START) // 4, len(seg)):
    if looks_like_entry(seg[i]):
        add(seg[i], "data-ptr")
# entradas de vtable (GCC 2.95: {int16 delta, int16 pad, void *fn}, 8 bytes por entrada).
# Se aceptan si la instrucción anterior es un nop (fin de función o de un fragmento
# de epílogo muerto), para no cortar funciones con falsos positivos.
def small(x):
    return x < 0x10000 or x >= 0xFFFF0000
base = (TEXT_END - SEG_START) // 4
for i in range(base + 3, len(seg) - 2):
    v = seg[i]
    if not in_text(v) or v - 4 < TEXT_START or word(v - 4) != 0 or word(v) == 0:
        continue
    if small(seg[i - 1]) and ((small(seg[i + 1]) and in_text(seg[i + 2])) or (small(seg[i - 3]) and in_text(seg[i - 2]))):
        add(v, "vtable")
# lui/addiu|ori en el código
lui = {}
for i, w in enumerate(words):
    op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
    if op == 0x0F:
        lui[rt] = (i, imm)
    elif op in (0x09, 0x0D) and rs in lui and i - lui[rs][0] < 16:
        lo = imm if op == 0x0D else (imm - 0x10000 if imm & 0x8000 else imm)
        t = ((lui[rs][1] << 16) + lo) & 0xFFFFFFFF
        if looks_like_entry(t):
            add(t, "code-ptr")

# Funciones hoja sin referencia directa: tras `jr $ra` (+relleno) dentro de una función,
# si ninguna rama anterior de la misma función salta a esa zona, es otra función.
def branch_target(addr, w):
    op = w >> 26
    if op in (4, 5, 6, 7, 20, 21, 22, 23) or (op == 1 and ((w >> 16) & 31) in (0, 1, 2, 3, 16, 17, 18, 19)):
        off = w & 0xFFFF
        off = off - 0x10000 if off & 0x8000 else off
        return addr + 4 + off * 4
    if op == 2:
        return (addr & 0xF0000000) | ((w & 0x03FFFFFF) << 2)
    return None

def split_pass(starts):
    s = sorted(starts)
    new = []
    for i, a in enumerate(s):
        e = s[i + 1] if i + 1 < len(s) else TEXT_END
        maxt, jumptable = a, False
        p = a
        while p < e:
            w = word(p)
            if (w & 0xFC1FFFFF) == 0x00000008 and ((w >> 21) & 31) != 31:   # jr $reg (switch)
                jumptable = True
            t = branch_target(p, w)
            if t is not None and a <= t < e:
                maxt = max(maxt, t)
            p += 4
            if jumptable:
                break
            if p < e and word(p) != 0 and p > maxt and after_return(p) and word(p - 4) == 0 and p - a > 8:
                new.append(p)
    return new

extra = split_pass(starts)
for a in extra:
    add(a, "leaf-split")

s = sorted(starts)
with open(dst, "w", newline="") as out:
    out.write("Name,Start,End,Size\n")
    for i, a in enumerate(s):
        b = s[i + 1] if i + 1 < len(s) else TEXT_END
        out.write("FUN_%08x,0x%08X,0x%08X,%d\n" % (a, a, b, b - a))
print("%d funciones -> %s  %s" % (len(s), dst, why))
