#!/usr/bin/env python3
"""Cooler Panel Linux: stdlib only; k10temp + amdgpu + USB hidraw."""
import argparse
import contextlib
import os
from pathlib import Path
import signal
import struct
import sys
import time

VERSION = "0.1.0-linux-preview"


def packet(cpu, gpu, cpu_load=0, gpu_load=0):
    values = (cpu, gpu, cpu_load, gpu_load)
    for value, limit in zip(values, (125, 125, 100, 100)):
        if not 0 <= value <= limit:
            raise ValueError("Temperatura deve ser 0–125 °C; utilização, 0–100%.")
    c, g, cl, gl = (int(v + 0.5) for v in values)
    return bytes((0, c, g, cl, 0, 0, gl, 0, 0, 0, 0))


def validate_descriptor(data):
    """Accept only an unnumbered, 10-byte Output report (11 with ID 0).

    Parse HID short/global items including Push/Pop and accumulated Output
    fields. Reject numbered/long/truncated descriptors rather than guessing.
    """
    size = count = bits = 0
    stack = []
    pos = 0
    while pos < len(data):
        prefix = data[pos]
        pos += 1
        if prefix == 0xFE:
            raise ValueError("Descritor HID com item longo não suportado.")
        length = (0, 1, 2, 4)[prefix & 3]
        if pos + length > len(data):
            raise ValueError("Descritor HID truncado.")
        value = int.from_bytes(data[pos:pos + length], "little")
        pos += length
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1:
            if tag == 7:
                size = value
            elif tag == 8:
                raise ValueError("Report ID numerado: protocolo não confirmado.")
            elif tag == 9:
                count = value
            elif tag == 10:
                stack.append((size, count))
            elif tag == 11:
                if not stack:
                    raise ValueError("HID Pop sem Push.")
                size, count = stack.pop()
        elif kind == 0 and tag == 9:
            if not size or not count:
                raise ValueError("Campo HID Output sem tamanho.")
            bits += size * count
    if bits != 80 or stack:
        raise ValueError(f"Saída HID incompatível ({bits} bits); esperado: 80 bits + ID zero.")


def devices(root=Path("/sys/class/hidraw")):
    found = []
    for entry in sorted(root.glob("hidraw*")):
        fields = dict(line.split("=", 1) for line in
                      (entry / "device/uevent").read_text().splitlines() if "=" in line)
        identity = fields.get("HID_ID", "").split(":")
        if len(identity) == 3 and tuple(int(x, 16) for x in identity) == (3, 0xAA88, 0x8666):
            found.append(Path("/dev") / entry.name)
    return found


