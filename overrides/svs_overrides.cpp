#include <cmath>
// svs_overrides.cpp - Parches específicos de Spy vs. Spy (SLES_530.78) para PS2Recomp.
//
// Se copia a ps2xRuntime/src/runner/ junto con el código generado.

#include "ps2_runtime.h"
#include "ps2_runtime_macros.h"
#include "game_overrides.h"

#include <cctype>
#include <cstdlib>
#include <algorithm>
#include <cstdio>
#include <cstring>
#include <vector>
#include <map>
#include <iostream>
#include <string>
namespace ps2_stubs { void sceSifFreeIopHeap(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime); }

extern float g_svsDisplayAspect; // set by the presenter (ps2_runtime.cpp)

namespace ps2_stubs
{
    // HLE del runtime (Kernel/Stubs/MPEG.cpp)
    void sceMpegIsEnd(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime);
}

namespace
{
    // ---------------------------------------------------------------------
    // 0x2D5930: parte de _InitSys de libkernel (llamada desde 0x2D5A40).
    //
    // Busca en la RAM del kernel (0x80000000-0x80080000), con el syscall
    // FindAddress (0x83), dos punteros a handlers del juego (0x2D58D8 y
    // 0x2D58A0) para ubicar una tabla interna del kernel y parchearla.
    // En la consola real siempre los encuentra; en PS2Recomp el kernel es HLE
    // y esa tabla no existe en memoria, así que FindAddress devuelve 0 y el
    // bucle de búsqueda nunca termina -> pantalla rosada para siempre.
    //
    // Nadie más lee el resultado (0x3DE938), así que basta con dejarlo en 0
    // y volver.
    // ---------------------------------------------------------------------
    void svs_InitSys_KernelTableScan(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)runtime;
        FAST_WRITE32(0x3DE938u, 0u);
        ctx->pc = GPR_U32(ctx, 31);
    }

    // ---------------------------------------------------------------------
    // Funciones de módulos IOP del SDK que PS2Recomp no tiene en HLE.
    // Las originales llaman a _lf_bind, que espera para siempre al servidor
    // RPC "loadfile" (0x80000006) del IOP; el runtime carga módulos por HLE
    // y ese servidor no existe -> el juego se queda en rosado.
    // ---------------------------------------------------------------------
    std::string guestString(uint8_t *rdram, uint32_t addr)
    {
        std::string out;
        addr &= 0x01FFFFFFu;
        while (addr < 0x02000000u && rdram[addr] != 0 && out.size() < 64)
            out.push_back(static_cast<char>(rdram[addr++]));
        return out;
    }

    // Módulos que el juego cargó desde el disco: nombre ("eznetcnf") -> id.
    // Se llena envolviendo _sceSifLoadModule; el nombre se saca del archivo
    // (cdrom0:\MODULES\EZNETCNF.IRX;1 -> "eznetcnf"), que en este juego coincide
    // con el nombre interno del IRX.
    std::map<std::string, int32_t> g_loadedModules;
    PS2Runtime::RecompiledFunction g_origLoadModule = nullptr;

    std::string moduleNameFromPath(std::string path)
    {
        const size_t slash = path.find_last_of("\\/:");
        if (slash != std::string::npos)
            path = path.substr(slash + 1);
        const size_t dot = path.find_first_of(".;");
        if (dot != std::string::npos)
            path = path.substr(0, dot);
        for (char &c : path)
            c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        return path;
    }

    // int _sceSifLoadModule(const char *path, int arglen, const char *args, int *modres, int fno)  @ 0x2D52A8
    void svs_sceSifLoadModule(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const std::string name = moduleNameFromPath(guestString(rdram, GPR_U32(ctx, 4)));
        g_origLoadModule(rdram, ctx, runtime);
        const int32_t id = static_cast<int32_t>(GPR_U32(ctx, 2));
        if (id > 0 && !name.empty())
            g_loadedModules[name] = id;
    }

    // int sceSifSearchModuleByName(const char *name)  @ 0x2D51E0
    // Los módulos que vienen en la BIOS de la PS2 siempre "existen"; los del
    // disco existen si el juego ya los cargó; el resto (HDD) no.
    void svs_sceSifSearchModuleByName(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)runtime;
        std::string name = guestString(rdram, GPR_U32(ctx, 4));
        for (char &c : name)
            c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
        static const char *kBiosModules[] = {"cdvd_ee_driver", "cdvd_iop_driver", "sifcmd", "sifman",
                                             "loadcore", "sysmem", "intrman", "thbase", "ioman"};
        int32_t result = -202; // KE_UNKNOWN_MODULE
        for (int i = 0; i < static_cast<int>(sizeof(kBiosModules) / sizeof(kBiosModules[0])); ++i)
        {
            if (name == kBiosModules[i])
            {
                result = 100 + i;
                break;
            }
        }
        auto it = g_loadedModules.find(name);
        if (it != g_loadedModules.end())
            result = it->second;
        std::cout << "[svs] sceSifSearchModuleByName(\"" << name << "\") -> " << result << std::endl;
        SET_GPR_S32(ctx, 2, result);
        ctx->pc = GPR_U32(ctx, 31);
    }

    // int sceSifStopModule(int modid, int arglen, const char *args, int *result)  @ 0x2D4F48
    void svs_sceSifStopModule(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)runtime;
        const uint32_t resultPtr = GPR_U32(ctx, 7);
        if (resultPtr != 0u)
            FAST_WRITE32(resultPtr, 1u); // NO_RESIDENT_END
        SET_GPR_S32(ctx, 2, static_cast<int32_t>(GPR_U32(ctx, 4)));
        ctx->pc = GPR_U32(ctx, 31);
    }

    // int sceSifUnloadModule(int modid)  @ 0x2D5150
    void svs_sceSifUnloadModule(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)rdram;
        (void)runtime;
        SET_GPR_S32(ctx, 2, static_cast<int32_t>(GPR_U32(ctx, 4)));
        ctx->pc = GPR_U32(ctx, 31);
    }

    // ---------------------------------------------------------------------
    // libfileio interna: el runtime ya hace sceOpen/sceRead/... por HLE, pero
    // sceDevctl, scePowerOffHandler y la espera por semáforo siguen siendo el
    // código original, que habla con el IOP real por SIF RPC.
    // ---------------------------------------------------------------------
    void svs_returnZero(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)rdram;
        (void)runtime;
        SET_GPR_S32(ctx, 2, 0);
        ctx->pc = GPR_U32(ctx, 31);
    }

    // int sceDevctl(const char *name, int cmd, void *arg, int arglen, void *buf, int buflen)  @ 0x2D4730
    void svs_sceDevctl(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)runtime;
        const std::string name = guestString(rdram, GPR_U32(ctx, 4));
        const uint32_t cmd = GPR_U32(ctx, 5);
        int32_t result = 0;
        if (name.rfind("hdd", 0) == 0 || name.rfind("pfs", 0) == 0)
            result = -19; // ENODEV: no hay disco duro
        std::cout << "[svs] sceDevctl(\"" << name << "\", 0x" << std::hex << cmd << std::dec << ") -> " << result
                  << std::endl;
        SET_GPR_S32(ctx, 2, result);
        ctx->pc = GPR_U32(ctx, 31);
    }

    // int sceCdPowerOff(int *result)  @ 0x2C0EA8
    // El original hace bind al servidor S-command del CD (0x80000593) en un
    // bucle infinito; el runtime emula el CD por HLE y ese servidor no existe.
    void svs_sceCdPowerOff(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        (void)runtime;
        const uint32_t resultPtr = GPR_U32(ctx, 4);
        if (resultPtr != 0u)
            FAST_WRITE32(resultPtr, 0u);
        SET_GPR_S32(ctx, 2, 1);
        ctx->pc = GPR_U32(ctx, 31);
    }

    // --- depuración: quién llama a DelayThread (SVS_TRACE_DELAY=1) ---
    PS2Runtime::RecompiledFunction g_origDelay = nullptr;
    void svs_traceDelay(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        static std::map<uint32_t, int> seen;
        const uint32_t ra = GPR_U32(ctx, 31);
        int &n = seen[ra];
        if (n++ < 3 || (n & 1023) == 0)
            std::cout << "[svs] DelayThread(" << GPR_U32(ctx, 4) << ") ra=0x" << std::hex << ra << std::dec
                      << " x" << n << std::endl;
        g_origDelay(rdram, ctx, runtime);
    }

    PS2Runtime::RecompiledFunction g_origNetReq = nullptr;
    void svs_traceNetReq(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t obj = GPR_U32(ctx, 4);
        const std::string arg = GPR_U32(ctx, 5) ? guestString(rdram, GPR_U32(ctx, 5)) : std::string("(null)");
        std::cout << "[svs] netreq-in arg=\"" << arg << "\"" << std::endl;
        g_origNetReq(rdram, ctx, runtime);
        std::cout << "[svs] netreq obj=0x" << std::hex << obj << " arg=\"" << arg << "\" -> " << std::dec
                  << static_cast<int32_t>(GPR_U32(ctx, 2)) << " status=" << static_cast<int32_t>(FAST_READ32(obj))
                  << std::endl;
    }

    PS2Runtime::RecompiledFunction g_origResize = nullptr;
    void svs_traceResize(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const int32_t n = static_cast<int32_t>(GPR_U32(ctx, 5));
        if (n < 0 || n > 0x100000)
            std::cout << "[svs] vector resize(" << n << ") vec=0x" << std::hex << GPR_U32(ctx, 4) << " ra=0x"
                      << GPR_U32(ctx, 31) << " sp=0x" << GPR_U32(ctx, 29) << std::dec << std::endl;
        if (n < 0 || n > 0x100000)
        {
            const uint32_t obj = 0x423180u;
            const uint32_t slot = FAST_READ32(obj + 0x10000u);
            std::cout << "[svs]   stream size=0x" << std::hex << FAST_READ32(obj + 0x10018u) << " pos=0x"
                      << (FAST_READ32(obj + 0x10000u + 0xCu + slot * 4u) + FAST_READ32(obj + 0x10008u)) << std::dec << std::endl;
        }
        g_origResize(rdram, ctx, runtime);
    }

    PS2Runtime::RecompiledFunction g_origStreamRead = nullptr;
    std::FILE *g_pak = nullptr;

    void svs_traceStreamRead(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t obj = GPR_U32(ctx, 4);
        const uint32_t slot = FAST_READ32(obj + 0x10000u);
        const uint32_t off = FAST_READ32(obj + 0x10008u);
        const uint32_t pos = FAST_READ32(obj + 0x10000u + 0xCu + slot * 4u) + off;
        const uint32_t size = FAST_READ32(obj + 0x10018u);
        const uint32_t want = GPR_U32(ctx, 6);
        static int reported = 0;
        if (size == 0xE01AC4u && reported < 8)
        {
            if (!g_pak)
                g_pak = std::fopen("DATA.PAK", "rb");
            const uint32_t n = std::min<uint32_t>({want, 0x8000u - off, size > pos ? size - pos : 0u});
            std::vector<uint8_t> expect(n);
            std::fseek(g_pak, static_cast<long>(0x786C800u + pos), SEEK_SET);
            if (g_pak && std::fread(expect.data(), 1, n, g_pak) == n)
            {
                const uint32_t src = obj + (slot << 15) + off;
                for (uint32_t i = 0; i < n; ++i)
                {
                    if (rdram[(src + i) & 0x01FFFFFFu] != expect[i])
                    {
                        std::cout << "[svs] BLOQUE DISTINTO pos=0x" << std::hex << (pos + i) << " slot=" << slot
                                  << " off=0x" << (off + i) << " leido=0x" << static_cast<int>(rdram[(src + i) & 0x01FFFFFFu])
                                  << " esperado=0x" << static_cast<int>(expect[i]) << std::dec << std::endl;
                        ++reported;
                        break;
                    }
                }
            }
        }
        if (pos + want > size)
            std::cout << "[svs] LECTURA MAS ALLA DEL FIN obj=0x" << std::hex << obj << " size=0x" << size << " pos=0x" << pos
                      << " len=0x" << want << " ra=0x" << GPR_U32(ctx, 31) << std::dec << std::endl;
        g_origStreamRead(rdram, ctx, runtime);
    }

    PS2Runtime::RecompiledFunction g_origLoadVec = nullptr;
    void svs_traceLoadVec(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t reader = GPR_U32(ctx, 5);
        const uint32_t p1 = reader ? FAST_READ32(reader) : 0u;
        const uint32_t stream = p1 ? FAST_READ32(p1) : 0u;
        if (stream == 0u)
            std::cout << "[svs] LoadVector con stream nulo reader=0x" << std::hex << reader << " *reader=0x" << p1
                      << " ra=0x" << GPR_U32(ctx, 31) << " a0=0x" << GPR_U32(ctx, 4) << std::dec << std::endl;
        g_origLoadVec(rdram, ctx, runtime);
    }

    // --- depuración: contar llamadas a funciones (SVS_TRACE_CALLS=0x...,0x...) ---
    std::map<uint32_t, PS2Runtime::RecompiledFunction> g_tracedOriginals;
    std::map<uint32_t, uint32_t> g_tracedCounts;
    template <int Slot>
    void svs_traceCall(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime);
    std::vector<uint32_t> g_tracedSlots;
    template <int Slot>
    void svs_traceCall(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t address = g_tracedSlots[Slot];
        uint32_t &n = g_tracedCounts[address];
        if (n++ < 3 || (n & (n - 1)) == 0)
            std::cout << "[svs] llamada 0x" << std::hex << address << " ra=0x" << GPR_U32(ctx, 31) << " a0=0x"
                      << GPR_U32(ctx, 4) << std::dec << " #" << n << std::endl;
        g_tracedOriginals[address](rdram, ctx, runtime);
    }
    using TraceFn = void (*)(uint8_t *, R5900Context *, PS2Runtime *);
    constexpr TraceFn kTraceFns[] = {svs_traceCall<0>, svs_traceCall<1>, svs_traceCall<2>, svs_traceCall<3>,
                                     svs_traceCall<4>, svs_traceCall<5>, svs_traceCall<6>, svs_traceCall<7>};

    void installCallTraces(PS2Runtime &runtime)
    {
        const char *list = std::getenv("SVS_TRACE_CALLS");
        if (!list)
            return;
        std::string text(list);
        size_t pos = 0;
        while (pos < text.size() && g_tracedSlots.size() < 8u)
        {
            const size_t comma = text.find(',', pos);
            const uint32_t address = static_cast<uint32_t>(std::stoul(text.substr(pos, comma - pos), nullptr, 16));
            if (auto original = runtime.lookupFunction(address))
            {
                g_tracedOriginals[address] = original;
                const int slot = static_cast<int>(g_tracedSlots.size());
                g_tracedSlots.push_back(address);
                runtime.registerFunction(address, kTraceFns[slot]);
            }
            if (comma == std::string::npos)
                break;
            pos = comma + 1;
        }
    }

    void svsMaybeDumpRam(uint8_t *rdram)
    {
        static const bool on = std::getenv("SVS_RAMDUMP") != nullptr;
        if (!on) return;
        static unsigned n = 0;
        if ((++n & 1023u) != 0u) return;
        if (std::remove("/tmp/ramdump_req") == 0)
            if (std::FILE *f = std::fopen("/tmp/rdram.bin", "wb")) { std::fwrite(rdram, 1, 32u * 1024u * 1024u, f); std::fclose(f); }
    }
    bool g_svsMatTrace = false;
    constexpr uint32_t kMatFns[9] = {0x257490u, 0x255728u, 0x257750u, 0x25a838u, 0x25b150u, 0x25b800u, 0x25bec0u, 0x25c540u, 0x248200u};
    PS2Runtime::RecompiledFunction g_origMat[9] = {};
    bool g_svsMatFix = true;
    void svsMatLog(int idx, uint8_t *rdram, R5900Context *ctx)
    {
        const uint32_t mat = getRegU32(ctx, 4);
        static std::map<uint64_t, uint32_t> seen;
        uint32_t col = 0;
        std::memcpy(&col, rdram + ((mat + 0x64u) & 0x1FFFFFFu), 4);
        const uint64_t key = (static_cast<uint64_t>(idx) << 32) | mat;
        auto it = seen.find(key);
        if (it == seen.end() || it->second != col)
        {
            seen[key] = col;
            if (seen.size() < 600)
                std::fprintf(stderr, "[svs-mat] fn=0x%x mat=0x%x col=0x%08x ra=0x%x a1=0x%x f12=%g f13=%g\n", kMatFns[idx], mat, col, getRegU32(ctx, 31), getRegU32(ctx, 5), ctx->f[12], ctx->f[13]);
        }
    }
    void svsMatAfter(int idx, uint8_t *rdram, uint32_t a0, uint32_t a1, uint32_t ra)
    {
        {
            static std::map<uint32_t, int> dumped;
            static const bool matDump = std::getenv("SVS_MAT_DUMP") != nullptr;
            if (matDump && dumped[a0]++ == 0 && dumped.size() < 12)
            {
                std::fprintf(stderr, "[svs-matdump] mat=0x%x fn=0x%x:", a0, kMatFns[idx]);
                for (int i = 0; i < 40; ++i) { uint32_t w; std::memcpy(&w, rdram + ((a0 + i * 4) & 0x1FFFFFFu), 4); std::fprintf(stderr, " %08x", w); }
                std::fprintf(stderr, "\n");
            }
        }
        float cur[4];
        std::memcpy(cur, rdram + 0x44a470u, 16);
        static std::map<std::string, int> seen;
        char key[256];
        float l[8];
        std::memcpy(l, rdram + (a1 & 0x1FFFFFFu), 32);
        std::snprintf(key, sizeof(key), "fn=0x%x ra=0x%x out=(%.2f %.2f %.2f)", kMatFns[idx], ra, cur[0], cur[1], cur[2]);
        if (seen[key]++ == 0 && seen.size() < 200)
            std::fprintf(stderr, "[svs-matout] mat=0x%x %s a=%.3f a1[0..7]=(%.3g %.3g %.3g %.3g %.3g %.3g %.3g %.3g)\n", a0, key, cur[3], l[0], l[1], l[2], l[3], l[4], l[5], l[6], l[7]);
    }
    void svsFixBakedTint(R5900Context *c)
    {
        static const bool tintTrace = std::getenv("SVS_TINT_TRACE") != nullptr;
        if (tintTrace)
        {
            static std::map<uint64_t, int> seen;
            const uint64_t k = (static_cast<uint64_t>(getRegU32(c, 31)) << 32) | getRegU32(c, 8);
            if (seen[k]++ == 0 && seen.size() < 50) std::fprintf(stderr, "[svs-tint] ra=0x%x t0=0x%x a3=0x%x\n", getRegU32(c, 31), getRegU32(c, 8), getRegU32(c, 7));
        }
    }
    void svsTintAfter(uint8_t *rdram, uint32_t ra)
    {
        static const bool tintTrace = std::getenv("SVS_TINT_TRACE") != nullptr;
        if (!tintTrace || ra != 0x25DE34u) return;
        float cur[4];
        std::memcpy(cur, rdram + 0x44a470u, 16);
        static std::map<std::string, int> seen;
        char key[96];
        std::snprintf(key, sizeof(key), "(%.3f %.3f %.3f %.3f)", cur[0], cur[1], cur[2], cur[3]);
        if (seen[key]++ == 0) std::fprintf(stderr, "[svs-tint] after B material: cache=%s\n", key);
    }
