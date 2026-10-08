"""Run unpaper in Docker from the Windows command wrapper."""

import logging
from pathlib import Path
import subprocess
import sys

from pdfwtf.configuration import ConfigurationError, load_config
from pdfwtf.logging_utils import configure_logging

DOCKER_IMAGE = "unpaper-alpine"
log = logging.getLogger("pdfwtf.unpaper_wrap")


def build_command(args: list[str]) -> list[str]:
    """Map the existing PNG/PNM file arguments into container paths."""
    options = []
    paths = []
    for arg in args:
        if arg.lower().endswith((".png", ".pnm")):
            paths.append(Path(arg).resolve())
        else:
            options.append(arg)
    if len(paths) < 2:
        return ["docker", "run", "--rm", DOCKER_IMAGE, *args]
    if len(paths) != 2:
        raise ValueError("unpaper requires one input and one output path.")
    input_file, output_file = paths
    if input_file == output_file:
        raise ValueError("unpaper output must differ from its input.")
    output_file.parent.mkdir(parents=True, exist_ok=True)
    mounts = {input_file.parent: "/data0"}
    output_mount = "/data0"
    if input_file.parent != output_file.parent:
        mounts[output_file.parent] = "/data1"
        output_mount = "/data1"
    cmd = ["docker", "run", "--rm", "-e", "TMP=/data0", "-e", "TEMP=/data0"]
    for host, container in mounts.items():
        cmd.extend(["-v", f"{host}:{container}"])
    return [
        *cmd,
        DOCKER_IMAGE,
        *options,
        "/data0/" + input_file.name,
        output_mount + "/" + output_file.name,
    ]


def main() -> None:
    configure_logging()
    try:
        load_config()
        args = sys.argv[1:]
        if not args:
            log.error("Supply unpaper options or input and output paths.")
            raise SystemExit(2)
        command = build_command(args)
        timeout = 10.0 if "--version" in args or "--help" in args else 290.0
        result = subprocess.run(command, timeout=timeout, check=False)
    except (ConfigurationError, ValueError):
        log.error("Invalid unpaper configuration or arguments.")
        raise SystemExit(2) from None
    except (OSError, subprocess.TimeoutExpired):
        log.error("Docker unpaper could not complete.")
        raise SystemExit(1) from None
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
