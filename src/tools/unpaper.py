"""
Wrapper for running unpaper inside Docker with OCRmyPDF on Windows.
Handles temp folder mounting, path rewriting, and TMP/TEMP inside Docker.
"""

import os
import sys
import subprocess
from pathlib import Path

DOCKER_IMAGE = "unpaper-alpine"


def main():
    args = sys.argv[1:]

    # Ensure PDFWTF_TEMP_DIR is set and exists
    pdfwtf_temp = os.environ.get("PDFWTF_TEMP_DIR")
    if not pdfwtf_temp:
        print("Error: PDFWTF_TEMP_DIR environment variable is not set.")
        sys.exit(1)

    pdfwtf_temp_path = Path(pdfwtf_temp).resolve()
    pdfwtf_temp_path.mkdir(parents=True, exist_ok=True)

    # Prepare Docker command
    docker_cmd = ["docker", "run", "--rm"]

    # Mount the entire temp folder as /data0
    docker_cmd += [
        "-v",
        f"{pdfwtf_temp_path}:/data0",
        "-e",
        "TMP=/data0",
        "-e",
        "TEMP=/data0",
    ]

    # Map host paths to container paths
    mount_map = {str(pdfwtf_temp_path): "/data0"}

    rewritten_args = []
    for a in args:
        if a.startswith("-"):
            rewritten_args.append(a)
            continue

        p = Path(a).resolve()
        mapped = None
        for host_dir, container_dir in mount_map.items():
            if str(p).startswith(host_dir):
                # Use Linux-style slashes for Docker
                rel_parts = Path(os.path.relpath(p, host_dir)).parts
                mapped = Path(container_dir, *rel_parts).as_posix()
                break
        if mapped is None:
            # Path outside temp folder? Leave it as is, or optionally error
            mapped = str(p)
        rewritten_args.append(mapped)

    # Add Docker image and rewritten args
    docker_cmd.append(DOCKER_IMAGE)
    #docker_cmd.extend(rewritten_args)
    docker_cmd.extend(args)

    # Debug print if needed
    # print("Docker command:", " ".join(docker_cmd))

    # Run Dockerized unpaper
    result = subprocess.run(docker_cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
