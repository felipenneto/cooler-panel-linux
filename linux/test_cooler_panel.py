import contextlib
import io
from pathlib import Path
import struct
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

import cooler_panel as cp


class ProtocolTests(unittest.TestCase):
    def test_matches_confirmed_windows_test_packet(self):
        self.assertEqual(cp.packet(42, 37), bytes.fromhex("00 2a 25 00 00 00 00 00 00 00 00"))
        self.assertEqual(cp.packet(42, 37, 80, 95), bytes.fromhex("00 2a 25 50 00 00 5f 00 00 00 00"))

    def test_bad_readings_never_become_usb_bytes(self):
        for values in [(-1, 37), (42, 126), (float("nan"), 37),
                       (42, float("inf")), (42, 37, 101), (42, 37, 0, -1)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                cp.packet(*values)

    def test_descriptor_gate(self):
        cp.validate_descriptor(bytes.fromhex("75 08 95 0a 91 02"))
        cp.validate_descriptor(bytes.fromhex("75 08 95 05 91 02 a4 95 01 b4 91 02"))
        for hex_data in ["", "75 08 95 0b 91 02", "85 01 75 08 95 0a 91 02",
                         "75", "b4", "91 02", "fe", "75 08 95 0a 91 02 a4"]:
            with self.subTest(hex_data=hex_data), self.assertRaises(ValueError):
                cp.validate_descriptor(bytes.fromhex(hex_data))

    def test_ambiguous_usb_and_partial_writes(self):
        @contextlib.contextmanager
        def panel(_path):
            yield 123
        with patch.object(cp, "devices", return_value=[Path("/dev/hidraw2")]), \
                patch.object(cp, "open_panel", panel), patch.object(cp.os, "write", return_value=10) as write:
            with self.assertRaises(OSError):
                cp.send(cp.packet(42, 37))
            write.assert_called_once()
        for found in ([], [Path("a"), Path("b")]):
            with patch.object(cp, "devices", return_value=found), patch.object(cp, "open_panel") as opened:
                with self.assertRaises(ValueError):
                    cp.send(cp.packet(42, 37))
                opened.assert_not_called()

    def test_open_node_identity_and_descriptor_checked_and_fd_closed(self):
        descriptor = bytes.fromhex("75 08 95 0a 91 02")

        def ioctl(_fd, request, buffer, _mutate):
            if request == 0x80084803:
                buffer[:] = struct.pack("=IHH", 3, 0xAA88, 0x8666)
            elif request == 0x80044801:
                buffer[:] = struct.pack("=I", len(descriptor))
            elif request == 0x90044802:
                buffer[4:4 + len(descriptor)] = descriptor
            else:
                self.fail("Unexpected ioctl")

        fake = types.SimpleNamespace(ioctl=ioctl)
        with patch.dict(sys.modules, {"fcntl": fake}), \
                patch.object(cp.os, "O_NONBLOCK", 2048, create=True), \
                patch.object(cp.os, "O_CLOEXEC", 524288, create=True), \
                patch.object(cp.os, "open", return_value=123), patch.object(cp.os, "close") as close:
            with cp.open_panel("fake") as fd:
                self.assertEqual(fd, 123)
            close.assert_called_once_with(123)
            descriptor = bytes.fromhex("75 08 95 0b 91 02")
            with self.assertRaises(ValueError):
                with cp.open_panel("fake"):
                    self.fail("Invalid descriptor accepted")
            self.assertEqual(close.call_count, 2)
            def wrong_device(_fd, _request, buffer, _mutate):
                buffer[:] = struct.pack("=IHH", 3, 0x1234, 0x8666)
            fake.ioctl = wrong_device
            with self.assertRaises(ValueError):
                with cp.open_panel("fake"):
                    self.fail("Wrong device accepted")
            self.assertEqual(close.call_count, 3)


class SensorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def put(self, relative, text):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_dynamic_hwmon_numbers_tdie_preferred_and_gpu_edge(self):
        self.put("hwmon7/name", "k10temp\n")
        self.put("hwmon7/temp1_label", "Tctl\n")
        self.put("hwmon7/temp2_label", "Tdie\n")
        cpu = self.put("hwmon7/temp2_input", "48500\n")
        self.put("hwmon2/name", "amdgpu\n")
        gpu = self.put("hwmon2/temp1_input", "41000\n")
        self.put("hwmon2/temp2_input", "85000\n")
        self.put("hwmon2/device/gpu_busy_percent", "63\n")
        self.assertEqual(cp.sensor_paths(self.root), (cpu, gpu))
        with patch.object(cp, "cpu_ticks", return_value=(200, 140)):
            payload, _, text = cp.readings((cpu, gpu), (100, 80))
        self.assertEqual(payload, cp.packet(48.5, 41, 40, 63))
        self.assertIn("41.0", text)
        cpu.unlink()
        with self.assertRaises(OSError):
            cp.readings((cpu, gpu), None)

    def test_tctl_fallback_optional_gpu_load_and_multiple_gpu_rejected(self):
        self.put("hwmon0/name", "k10temp")
        cpu = self.put("hwmon0/temp1_input", "40000")
        self.put("hwmon9/name", "amdgpu")
        gpu = self.put("hwmon9/temp1_input", "50000")
        self.assertEqual(cp.sensor_paths(self.root), (cpu, gpu))
        with patch.object(cp, "cpu_ticks", return_value=(100, 80)):
            self.assertEqual(cp.readings((cpu, gpu), None)[0], cp.packet(40, 50))
        self.put("hwmon6/name", "amdgpu")
        with self.assertRaises(ValueError):
            cp.sensor_paths(self.root)

    def test_proc_stat_does_not_double_count_guest(self):
        path = self.put("stat", "cpu  10 20 30 40 5 6 7 8 9 10\ncpu0 1 2 3 4\n")
        self.assertEqual(cp.cpu_ticks(path), (126, 45))
        self.assertEqual(cp.cpu_percent((100, 80), (200, 140)), 40)
        self.assertEqual(cp.cpu_percent((200, 140), (100, 80)), 0)

    def test_usb_enumeration_filters_vendor_product_and_bus(self):
        self.put("hidraw5/device/uevent", "HID_ID=0003:0000AA88:00008666\nHID_NAME=Panel\n")
        self.put("hidraw3/device/uevent", "HID_ID=0003:0000AA88:00001234\n")
        self.put("hidraw7/device/uevent", "HID_ID=0005:0000AA88:00008666\n")
        self.assertEqual(cp.devices(self.root), [Path("/dev/hidraw5")])


class RecoveryTests(unittest.TestCase):
    def test_missing_sensor_suspends_writes_then_reconnects(self):
        handlers = {}
        sleeps = []
        sent = []
        payload = cp.packet(42, 37)

        def sleep(seconds):
            sleeps.append(seconds)
            if sent:
                handlers[cp.signal.SIGTERM](None, None)

        with patch.object(cp.sys, "argv", ["cooler_panel.py", "--run"]), \
                patch.object(cp.sys, "platform", "linux"), \
                patch.object(cp.os, "uname", return_value=types.SimpleNamespace(machine="x86_64"), create=True), \
                patch.object(cp, "instance_lock", contextlib.nullcontext), \
                patch.object(cp.signal, "signal", side_effect=lambda signum, fn: handlers.update({signum: fn})), \
                patch.object(cp, "sensor_paths", return_value=("cpu", "gpu")), \
                patch.object(cp, "readings", side_effect=[OSError("sensor desconectado"), (payload, (100, 80), "ok")]) as read, \
                patch.object(cp, "send", side_effect=sent.append), \
                patch.object(cp.time, "sleep", side_effect=sleep), \
                contextlib.redirect_stdout(io.StringIO()) as out, contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(cp.main(), 0)
        self.assertEqual(sent, [payload])
        self.assertEqual(len(sleeps), 6)
        self.assertEqual(read.call_args_list[1].args[1], None)
        self.assertIn("Envios suspensos", err.getvalue())
        self.assertIn("reconectados", out.getvalue())


if __name__ == "__main__":
    unittest.main()
