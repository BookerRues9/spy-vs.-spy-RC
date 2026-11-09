#!/usr/bin/env python3
"""Compacta register_functions.cpp generado por ps2_recomp.

El original asigna cada slot de la tabla (cientos de miles de líneas dentro de
un único constructor), lo que tarda >1 h en compilar y usa varios GB de RAM.
Esta versión guarda rangos {primer_slot, cantidad, función} y los rellena con
un bucle: mismo resultado en tiempo de ejecución, compila en segundos.

Uso: python compact_register.py register_functions.cpp [salida.cpp]
"""
import re, sys

src = sys.argv[1]
dst = sys.argv[2] if len(sys.argv) > 2 else src
text = open(src, encoding="utf-8").read()
if "kSvsRegisterRuns" in text:
    print("ya estaba compactado"); sys.exit(0)

assign = re.compile(r"^\s*g_ps2RecompiledFunctionTable\[(\d+)\] = ([A-Za-z_][A-Za-z0-9_]*);")
head, runs, tail, state = [], [], [], 0
for line in text.splitlines():
    m = assign.match(line)
    if m:
        state = 1
        idx, fn = int(m.group(1)), m.group(2)
        if runs and runs[-1][2] == fn and runs[-1][0] + runs[-1][1] == idx:
            runs[-1][1] += 1
        else:
            runs.append([idx, 1, fn])
    elif state == 0:
        head.append(line)
    else:
        tail.append(line)

# head termina en "    GeneratedFunctionTableInitializer() {"; tail empieza en "    }"
ctor = head.pop()
assert "GeneratedFunctionTableInitializer()" in ctor, ctor
struct_line = head.pop()
assert "struct GeneratedFunctionTableInitializer" in struct_line, struct_line
out = head[:]
# Declaraciones propias: así no depende de que la cabecera precompilada esté al día.
names = sorted({f for _, _, f in runs})
assert out[-1].strip() == "namespace {", out[-1]
out.pop()
out += [f"void {n}(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime);" for n in names]
out.append("namespace {")
out.append("struct SvsRegisterRun { uint32_t first; uint32_t count; PS2Runtime::RecompiledFunction fn; };")
out.append("static const SvsRegisterRun kSvsRegisterRuns[] = {")
out += [f"    {{{a}u, {c}u, {f}}}," for a, c, f in runs]
out.append("};")
out.append(struct_line)
out.append(ctor)
out.append("        for (const SvsRegisterRun &r : kSvsRegisterRuns)")
out.append("            for (uint32_t i = 0; i < r.count; ++i)")
out.append("                g_ps2RecompiledFunctionTable[r.first + i] = r.fn;")
out += tail
open(dst, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
print(f"{sum(r[1] for r in runs)} slots -> {len(runs)} rangos")
