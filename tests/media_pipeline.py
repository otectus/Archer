#!/usr/bin/env python3
"""Process generated video through each production filter. No capture device is opened."""
import hashlib
from pathlib import Path
import subprocess
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "gui"))
from archer_session import VIDEO_BASE, VIDEO_FILTERS


def main():
    checksums = set()
    for name, graph in VIDEO_FILTERS.items():
        # A green stripe and hard edges exercise both the foreground and keyed background.
        output = subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                                 "testsrc2=size=320x240:rate=15,drawbox=x=60:y=40:w=70:h=60:c=0x00ff00:t=fill",
                                 "-filter_threads", "2", "-vf", VIDEO_BASE + "," + graph, "-frames:v", "4",
                                 "-pix_fmt", "yuv420p", "-f", "rawvideo", "-"],
                                capture_output=True, timeout=10, check=True)
        if len(output.stdout) != 4 * 640 * 480 * 3 // 2:
            raise RuntimeError(f"{name} produced the wrong frame count or dimensions")
        checksums.add(hashlib.sha256(output.stdout).digest())
    if len(checksums) != len(VIDEO_FILTERS):
        raise RuntimeError("Two different camera effects produced identical frames")
    print("Four frames at 640×480 passed through each production camera filter with distinct output.")


if __name__ == "__main__":
    main()
