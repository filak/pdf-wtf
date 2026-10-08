"""External unpaper invocation."""

import logging
from pathlib import Path
import subprocess
import sys

log = logging.getLogger(__name__)
PROBE_TIMEOUT = 15.0
PROCESS_TIMEOUT = 300.0


def patch_windows_unpaper_args(args: list[str]) -> list[str]:
    if sys.platform.startswith("win") and args and args[0] == "unpaper":
        return ["unpaper.cmd", *args[1:]]
    return args


def get_unpaper_args(
    layout: str | None = None,
    output_pages: str | None = None,
    pre_rotate: int | None = None,
    as_string: bool = False,
    get_default: bool = False,
    unpaper_ok: bool = False,
) -> list[str] | str | None:
    if not unpaper_ok:
        return None
    args = []
    if get_default:
        args.extend(
            [
                "--mask-scan-size",
                "100",
                "--no-border-align",
                "--no-mask-center",
                "--no-grayfilter",
                "--no-blackfilter",
            ]
        )
    if layout is not None and layout != "none":
        args.extend(["--layout", layout])
    if pre_rotate is not None:
        args.extend(["--pre-rotate", str(pre_rotate)])
    if output_pages in ("1", "2"):
        args.extend(["--output-pages", str(output_pages)])
    if not args:
        return None
    return " ".join(args) if as_string else args


def get_unpaper_version() -> tuple[bool, str]:
    """Probe availability without treating a missing optional tool as fatal."""
    try:
        result = subprocess.run(
            patch_windows_unpaper_args(["unpaper", "--version"]),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            check=False,
            timeout=PROBE_TIMEOUT,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False, "unpaper is unavailable."
    if result.returncode != 0:
        return False, "unpaper version check failed."
    return True, result.stdout.strip()


def run_unpaper_simple(
    input_file: Path,
    output_file: Path,
    tmpdir: Path,
    dpi: float = 300,
    mode_args: list[str] | None = None,
) -> None:
    output_file = Path(output_file).resolve()
    output_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = patch_windows_unpaper_args(
        [
            "unpaper",
            "-v",
            "--dpi",
            str(round(dpi, 6)),
            *(mode_args or []),
            str(Path(input_file).resolve()),
            str(output_file),
        ]
    )
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=tmpdir,
            timeout=PROCESS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RuntimeError("unpaper could not complete page processing.") from None
    if result.returncode != 0:
        # Do not expose external-tool output, which may contain document data.
        raise RuntimeError("unpaper page processing failed.")
