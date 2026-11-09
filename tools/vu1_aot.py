#!/usr/bin/env python3
"""Compila de antemano (AOT) los microprogramas del VU1 de Spy vs Spy a C++.

1. Correr el juego con SVS_VU_AOT_DUMP=<carpeta> y jugar un rato: cada imagen de
   código del VU1 queda en vu1_<hash>.txt y sus puntos de entrada (MSCAL y destinos
   de saltos JR/JALR) en vu1_<hash>.entries.
2. python vu1_aot.py <carpeta> ps2xRuntime/src/lib/vu/svs_vu1_aot.inc
3. Recompilar: ps2_vu1_core.cpp incluye svs_vu1_aot.inc si existe.

Cada programa se identifica por su contenido (las palabras de todas las
instrucciones alcanzables), no por la dirección: al cambiar el código del VU1 el
runtime comprueba qué programas compilados coinciden con la memoria y solo esos se
usan. Lo que no está compilado sigue en el intérprete (ruta rápida).

La semántica es la del intérprete en modo rápido, instrucción por instrucción:
mismas esperas (stalls) por registro y carril, mismos ciclos, mismas latencias de
Q/P/flags; las operaciones raras llaman a execUpper/execLower del intérprete.
"""
import glob
import hashlib
import os
import sys

MASK = 0x3FFF

F_IBIT, F_EBIT, F_MBIT, F_DBIT, F_TBIT = 1, 2, 4, 8, 16
F_URES, F_LRES, F_DELAYBR, F_SPECIAL, F_BRANCH, F_FLAGS = 32, 64, 128, 256, 512, 1024


def imm11(x):
    v = x & 0x7FF
    return v - 0x800 if v & 0x400 else v


def load_image(path):
    pairs = {}
    size = 0
    with open(path) as f:
        for line in f:
            t = line.split()
            if not t:
                continue
            if t[0] == 'image':
                size = int(t[2])
                continue
            if t[0] != 'p':
                continue
            idx = int(t[1])
            p = {'idx': idx, 'pc': idx * 8, 'upper': int(t[2], 16), 'lower': int(t[3], 16), 'flags': int(t[4]),
                 'shadow': int(t[5]), 'suppressed': int(t[6]), 'viw': int(t[7]), 'reads': [], 'writes': []}
            mode = None
            for tok in t[8:]:
                if tok in ('r', 'w'):
                    mode = tok
                elif mode == 'r':
                    p['reads'].append(tok)
                else:
                    a, b = tok.split(':')
                    p['writes'].append((int(a), int(b)))
            pairs[idx * 8] = p
    return size, pairs


def lower_op(p):
    if p['flags'] & F_IBIT:
        return None
    lo = p['lower']
    if lo >> 31:
        return 0x40
    return (lo >> 25) & 0x7F


def reachable(pairs, entries, size):
    """Pairs reachable from the entries, following delay slots properly."""
    seen = set()
    work = [(e & MASK, None) for e in entries]
    visited = set()
    while work:
        pc, after = work.pop()
        if (pc, after) in visited or pc not in pairs:
            continue
        visited.add((pc, after))
        seen.add(pc)
        p = pairs[pc]
        nxt = (pc + 8) % size
        if after is not None:
            # this pair is the delay slot of `after`
            a = pairs[after]
            if a['flags'] & F_EBIT:
                continue  # program ends after the delay slot
            op = lower_op(a)
            if op in (0x20, 0x21):
                work.append(((after + 8 + imm11(a['lower']) * 8) & MASK, None))
            elif op in (0x28, 0x29, 0x2C, 0x2D, 0x2E, 0x2F):
                work.append(((after + 8 + imm11(a['lower']) * 8) & MASK, None))
                work.append((nxt, None))
            elif op in (0x24, 0x25):
                pass  # dynamic target: comes from the recorded entries
            else:
                work.append((nxt, None))
            continue
        op = lower_op(p)
        if (p['flags'] & F_EBIT) or (p['flags'] & F_BRANCH):
            work.append((nxt, pc))
            continue
        work.append((nxt, None))
    return seen


# ------------------------------------------------------------------ upper

def lanes(dest):
    return [c for c in range(4) if dest & (8 >> c)]


def F(x, n):
    return (x >> n) & 0x1F


