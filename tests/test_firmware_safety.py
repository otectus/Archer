"""Compile prepared driver functions with hostile ACPI replies and no hardware."""

import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r'static\s+[\w\s*]+\b' + name + r'\([^;]*?\)\s*\{', source)
    start = match.start()
    opening = source.index('{', match.start())
    level = 1
    for end in range(opening + 1, len(source)):
        level += (source[end] == '{') - (source[end] == '}')
        if not level:
            return source[start:end + 1]
    raise AssertionError('Unterminated function')


COMMON = r'''
#include <assert.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <stdio.h>
#include <errno.h>
#include <sys/types.h>
#include <stdbool.h>
typedef uint8_t u8;
typedef int8_t s8;
typedef uint32_t u32;
typedef uint64_t u64;
typedef int acpi_status;
typedef size_t acpi_size;
#define __packed __attribute__((packed))
#define ACPI_TYPE_BUFFER 1
#define ACPI_ALLOCATE_BUFFER 0
#define ACPI_FAILURE(v) ((v) != 0)
#define AE_OK 0
#define AE_ERROR 1
#define pr_err(...) ((void)0)
#define pr_info(...) ((void)0)
#define kfree(p) ((void)0)
union acpi_object { int type; struct { int type; unsigned length; void *pointer; } buffer; };
struct acpi_buffer { size_t length; void *pointer; };
struct device {};
struct device_driver {};
struct device_attribute {};
static unsigned char payload[16];
static union acpi_object object;
static void *reply;
static int calls, fail_at;
static acpi_status wmi_evaluate_method(const char *guid, int instance, int method,
                                     struct acpi_buffer *input, struct acpi_buffer *output) {
    calls++;
    if (calls == fail_at) return AE_ERROR;
    output->pointer = reply;
    return AE_OK;
}
static void response(unsigned length) {
    calls = fail_at = 0;
    memset(payload, 0, sizeof(payload));
    object.type = ACPI_TYPE_BUFFER;
    object.buffer.length = length;
    object.buffer.pointer = payload;
    reply = &object;
}
static int kstrtou8(const char *text, int base, u8 *value) {
    char *end; unsigned long result = strtoul(text, &end, base);
    if (*end || result > 255) return -EINVAL;
    *value = result; return 0;
}
static int kstrtobool(const char *text, bool *value) {
    if (!strcmp(text, "1")) { *value = true; return 0; }
    if (!strcmp(text, "0")) { *value = false; return 0; }
    return -EINVAL;
}
'''


@unittest.skipUnless(os.environ.get('LINUWU_TEST_REPO') and os.environ.get('BATTERY_TEST_REPO'),
                     'Set both pinned upstream test repositories')
class FirmwareSafetyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.sources = {}
        for package, variable, file in (('linuwu-sense', 'LINUWU_TEST_REPO', 'src/linuwu_sense.c'),
                                        ('acer-wmi-battery', 'BATTERY_TEST_REPO', 'acer-wmi-battery.c')):
            tree = cls.root / package
            result = subprocess.run(['bash', str(ROOT / 'driver' / package / 'prepare.sh'), str(tree), os.environ[variable]],
                                    capture_output=True, text=True)
            if result.returncode:
                raise AssertionError(result.stderr)
            cls.sources[package] = (tree / file).read_text()

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def compile_and_run(self, body):
        source = self.root / 'fault.c'
        executable = self.root / 'fault'
        source.write_text(COMMON + body)
        result = subprocess.run(['cc', '-std=gnu11', '-fsanitize=undefined,bounds', str(source), '-o', str(executable)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        result = subprocess.run([str(executable)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_no_kernel_file_replay_or_save_remains(self):
        source = self.sources['linuwu-sense']
        self.assertNotRegex(source, r'\b(filp_open|filp_close|kernel_read|kernel_write)\s*\(')
        self.assertNotIn('/etc/predator_state', source)
        self.assertNotIn('/etc/four_zone_kb_state', source)

    def test_linuwu_null_wrong_type_pointer_and_length_replies(self):
        source = self.sources['linuwu-sense']
        structures = source[source.index(' struct get_battery_health_control_status_input'):source.index(' static acpi_status battery_health_query')]
        structures += 'struct get_four_zoned_kb_output { u8 gmReturn; u8 gmOutput[15]; } __packed;\n'
        body = structures + '\n'.join(function(source, name) for name in ('battery_health_query', 'battery_health_set', 'get_kb_status'))
        constants = '\n'.join('#define ' + name + (' "guid"' if 'GUID' in name else ' 1') for name in sorted(set(re.findall(r'\b(?:ACER_\w+|WMID_GUID\w*)', body))))
        cases = ''
        for invocation, length in (('battery_health_query(HEALTH_MODE, &enabled)', 8), ('battery_health_set(1, 1)', 4), ('get_kb_status(&keyboard)', 16)):
            cases += f'''
response({length}); reply = NULL; assert({invocation} == AE_ERROR);
response({length}); object.type = 9; assert({invocation} == AE_ERROR);
response({length}); object.buffer.pointer = NULL; assert({invocation} == AE_ERROR);
response(1); object.buffer.pointer = (void *)1; assert({invocation} == AE_ERROR);
response({length + 1}); assert({invocation} == AE_ERROR);
response({length}); assert({invocation} == AE_OK);
'''
        self.compile_and_run(constants + '\n#define HEALTH_MODE 1\n#define CALIBRATION_MODE 2\n' + body
                             + '\nint main(void) { int enabled; struct get_four_zoned_kb_output keyboard;\n' + cases + 'return 0; }')

    def test_all_scalar_stores_reject_overflow_signs_and_trailing_bytes(self):
        source = self.sources['linuwu-sense']
        names = ('predator_usb_charging_store', 'predator_battery_limit_store', 'preadtor_battery_calibration_store',
                 'predator_lcd_override_store', 'predator_backlight_timeout_store', 'predator_boot_animation_sound_store')
        body = function(source, 'archer_parse_scalar') + '\n' + '\n'.join(function(source, name) for name in names)
        constants = '\n'.join('#define ' + name + ' 1' for name in sorted(set(re.findall(r'\bACER_\w+', body))))
        stubs = '''
#define HEALTH_MODE 1
#define CALIBRATION_MODE 2
static int battery_health_set(int a, int b) { calls++; return AE_OK; }
static int WMI_apgeaction_execute_u64(int a, u64 b, u64 *out) { calls++; *out=0; return AE_OK; }
static int WMI_gaming_execute_u64(int a, u64 b, u64 *out) { calls++; *out=0; return AE_OK; }
'''
        cases = ''
        for name in names:
            cases += f'''
for (size_t i = 0; i < sizeof(bad)/sizeof(*bad); i++) {{
    calls = 0; assert({name}(NULL, NULL, bad[i], strlen(bad[i])) == -EINVAL); assert(calls == 0);
}}
calls = 0; assert({name}(NULL, NULL, "1\\0x", 3) == -EINVAL); assert(calls == 0);
'''
        self.compile_and_run(constants + stubs + body + r'''
int main(void) {
const char *bad[] = {"", "\n", "-1", "+1", "256", "257", "1garbage", "1 0", "10000"};
''' + cases + 'return 0; }')

    def test_battery_short_responses_and_failed_store_readback(self):
        source = self.sources['acer-wmi-battery']
        structures = source[source.index('struct get_battery_health_control_status_input'):source.index('static short enable_health_mode')]
        body = structures + '\n'.join(function(source, name) for name in
                                      ('get_battery_health_control_status', 'set_battery_health_control', 'update_state',
                                       'health_mode_store', 'calibration_mode_store'))
        self.compile_and_run('#define WMI_GUID "guid"\n#define ACER_BATTERY_INDEX 1\n' + body + r'''
int main(void) {
    struct battery_info state = {1, 1};
    response(1); object.buffer.pointer = (void *)1;
    assert(get_battery_health_control_status(&state) == AE_ERROR);
    assert(state.health_mode == 1 && state.calibration_mode == 1);
    assert(set_battery_health_control(1, true) == AE_ERROR);
    response(8); reply = NULL; assert(get_battery_health_control_status(&state) == AE_ERROR);
    response(8); object.buffer.pointer = NULL; assert(get_battery_health_control_status(&state) == AE_ERROR);
    response(8); payload[0] = 3; assert(get_battery_health_control_status(&state) == AE_OK);
    battery_status.health_mode = battery_status.calibration_mode = 0;
    response(4); fail_at = 1; assert(health_mode_store(NULL, "1", 1) == -EIO);
    response(4); fail_at = 2; assert(calibration_mode_store(NULL, "1", 1) == -EIO);
    assert(battery_status.health_mode == 0 && battery_status.calibration_mode == 0);
    return 0;
}''')


if __name__ == '__main__':
    unittest.main()