#define SVS_MAT_WRAP(i) void svs_mat##i(uint8_t *r, R5900Context *c, PS2Runtime *rt) { const uint32_t raIn = getRegU32(c, 31); svsFixBakedTint(c); if (g_svsMatTrace) svsMatLog(i, r, c); const uint32_t a0 = getRegU32(c, 4), a1 = getRegU32(c, 5), ra = getRegU32(c, 31); g_origMat[i](r, c, rt); svsMaybeDumpRam(r); if (g_svsMatFix) { uint32_t fl; std::memcpy(&fl, r + 0x3D9C10u, 4); fl |= 0x400u; std::memcpy(r + 0x3D9C10u, &fl, 4); } svsTintAfter(r, raIn); if (g_svsMatTrace) svsMatAfter(i, r, a0, a1, ra); }
    SVS_MAT_WRAP(0) SVS_MAT_WRAP(1) SVS_MAT_WRAP(2) SVS_MAT_WRAP(3) SVS_MAT_WRAP(4) SVS_MAT_WRAP(5) SVS_MAT_WRAP(6) SVS_MAT_WRAP(7) SVS_MAT_WRAP(8)
    const PS2Runtime::RecompiledFunction kMatWrappers[9] = {svs_mat0, svs_mat1, svs_mat2, svs_mat3, svs_mat4, svs_mat5, svs_mat6, svs_mat7, svs_mat8};

    // Widescreen: 0x2036C8 turns the camera's vertical FOV into horizontal/vertical
    // FOVs using the viewport width/height ints at camera+0x10/+0x14. With
    // SVS_ASPECT=16:9 (or any w:h) the width is widened for that computation, so
    // both the projection and the frustum culling see the wider view.
    PS2Runtime::RecompiledFunction g_origFov = nullptr;
    float g_svsAspect = 0.0f; // width/height forced by SVS_ASPECT, 0 = follow the window
    void svs_freeIopHeap(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        ctx->pc = getRegU32(ctx, 31);
        ps2_stubs::sceSifFreeIopHeap(rdram, ctx, runtime);
    }

    void svs_fov(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t cam = getRegU32(ctx, 4) & 0x1FFFFFFu;
        int32_t w = 0, h = 0;
        std::memcpy(&w, rdram + cam + 0x10u, 4);
        std::memcpy(&h, rdram + cam + 0x14u, 4);
        const float aspect = g_svsAspect > 0.0f ? g_svsAspect : g_svsDisplayAspect;
        const bool patch = aspect > 1.34f && h > 0 && w > 0;
        if (patch)
        {
            const float k = aspect / (4.0f / 3.0f);
            const int32_t nw = static_cast<int32_t>(static_cast<float>(w) * k + 0.5f);
            std::memcpy(rdram + cam + 0x10u, &nw, 4);
            // f12 is the horizontal FOV in degrees: widen it so the vertical FOV stays the same
            const float halfRad = ctx->f[12] * 0.5f * 0.0174532925f;
            ctx->f[12] = 2.0f * std::atan(std::tan(halfRad) * k) / 0.0174532925f;
        }
        {
            static int logged = 0;
            if (logged++ < 8)
                std::fprintf(stderr, "[svs-fov] cam=0x%x w=%d h=%d aspect=%.3f f12=%g f13=%g\n", cam, w, h, aspect, ctx->f[12], ctx->f[13]);
        }
        g_origFov(rdram, ctx, runtime);
        if (patch)
            std::memcpy(rdram + cam + 0x10u, &w, 4);
    }

    PS2Runtime::RecompiledFunction g_orig1bb858 = nullptr;
    void svs_trace1bb858(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        std::fprintf(stderr, "[svs-1bb858] ra=0x%x sp=0x%x a0=0x%x a1=0x%x a2=0x%x pc=0x%x\n", getRegU32(ctx, 31), getRegU32(ctx, 29), getRegU32(ctx, 4), getRegU32(ctx, 5), getRegU32(ctx, 6), ctx->pc);
        g_orig1bb858(rdram, ctx, runtime);
    }

    PS2Runtime::RecompiledFunction g_origSphereCull = nullptr;
    void svs_sphereCull(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t ra = getRegU32(ctx, 31);
        const uint32_t sphere = getRegU32(ctx, 5);
        g_origSphereCull(rdram, ctx, runtime);
        static std::map<uint32_t, std::pair<uint64_t, uint64_t>> stats;
        static uint64_t n = 0;
        auto &st = stats[ra];
        (getRegU32(ctx, 2) ? st.first : st.second)++;
        if (getRegU32(ctx, 2) == 0u && std::getenv("SVS_FORCE_VISIBLE"))
            SET_GPR_U32(ctx, 2, 0x40u);
        if (std::getenv("SVS_CULL_TRACE") && (++n % 2000u) == 0u)
        {
            float sp[4];
            std::memcpy(sp, rdram + (sphere & 0x1FFFFFFu), 16);
            for (auto &kv : stats)
                std::fprintf(stderr, "[svs-cull] ra=0x%x visible=%llu culled=%llu\n", kv.first, (unsigned long long)kv.second.first, (unsigned long long)kv.second.second);
            std::fprintf(stderr, "[svs-cull] last sphere=(%g %g %g r=%g)\n", sp[0], sp[1], sp[2], sp[3]);
        }
    }

    PS2Runtime::RecompiledFunction g_origRenderList = nullptr;
    void svs_renderList(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        static int dumps = 0;
        auto rd32 = [&](uint32_t a) { uint32_t v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 4); return v; };
        auto rd16 = [&](uint32_t a) { uint16_t v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 2); return v; };
        static int calls = 0;
        if ((++calls % 300) == 0 && calls >= 900 && dumps++ < 2)
        {
            const uint32_t lists = getRegU32(ctx, 4), cam = getRegU32(ctx, 5), pass = getRegU32(ctx, 7);
            std::fprintf(stderr, "[svs-rl] lists=0x%x camVis=0x%x passMask=0x%x t0=%u\n", lists, rd32(cam + 4), rd16(pass + 4), getRegU32(ctx, 8));
            for (int l = 0; l < 3; ++l)
            {
                const uint32_t head = rd32(lists + 4 + l * 4);
                uint32_t o = head; int n = 0;
                while (o && n < 200)
                {
                    const uint8_t f16 = rdram[(o + 0x16) & 0x1FFFFFF], f17 = rdram[(o + 0x17) & 0x1FFFFFF];
                    const uint32_t vis = rd32(o + 0x58), vt = rd32(o + 4);
                    std::fprintf(stderr, "[svs-rl]  list%d obj=0x%x vt=0x%x render=0x%x f16=0x%x f17=0x%x vis=0x%x pass=%d\n", l, o, vt, rd32(vt + 0x7c), f16, f17, vis,
                                 ((f17 & rd16(pass + 4)) != 0) && ((vis & rd32(cam + 4)) != 0));
                    o = rd32(o + 0x4c); ++n;
                    if (o == head) break;
                }
            }
        }
        g_origRenderList(rdram, ctx, runtime);
    }

    PS2Runtime::RecompiledFunction g_origDrawQueue = nullptr;
    void svs_drawQueue(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        static int calls = 0, dumps = 0;
        auto rd32 = [&](uint32_t a) { uint32_t v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 4); return v; };
        static std::map<uint32_t, uint32_t> maxN;
        const uint32_t q0 = getRegU32(ctx, 4);
        ++calls;
        if (rd32(q0 + 8) > maxN[q0] && dumps++ < 40)
        {
            const uint32_t q = q0;
            const uint32_t n = rd32(q + 8);
            maxN[q] = n;
            std::map<uint32_t, int> byFn;
            for (uint32_t i = 0; i < n && i < 5000; ++i)
            {
                const uint32_t obj = rd32(q + 0x10 + i * 0x20 + 0x10);
                const uint32_t vt = rd32(obj + 4);
                byFn[rd32(vt + 0x84)]++;
            }
            std::fprintf(stderr, "[svs-dq] queue=0x%x n=%u:", q, n);
            for (auto &kv : byFn) std::fprintf(stderr, " fn0x%x=%d", kv.first, kv.second);
            std::fprintf(stderr, "\n");
        }
        g_origDrawQueue(rdram, ctx, runtime);
    }

    PS2Runtime::RecompiledFunction g_origOctree = nullptr;
    void svs_octree(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        static int calls = 0, dumps = 0;
        auto rd32 = [&](uint32_t a) { uint32_t v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 4); return v; };
        auto rd16 = [&](uint32_t a) { uint16_t v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 2); return v; };
        if ((++calls % 300) == 0 && calls >= 600 && dumps++ < 2)
        {
            const uint32_t oct = getRegU32(ctx, 4), cam = getRegU32(ctx, 5), pass = getRegU32(ctx, 7);
            const uint32_t nodes = rd32(oct + 0x54), count = rd32(oct + 0x38);
            std::fprintf(stderr, "[svs-oct] oct=0x%x nodes=%u camVis=0x%x pass=0x%x\n", oct, count, rd32(cam + 4), rd16(pass + 4));
            std::map<std::string, int> hist;
            for (uint32_t n = 0; n < count && n < 4096; ++n)
            {
                const uint32_t head = rd32(nodes + n * 16);
                uint32_t o = head; int k = 0;
                while (o && k++ < 500)
                {
                    char key[160];
                    const uint32_t vt = rd32(o + 4);
                    std::snprintf(key, sizeof(key), "vt=0x%x render=0x%x f16=0x%x f17=0x%x vis=0x%x", vt, rd32(vt + 0x7c), rdram[(o + 0x16) & 0x1FFFFFF], rdram[(o + 0x17) & 0x1FFFFFF], rd32(o + 0x58));
                    hist[key]++;
                    o = rd32(o + 0x4c);
                    if (o == head) break;
                }
            }
            for (auto &kv : hist) std::fprintf(stderr, "[svs-oct]  %4d %s\n", kv.second, kv.first.c_str());
        }
        g_origOctree(rdram, ctx, runtime);
    }

    PS2Runtime::RecompiledFunction g_origMeshCtor = nullptr, g_origMeshSubmit = nullptr;
    std::map<uint32_t, uint32_t> g_meshObjs; // obj -> submits
    void svs_meshCtor(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        const uint32_t obj = getRegU32(ctx, 4);
        std::fprintf(stderr, "[svs-mesh] ctor obj=0x%x ra=0x%x a1=0x%x\n", obj, getRegU32(ctx, 31), getRegU32(ctx, 5));
        g_meshObjs[obj] = 0;
        g_origMeshCtor(rdram, ctx, runtime);
    }
    void svs_meshSubmit(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        g_meshObjs[getRegU32(ctx, 4)]++;
        static int calls = 0;
        if ((++calls % 400) == 0)
        {
            int never = 0, some = 0;
            for (auto &kv : g_meshObjs) (kv.second ? some : never)++;
            std::fprintf(stderr, "[svs-mesh] objs=%zu submitted=%d never=%d (ra=0x%x)\n", g_meshObjs.size(), some, never, getRegU32(ctx, 31));
            static bool dumped = false;
            if (!dumped)
            {
                dumped = true;
                auto rd32 = [&](uint32_t a) { uint32_t v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 4); return v; };
                auto rdf = [&](uint32_t a) { float v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 4); return v; };
                {
                    const uint32_t p64 = rd32(0x988f40u + 0x64);
                    std::fprintf(stderr, "[svs-mesh] B+0x64=0x%x ->", p64);
                    for (int i = 0; i < 24; ++i) std::fprintf(stderr, " %08x", rd32(p64 + i * 4));
                    std::fprintf(stderr, "\n");
                    const uint32_t a2 = rd32(0x988f40u + 8);
                    std::fprintf(stderr, "[svs-mesh] B+8=0x%x byte58=%u ->", a2, rdram[(a2 + 0x58) & 0x1FFFFFF]);
                    for (int i = 0; i < 32; ++i) std::fprintf(stderr, " %08x", rd32(a2 + i * 4));
                    std::fprintf(stderr, "\n");
                }
                {
                    const uint32_t o = 0x988f40u;
                    std::fprintf(stderr, "[svs-mesh] B obj=0x%x vt=0x%x:", o, rd32(o + 4));
                    for (int i = 0; i < 40; ++i) std::fprintf(stderr, " %08x", rd32(o + i * 4));
                    std::fprintf(stderr, "\n");
                }
                for (auto &kv : g_meshObjs)
                {
                    const uint32_t o = kv.first;
                    std::fprintf(stderr, "[svs-mesh] obj=0x%x sub=%u vt=0x%x f14=%08x f16=%02x f17=%02x vis=0x%x next=0x%x prev=0x%x pos=(%.1f %.1f %.1f) r=%.2f*%.2f w40=%08x %08x %08x %08x 5c=%08x 60=%08x\n",
                                 o, kv.second, rd32(o + 4), rd32(o + 0x14), rdram[(o + 0x16) & 0x1FFFFFF], rdram[(o + 0x17) & 0x1FFFFFF], rd32(o + 0x58), rd32(o + 0x4c), rd32(o + 0x48),
                                 rdf(o + 0x18), rdf(o + 0x1c), rdf(o + 0x20), rdf(o + 0x50), rdf(o + 0x54), rd32(o + 0x40), rd32(o + 0x44), rd32(o + 0x8), rd32(o + 0xc), rd32(o + 0x5c), rd32(o + 0x60));
                }
            }
        }
        g_origMeshSubmit(rdram, ctx, runtime);
    }

    PS2Runtime::RecompiledFunction g_origOctInsert = nullptr;
    void svs_octInsert(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        auto rd32 = [&](uint32_t a) { uint32_t v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 4); return v; };
        auto rdf = [&](uint32_t a) { float v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 4); return v; };
        const uint32_t oct = getRegU32(ctx, 4), obj = getRegU32(ctx, 5), sph = getRegU32(ctx, 6), ra = getRegU32(ctx, 31);
        const uint32_t lock = rd32(rd32(oct + 8) + 0x1c);
        static std::map<uint32_t, int> seen;
        const bool log = seen[obj]++ < 2;
        g_origOctInsert(rdram, ctx, runtime);
        if (log && obj == 0x988f40u)
        {
            std::fprintf(stderr, "[svs-ins] octdump:");
            for (int i = 0; i < 24; ++i) std::fprintf(stderr, " %08x(%g)", rd32(oct + i * 4), rdf(oct + i * 4));
            std::fprintf(stderr, "\n");
        }
        if (log)
            std::fprintf(stderr, "[svs-ins] ra=0x%x sphAddr=0x%x oct=0x%x obj=0x%x vt=0x%x lock=%d f16=0x%x sph=(%.1f %.1f %.1f r=%.1f) octc=(%.1f %.1f %.1f) node->%d\n", ra, sph, oct, obj, rd32(obj + 4), (int)lock,
                         rdram[(obj + 0x16) & 0x1FFFFFF], rdf(sph), rdf(sph + 4), rdf(sph + 8), rdf(sph + 12), rdf(oct + 0xc), rdf(oct + 0x10), rdf(oct + 0x14), (int)(int16_t)(rd32(obj + 0x14) & 0xFFFF));
    }

    PS2Runtime::RecompiledFunction g_origOctNode = nullptr;
    void svs_octNode(uint8_t *rdram, R5900Context *ctx, PS2Runtime *runtime)
    {
        auto rdf = [&](uint32_t a) { float v = 0; std::memcpy(&v, rdram + (a & 0x1FFFFFFu), 4); return v; };
        const uint32_t p = getRegU32(ctx, 4), sph = getRegU32(ctx, 5);
        const float r = rdf(sph + 12);
        g_origOctNode(rdram, ctx, runtime);
        if (r > 10.0f)
            std::fprintf(stderr, "[svs-node] params=(%g %g %g) sph=(%g %g %g r=%g) -> %d ra=0x%x a0=%d a1=%d a2=%d v1=%d t0=%d f0=%g f1=%g f3=%g\n", rdf(p), rdf(p + 4), rdf(p + 8), rdf(sph), rdf(sph + 4), rdf(sph + 8), r, (int)getRegU32(ctx, 2), getRegU32(ctx, 31), (int)getRegU32(ctx, 4), (int)getRegU32(ctx, 5), (int)getRegU32(ctx, 6), (int)getRegU32(ctx, 3), (int)getRegU32(ctx, 8), ctx->f[0], ctx->f[1], ctx->f[3]);
    }

    void applySpyVsSpy(PS2Runtime &runtime)
    {
        if (std::getenv("SVS_INS_TRACE") && (g_origOctNode = runtime.lookupFunction(0x226F80u)))
            runtime.registerFunction(0x226F80u, svs_octNode);
        if (std::getenv("SVS_INS_TRACE") && (g_origOctInsert = runtime.lookupFunction(0x2432D0u)))
            runtime.registerFunction(0x2432D0u, svs_octInsert);
        if (std::getenv("SVS_MESH_TRACE") && (g_origMeshCtor = runtime.lookupFunction(0x20BF38u)))
            runtime.registerFunction(0x20BF38u, svs_meshCtor);
        if (std::getenv("SVS_MESH_TRACE") && (g_origMeshSubmit = runtime.lookupFunction(0x20EEA0u)))
            runtime.registerFunction(0x20EEA0u, svs_meshSubmit);
        if (std::getenv("SVS_OCT_TRACE") && (g_origOctree = runtime.lookupFunction(0x243790u)))
            runtime.registerFunction(0x243790u, svs_octree);
        if (std::getenv("SVS_DQ_TRACE") && (g_origDrawQueue = runtime.lookupFunction(0x204438u)))
            runtime.registerFunction(0x204438u, svs_drawQueue);
        if (std::getenv("SVS_RL_TRACE") && (g_origRenderList = runtime.lookupFunction(0x242000u)))
            runtime.registerFunction(0x242000u, svs_renderList);
        if ((std::getenv("SVS_CULL_TRACE") || std::getenv("SVS_FORCE_VISIBLE")) && (g_origSphereCull = runtime.lookupFunction(0x1D7100u)))
            runtime.registerFunction(0x1D7100u, svs_sphereCull);
        {
            // SVS_ASPECT: "auto" (default, follows the window), "4:3" (original) or w:h.
            const char *asp = std::getenv("SVS_ASPECT");
            bool classic = false;
            float w = 0.0f, h = 0.0f;
            if (asp && std::sscanf(asp, "%f:%f", &w, &h) == 2 && w > 0.0f && h > 0.0f)
            {
                if (std::fabs(w / h - 4.0f / 3.0f) < 0.01f)
                    classic = true;
                else
                    g_svsAspect = w / h;
            }
            if (!classic && (g_origFov = runtime.lookupFunction(0x2036C8u)))
                runtime.registerFunction(0x2036C8u, svs_fov);
        }
        if (std::getenv("SVS_TRACE_1BB858") && (g_orig1bb858 = runtime.lookupFunction(0x1BB858u)))
            runtime.registerFunction(0x1BB858u, svs_trace1bb858);
        g_svsMatTrace = std::getenv("SVS_MAT_TRACE") != nullptr;
        // Material cache fix: the toon-outline pass writes VU1 q21 (material colour) without
        // going through the EE-side cache at 0x44A470, so the next frame's room skipped its
        // upload and was drawn with the outline's black. Mark the material dirty after
        // every material call so it is always re-sent (SVS_NO_MATFIX disables this).
        g_svsMatFix = std::getenv("SVS_NO_MATFIX") == nullptr;
        if (g_svsMatFix || g_svsMatTrace || std::getenv("SVS_TINT_TRACE"))
        for (int i = 0; i < 9; ++i)
                if ((g_origMat[i] = runtime.lookupFunction(kMatFns[i])))
                    runtime.registerFunction(kMatFns[i], kMatWrappers[i]);
        installCallTraces(runtime);
        // sceMpegIsEnd original lee mpeg->sys->isEnd, que el MPEG HLE del runtime no
        // mantiene: los videos terminaban en el primer cuadro.
        runtime.registerFunction(0x2B75E0u, ps2_stubs::sceMpegIsEnd);
        // sceSifFreeIopHeap del juego va por RPC a un servidor que el IOP HLE no atiende:
        // nunca liberaba y cada video perdia ~190 KB de RAM del IOP hasta agotarla.
        runtime.registerFunction(0x2D4AD0u, svs_freeIopHeap);
        if (std::getenv("SVS_TRACE_DELAY") && (g_origLoadVec = runtime.lookupFunction(0x13ADA8u)))
            runtime.registerFunction(0x13ADA8u, svs_traceLoadVec);
        if (std::getenv("SVS_TRACE_DELAY") && (g_origStreamRead = runtime.lookupFunction(0x1E4320u)))
            runtime.registerFunction(0x1E4320u, svs_traceStreamRead);
        if (std::getenv("SVS_TRACE_DELAY") && (g_origResize = runtime.lookupFunction(0x13A9D0u)))
            runtime.registerFunction(0x13A9D0u, svs_traceResize);
        if (std::getenv("SVS_TRACE_DELAY") && (g_origNetReq = runtime.lookupFunction(0x1E5650u)))
            runtime.registerFunction(0x1E5650u, svs_traceNetReq);
        if (std::getenv("SVS_TRACE_DELAY") && (g_origDelay = runtime.lookupFunction(0x2CFF48u)))
            runtime.registerFunction(0x2CFF48u, svs_traceDelay);
        runtime.registerFunction(0x2C0EA8u, svs_sceCdPowerOff);
        runtime.registerFunction(0x139A28u, svs_returnZero); // sceCdPOffCallback
        runtime.registerFunction(0x2D3708u, svs_returnZero); // _sceFsSemInit
        runtime.registerFunction(0x2D3760u, svs_returnZero); // _sceFsWaitS
        runtime.registerFunction(0x2D37A0u, svs_returnZero); // scePowerOffHandler
        runtime.registerFunction(0x2D4730u, svs_sceDevctl);
        runtime.registerFunction(0x2D5930u, svs_InitSys_KernelTableScan);
        runtime.registerFunction(0x2D51E0u, svs_sceSifSearchModuleByName);
        g_origLoadModule = runtime.lookupFunction(0x2D52A8u);
        if (g_origLoadModule)
            runtime.registerFunction(0x2D52A8u, svs_sceSifLoadModule);
        runtime.registerFunction(0x2D4F48u, svs_sceSifStopModule);
        runtime.registerFunction(0x2D5150u, svs_sceSifUnloadModule);
        std::cout << "[svs] Spy vs. Spy overrides aplicados" << std::endl;
    }
}

PS2_REGISTER_GAME_OVERRIDE("Spy vs. Spy (PAL)", "SLES_530.78", 0u, 0u, applySpyVsSpy)
