"""Shared configuration defined by specs/CONFIGURATION.md."""

import ast
import configparser
from dataclasses import dataclass
import io
import os
from pathlib import Path
import tokenize
from typing import Any, Mapping

from dotenv import dotenv_values


class ConfigurationError(ValueError):
    """Configuration is missing or invalid."""


def parse_value(value: str) -> Any:
    """Parse safe Python literals, including case-insensitive JSON scalars."""
    value = value.strip()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(value).readline))
        tokens = [
            (
                token._replace(
                    string={"true": "True", "false": "False", "null": "None"}.get(
                        token.string.lower(), token.string
                    )
                )
                if token.type == tokenize.NAME
                else token
            )
            for token in tokens
        ]
        return ast.literal_eval(tokenize.untokenize(tokens))
    except (ValueError, SyntaxError, tokenize.TokenError, IndentationError):
        return value


@dataclass(frozen=True)
class AppConfig:
    """Resolved application root and shared settings."""

    home: Path
    values: Mapping[str, Any]

    def directory(self, name: str, default: str) -> Path:
        """Resolve a configured runtime directory from the application root."""
        value = self.values.get(name, default)
        if not isinstance(value, str) or not value.strip():
            raise ConfigurationError(f"PDFWTF_{name.upper()} must be a directory path.")
        path = Path(value)
        if not path.is_absolute():
            path = self.home / path
        path = path.resolve()
        if path.exists() and not path.is_dir():
            raise ConfigurationError(f"PDFWTF_{name.upper()} must be a directory path.")
        return path

    @property
    def output_dir(self) -> Path:
        return self.directory("output_dir", "instance/_data/out")

    @property
    def temp_dir(self) -> Path:
        return self.directory("temp_dir", "instance/temp")

    @property
    def logs_dir(self) -> Path:
        return self.directory("logs_dir", "instance/logs")


def load_config() -> AppConfig:
    """Load INI, optional dotenv, and environment values without global mutation."""
    raw_home = os.environ.get("PDFWTF_HOME", "").strip()
    if not raw_home:
        raise ConfigurationError("PDFWTF_HOME is required.")
    home = Path(raw_home)
    if not home.is_absolute() or not home.is_dir():
        raise ConfigurationError("PDFWTF_HOME must be an existing absolute directory.")
    home = home.resolve()
    parser = configparser.ConfigParser(interpolation=None)
    try:
        with (home / "instance/conf/pdf-wtf.ini").open(encoding="utf-8-sig") as stream:
            parser.read_file(stream)
        if not parser.has_section("pdf-wtf"):
            raise ConfigurationError("The configuration requires a [pdf-wtf] section.")
        values = {
            key.removeprefix("pdfwtf_"): parse_value(value)
            for key, value in parser.items("pdf-wtf")
        }
        # Ignore a dotenv HOME: only the inherited environment can select the root.
        dotenv = dotenv_values(home / ".env", encoding="utf-8", interpolate=False)
    except (OSError, UnicodeError, configparser.Error):
        raise ConfigurationError(
            "Cannot read valid application configuration."
        ) from None

    for source in (dotenv, os.environ):
        for key, value in source.items():
            if (
                key.startswith("PDFWTF_")
                and key != "PDFWTF_HOME"
                and value is not None
                and value.strip()
            ):
                values[key[7:].lower()] = parse_value(value)
    return AppConfig(home, values)
