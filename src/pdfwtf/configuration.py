"""Shared configuration defined by specs/CONFIGURATION.md."""

import ast
import configparser
import importlib
import io
import json
import os
import re
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping

from dotenv import dotenv_values

# Command identities. Every command accepts LLM task selection.
COMMAND_ANALYZE = "analyze"
COMMAND_ENHANCE = "enhance"
COMMAND_PARTITION = "partition"
COMMAND_EXPORT = "export"
COMMANDS: tuple[str, ...] = (
    COMMAND_ANALYZE,
    COMMAND_ENHANCE,
    COMMAND_PARTITION,
    COMMAND_EXPORT,
)
# LLM task stages are defined in code. Each stage lists its supported commands.
# Export is the only command with an implemented stage. Future stages can add
# enhancement (for example OCR assistance) or partitioning support.
LLM_STAGES: Dict[str, frozenset[str]] = {
    "after_unit_html": frozenset({COMMAND_EXPORT}),
}
# Output suffixes that each stage requires.
LLM_STAGE_SUFFIXES: Dict[str, str] = {"after_unit_html": ".html"}
# Seconds for one LLM request.
DEFAULT_LLM_TIMEOUT = 180
LLM_TASK_KEYS = frozenset(
    {"description", "commands", "stage", "provider", "template", "output"}
)
_TASK_NAME = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
_TEMPLATE_NAME = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_OUTPUT_SUFFIX = re.compile(r"\.[a-z0-9]+(?:[.-][a-z0-9]+)*\Z")
_RESERVED_SUFFIXES = (".metadata.json", ".pdf")


class ConfigurationError(ValueError):
    """Configuration is missing or invalid."""


@dataclass(frozen=True)
class LLMTaskConfig:
    """Validated configuration for a single LLM task."""

    name: str
    description: Dict[str, str]
    commands: tuple[str, ...]
    stage: str
    provider: str
    template: str
    output_suffix: str
    prompt: str
    system: str

    def label(self, language: str | None) -> str:
        """Return the description for a language, with English as fallback."""
        return self.description.get(language or "en") or self.description["en"]

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "LLMTaskConfig":
        """Validate raw task dict and load template attributes."""
        if not isinstance(name, str) or not _TASK_NAME.match(name):
            raise ConfigurationError(
                f"LLM task name '{name}' must use lowercase letters, digits, "
                "hyphens, or underscores."
            )
        if not isinstance(data, dict):
            raise ConfigurationError(f"LLM task '{name}' must be a dictionary.")
        unknown = set(data) - LLM_TASK_KEYS
        missing = LLM_TASK_KEYS - set(data)
        if unknown or missing:
            raise ConfigurationError(
                f"LLM task '{name}' must define exactly these keys: "
                f"{sorted(LLM_TASK_KEYS)}. Unknown: {sorted(unknown)}. "
                f"Missing: {sorted(missing)}."
            )

        # 1. Validate 'description'
        description = data["description"]
        if not isinstance(description, dict) or not description:
            raise ConfigurationError(
                f"LLM task '{name}': 'description' must be a non-empty dictionary "
                "mapping language codes to strings."
            )
        for lang, text in description.items():
            if (
                not isinstance(lang, str)
                or not isinstance(text, str)
                or not text.strip()
            ):
                raise ConfigurationError(
                    f"LLM task '{name}': invalid description entry for '{lang}'."
                )
        if "en" not in description:
            raise ConfigurationError(
                f"LLM task '{name}': 'description' must contain an 'en' entry."
            )

        # 2. Validate 'stage'
        stage = data["stage"]
        if not isinstance(stage, str) or stage not in LLM_STAGES:
            raise ConfigurationError(
                f"LLM task '{name}': invalid stage '{stage}'. "
                f"Allowed values: {sorted(LLM_STAGES)}"
            )

        # 3. Validate 'commands' against the stage
        commands = data["commands"]
        if (
            not isinstance(commands, list)
            or not commands
            or not all(isinstance(command, str) for command in commands)
            or len(set(commands)) != len(commands)
        ):
            raise ConfigurationError(
                f"LLM task '{name}': 'commands' must be a non-empty list of "
                "unique command names."
            )
        unsupported = set(commands) - LLM_STAGES[stage]
        if unsupported:
            raise ConfigurationError(
                f"LLM task '{name}': stage '{stage}' does not support commands "
                f"{sorted(unsupported)}. Allowed values: {sorted(LLM_STAGES[stage])}"
            )

        # 4. Validate 'provider' against the llm-call provider names
        from llm_call import PROVIDERS

        provider = data["provider"]
        if not isinstance(provider, str) or provider not in PROVIDERS:
            raise ConfigurationError(
                f"LLM task '{name}': unknown provider '{provider}'. "
                f"Allowed values: {sorted(PROVIDERS)}"
            )

        # 5. Validate 'output'
        output = data["output"]
        if not isinstance(output, dict) or set(output) != {"suffix"}:
            raise ConfigurationError(
                f"LLM task '{name}': 'output' must be a dictionary with only 'suffix'."
            )
        suffix = output["suffix"]
        required_end = LLM_STAGE_SUFFIXES[stage]
        if (
            not isinstance(suffix, str)
            or len(suffix) > 32
            or not _OUTPUT_SUFFIX.match(suffix)
            or not suffix.endswith(required_end)
            or suffix == required_end
            or suffix.endswith(_RESERVED_SUFFIXES)
        ):
            raise ConfigurationError(
                f"LLM task '{name}': unsafe output suffix '{suffix}'. Use a "
                f"lowercase suffix such as '.clean{required_end}' that ends with "
                f"'{required_end}' and differs from '{required_end}'."
            )

        # 6. Validate 'template' & load prompt/system strings from module
        template_name = data["template"]
        if not isinstance(template_name, str) or not _TEMPLATE_NAME.match(
            template_name
        ):
            raise ConfigurationError(
                f"LLM task '{name}': 'template' must be a valid module name string."
            )

        module_path = f"pdfwtf.templates.llm.{template_name}"
        try:
            module = importlib.import_module(module_path)
        except ImportError as err:
            raise ConfigurationError(
                f"LLM task '{name}': template '{template_name}' not found at 'src/pdfwtf/templates/llm/{template_name}.py'."
            ) from err

        prompt = getattr(module, "prompt", None)
        system = getattr(module, "system", None)

        if not isinstance(prompt, str) or not prompt.strip():
            raise ConfigurationError(
                f"Template '{template_name}' in task '{name}' must define a non-empty string 'prompt'."
            )
        if not isinstance(system, str) or not system.strip():
            raise ConfigurationError(
                f"Template '{template_name}' in task '{name}' must define a non-empty string 'system'."
            )

        return cls(
            name=name,
            description=dict(description),
            commands=tuple(commands),
            stage=stage,
            provider=provider,
            template=template_name,
            output_suffix=suffix,
            prompt=prompt.strip(),
            system=system.strip(),
        )


