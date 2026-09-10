/* Kernel-service stubs, used only by the userspace test executable. */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <errno.h>
#include <sys/types.h>
typedef uint64_t u64;
typedef int acpi_status;
struct device {};
struct device_attribute {};
#define AE_OK 0
#define AE_ERROR 1
#define AE_BAD_PARAMETER 2
#define AE_SUPPORT 3
#define ACPI_FAILURE(s) ((s) != AE_OK)
#define ACER_CAP_PREDATOR_SENSE 1
#define ACER_CAP_NITRO_SENSE 2
#define ACER_CAP_NITRO_SENSE_V4 4
/* Synthetic transport IDs, never used outside this mock. */
#define ACER_WMID_SET_GAMING_FAN_BEHAVIOR_METHODID 1001
#define ACER_WMID_SET_GAMING_FAN_SPEED_METHODID 1002
#define WMID_GUID4 "mock-gaming-interface"
#define DEFINE_MUTEX(name) int name
#define mutex_lock(m) ((void)(m))
#define mutex_unlock(m) ((void)(m))
#define pr_info(...) ((void)0)
#define pr_err(...) ((void)0)
#define sysfs_emit sprintf
static bool fan_interface_ready = true;
static bool supported = true;
static int calls, fail_on_call;
static u64 last_command;
static bool wmi_has_guid(const char *guid) { return supported; }
static bool has_cap(int cap) { return supported; }
static int kstrtoint(const char *str, int base, int *out) {
    char *end;
    long value;
    if (!str || !*str) return -EINVAL;
    errno = 0;
    value = strtol(str, &end, base);
    if (errno || *end || value < 0 || value > 100) return -EINVAL;
    *out = value;
    return 0;
}
static acpi_status WMI_gaming_execute_u64(int method, u64 value, void *out) {
    last_command = value;
    return ++calls == fail_on_call ? AE_ERROR : AE_OK;
}
/* DRIVER_FAN_IMPLEMENTATION */
int main(void) {
    const char *invalid[] = {"", "-1,40", "101,40", "40,101", "40", "40,", "40,50,60", "40,50xxx", "40,50\nextra"};
    for (size_t i = 0; i < sizeof(invalid) / sizeof(*invalid); i++) {
        calls = 0;
        assert(predator_fan_speed_store(NULL, NULL, invalid[i], strlen(invalid[i])) == -EINVAL);
        assert(calls == 0);
    }
    calls = 0;
    assert(predator_fan_speed_store(NULL, NULL, "40,50\0x", 7) == -EINVAL);
    assert(calls == 0);
    assert(predator_fan_speed_store(NULL, NULL, "100,100\n", 8) == 8);
    assert(cpu_fan_speed == 100 && gpu_fan_speed == 100);
    assert(predator_fan_speed_store(NULL, NULL, "0,0", 3) == 3);
    assert(cpu_fan_speed == 0 && gpu_fan_speed == 0);
    for (int step = 1; step <= 3; step++) {
        calls = 0;
        fail_on_call = step;
        assert(acer_set_fan_speed(65, 65) != AE_OK);
        assert(last_command == 0x410009); /* Existing upstream Auto command. */
        assert(cpu_fan_speed == 0 && gpu_fan_speed == 0);
    }
    fail_on_call = 0;
    calls = 0;
    supported = false;
    assert(acer_set_fan_speed(65, 65) == AE_SUPPORT);
    assert(calls == 0);
    supported = true;
    fan_interface_ready = false;
    assert(acer_set_fan_speed(65, 65) == AE_SUPPORT);
    assert(calls == 0);
    puts("PASS: actual fan parser, range gates, capability gates and ACPI rollback");
    return 0;
}
