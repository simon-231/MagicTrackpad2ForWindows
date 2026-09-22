"""Compile the production AmtPtpGetStrings body against small WDF test doubles.

Run: python tests/test_usb_strings.py --cc clang
Or:  python tests/test_usb_strings.py --cc /path/to/zig cc
Requires Python 3 and a C11 compiler; does not load a driver or need the WDK.
Use --source-root to test another checkout, including the unpatched baseline.
The doubles check destination identity before copying, so the baseline can be
tested without actually corrupting the test process's stack.
"""
import argparse
from pathlib import Path
import subprocess
import tempfile

STUBS = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>
/* Microsoft CRT headers may already provide SAL annotations. */
#ifndef _In_
#define _In_
#endif
#define TraceEvents(...) ((void)0)
#define STATUS_SUCCESS 0
#define STATUS_INVALID_PARAMETER (-1)
#define STATUS_INVALID_BUFFER_SIZE (-2)
#define STATUS_BUFFER_TOO_SMALL (-3)
#define STATUS_UNSUCCESSFUL (-4)
#define NT_SUCCESS(s) ((s) >= 0)
#define WDF_NO_OBJECT_ATTRIBUTES NULL
#define HID_STRING_ID_IMANUFACTURER 1
#define HID_STRING_ID_IPRODUCT 2
#define HID_STRING_ID_ISERIALNUMBER 3
typedef int NTSTATUS;
typedef uint32_t ULONG;
typedef ULONG *PULONG;
typedef uint16_t USHORT, WCHAR;
typedef uint8_t UCHAR;
typedef void *PVOID;
typedef struct { void *data; size_t size; } Memory, *WDFMEMORY;
typedef struct {
    struct { UCHAR iManufacturer, iProduct, iSerialNumber; } DeviceDescriptor;
    void *UsbDevice;
} DeviceContext, *PDEVICE_CONTEXT, *WDFDEVICE;
typedef struct { size_t capacity, information; WCHAR output[32]; } Request, *WDFREQUEST;
static ULONG input;
static WCHAR descriptor[16];
static Memory inMemory, stringMemory;
static int deleted, queries, copies, badDestination, queryFail, copyFail, inputFail;
static UCHAR queriedIndex;
static USHORT queriedLanguage;
static Request request;
static DeviceContext device = {{11, 22, 33}, NULL};
static PDEVICE_CONTEXT DeviceGetContext(WDFDEVICE d) { return d; }
static NTSTATUS WdfRequestRetrieveInputMemory(WDFREQUEST r, WDFMEMORY *m) {
    (void)r; *m = &inMemory; return inputFail ? STATUS_UNSUCCESSFUL : 0;
}
static void *WdfMemoryGetBuffer(WDFMEMORY m, size_t *n) {
    if (n) *n = m->size; return m->data;
}
static NTSTATUS WdfUsbTargetDeviceAllocAndQueryString(void *d, void *a,
    WDFMEMORY *m, USHORT *n, UCHAR index, USHORT language) {
    (void)d; (void)a; ++queries; queriedIndex = index; queriedLanguage = language;
    if (queryFail) return STATUS_UNSUCCESSFUL;
    *m = &stringMemory; *n = (USHORT)(stringMemory.size / sizeof(WCHAR)); return 0;
}
static NTSTATUS WdfRequestRetrieveOutputBuffer(WDFREQUEST r, size_t min,
    void **p, size_t *actual) {
    if (r->capacity < min) return STATUS_BUFFER_TOO_SMALL;
    *p = r->output; if (actual) *actual = r->capacity; return 0;
}
static NTSTATUS WdfMemoryCopyToBuffer(WDFMEMORY m, size_t off, void *p, size_t n) {
    ++copies;
    if (p != request.output) { ++badDestination; return STATUS_INVALID_PARAMETER; }
    if (n > m->size || off > m->size - n) return STATUS_BUFFER_TOO_SMALL;
    if (copyFail) return STATUS_UNSUCCESSFUL;
    memcpy(p, (char*)m->data + off, n); return 0;
}
static void WdfRequestSetInformation(WDFREQUEST r, size_t n) { r->information = n; }
void WdfObjectDelete(WDFMEMORY m) { if (m == &stringMemory) ++deleted; }
'''

CASES = r'''
static int failures;
#define CHECK(x) do { if (!(x)) { printf("FAIL line %d: %s\n", __LINE__, #x); ++failures; } } while (0)
static void reset(size_t chars, size_t capacity) {
    memset(&request, 0, sizeof(request));
    memset(request.output, 0xA5, sizeof(request.output));
    descriptor[0] = 'A'; descriptor[1] = 'p'; descriptor[2] = 'p';
    descriptor[3] = 'l'; descriptor[4] = 'e'; descriptor[5] = 0;
    input = (0x0409u << 16) | HID_STRING_ID_IPRODUCT;
    inMemory = (Memory){&input, sizeof(input)};
    stringMemory = (Memory){descriptor, chars * sizeof(WCHAR)};
    request.capacity = capacity;
    deleted = queries = copies = badDestination = queryFail = copyFail = inputFail = 0;
}
int main(void) {
    /* Unterminated descriptor, exact and oversized buffers. */
    for (size_t capacity = 12; capacity <= 64; capacity += 52) {
        reset(5, capacity);
        CHECK(AmtPtpGetStrings(&device, &request) == 0);
        CHECK(!badDestination && copies == 1 && deleted == 1);
        CHECK(request.information == 12);
        CHECK(memcmp(request.output, descriptor, 10) == 0);
        CHECK(request.output[5] == 0 && request.output[6] == 0xA5A5);
        CHECK(queriedIndex == 22 && queriedLanguage == 0x0409);
    }
    /* Existing terminator must not demand another WCHAR of capacity. */
    reset(6, 12);
    CHECK(AmtPtpGetStrings(&device, &request) == 0);
    CHECK(request.information == 12 && request.output[5] == 0 && deleted == 1);
    reset(0, 2);
    CHECK(AmtPtpGetStrings(&device, &request) == 0);
    CHECK(request.information == 2 && request.output[0] == 0 && copies == 0 && deleted == 1);
    reset(5, 10);
    CHECK(AmtPtpGetStrings(&device, &request) == STATUS_BUFFER_TOO_SMALL);
    CHECK(request.information == 0 && copies == 0 && deleted == 1);
    reset(5, 64); copyFail = 1;
    CHECK(AmtPtpGetStrings(&device, &request) == STATUS_UNSUCCESSFUL);
    CHECK(request.information == 0 && deleted == 1);
    reset(5, 64); queryFail = 1;
    CHECK(AmtPtpGetStrings(&device, &request) == STATUS_UNSUCCESSFUL);
    CHECK(request.information == 0 && copies == 0 && deleted == 0);
    reset(5, 64); input = 99;
    CHECK(AmtPtpGetStrings(&device, &request) == STATUS_INVALID_PARAMETER);
    CHECK(queries == 0 && request.information == 0);
    reset(5, 64); inMemory.size = 1;
    CHECK(AmtPtpGetStrings(&device, &request) == STATUS_INVALID_BUFFER_SIZE);
    CHECK(queries == 0);
    reset(5, 64); inputFail = 1;
    CHECK(AmtPtpGetStrings(&device, &request) == STATUS_UNSUCCESSFUL);
    CHECK(queries == 0);
    for (ULONG id = 1; id <= 3; ++id) {
        reset(5, 12); input = id;
        CHECK(AmtPtpGetStrings(&device, &request) == 0);
        CHECK(queriedIndex == id * 11);
    }
    printf("13 scenarios, %d failed assertions\n", failures);
    return failures ? 1 : 0;
}
'''

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--cc', nargs='+', default=['clang'])
    args = parser.parse_args()
    source = (args.source_root / 'AmtPtpDeviceUsbUm/Hid.c').read_text()
    start = source.index('NTSTATUS\nAmtPtpGetStrings(')
    end = source.index('\n_IRQL_requires_', start)
    with tempfile.TemporaryDirectory(prefix='amtptp-test-') as directory:
        code = Path(directory) / 'strings.c'
        exe = Path(directory) / 'strings.exe'
        code.write_text(STUBS + '\n' + source[start:end] + '\n' + CASES)
        subprocess.run(args.cc + ['-std=c11', '-Wall', '-Wextra', '-Werror', str(code), '-o', str(exe)], check=True)
        subprocess.run([str(exe)], check=True)

if __name__ == '__main__':
    main()
