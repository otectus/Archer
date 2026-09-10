/* Run the real sysfs parsers with counted buffers and a fake WMI transport. */
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <limits.h>
#include <sys/types.h>
typedef unsigned long long u64;
typedef int acpi_status;
struct device {};
struct device_attribute {};
struct per_zone_color { u64 zone1, zone2, zone3, zone4; int brightness; };
#define ACPI_FAILURE(s) ((s) != 0)
#define pr_err(...) ((void)0)
static int calls;
static struct { int per_zone; } current_kb_state;
static int kstrtoint(const char *str, int base, int *out) {
    char *end;
    if (!str || !*str) return -EINVAL;
    errno = 0;
    long v = strtol(str, &end, base);
    if (errno || *end || v < INT_MIN || v > INT_MAX) return -EINVAL;
    *out = v; return 0;
}
static int kstrtoull(const char *str, int base, u64 *out) {
    char *end;
    if (!str || !*str) return -EINVAL;
    errno = 0;
    *out = strtoull(str, &end, base);
    return errno || *end ? -EINVAL : 0;
}
static acpi_status set_kb_status(int a, int b, int c, int d, int e, int f, int g) { calls++; return 0; }
static acpi_status set_per_zone_color(struct per_zone_color *colors) { calls++; return 0; }
/* DRIVER_RGB_IMPLEMENTATION */
int main(void) {
    const char *bad[] = {"", "\n", "ffffff,100", "ffffff,ffffff,ffffff,100", "ffffff,ffffff,ffffff,ffffff,100,1", "ffffff,ffffff,ffffff,ffffff,100garbage"};
    for (size_t i = 0; i < sizeof(bad)/sizeof(*bad); i++) {
        calls = 0;
        assert(per_zoned_rgb_kb_store(NULL, NULL, bad[i], strlen(bad[i])) == -EINVAL);
        assert(calls == 0);
    }
    const char rgb[] = "ffffff,123456,000000,abcdef,100\n";
    assert(per_zoned_rgb_kb_store(NULL, NULL, rgb, sizeof(rgb)-1) == sizeof(rgb)-1);
    assert(per_zoned_rgb_kb_store(NULL, NULL, "ffffff\0,ffffff,ffffff,ffffff,100", 29) == -EINVAL);
    assert(four_zoned_rgb_kb_store(NULL, NULL, "", 0) == -EINVAL);
    assert(four_zoned_rgb_kb_store(NULL, NULL, "1,1,100,1,1,1,1,1", 17) == -EINVAL);
    const char valid[] = {'1',',','1',',','1',',','1',',','1',',','1',',','1'};
    assert(four_zoned_rgb_kb_store(NULL, NULL, valid, sizeof(valid)) == sizeof(valid));
    assert(four_zoned_rgb_kb_store(NULL, NULL, "1,1,1,1,1,1,1\0x", 15) == -EINVAL);
    return 0;
}