@contextlib.contextmanager
def open_panel(path):
    import fcntl
    fd = os.open(path, os.O_WRONLY | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        # Recheck the opened node, guarding against hidraw renumbering/unplug.
        info = bytearray(8)
        fcntl.ioctl(fd, 0x80084803, info, True)  # HIDIOCGRAWINFO, x86_64 Linux
        if struct.unpack("=IHH", info) != (3, 0xAA88, 0x8666):
            raise ValueError("O dispositivo aberto não é USB AA88:8666.")
        size_buffer = bytearray(4)
        fcntl.ioctl(fd, 0x80044801, size_buffer, True)  # HIDIOCGRDESCSIZE
        length = struct.unpack("=I", size_buffer)[0]
        if not 0 < length <= 4096:
            raise ValueError("Tamanho de descritor HID inválido.")
        descriptor = bytearray(struct.pack("=I", length) + bytes(4096))
        fcntl.ioctl(fd, 0x90044802, descriptor, True)  # HIDIOCGRDESC
        validate_descriptor(descriptor[4:4 + length])
        yield fd
    finally:
        os.close(fd)


def send(payload):
    found = devices()
    if len(found) != 1:
        raise ValueError(f"Encontradas {len(found)} interfaces AA88:8666; é necessária exatamente uma.")
    with open_panel(found[0]) as fd:
        # Never retry a partial write: each write is one complete HID report.
        if os.write(fd, payload) != len(payload):
            raise OSError("Envio HID incompleto.")


def sensor_paths(root=Path("/sys/class/hwmon")):
    cpus, gpus = [], []
    for entry in sorted(root.glob("hwmon*")):
        name = (entry / "name").read_text().strip()
        if name == "k10temp":
            labels = {p.read_text().strip(): p.with_name(p.name.replace("_label", "_input"))
                      for p in entry.glob("temp*_label")}
            cpus.append(labels.get("Tdie", labels.get("Tctl", entry / "temp1_input")))
        elif name == "amdgpu":
            # temp1 = edge; do not silently substitute junction/hotspot.
            gpus.append(entry / "temp1_input")
    if len(cpus) != 1 or len(gpus) != 1:
        raise ValueError(f"Sensores ambíguos/ausentes: k10temp={len(cpus)}, amdgpu={len(gpus)}. "
                         "Esperado um de cada; confira o kernel e os drivers.")
    return cpus[0], gpus[0]


def cpu_ticks(path=Path("/proc/stat")):
    fields = path.read_text().splitlines()[0].split()
    if fields[0] != "cpu" or len(fields) < 5:
        raise ValueError("/proc/stat sem contadores válidos de CPU.")
    # guest and guest_nice are already included in user/nice.
    values = [int(x) for x in fields[1:9]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return sum(values), idle


def cpu_percent(previous, current):
    total, idle = current[0] - previous[0], current[1] - previous[1]
    if total <= 0 or idle < 0 or idle > total:
        return 0
    return 100 * (total - idle) / total


def readings(paths, previous):
    cpu, gpu = paths
    c = int(cpu.read_text()) / 1000
    g = int(gpu.read_text()) / 1000
    current = cpu_ticks()
    cpu_load = cpu_percent(previous, current) if previous is not None else 0
    busy_path = gpu.parent / "device/gpu_busy_percent"
    try:
        gpu_load = int(busy_path.read_text())
    except FileNotFoundError:
        gpu_load = 0
    payload = packet(c, g, cpu_load, gpu_load)
    return payload, current, f"CPU {c:.1f} °C / {cpu_load:.0f}% | GPU {g:.1f} °C / {gpu_load}%"


@contextlib.contextmanager
def instance_lock():
    import fcntl
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise ValueError("XDG_RUNTIME_DIR ausente; execute na sessão do seu usuário, sem sudo.")
    with (Path(runtime) / "cooler-panel.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ValueError("Cooler Panel já está em execução. Pare o serviço antes do teste.") from exc
        yield


def main():
    parser = argparse.ArgumentParser(description="Cooler Panel — Ryzen + Radeon, Linux x86_64")
    parser.add_argument("--version", action="version", version=VERSION)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--probe", action="store_true", help="Identificar visor e validar descritor, sem enviar")
    mode.add_argument("--sensors", action="store_true", help="Mostrar sensores, sem acessar USB")
    mode.add_argument("--send-test", action="store_true", help="Enviar CPU 42 °C e GPU 37 °C simulados")
    mode.add_argument("--run", action="store_true", help="Atualizar visor a cada segundo")
    args = parser.parse_args()
    if sys.platform != "linux" or os.uname().machine != "x86_64":
        parser.error("Esta versão requer Linux x86_64.")
    try:
        if args.probe:
            found = devices()
            if len(found) != 1:
                raise ValueError(f"Encontradas {len(found)} interfaces AA88:8666; esperado: uma.")
            with open_panel(found[0]):
                print(f"{found[0]}: USB AA88:8666, saída compatível (11 bytes incluindo ID 0).")
            return 0
        if args.sensors:
            paths = sensor_paths()
            previous = cpu_ticks()
            time.sleep(1)
            print(readings(paths, previous)[2])
            print(f"CPU: {paths[0]}\nGPU: {paths[1]}")
            return 0
        with instance_lock():
            if args.send_test:
                send(packet(42, 37))
                print("Teste enviado: CPU 42 °C, GPU 37 °C (valores simulados). Confira o visor.")
                return 0
            running = True

            def stop(_signum, _frame):
                nonlocal running
                running = False

            signal.signal(signal.SIGTERM, stop)
            signal.signal(signal.SIGINT, stop)
            previous = None
            last_error = None
            print(f"Cooler Panel {VERSION} iniciado.", flush=True)
            while running:
                try:
                    payload, current, _ = readings(sensor_paths(), previous)
                    send(payload)
                    previous = current
                    if last_error:
                        print("Sensores e visor reconectados.", flush=True)
                    last_error = None
                    time.sleep(1)
                except (OSError, ValueError) as exc:
                    previous = None
                    error = str(exc)
                    if error != last_error:
                        print(f"Envios suspensos: {error}. Nova tentativa em 5 s.", file=sys.stderr, flush=True)
                    last_error = error
                    for _ in range(5):
                        if not running:
                            break
                        time.sleep(1)
        return 0
    except (OSError, ValueError) as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
