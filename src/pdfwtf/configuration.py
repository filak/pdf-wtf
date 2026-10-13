"""Shared configuration defined by specs/CONFIGURATION.md."""

import ast
import configparser
import importlib
import io
import json
import os
import tokenize
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Set

from dotenv import dotenv_values

# Extensible validation constants
ALLOWED_CONTEXTS: Set[str] = {"export"}
ALLOWED_DOCUMENT_TYPES: Set[str] = {"unit"}
ALLOWED_PIPELINES: Set[str] = {"export_html_flag"}


class ConfigurationError(ValueError):
    """Configuration is missing or invalid."""


@dataclass(frozen=True)
class LLMTaskConfig:
    """Validated configuration for a single LLM task."""

    name: str
    context: str
    description: Dict[str, str]
    document_types: List[str]
    pipeline: str
    template: str
    prompt: str
    system: str

    @classmethod
    def from_dict(cls, name: str, data: Dict[str, Any]) -> "LLMTaskConfig":
        """Validate raw task dict and load template attributes."""
        if not isinstance(data, dict):
            raise ConfigurationError(f"LLM task '{name}' must be a dictionary.")

        # 1. Validate 'context'
        context = data.get("context")
        if not isinstance(context, str) or context not in ALLOWED_CONTEXTS:
            raise ConfigurationError(
                f"LLM task '{name}': invalid context '{context}'. "
                f"Allowed values: {sorted(ALLOWED_CONTEXTS)}"
            )

        # 2. Validate 'description'
        description = data.get("description")
        if not isinstance(description, dict) or not description:
            raise ConfigurationError(
                f"LLM task '{name}': 'description' must be a non-empty dictionary mapping language codes to strings."
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

        # 3. Validate 'document_types'
        doc_types = data.get("document_types")
        if not isinstance(doc_types, list) or not doc_types:
            raise ConfigurationError(
                f"LLM task '{name}': 'document_types' must be a non-empty list."
            )
        invalid_types = set(doc_types) - ALLOWED_DOCUMENT_TYPES
        if invalid_types:
            raise ConfigurationError(
                f"LLM task '{name}': unsupported document_types {sorted(invalid_types)}. "
                f"Allowed values: {sorted(ALLOWED_DOCUMENT_TYPES)}"
            )

        # 4. Validate 'pipeline'
        pipeline = data.get("pipeline")
        if not isinstance(pipeline, str) or pipeline not in ALLOWED_PIPELINES:
            raise ConfigurationError(
                f"LLM task '{name}': invalid pipeline '{pipeline}'. "
                f"Allowed values: {sorted(ALLOWED_PIPELINES)}"
            )

        # 5. Validate 'template' & load prompt/system strings from module
        template_name = data.get("template")
        if not isinstance(template_name, str) or not template_name.strip():
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
            context=context,
            description=description,
            document_types=doc_types,
            pipeline=pipeline,
            template=template_name,
            prompt=prompt.strip(),
            system=system.strip(),
        )


def validate_llm_tasks(raw_tasks: Any) -> Dict[str, LLMTaskConfig]:
    """Validate raw LLM_TASKS configuration map and return strongly-typed tasks."""
    if not isinstance(raw_tasks, dict):
        raise ConfigurationError("PDFWTF_LLM_TASKS must be a valid JSON dictionary.")

    validated_tasks: Dict[str, LLMTaskConfig] = {}
    for task_name, task_data in raw_tasks.items():
        validated_tasks[task_name] = LLMTaskConfig.from_dict(task_name, task_data)

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
