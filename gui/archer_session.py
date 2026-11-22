#!/usr/bin/python3
"""Unprivileged, foreground multimedia workers. Capture starts only on request."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import struct
import signal
import time
import subprocess
import sys
import tempfile

from archer_runtime import run_command
import archer_workspace as workspace


VIDEO_FILTERS = {
    "color": "format=yuv420p",
    "monochrome": "hue=s=0",
    "close-up": "crop=iw*0.75:ih*0.75,scale=640:480",
    "green-background-blur": "split[fg][bg];[bg]boxblur=12:2[soft];[fg]chromakey=0x00ff00:0.20:0.08[key];[soft][key]overlay",
}
VIDEO_BASE = "fps=15,scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2"


def camera_devices(sysfs=Path("/sys"), dev=Path("/dev")):
    result = []
    for node in sorted((sysfs / "class/video4linux").glob("video[0-9]*")):
        if not re.fullmatch(r"video\d+", node.name):
            continue
        path = dev / node.name
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            try:
                # VIDIOC_QUERYCAP, Linux struct v4l2_capability is 104 bytes.
                buf = bytearray(104)
                fcntl.ioctl(fd, 0x80685600, buf)
            finally:
                os.close(fd)
            driver, name, _, _, caps, device_caps = struct.unpack_from("=16s32s32sIII", buf)
            caps = device_caps if caps & 0x80000000 else caps
            driver = driver.split(b"\0")[0].decode(errors="replace")
            name = name.split(b"\0")[0].decode(errors="replace")
            virtual = "loopback" in driver or "v4l2 loopback" in driver
            role = ("output" if virtual and name == "Archer Camera" and caps & 2
                    else "source" if not virtual and caps & 1 else "other")
            result.append({"node": node.name, "label": name, "role": role, "driver": driver,
                           "identity": str(node.resolve())})
        except OSError as error:
            result.append({"node": node.name, "label": node.name, "role": "unavailable", "reason": error.strerror})
    return result


def camera_command(source, effect, output=None):
    devices = camera_devices()
    if source not in [d for d in devices if d["role"] == "source"]:
        raise ValueError("Selected physical capture device changed or is unavailable")
    source_path = "/dev/" + source["node"]
    graph = VIDEO_BASE + "," + VIDEO_FILTERS[effect]
    if output is None:
        return ["ffplay", "-nostats", "-loglevel", "error", "-threads", "2", "-f", "video4linux2",
                "-vf", graph, "-window_title", "Archer camera preview", source_path]
    if output not in [d for d in devices if d["role"] == "output"]:
        raise ValueError("Archer Camera is busy or unavailable. Check the loopback module.")
    return ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-filter_threads", "2", "-threads", "2",
            "-f", "video4linux2", "-i", source_path, "-vf", graph, "-pix_fmt", "yuv420p", "-threads", "2",
            "-f", "v4l2", "/dev/" + output["node"]]


AUDIO_PRESETS = {"balanced": 50.0, "quiet-room": 25.0, "noisy-room": 75.0}


def mic_preset(source, value=None):
    folder = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "archer/microphones"
    profile = folder / (hashlib.sha256(source.encode()).hexdigest() + ".json")
    if value is None:
        try:
            with profile.open() as stream:
                saved = json.loads(stream.read(256))
            return saved if saved in AUDIO_PRESETS else "balanced"
        except (OSError, ValueError, TypeError):
            return "balanced"
    if value not in AUDIO_PRESETS:
        raise ValueError("Unknown microphone preset")
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", dir=folder, delete=False) as stream:
        json.dump(value, stream)
    os.replace(stream.name, profile)
    return value


def audio_nodes(outputs=False):
    result = run_command(["pw-dump"], timeout=3)
    if not result.success:
        raise ValueError("The PipeWire session is unavailable")
    rows = json.loads(result.stdout)
    nodes = []
    for row in rows:
        props = (row.get("info") or {}).get("props", {})
        if props.get("media.class") != ("Audio/Sink" if outputs else "Audio/Source"):
            continue
        name = props.get("node.name") or ""
        prefixes = ("alsa_output.", "bluez_output.") if outputs else ("alsa_input.", "bluez_input.")
        if props.get("device.api") in ("alsa", "bluez5") or name.startswith(prefixes):
            nodes.append({"source": name, "label": props.get("node.description") or name, "physical": True})
        elif name in ("archer_noise_suppressed", "archer_echo_source"):
            nodes.append({"source": name, "label": props.get("node.description") or name, "physical": False})
    return nodes


@contextmanager
def noise_lock():
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime or Path(runtime).stat().st_uid != os.getuid():
        raise ValueError("Start the filter in your own login session")
    fd = os.open(Path(runtime) / "archer-noise.lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("An Archer session already owns the noise filter") from None
        yield
    finally:
        os.close(fd)


def audio_linked(source, output=None):
    result = run_command(["pw-dump"], timeout=3)
    if not result.success:
        return False
    objects = json.loads(result.stdout)
    names = {n["id"]: (n.get("info") or {}).get("props", {}).get("node.name") for n in objects}
    pairs = {(names.get(n["info"].get("output-node-id")), names.get(n["info"].get("input-node-id")))
             for n in objects if n.get("type") == "PipeWire:Interface:Link"}
    if output:
        return ("archer_echo_source" in names.values() and (source, "archer_echo_capture") in pairs
                and ("archer_echo_playback", output) in pairs)
    return "archer_noise_suppressed" in names.values() and (source, "archer_noise_capture") in pairs


def echo_config(source, output):
    config = noise_config(source)
    config["context.spa-libs"]["aec.*"] = "aec/libspa-aec-webrtc"
    config["context.modules"][-1] = {"name": "libpipewire-module-echo-cancel", "args": {
        "library.name": "aec/libspa-aec-webrtc", "audio.position": ["MONO"],
        "capture.props": {"node.name": "archer_echo_capture", "target.object": source, "node.dont-fallback": True},
        "playback.props": {"node.name": "archer_echo_playback", "target.object": output, "node.dont-fallback": True},
        "source.props": {"node.name": "archer_echo_source", "node.description": "Archer Echo Microphone",
                         "priority.session": 1},
        "sink.props": {"node.name": "archer_echo_return", "node.description": "Archer Call Output",
                       "priority.session": 1}}}
    return config


def noise_config(source, preset="balanced"):
    return {
        "context.spa-libs": {"audio.convert.*": "audioconvert/libspa-audioconvert", "support.*": "support/libspa-support"},
        "context.modules": [{"name": "libpipewire-module-" + name} for name in ("protocol-native", "client-node", "adapter")]
        + [{"name": "libpipewire-module-filter-chain", "args": {
            "node.description": "Archer Noise Suppression",
            "filter.graph": {"nodes": [{"type": "ladspa", "name": "rnnoise", "plugin": "librnnoise_ladspa",
                                        "label": "noise_suppressor_mono", "control": {"VAD Threshold (%)": AUDIO_PRESETS[preset]}}]},
            "capture.props": {"node.name": "archer_noise_capture", "target.object": source, "node.passive": True,
                              "node.dont-fallback": True, "audio.rate": 48000},
            "playback.props": {"node.name": "archer_noise_suppressed", "media.class": "Audio/Source", "audio.rate": 48000}}}],
    }


def speaker_check():
    result = run_command(["ffplay", "-hide_banner", "-loglevel", "error", "-nodisp", "-autoexit",
                          "-f", "lavfi", "-i", "sine=frequency=440:duration=1,volume=0.2,afade=t=out:st=0.8:d=0.2"], timeout=8)
    if not result.success:
        raise ValueError("Speaker playback failed. Check FFplay and the default session output.")
    return {"playback": "completed", "audible_result": "requires_listener"}


def audio_command(action, source, directory, output=None, preset=None):
    nodes = audio_nodes()
    if not any(d["physical"] and d["source"] == source for d in nodes):
        raise ValueError("Select a connected physical microphone")
    if action == "meter":
        return ["ffmpeg", "-nostdin", "-hide_banner", "-f", "pulse", "-i", source, "-t", "3", "-af",
                "volumedetect", "-f", "null", "-"]
    if any(not d["physical"] for d in nodes):
        raise ValueError("An Archer processing chain already exists. Turn it off before starting another.")
    if action == "echo":
        if not Path("/usr/lib/spa-0.2/aec/libspa-aec-webrtc.so").is_file():
            raise ValueError("The PipeWire WebRTC echo cancellation plugin is unavailable")
        if not any(d["physical"] and d["source"] == output for d in audio_nodes(outputs=True)):
            raise ValueError("Select a connected physical speaker or headset")
    elif not Path("/usr/lib/ladspa/librnnoise_ladspa.so").is_file():
        raise ValueError("Install the audio-enhance module to enable noise suppression")
    config = directory / "session-filter.conf"
    config.write_text(json.dumps(echo_config(source, output) if action == "echo" else noise_config(source, preset or mic_preset(source))))
    return ["pipewire", "-c", str(config)]


def run_filter(source, directory, output=None, preset=None):
    """Reconnect only the chosen source during this foreground user request."""
    stop = False

    def terminate(_signal, _frame):
        nonlocal stop
        stop = True

    previous = signal.signal(signal.SIGTERM, terminate)
    worker = None
    try:
        with noise_lock():
            cmd = audio_command("echo" if output else "noise", source, directory, output, preset)
            deadline = time.monotonic() + 30
            reported = None
            started = None
            while not stop:
                try:
                    nodes = audio_nodes()
                    present = any(p["physical"] and p["source"] == source for p in nodes)
                    if output:
                        present = present and any(p["physical"] and p["source"] == output for p in audio_nodes(outputs=True))
                    linked = present and worker is not None and worker.poll() is None and audio_linked(source, output)
                except (ValueError, OSError):
                    present, linked = False, False
                now = time.monotonic()
                if linked:
                    deadline = now + 30
                    started = now
                elif now >= deadline:
                    raise ValueError("Selected microphone did not reconnect within 30 seconds")
                elif worker is not None and worker.poll() is None and (not present or now - started >= 5):
                    # A disconnected filter process can survive the loss of its server.
                    worker.terminate()
                    try:
                        worker.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        worker.kill()
                        worker.wait()
                elif present and (worker is None or worker.poll() is not None):
                    # Let another program retain ownership if it created a filter while ours was disconnected.
                    if any(not p["physical"] for p in nodes):
                        raise ValueError("Another processing chain owns the virtual microphone")
                    worker = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
                    started = now
                if reported != linked:
                    print("ARCHER_SOURCE_CONNECTED" if linked else "ARCHER_SOURCE_WAITING", file=sys.stderr, flush=True)
                    reported = linked
                time.sleep(1)
    finally:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            try:
                worker.wait(timeout=3)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
        signal.signal(signal.SIGTERM, previous)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("cameras", "microphones", "speakers"):
        sub.add_parser(name)
    for name in ("preview", "stream"):
        action = sub.add_parser(name)
        action.add_argument("source")
        action.add_argument("--effect", choices=VIDEO_FILTERS, default="color")
        if name == "stream":
            action.add_argument("output")
    noise = sub.add_parser("noise")
    noise.add_argument("source")
    noise.add_argument("--preset", choices=AUDIO_PRESETS)
    sub.add_parser("meter").add_argument("source")
    echo = sub.add_parser("echo")
    echo.add_argument("source")
    echo.add_argument("output")
    import archer_peripherals as peripherals
    sub.add_parser("peripheral").add_argument("kind", choices=("fingerprint", "bluetooth", "network", "wwan", "orientation", "storage"))
    sub.add_parser("enroll").add_argument("finger", choices=peripherals.FINGERS)
    sub.add_parser("firmware")
    flashing = sub.add_parser("firmware-install")
    flashing.add_argument("device_key")
    flashing.add_argument("version")
    sub.add_parser("lights")
    lighting = sub.add_parser("brightness")
    lighting.add_argument("kind", choices=("backlight", "leds"))
    lighting.add_argument("device")
    lighting.add_argument("level", type=int)
    adapt = sub.add_parser("follow-power")
    adapt.add_argument("--ac", choices=workspace.PRESETS, required=True)
    adapt.add_argument("--dock", choices=workspace.PRESETS, help="Preset for an attached external display or an authorized Thunderbolt peripheral")
    adapt.add_argument("--battery", choices=workspace.PRESETS, required=True)
    adapt.add_argument("--minutes", type=int, default=60)
    adapt.add_argument("--apply", action="store_true")
    sub.add_parser("speaker-test", help="Play a one-second tone on your current output")
    sub.add_parser("gpus")
    sub.add_parser("npu-test")
    launcher = sub.add_parser("launch")
    launcher.add_argument("gpu", type=int)
    launcher.add_argument("desktop_id")
    preset = sub.add_parser("workspace")
    preset.add_argument("preset", nargs="?", choices=workspace.PRESETS)
    preset.add_argument("--minutes", type=int, default=30)
    preset.add_argument("--import-file", type=Path)
    preset.add_argument("--export", action="store_true")
    preset.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise ValueError("Run media tasks in your desktop session as your login user")
    if args.action in ("firmware", "firmware-install"):
        from archer_firmware import firmware_status, install_reviewed
        data = (firmware_status() if args.action == "firmware" else install_reviewed(args.device_key, args.version))
        print(json.dumps(data, indent=2))
        return data.get("exit_status", 0)
    if args.action == "peripheral":
        print(json.dumps(peripherals.probe(args.kind), indent=2))
        return 0
    if args.action == "speaker-test":
        print(json.dumps(speaker_check()))
        return 0
    if args.action == "enroll":
        from gi.repository import Gio
        return peripherals.enroll(args.finger, Gio.bus_get_sync(Gio.BusType.SYSTEM, None))
    if args.action in ("lights", "brightness", "follow-power"):
        if args.action == "lights":
            data = workspace.brightness_devices()
        elif args.action == "brightness":
            data = workspace.set_brightness(args.kind, args.device, args.level)
        else:
            if not 1 <= args.minutes <= 240:
                raise ValueError("Choose a duration between one minute and four hours")
            data = workspace.follow_power(args.ac, args.battery, args.minutes, args.apply, args.dock)
        print(json.dumps(data, indent=2))
        return 0
    if args.action in ("gpus", "npu-test", "launch", "workspace"):
        if args.action == "gpus":
            data = [{"id": i, "name": d["Name"], "default": d["Default"]} for i, d in enumerate(workspace.switcheroo())]
        elif args.action == "npu-test":
            data = workspace.npu_self_test()
        elif args.action == "launch":
            data = workspace.launch(args.gpu, args.desktop_id)
        else:
            pref = (workspace.preference_file(args.import_file) if args.import_file else
                    workspace.valid_profile({"schema_version": 1, "preset": args.preset, "duration_minutes": args.minutes}))
            data = pref if args.export else workspace.run_preset(pref, apply=args.apply)
        print(json.dumps(data, indent=2))
        return 0
    if args.action in ("cameras", "microphones", "speakers"):
        print(json.dumps(camera_devices() if args.action == "cameras" else audio_nodes(outputs=args.action == "speakers"), indent=2))
        return 0
    with tempfile.TemporaryDirectory(prefix="archer-media-") as directory:
        if args.action in ("preview", "stream"):
            devices = {d["node"]: d for d in camera_devices()}
            command = camera_command(devices[args.source], args.effect, devices[getattr(args, "output")] if args.action == "stream" else None)
        else:
            if args.action in ("noise", "echo"):
                return run_filter(args.source, Path(directory), getattr(args, "output", None), getattr(args, "preset", None))
            command = audio_command(args.action, args.source, Path(directory))
        return subprocess.call(command)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ImportError, RuntimeError, KeyError, ValueError, OSError) as error:
        sys.exit(f"Session operation failed: {error}")
