import sys
import subprocess
from pathlib import Path
from typing import List


def patch_windows_unpaper_args(args):
    if sys.platform.startswith("win"):
        if args[0] == "unpaper":
            args[0] = "unpaper.cmd"
    return args


def get_unpaper_args(
    layout=None, output_pages=None, pre_rotate=None, as_string=False, full=False
):
    unpaper_args_list = []
    if full:
        default_args = [
            "--mask-scan-size",
            "100",  # don't blank out narrow columns
            "--no-border-align",  # don't align visible content to borders
            "--no-mask-center",  # don't center visible content within page
            "--no-grayfilter",  # don't remove light gray areas
            "--no-blackfilter",  # don't remove solid black areas
        ]
        unpaper_args_list.extend(default_args)

    if layout is not None:
        unpaper_args_list.append("--layout")
        unpaper_args_list.append(layout)

    if pre_rotate is not None:
        unpaper_args_list.append("--pre-rotate")
        unpaper_args_list.append(str(pre_rotate))

    if output_pages in ["1", "2"]:
        unpaper_args_list.append("--output-pages")
        unpaper_args_list.append(str(output_pages))

    if as_string:
        return " ".join(unpaper_args_list)

    return unpaper_args_list


def run_unpaper_version() -> None:
    cmd = ["unpaper", "--version"]

    cmd = patch_windows_unpaper_args(cmd)

    # Run Unpaper
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,  # capture output
        stderr=subprocess.STDOUT,
        text=True,
        check=False,
    )

    # Print the version
    if result.returncode == 0:
        print("Unpaper version:", result.stdout.strip())
    else:
        print("Failed to run unpaper. Output:", result.stdout.strip())


def run_unpaper_simple(
    input_file: Path,
    output_file: Path,
    tmpdir: Path,
    dpi: float = 300,
    mode_args: List[str] = None,
) -> None:
    """
    Run unpaper via the unpaper.CMD wrapper (Docker-based).

    Args:
        input_file (Path): PNG input file.
        output_file (Path): Target PNM (or PNG).
        dpi (float): Resolution in DPI (default: 300).
        mode_args (List[str]): Extra unpaper options.
    """
    if mode_args is None:
        mode_args = []

    input_file = input_file.resolve()
    output_file = output_file.resolve()

    # Ensure output directory exists
    output_file.parent.mkdir(parents=True, exist_ok=True)

    cmd = (
        [
            "unpaper",
            "-v",
            "--dpi",
            str(round(dpi, 6)),
        ]
        + mode_args
        + [str(input_file), str(output_file)]
    )

    cmd = patch_windows_unpaper_args(cmd)

    # Run the command
    result = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        cwd=tmpdir,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"Unpaper failed for {input_file}\n"
            f"Command: {' '.join(cmd)}\n"
            f"Output:\n{result.stdout}"
        )