def validate_llm_tasks(raw_tasks: Any) -> Dict[str, LLMTaskConfig]:
    """Validate raw LLM_TASKS configuration map and return strongly-typed tasks."""
    if not isinstance(raw_tasks, dict):
        raise ConfigurationError("PDFWTF_LLM_TASKS must be a valid JSON dictionary.")

    validated_tasks: Dict[str, LLMTaskConfig] = {}
    suffixes: Dict[str, str] = {}
    for task_name, task_data in raw_tasks.items():
        task = LLMTaskConfig.from_dict(task_name, task_data)
        if task.output_suffix in suffixes:
            raise ConfigurationError(
                f"LLM tasks '{suffixes[task.output_suffix]}' and '{task_name}' "
                f"use the same output suffix '{task.output_suffix}'."
            )
        suffixes[task.output_suffix] = task_name
        validated_tasks[task_name] = task

    return validated_tasks


def parse_value(value: str) -> Any:
    """Parse JSON structures, case-insensitive literals, safe Python literals, or raw text."""
    value = value.strip()
    if not value:
        return value

    # 1. Try standard JSON first
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        pass

    # 2. Fall back to ast.literal_eval with case-insensitive token mapping
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
        # 3. Plain string fallback
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
    def input_dir(self) -> Path:
        return self.directory("input_dir", "instance/_data/in")

    @property
    def temp_dir(self) -> Path:
        return self.directory("temp_dir", "instance/temp")

    @property
    def logs_dir(self) -> Path:
        return self.directory("logs_dir", "instance/logs")

    @property
    def llm_tasks(self) -> Dict[str, LLMTaskConfig]:
        """Get validated LLM task configurations."""
        tasks = self.values.get("llm_tasks", {})
        if isinstance(tasks, dict) and not all(
            isinstance(v, LLMTaskConfig) for v in tasks.values()
        ):
            return validate_llm_tasks(tasks)
        return tasks

    @property
    def llm_timeout(self) -> float:
        """Get the timeout in seconds for one LLM request."""
        value = self.values.get("llm_timeout", DEFAULT_LLM_TIMEOUT)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
            raise ConfigurationError(
                "PDFWTF_LLM_TIMEOUT must be a positive number of seconds."
            )
        return float(value)

    @property
    def gemini_api_key_loaded(self) -> bool:
        """Report whether configuration loading found a Gemini API key."""
        value = self.values.get("gemini_api_key")
        return isinstance(value, str) and bool(value.strip())

    @property
    def gemini_api_key_present(self) -> bool:
        """Report whether llm-call can find the key in the process environment.

        llm-call reads only ``os.environ``. A key only in ``.env`` is not enough.
        The key value is never returned.
        """
        return self.gemini_api_key_loaded and bool(
            os.environ.get("GEMINI_API_KEY", "").strip()
        )


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
        ini_path = home / "instance/conf/pdf-wtf.ini"
        with ini_path.open(encoding="utf-8-sig") as stream:
            parser.read_file(stream)
        if not parser.has_section("pdf-wtf"):
            raise ConfigurationError("The configuration requires a [pdf-wtf] section.")

        values = {
            key.lower().removeprefix("pdfwtf_"): parse_value(value)
            for key, value in parser.items("pdf-wtf")
        }

        dotenv = dotenv_values(home / ".env", encoding="utf-8", interpolate=False)
    except (OSError, UnicodeError, configparser.Error) as err:
        raise ConfigurationError(
            f"Cannot read valid application configuration from '{ini_path}': {err}"
        ) from err

    # Environment variables and .env overrides
    for source in (dotenv, os.environ):
        for key, value in source.items():
            if key == "GEMINI_API_KEY" and value is not None and value.strip():
                values["gemini_api_key"] = value.strip()
                continue
            if (
                key.upper().startswith("PDFWTF_")
                and key.upper() != "PDFWTF_HOME"
                and value is not None
                and value.strip()
            ):
                clean_key = key[7:].lower()
                values[clean_key] = parse_value(value)

    # Validate LLM tasks upfront if present
    if "llm_tasks" in values:
        values["llm_tasks"] = validate_llm_tasks(values["llm_tasks"])

    return AppConfig(home, values)
