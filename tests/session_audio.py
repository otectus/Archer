#!/usr/bin/env python3
"""Exercise the session worker with a generated mono tone on a private PipeWire server."""
import array
from contextlib import ExitStack
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import wave

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
from archer_session import noise_config


def main():
    with tempfile.TemporaryDirectory(prefix="archer-session-audio-") as temporary, ExitStack() as stack:
        work = Path(temporary)
        config = work / "server"
        config.mkdir()
        shutil.copy("/usr/share/pipewire/pipewire.conf", config / "pipewire.conf")
        dropins = config / "pipewire.conf.d"
        dropins.mkdir()
        name = "alsa_input.archer_test"
        (dropins / "test.conf").write_text("""
context.spa-libs = { audiotestsrc = audiotestsrc/libspa-audiotestsrc }
context.objects = [
    { factory = adapter args = { factory.name = support.null-audio-sink node.name = alsa_output.archer_test
      media.class = Audio/Sink audio.position = [ MONO ] } }
    { factory = adapter args = {
        factory.name = audiotestsrc node.name = alsa_input.archer_test media.class = Audio/Source
        audio.position = [ MONO ] node.param.Props = { live = true volume = 0.5 }
    }}
]
""")
        env = dict(os.environ, XDG_RUNTIME_DIR=temporary, XDG_STATE_HOME=str(work / "state"),
                   XDG_CONFIG_HOME=str(work / "config"), XDG_CACHE_HOME=str(work / "cache"),
                   DBUS_SYSTEM_BUS_ADDRESS="unix:path=" + str(work / "nonexistent"))
        env.pop("PIPEWIRE_REMOTE", None)
        # Do not start any physical device monitor in the private session.
        wp_config = work / "config/wireplumber/wireplumber.conf.d"
        wp_config.mkdir(parents=True)
        (wp_config / "50-test.conf").write_text("""
wireplumber.profiles = { main = {
 monitor.alsa = disabled monitor.alsa-midi = disabled monitor.bluez = disabled
 monitor.libcamera = disabled monitor.v4l2 = disabled monitor.bluez-midi = disabled
 support.logind = disabled
} }
""")

        def start(argv, environment):
            log = stack.enter_context((work / (str(len(processes)) + ".log")).open("w+"))
            proc = subprocess.Popen(argv, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log)

            def stop():
                if proc.poll() is None:
                    proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            stack.callback(stop)
            processes.append(proc)
            return proc

        def graph():
            result = subprocess.run(["pw-dump"], env=env, capture_output=True, timeout=3)
            return json.loads(result.stdout or "[]") if result.returncode == 0 else []

        processes = []

        def server():
            return start(["pipewire"], {**env, "PIPEWIRE_CONFIG_DIR": str(config)})

        def wait_for_link(worker):
            for _ in range(80):
                objects = graph()
                names = {n["id"]: (n.get("info") or {}).get("props", {}).get("node.name") for n in objects}
                if any(n.get("type") == "PipeWire:Interface:Link"
                       and names.get(n["info"].get("output-node-id")) == name
                       and names.get(n["info"].get("input-node-id")) == "archer_noise_capture"
                       for n in objects):
                    return
                if worker.poll() is not None:
                    raise AssertionError("Session worker exited before its selected microphone connected")
                time.sleep(.1)
            raise AssertionError("Selected source did not connect to the noise filter")

        audio_server = server()
        for _ in range(50):
            if any((n.get("info") or {}).get("props", {}).get("node.name") == name for n in graph()):
                break
            time.sleep(.1)
        manager = start(["wireplumber"], env)
        worker_config = noise_config(name)
        # Remove the VAD gate for this synthetic non-speech signal. Keep the real rnnoise plugin.
        worker_config["context.modules"][-1]["args"]["filter.graph"]["nodes"][0]["control"]["VAD Threshold (%)"] = 0.0
        path = work / "filter.conf"
        path.write_text(json.dumps(worker_config))
        worker = start(["pipewire", "-c", str(path)], env)
        wait_for_link(worker)

        def record_signal(target):
            audio = work / (target + ".pcm")
            with audio.open("wb") as output, subprocess.Popen(
                    ["pw-cat", "--record", "--target", target, "--channels", "1",
                     "--rate", "48000", "--format", "s16", "-"], stdout=output, env=env) as capture:
                try:
                    for _ in range(80):
                        if audio.stat().st_size >= 96000:
                            break
                        if capture.poll() is not None:
                            raise RuntimeError("The virtual-source recorder exited early")
                        time.sleep(.05)
                finally:
                    capture.terminate()
                    capture.wait(timeout=3)
            values = array.array("h", audio.read_bytes())
            assert len(values) >= 16000 and max(map(abs, values)) > 0, f"No signal reached {target}"

        record_signal("archer_noise_suppressed")
        worker.terminate()
        worker.wait(timeout=3)
        for _ in range(20):
            if not any((n.get("info") or {}).get("props", {}).get("node.name") == "archer_noise_suppressed" for n in graph()):
                break
            time.sleep(.1)
        else:
            raise AssertionError("Virtual source remained after worker exit")
        tool = Path(__file__).resolve().parents[1] / "gui/archer_session.py"
        foreground = start([sys.executable, str(tool), "noise", name], env)
        wait_for_link(foreground)
        manager.terminate()
        manager.wait(timeout=3)
        audio_server.terminate()
        audio_server.wait(timeout=3)
        time.sleep(2)
        server()
        for _ in range(50):
            if any((n.get("info") or {}).get("props", {}).get("node.name") == name for n in graph()):
                break
            time.sleep(.1)
        start(["wireplumber"], env)
        wait_for_link(foreground)
        foreground.terminate()
        assert foreground.wait(timeout=6) == 0
        echo = start([sys.executable, str(tool), "echo", name, "alsa_output.archer_test"], env)
        for _ in range(100):
            objects = graph()
            names = {n["id"]: (n.get("info") or {}).get("props", {}).get("node.name") for n in objects}
            pairs = {(names.get(n["info"].get("output-node-id")), names.get(n["info"].get("input-node-id")))
                     for n in objects if n.get("type") == "PipeWire:Interface:Link"}
            if (name, "archer_echo_capture") in pairs and ("archer_echo_playback", "alsa_output.archer_test") in pairs:
                break
            if echo.poll() is not None:
                raise AssertionError("Echo worker exited: " + (work / f"{len(processes) - 1}.log").read_text()[-1024:])
            time.sleep(.1)
        else:
            raise AssertionError(f"Echo reference or capture path absent: {pairs}, {list(names.values())}")
        assert "archer_echo_source" in names.values() and "archer_echo_return" in names.values()
        far_end_audio = work / "far-end.wav"
        with wave.open(str(far_end_audio), "wb") as wav:
            wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            wav.writeframes(bytes(48000 * 2 * 8))
        with subprocess.Popen(["pw-play", "--target", "archer_echo_return", str(far_end_audio)], env=env) as far_end:
            try:
                record_signal("archer_echo_source")
                assert far_end.poll() is None, "Reference playback exited before the capture check"
            finally:
                far_end.terminate()
                far_end.wait(timeout=3)
        echo.terminate()
        assert echo.wait(timeout=6) == 0
        print("WebRTC created the call microphone and return sink and linked both selected endpoints.")
        print("Generated tone reached rnnoise. The foreground worker reconnected its selected source after a private PipeWire restart and stopped cleanly.")


if __name__ == "__main__":
    main()
