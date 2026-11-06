/* Shared helpers for CFW patches: freestanding, no libc, no writable sections.
 *
 * Firmware objects are reached through raw offsets (from cfw/targets/<t>/target.h) because
 * the stock firmware has no headers. Keep every access going through these macros so a port
 * to another firmware version only has to change target.h and symbols.ld. */
#ifndef CFW_H
#define CFW_H

typedef unsigned char u8;
typedef signed char s8;
typedef unsigned short u16;
typedef signed short s16;
typedef unsigned int u32;
typedef signed int s32;
typedef unsigned long long u64; /* multiply and shift only: no 64-bit division helpers */

#define AT(base, off, type) (*(type *)((char *)(base) + (off)))
#define PTR(base, off) AT(base, off, void *)
#define U8(base, off) AT(base, off, u8)
#define U32(base, off) AT(base, off, u32)

/* Call virtual method at byte offset `off` of the object's vtable. */
#define VFN(obj, off, ret, ...) ((ret(*)(void *, ##__VA_ARGS__))AT(*(void **)(obj), off, void *))

/* libstdc++ std::function<void()> on 32-bit ARM: 8-byte _Any_data, manager, invoker.
 * The menu copies it with manager(dst, src, 2) and runs invoker(&storage). */
typedef struct {
    void *capture;
    void *pad;
    int (*manager)(void *dst, const void *src, int op);
    void (*invoker)(void *const *storage);
} std_function;

#endif
