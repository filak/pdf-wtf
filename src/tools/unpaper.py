import os
import sys
import subprocess
from pathlib import Path

DOCKER_IMAGE = "unpaper-alpine"

def main():
    args = sys.argv[1:]
    if not args:
        print("Usage: unpaper.py [options] [input_file output_file]")
        sys.exit(0)

    # Separate options (starting with '-') from paths
    options = []
    paths = []

    for a in args:
        if a.lower().endswith(".png") or a.lower().endswith(".pnm"):
            paths.append(Path(a).resolve())
        else:
            options.append(a)

    # Handle calls like "--version" or "--help" (no input/output paths)
    if len(paths) < 2:
        docker_cmd = ["docker", "run", "--rm", DOCKER_IMAGE] + args
        subprocess.run(docker_cmd)
        sys.exit(0)

    # The last two paths are input and output
    input_file = paths[-2]
    output_file = paths[-1]

    # Ensure output folder exists on the host
    output_file.parent.mkdir(parents=True, exist_ok=True)

    # Prepare Docker mounts
    mounts = {}
    container_paths = {}

    # Mount input parent
    mounts[input_file.parent.resolve()] = "/data0"
    container_paths[input_file] = "/data0/" + input_file.name

    # Mount output parent (different from input?)
    if input_file.parent.resolve() != output_file.parent.resolve():
        mounts[output_file.parent.resolve()] = "/data1"
        container_paths[output_file] = "/data1/" + output_file.name
    else:
        container_paths[output_file] = container_paths[input_file]

    # Build Docker command
    docker_cmd = ["docker", "run", "--rm", "-e", "TMP=/data0", "-e", "TEMP=/data0"]
    for host_dir, container_dir in mounts.items():
        docker_cmd.extend(["-v", f"{host_dir}:{container_dir}"])

    docker_cmd.append(DOCKER_IMAGE)
    docker_cmd.extend(options)
    docker_cmd.append(container_paths[input_file])
    docker_cmd.append(container_paths[output_file])

    # Debug: print("Docker command:", " ".join(docker_cmd))

    # Run Dockerized unpaper
    result = subprocess.run(docker_cmd)
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()