def gen_upper(p, out):
    """Inline code for the upper op, or None for the interpreter."""
    U = p['upper']
    dest = (U >> 21) & 0xF
    ft, fs, fd = F(U, 16), F(U, 11), F(U, 6)
    bc = U & 3
    op = U & 0x3F
    L = lanes(dest)

    def arith(fam, src, toacc):
        o = []
        # operands, normalised like execUpper (only the lanes used)
        need_s = set(L)
        for c in sorted(need_s):
            o.append('const float s%d = svsfast::clf(vf[%d][%d]);' % (c, fs, c))
        if src == 'vt':
            for c in L:
                o.append('const float t%d = svsfast::clf(vf[%d][%d]);' % (c, ft, c))
            tv = lambda c: 't%d' % c
        else:
            if src == 'bc':
                o.append('const float tb = svsfast::clf(vf[%d][%d]);' % (ft, bc))
            elif src == 'q':
                o.append('const float tb = svsfast::clf(S.q);')
            else:
                o.append('const float tb = svsfast::clf(S.i);')
            tv = lambda c: 'tb'
        if fam in ('madd', 'msub'):
            for c in L:
                o.append('const float a%d = svsfast::clf(S.acc[%d]);' % (c, c))
        dst = 'S.acc' if toacc else 'vf[%d]' % fd
        for c in L:
            s, t = 's%d' % c, tv(c)
            if fam == 'add':
                e = '%s + %s' % (s, t)
            elif fam == 'sub':
                e = '%s - %s' % (s, t)
            elif fam == 'mul':
                e = '%s * %s' % (s, t)
            elif fam == 'madd':
                e = 'a%d + %s * %s' % (c, s, t)
            elif fam == 'msub':
                e = 'a%d - %s * %s' % (c, s, t)
            elif fam == 'max':
                e = '(%s > %s) ? %s : %s' % (s, t, s, t)
            else:
                e = '(%s < %s) ? %s : %s' % (s, t, s, t)
            o.append('const float r%d = %s;' % (c, e))
        for c in L:
            if fam in ('max', 'mini'):
                o.append('%s[%d] = r%d;' % (dst, c, c))
            else:
                o.append('%s[%d] = svsfast::clf(r%d);' % (dst, c, c))
        return o

    fam4 = ['add', 'sub', 'madd', 'msub', 'max', 'mini', 'mul']
    if op < 0x3C:
        if op <= 0x1B:
            return arith(fam4[op >> 2], 'bc', False)
        table = {0x1C: ('mul', 'q'), 0x1D: ('max', 'i'), 0x1E: ('mul', 'i'), 0x1F: ('mini', 'i'),
                 0x20: ('add', 'q'), 0x21: ('madd', 'q'), 0x22: ('add', 'i'), 0x23: ('madd', 'i'),
                 0x24: ('sub', 'q'), 0x25: ('msub', 'q'), 0x26: ('sub', 'i'), 0x27: ('msub', 'i'),
                 0x28: ('add', 'vt'), 0x29: ('madd', 'vt'), 0x2A: ('mul', 'vt'), 0x2B: ('max', 'vt'),
                 0x2C: ('sub', 'vt'), 0x2D: ('msub', 'vt'), 0x2F: ('mini', 'vt')}
        if op in table:
            return arith(table[op][0], table[op][1], False)
        if op == 0x2E:  # OPMSUB
            o = ['float s_[4], t_[4], a_[4], r_[4];',
                 'svsfast::ld4(s_, vf[%d]); svsfast::ld4(t_, vf[%d]); svsfast::ld4(a_, S.acc);' % (fs, ft),
                 'r_[0] = a_[0] - s_[1] * t_[2]; r_[1] = a_[1] - s_[2] * t_[0]; r_[2] = a_[2] - s_[0] * t_[1]; r_[3] = 0.0f;',
                 'svsfast::ld4(r_, r_); svsfast::st4(vf[%d], r_, %d);' % (fd, dest)]
            return o
        return None
    sp = (U & 3) | ((U >> 4) & 0x7C)
    if sp <= 0x0F:
        return arith(['add', 'sub', 'madd', 'msub'][sp >> 2], 'bc', True)
    if 0x10 <= sp <= 0x13:
        div = ['', ' / 16.0f', ' / 4096.0f', ' / 32768.0f'][sp - 0x10]
        o = []
        for c in L:
            o.append('{ int32_t iv; std::memcpy(&iv, &vf[%d][%d], 4); vf[%d][%d] = static_cast<float>(iv)%s; }' % (fs, c, ft, c, div))
        # ITOF reads all of vf[fs] before writing vf[ft] in the interpreter
        if fs == ft and len(L) > 1:
            o = ['float r_[4];']
            for c in range(4):
                o.append('{ int32_t iv; std::memcpy(&iv, &vf[%d][%d], 4); r_[%d] = static_cast<float>(iv)%s; }' % (fs, c, c, div))
            o.append('svsfast::st4(vf[%d], r_, %d);' % (ft, dest))
        return o
    if 0x14 <= sp <= 0x17:
        scale = ['1.0f', '16.0f', '4096.0f', '32768.0f'][sp - 0x14]
        o = ['float s_[4]; svsfast::ld4(s_, vf[%d]);' % fs]
        for c in L:
            o.append('{ const int32_t iv = vuFloatToInt(s_[%d], %s); std::memcpy(&vf[%d][%d], &iv, 4); }' % (c, scale, ft, c))
        return o
    t2 = {0x18: ('mul', 'bc'), 0x19: ('mul', 'bc'), 0x1A: ('mul', 'bc'), 0x1B: ('mul', 'bc'),
          0x1C: ('mul', 'q'), 0x1E: ('mul', 'i'),
          0x20: ('add', 'q'), 0x21: ('madd', 'q'), 0x22: ('add', 'i'), 0x23: ('madd', 'i'),
          0x24: ('sub', 'q'), 0x25: ('msub', 'q'), 0x26: ('sub', 'i'), 0x27: ('msub', 'i'),
          0x28: ('add', 'vt'), 0x29: ('madd', 'vt'), 0x2A: ('mul', 'vt'), 0x2C: ('sub', 'vt'), 0x2D: ('msub', 'vt')}
    if sp in t2:
        return arith(t2[sp][0], t2[sp][1], True)
    if sp == 0x1D:  # ABS
        o = ['float s_[4]; svsfast::ld4(s_, vf[%d]);' % fs]
        for c in L:
            o.append('vf[%d][%d] = std::fabs(s_[%d]);' % (ft, c, c))
        return o
    if sp == 0x2E:  # OPMULA
        return ['float s_[4], t_[4], r_[4];',
                'svsfast::ld4(s_, vf[%d]); svsfast::ld4(t_, vf[%d]);' % (fs, ft),
                'r_[0] = s_[1] * t_[2]; r_[1] = s_[2] * t_[0]; r_[2] = s_[0] * t_[1]; r_[3] = 0.0f;',
                'svsfast::ld4(r_, r_); svsfast::st4(S.acc, r_, %d);' % dest]
    if sp in (0x2F, 0x30):
        return []
    return None


