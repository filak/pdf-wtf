import sys
import subprocess
from pathlib import Path
import logging

DOCKER_IMAGE = "unpaper-alpine"


def find_project_root(marker="instance") -> Path:
    current = Path(__file__).resolve()
    for parent in [current.parent] + list(current.parents):
        if (parent / marker).exists():
            return parent
    raise RuntimeError(f"Project root with marker '{marker}' not found.")


try:
    project_root = find_project_root()
except RuntimeError as e:  # noqa: F841
    project_root = Path(__file__).resolve().parent

log_dir = project_root / "instance" / "logs"
log_dir.mkdir(parents=True, exist_ok=True)
log_file = log_dir / "unpaper_wrap.log"

log = logging.getLogger("unpaper_wrap")
# log.setLevel(logging.DEBUG)
log.setLevel(logging.ERROR)

fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
# fh.setLevel(logging.DEBUG)
fh.setLevel(logging.ERROR)

formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
fh.setFormatter(formatter)
log.addHandler(fh)

# Prevent logging to stdout
log.propagate = False


def main():
    args = sys.argv[1:]
    if not args:
        log.info("Usage: unpaper_wrap.py [options] [input_file output_file]")
        sys.exit(0)

    # Separate options (starting with '-') from paths
    options = []
    paths = []

    for a in args:
        if a.lower().endswith(".png") or a.lower().endswith(".pnm"):
            paths.append(Path(a).resolve())
        else:
            options.append(a)

    log.debug("Arguments received: %s", args)

    # Handle calls like "--version" or "--help" (no input/output paths)
    if len(paths) < 2:
        docker_cmd = ["docker", "run", "--rm", DOCKER_IMAGE] + args
        log.info("Running Docker command (no input/output paths): %s", docker_cmd)
        subprocess.run(docker_cmd)
        sys.exit(0)

    # The last two paths are input and output
    input_file = paths[-2]
    output_file = paths[-1]

    # Ensure output folder exists on the host
    if not output_file.parent.exists():
        log.debug("Creating output folder: %s", output_file.parent)
        output_file.parent.mkdir(parents=True, exist_ok=True)

    log.debug("Input file: %s", input_file)
    log.debug("Output file: %s", output_file)
    log.debug("Input exists: %s", input_file.exists())
    log.debug("Output parent exists: %s", output_file.parent.exists())

    # Prepare Docker mounts
    mounts = {}
    container_paths = {}

    mounts[input_file.parent] = "/data0"
    container_paths[input_file] = "/data0/" + input_file.name

    if input_file.parent != output_file.parent:
        mounts[output_file.parent] = "/data1"
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

    log.debug("Docker command: %s", " ".join(docker_cmd))

    # Run Dockerized unpaper
    try:
        result = subprocess.run(docker_cmd, check=True)
        log.info("Unpaper completed successfully: return code %s", result.returncode)
        sys.exit(result.returncode)
    except subprocess.CalledProcessError as e:
        log.error("Unpaper failed: %s", e)
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