# ------------------------------------------------------------------ lower

def gen_lower(p):
    """Inline code for the lower op, or None for the interpreter."""
    L = p['lower']
    if L in (0, 0x8000033C):
        return []
    dest = (L >> 21) & 0xF
    ft, fs = F(L, 16), F(L, 11)
    it, is_, id_ = (L >> 16) & 0xF, (L >> 11) & 0xF, (L >> 6) & 0xF
    op = (L >> 25) & 0x7F
    pc = p['pc']
    lanes_ = lanes(dest)

    def load_into(reg, addr_expr):
        o = ['{ uint32_t addr = (%s) & (dataSize - 1u);' % addr_expr,
             '  if (addr + 16u <= dataSize) { const float *src_ = reinterpret_cast<const float *>(vuData + addr);']
        if dest == 0xF:
            o.append('    float tmp_[4]; std::memcpy(tmp_, src_, 16); std::memcpy(vf[%d], tmp_, 16); } }' % reg)
        else:
            o.append('    float tmp_[4]; std::memcpy(tmp_, src_, 16); %s } }' % ' '.join('vf[%d][%d] = tmp_[%d];' % (reg, c, c) for c in lanes_))
        return o

    def store_words(words_expr_setup, addr_expr):
        # queueStore in fast mode: lanes land in VU memory immediately
        o = ['{ uint32_t addr = (%s) & (dataSize - 1u);' % addr_expr,
             '  if (addr + 16u <= dataSize) { %s' % words_expr_setup]
        if dest == 0xF:
            o.append('    std::memcpy(vuData + addr, w_, 16); } }')
        else:
            o.append('    %s } }' % ' '.join('std::memcpy(vuData + addr + %d, &w_[%d], 4);' % (c * 4, c) for c in lanes_))
        return o

    def store_from(reg, addr_expr):
        return store_words('uint32_t w_[4]; std::memcpy(w_, vf[%d], 16);' % reg, addr_expr)

    if op == 0x00:  # LQ
        return load_into(ft, '(static_cast<uint32_t>(static_cast<int32_t>(vi[%d] + (%d)))) * 16u' % (is_, imm11(L)))
    if op == 0x01:  # SQ
        return store_from(fs, '(static_cast<uint32_t>(static_cast<int32_t>(vi[%d] + (%d)))) * 16u' % (it, imm11(L)))
    if op in (0x08, 0x09):  # IADDIU / ISUBIU
        imm = (L & 0x7FF) | ((L >> 10) & 0x7800)
        if imm & 0x8000:
            imm -= 0x10000
        if it == 0:
            return []
        return ['vi[%d] = static_cast<int16_t>(vi[%d] %s (%d));' % (it, is_, '+' if op == 0x08 else '-', imm)]
    if op in (0x20, 0x21):  # B / BAL
        tgt = (pc + 8 + imm11(L) * 8) & MASK
        o = []
        if op == 0x21 and it != 0:
            o.append('vi[%d] = %d;' % (it, (pc + 16) // 8))
        o.append('S.branchPending = true; S.branchTarget = 0x%xu; S.branchDelay = 1;' % tgt)
        return o
    if op in (0x28, 0x29, 0x2C, 0x2D, 0x2E, 0x2F):  # IBxx
        tgt = (pc + 8 + imm11(L) * 8) & MASK
        a = 'static_cast<int16_t>(v.readBranchVi(%d))' % is_
        b = 'static_cast<int16_t>(v.readBranchVi(%d))' % it
        cond = {0x28: '%s == %s' % (a, b), 0x29: '%s != %s' % (a, b), 0x2C: '%s < 0' % a, 0x2D: '%s > 0' % a,
                0x2E: '%s <= 0' % a, 0x2F: '%s >= 0' % a}[op]
        return ['if (%s) { S.branchPending = true; S.branchTarget = 0x%xu; S.branchDelay = 1; }' % (cond, tgt)]
    if op in (0x04, 0x05):  # ILW / ISW
        comp = 0 if dest & 8 else (1 if dest & 4 else (2 if dest & 2 else 3))
        addr = '(static_cast<uint32_t>(static_cast<int32_t>(vi[%d] + (%d)))) * 16u' % (is_, imm11(L))
        if op == 0x04:
            if it == 0:
                return ['{ (void)0; }']
            return ['{ uint32_t addr = (%s) & (dataSize - 1u);' % addr,
                    '  if (addr + 16u <= dataSize) { uint32_t w; std::memcpy(&w, vuData + addr + %d, 4); vi[%d] = static_cast<int32_t>(static_cast<int16_t>(w & 0xFFFFu)); } }' % (comp * 4, it)]
        return store_words('const uint32_t val_ = static_cast<uint32_t>(static_cast<uint16_t>(vi[%d] & 0xFFFF)); const uint32_t w_[4] = {val_, val_, val_, val_};' % it, addr)
    if op == 0x10:  # FCEQ
        return ['vi[1] = ((S.clip & 0xFFFFFFu) == 0x%xu) ? 1 : 0;' % (L & 0xFFFFFF)]
    if op == 0x12:  # FCAND
        return ['vi[1] = ((S.clip & 0x%xu) != 0u) ? 1 : 0;' % (L & 0xFFFFFF)]
    if op == 0x13:  # FCOR
        return ['vi[1] = ((S.clip | 0x%xu) == 0xFFFFFFu) ? 1 : 0;' % (L & 0xFFFFFF)]
    if op == 0x1C:  # FCGET
        return ['vi[%d] = static_cast<int32_t>(S.clip & 0x0FFFu);' % it] if it else []
    if op == 0x40:
        funct = L & 0x3F
        if funct == 0x30 and id_:
            return ['vi[%d] = static_cast<int16_t>(vi[%d] + vi[%d]);' % (id_, is_, it)]
        if funct == 0x31 and id_:
            return ['vi[%d] = static_cast<int16_t>(vi[%d] - vi[%d]);' % (id_, is_, it)]
        if funct == 0x32:
            imm5 = (L >> 6) & 0x1F
            if imm5 & 0x10:
                imm5 -= 0x20
            return ['vi[%d] = static_cast<int16_t>(vi[%d] + (%d));' % (it, is_, imm5)] if it else []
        if funct == 0x34 and id_:
            return ['vi[%d] = vi[%d] & vi[%d];' % (id_, is_, it)]
        if funct == 0x35 and id_:
            return ['vi[%d] = vi[%d] | vi[%d];' % (id_, is_, it)]
        if funct in (0x30, 0x31, 0x34, 0x35):
            return []  # writes VI0
        if funct >= 0x3C:
            f2 = (L & 3) | ((L >> 4) & 0x7C)
            if f2 == 0x30:  # MOVE
                if dest == 0xF:
                    return ['{ float tmp_[4]; std::memcpy(tmp_, vf[%d], 16); std::memcpy(vf[%d], tmp_, 16); }' % (fs, ft)]
                return ['{ float tmp_[4]; std::memcpy(tmp_, vf[%d], 16); %s }' % (fs, ' '.join('vf[%d][%d] = tmp_[%d];' % (ft, c, c) for c in lanes_))]
            if f2 == 0x31:  # MR32
                return ['{ const float tmp_[4] = {vf[%d][1], vf[%d][2], vf[%d][3], vf[%d][0]}; %s }' % (fs, fs, fs, fs, ' '.join('vf[%d][%d] = tmp_[%d];' % (ft, c, c) for c in lanes_))]
            if f2 == 0x34:  # LQI
                o = load_into(ft, '(static_cast<uint32_t>(static_cast<uint16_t>(vi[%d]))) * 16u' % is_)
                if is_:
                    o.append('vi[%d] = static_cast<int16_t>(vi[%d] + 1);' % (is_, is_))
                return o
            if f2 == 0x3B:  # WAITQ (the wait is in the readiness check)
                return []
            if f2 == 0x3C:  # MTIR
                comp = (L >> 21) & 3
                return ['{ uint32_t fv_; std::memcpy(&fv_, &vf[%d][%d], 4); vi[%d] = static_cast<int32_t>(static_cast<int16_t>(fv_ & 0xFFFFu)); }' % (fs, comp, it)] if it else []
            if f2 == 0x3D:  # MFIR
                return ['{ const int32_t val_ = static_cast<int32_t>(static_cast<int16_t>(vi[%d] & 0xFFFF)); %s }' % (is_, ' '.join('std::memcpy(&vf[%d][%d], &val_, 4);' % (ft, c) for c in lanes_))]
            if f2 == 0x3E:  # ILWR
                comp = 0 if dest & 8 else (1 if dest & 4 else (2 if dest & 2 else 3))
                if it == 0:
                    return []
                return ['{ uint32_t addr = ((static_cast<uint32_t>(static_cast<uint16_t>(vi[%d]))) * 16u) & (dataSize - 1u);' % is_,
                        '  if (addr + 16u <= dataSize) { uint32_t w; std::memcpy(&w, vuData + addr + %d, 4); vi[%d] = static_cast<int32_t>(static_cast<int16_t>(w & 0xFFFFu)); } }' % (comp * 4, it)]
            if f2 == 0x3F:  # ISWR
                return store_words('const uint32_t val_ = static_cast<uint32_t>(static_cast<uint16_t>(vi[%d] & 0xFFFF)); const uint32_t w_[4] = {val_, val_, val_, val_};' % it,
                                   '(static_cast<uint32_t>(static_cast<uint16_t>(vi[%d]))) * 16u' % is_)
            if f2 == 0x35:  # SQI
                o = store_from(fs, '(static_cast<uint32_t>(static_cast<uint16_t>(vi[%d]))) * 16u' % it)
                if it:
                    o.append('vi[%d] = static_cast<int16_t>(vi[%d] + 1);' % (it, it))
                return o
    return None


def special_ready(p):
    """Inline form of the pipeline part of calculatePairReadyCycle, by lower opcode."""
    if p['flags'] & F_IBIT:
        return None
    L = p['lower']
    if not (L >> 31) or (L & 0x3F) < 0x3C:
        return None
    f2 = (L & 3) | ((L >> 4) & 0x7C)
    if f2 in (0x38, 0x39, 0x3A, 0x3B):  # DIV / SQRT / RSQRT (FDIV pipe) and WAITQ
        return ['if (v.m_fdiv.valid && v.m_fdiv.readyCycle > r_) r_ = v.m_fdiv.readyCycle;']
    if f2 == 0x6C:  # XGKICK
        return ['if (v.m_xgkick.active && cyc + 1u > r_) r_ = cyc + 1u;']
    if f2 == 0x7B:  # WAITP
        return ['for (const auto &e_ : v.m_efu) if (e_.valid && e_.readyCycle > r_) r_ = e_.readyCycle;']
    if 0x70 <= f2 <= 0x7D:  # EFU ops
        return ['if (v.m_efuResourceReady > r_) r_ = v.m_efuResourceReady;']
    return None


# ------------------------------------------------------------------ pair

def writes_zero(p, up_inline, lo_inline):
    """Could this pair write VF0/VI0 (which the interpreter resets after every pair)?"""
    if not up_inline or (not lo_inline and not (p['flags'] & F_IBIT)):
        return True
    U = p['upper']
    op = U & 0x3F
    if op < 0x3C:
        if op not in (0x2F,) or True:
            if (U >> 6) & 0x1F == 0 and op <= 0x2F:
                return True
    else:
        sp = (U & 3) | ((U >> 4) & 0x7C)
        if 0x10 <= sp <= 0x17 or sp == 0x1D:
            if (U >> 16) & 0x1F == 0:
                return True
    if p['flags'] & F_IBIT:
        return False
    L = p['lower']
    if L in (0, 0x8000033C):
        return False
    op = (L >> 25) & 0x7F
    ft = (L >> 16) & 0x1F
    if op == 0x00 and ft == 0:
        return True
    if op == 0x40 and (L & 0x3F) >= 0x3C:
        f2 = (L & 3) | ((L >> 4) & 0x7C)
        if f2 in (0x30, 0x31, 0x34, 0x3D) and ft == 0:
            return True
    return False


def ready_name(w):
    if w < 128:
        return 'vfr[%d]' % w
    if w < 144:
        return 'vir[%d]' % (w - 128)
    return 'accr[%d]' % (w - 144)


def gen_pair(o, p, size, prev, recent=None):
    """prev: the pair at pc-8 (static predecessor) or None.
    recent: ready slots written by the 3 straight-line predecessors (pc-8..pc-24), or
    None when unknown. Every latency is <= 4 cycles and every pair takes >= 1 cycle, so
    after 4 pairs without a jump (se_ >= 4) only those slots can still be pending."""
    pc = p['pc']
    idx = p['idx']
    flags = p['flags']
    o.append('L_%04x:' % pc)
    o.append('{')
    o.append('    if (cyc >= budgetEnd) { S.pc = 0x%xu; SVS_AOT_EXIT(2); }' % pc)
    o.append('    if (cyc >= s_svsNextCommit[1]) { v.m_cycle = cyc; v.commitReadyPipelines(); }')
    if flags & (F_URES | F_LRES):
        o.append('    S.pc = 0x%xu; SVS_AOT_EXIT(0);' % pc)
        o.append('}')
        return (pc + 8) % size
    reads = []
    for r in p['reads']:
        k, n = r[0], int(r[1:])
        reads.append({'f': 'vfr[%d]', 'i': 'vir[%d]', 'a': 'accr[%d]'}[k] % n)
    if reads or (flags & F_SPECIAL):
        o.append('    {')
        o.append('        uint64_t r_ = cyc;')
        hot = [e for e in reads if recent is not None and e in recent]
        if recent is not None and len(hot) < len(reads):
            # se_: pairs run since the last jump/entry/interpreter pair
            o.append('        if (se_ < 4u) {')
            for e in reads:
                o.append('            if (%s > r_) r_ = %s;' % (e, e))
            o.append('        } else {')
            for e in hot:
                o.append('            if (%s > r_) r_ = %s;' % (e, e))
            o.append('        }')
        else:
            for e in reads:
                o.append('        if (%s > r_) r_ = %s;' % (e, e))
        if flags & F_SPECIAL:
            sp = special_ready(p)
            if sp is None:
                o.append('        v.m_cycle = cyc; S.pc = 0x%xu; { const uint64_t q_ = v.calculatePairReadyCycle(v.m_decodedCodeCache[%d]); if (q_ > r_) r_ = q_; }' % (pc, idx))
            else:
                for line in sp:
                    o.append('        ' + line)
        o.append('        if (r_ > cyc) { v.m_cycle = cyc; S.pc = 0x%xu; const bool b_ = svsAotStall(v, %d, r_, budgetEnd); cyc = v.m_cycle; if (b_) { S.pc = 0x%xu; SVS_AOT_EXIT(2); } }' % (pc, idx, pc))
        o.append('    }')
    o.append('    ++ninstr;')
    viw = p['viw'] & 0xFFFE
    k = 0
    if viw:
        k = (viw & -viw).bit_length() - 1
    need_old = bool(viw) and bool(flags & F_DELAYBR)
    if need_old:
        o.append('    const int32_t oldVi_ = vi[%d];' % k)
    live = bool(flags & F_FLAGS)
    up = None if live else gen_upper(p, o)
    lo = None if (flags & F_IBIT) else gen_lower(p)
    generic = False
    if p['shadow']:
        generic = True
        s_ = p['shadow']
        o.append('    S.pc = 0x%xu; s_svsFlagsLive[1] = %s;' % (pc, 'true' if live else 'false'))
        o.append('    { float oldVf_[4], upperVf_[4];')
        o.append('      std::memcpy(oldVf_, vf[%d], 16);' % s_)
        o.append('      v.execUpper(0x%08xu);' % p['upper'])
        o.append('      std::memcpy(upperVf_, vf[%d], 16);' % s_)
        o.append('      std::memcpy(vf[%d], oldVf_, 16);' % s_)
        o.append('      v.m_cycle = cyc; v.execLower(0x%08xu, vuData, dataSize, gs, memory, 0x%08xu); cyc = v.m_cycle;' % (p['lower'], p['upper']))
        o.append('      std::memcpy(vf[%d], upperVf_, 16); }' % s_)
    else:
        if up is None:
            generic = True
            o.append('    S.pc = 0x%xu; s_svsFlagsLive[1] = %s; v.m_cycle = cyc; v.execUpper(0x%08xu);' % (pc, 'true' if live else 'false', p['upper']))
        else:
            o.append('    {')
            for line in up:
                o.append('        ' + line)
            o.append('    }')
        if flags & F_IBIT:
            o.append('    { const uint32_t imm_ = 0x%08xu; float f_; std::memcpy(&f_, &imm_, 4); S.i = v.normalizeOperand(f_); }' % p['lower'])
        elif lo is None:
            generic = True
            o.append('    S.pc = 0x%xu; s_svsFlagsLive[1] = %s; v.m_cycle = cyc; v.execLower(0x%08xu, vuData, dataSize, gs, memory, 0x%08xu); cyc = v.m_cycle;' % (pc, 'true' if live else 'false', p['lower'], p['upper']))
        else:
            o.append('    {')
            for line in lo:
                o.append('        ' + line)
            o.append('    }')
    o.append('    v.m_viBranchBackupValid = false;')
    for (w, lat) in p['writes']:
        if w < 128:
            o.append('    vfr[%d] = cyc + %du;' % (w, lat))
        elif w < 144:
            o.append('    vir[%d] = cyc + %du;' % (w - 128, lat))
        else:
            o.append('    accr[%d] = cyc + %du;' % (w - 144, lat))
    if need_old:
        o.append('    v.recordViWriteForBranch(%d, oldVi_);' % k)
    if writes_zero(p, up is not None and not p['shadow'], (lo is not None) and not p['shadow']):
        o.append('    vf[0][0] = 0.0f; vf[0][1] = 0.0f; vf[0][2] = 0.0f; vf[0][3] = 1.0f; vi[0] = 0;')
    nxt = (pc + 8) % size
    is_branch = bool(flags & F_BRANCH)
    after_branch = prev is not None and bool(prev['flags'] & F_BRANCH)
    after_e = prev is not None and bool(prev['flags'] & F_EBIT)
    may_jump = is_branch or after_branch or generic
    if may_jump:
        o.append('    uint32_t npc_ = 0x%xu;' % nxt)
        o.append('    bool jumped_ = false;')
        o.append('    if (S.branchPending) { if (S.branchDelay == 0u) { npc_ = S.branchTarget & 0x%xu; S.branchPending = false; jumped_ = true; } else --S.branchDelay; }' % MASK)
    # halting (programs with D/T bits are not compiled, so only the E bit matters)
    if after_e or generic:
        o.append('    const bool ended_ = S.ebit;')
    else:
        o.append('    const bool ended_ = false;')
    if flags & F_EBIT:
        o.append('    if (!ended_) S.ebit = true;')
    o.append('    ++cyc;')
    # interpreter-run pairs may leave other latencies behind: start counting again
    o.append('    se_ = 0u;' if generic else '    ++se_;')
    if generic:
        o.append('    if (v.m_xgkick.active) { v.m_cycle = cyc; S.cycles = cyc; v.commitReadyPipelines(); v.progressXgkick(); cyc = v.m_cycle; }')
    if may_jump:
        o.append('    if (ended_) { S.pc = npc_; SVS_AOT_EXIT(1); }')
        o.append('    if (jumped_) { pc = npc_; goto dispatch; }')
    else:
        o.append('    if (ended_) { S.pc = 0x%xu; SVS_AOT_EXIT(1); }' % nxt)
    o.append('}')
    return nxt


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    dump_dir, out_path = sys.argv[1], sys.argv[2]
    progs = {}  # signature -> [entry pc, pairs, size, order]
    for path in sorted(glob.glob(os.path.join(dump_dir, 'vu1_*.txt'))):
        size, pairs = load_image(path)
        ent_path = path[:-4] + '.entries'
        if not pairs or not os.path.exists(ent_path):
            continue
        entries = sorted({int(x) & MASK for x in open(ent_path).read().split()})
        reaches = {e: reachable(pairs, [e], size) for e in entries}
        # keep a minimal set: an entry already inside another kept program is reached
        # through that program's dispatch switch (the runtime maps every covered pc)
        kept, covered = [], set()
        for e in sorted(entries, key=lambda x: -len(reaches[x])):
            if e in covered or not reaches[e]:
                continue
            kept.append(e)
            covered |= reaches[e]
        for e in kept:
            reach = reaches[e]
            order = sorted(reach)
            # D/T bits make halting depend on the debug enables: keep those in the interpreter
            if any(pairs[pc]['flags'] & (F_DBIT | F_TBIT) for pc in order):
                continue
            sig = hashlib.sha1(repr((e, [(pc, pairs[pc]['upper'], pairs[pc]['lower']) for pc in order])).encode()).hexdigest()[:16]
            if sig not in progs:
                progs[sig] = [e, pairs, size, order]

    o = ['// Generado por tools/vu1_aot.py: microprogramas del VU1 compilados a C++ (no editar).',
         '// Incluido al final de ps2_vu1_core.cpp, después de ps2_vu1_fast.inc.',
         '#define SVS_AOT_EXIT(code) do { v.m_cycle = cyc; S.cycles = cyc; g_svsVuInstr += ninstr; g_svsAotInstr += ninstr; ++g_svsAotExit[(code)]; v.commitReadyPipelines(); return (code); } while (0)',
         'namespace svsaot {']
    names = []
    total = 0
    for sig, (entry, pairs, size, order) in sorted(progs.items(), key=lambda kv: (kv[1][0], kv[0])):
        name = 'prog_%04x_%s' % (entry, sig)
        reach_set = set(order)
        names.append((name, entry, order, pairs))
        total += len(order)
        o.append('static int %s(VU1Interpreter &v, uint8_t *vuData, uint32_t dataSize, GS &gs, PS2Memory *memory, uint64_t budgetEnd)' % name)
        o.append('{')
        o.append('    VU1State &S = v.m_state;')
        o.append('    float (*vf)[4] = S.vf;')
        o.append('    int32_t *vi = S.vi;')
        o.append('    uint64_t *vfr = &v.m_vfReady[0][0];')
        o.append('    uint64_t *vir = v.m_viReady.data();')
        o.append('    uint64_t *accr = v.m_accReady.data();')
        o.append('    uint64_t cyc = v.m_cycle;')
        o.append('    uint64_t ninstr = 0;')
        o.append('    uint32_t pc = S.pc;')
        o.append('    uint32_t se_ = 0u; // pairs run straight since the last entry/jump')
        o.append('    (void)gs; (void)memory; (void)vuData; (void)dataSize;')
        o.append('dispatch:')
        o.append('    se_ = 0u;')
        o.append('    if (v.m_stopRequested) { S.pc = pc; SVS_AOT_EXIT(2); }')
        o.append('    switch (pc)')
        o.append('    {')
        for pc in order:
            o.append('    case 0x%xu: goto L_%04x;' % (pc, pc))
        o.append('    default: S.pc = pc; SVS_AOT_EXIT(0);')
        o.append('    }')
        for i, pc in enumerate(order):
            prevpc = (pc - 8) % size
            # writes of the straight-line predecessors (only when they are compiled here:
            # se_ >= 4 then means exactly those 3 pairs ran right before this one)
            recent = set()
            for d in (8, 16, 24):
                q = pairs.get((pc - d) % size)
                if q is None or ((pc - d) % size) not in reach_set:
                    recent = None
                    break
                for (w, lat) in q['writes']:
                    recent.add(ready_name(w))
            nxt = gen_pair(o, pairs[pc], size, pairs.get(prevpc), recent)
            following = order[i + 1] if i + 1 < len(order) else None
            if nxt != following:
                o.append('    pc = 0x%xu; goto dispatch;' % nxt)
        o.append('}')
        o.append('static const uint32_t %s_sig[] = {' % name)
        line = []
        for pc in order:
            line.append('0x%xu, 0x%08xu, 0x%08xu' % (pc, pairs[pc]['upper'], pairs[pc]['lower']))
            if len(line) == 4:
                o.append('    ' + ', '.join(line) + ',')
                line = []
        if line:
            o.append('    ' + ', '.join(line) + ',')
        o.append('};')
    o.append('using Fn = int (*)(VU1Interpreter &, uint8_t *, uint32_t, GS &, PS2Memory *, uint64_t);')
    o.append('struct Prog { Fn fn; uint32_t entry; const uint32_t *sig; uint32_t count; };')
    o.append('static const Prog kProgs[] = {')
    for name, entry, order, pairs in names:
        o.append('    {%s, 0x%xu, %s_sig, %du},' % (name, entry, name, len(order)))
    o.append('};')
    o.append('static constexpr uint32_t kProgCount = %d;' % len(names))
    o.append('} // namespace svsaot')
    o.append('#undef SVS_AOT_EXIT')
    with open(out_path, 'w') as f:
        f.write('\n'.join(o) + '\n')
    print('%d programas, %d pares compilados -> %s' % (len(names), total, out_path))


if __name__ == '__main__':
    main()
