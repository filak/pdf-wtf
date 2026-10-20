# pdf-wtf configuration specification

## Purpose

This document defines shared application configuration, configuration-source
precedence, validation, secret handling, and instance directories. Component
specifications define component-specific settings.

## Application root and configuration sources

- `PDFWTF_HOME` is the mandatory application-root environment variable.
- python-dotenv loads `PDFWTF_HOME/.env`.
- `instance/conf/pdf-wtf.ini` contains shared non-secret INI
  configuration.
- `PDFWTF_*` environment variables contain secrets and machine-specific
  overrides.

The INI file must use UTF-8 encoding. Configuration parsing disables INI
interpolation. Values can use Python-style scalars and collections. Parsing is
case-insensitive for `true`, `false`, and `null`. Values that are not valid
literals remain strings.

Relative configured paths resolve from `PDFWTF_HOME`. Environment variables take
precedence over values loaded from `.env`. Values loaded from `.env` take
precedence over values in `instance/conf/pdf-wtf.ini`.

All application, background-processing, administration, and packaged
executable processes use the same `PDFWTF_HOME` value. The repository root is
`PDFWTF_HOME` during development. The deployment root is `PDFWTF_HOME` in
production. The shared instance directory is `PDFWTF_HOME/instance`.

The operating system supplies `PDFWTF_HOME`. The application uses it to locate
`PDFWTF_HOME/.env`, so `.env` cannot supply `PDFWTF_HOME`. Loading `.env` does not
replace values already present in the process environment.

Relative instance paths resolve from `PDFWTF_HOME`. Relative configuration-file
paths resolve from the selected instance directory. Restart the affected
process after a configuration change.

## Development environment setup

Set `PDFWTF_HOME` in the operating-system environment before starting a process.
Use the absolute repository path during development.
Do not put this value in `.env`.

On Windows, open CMD in the repository root:

```cmd
setx PDFWTF_HOME "%CD%"
```

This command sets the value for future processes.
Restart the terminal or VS Code after changing the value.

On Linux, open Bash in the repository root.
Set the value for the current terminal and its child processes:

```bash
export PDFWTF_HOME="$(pwd -P)"
```

For future interactive Bash terminals, add this line to `~/.bashrc`.
Replace the example with the absolute repository path:

```bash
export PDFWTF_HOME="/home/your-user/pdf-wtf"
```

Open a new terminal after saving `~/.bashrc`.
Check the value:

```bash
printf '%s\n' "$PDFWTF_HOME"
```

Check that the configuration file exists:

```bash
test -f "$PDFWTF_HOME/instance/conf/pdf-wtf.ini" && echo "Configuration file found."
```

To give VS Code the configured environment, close all VS Code windows.
Start VS Code from the configured terminal:

```bash
code .
```

## Validation

`PDFWTF_HOME` must be an existing absolute path. It must contain a valid
`instance/conf/pdf-wtf.ini` file. The `.env` file is optional.

An empty environment value is treated as unset. After all configuration
sources are loaded, each process validates only the settings it requires. A
process stops with a clear error if a required value is missing or invalid.
Errors must not expose secrets.

## Secrets

Secrets must come from environment variables, `PDFWTF_HOME/.env`, or another
approved secret store. Do not put secrets in
`instance/conf/pdf-wtf.ini`. Commit `.env.example` with safe placeholders.
Do not commit `.env`.

## Instance directories

- `instance/conf` contains committed non-secret configuration.
- `instance/resources` contains committed application resources.
- `instance/_data/in` contains runtime input awaiting processing.
- `instance/_data/out` contains runtime export output.
- `instance/cache` contains disposable file cache data.
- `instance/logs` contains native-host application logs.
- `instance/temp` contains disposable temporary files.

The application creates writable runtime directories when required. Runtime
data is not committed. The `instance` directory is part of the repository, but
credentials, tokens, private keys, and machine-specific secrets must not be
stored in it.

## Shared path settings

The shared INI section is `[pdf-wtf]`. The following settings define runtime
paths. Relative paths resolve from `PDFWTF_HOME`.

| INI setting | Environment override | Default |
| --- | --- | --- |
| `input_dir` | `PDFWTF_INPUT_DIR` | `instance/_data/in` |
| `output_dir` | `PDFWTF_OUTPUT_DIR` | `instance/_data/out` |
| `temp_dir` | `PDFWTF_TEMP_DIR` | `instance/temp` |
| `logs_dir` | `PDFWTF_LOGS_DIR` | `instance/logs` |

Configuration loading does not change the inherited process environment.
Empty environment values do not override dotenv or INI values. Console logging
uses standard error. Debug mode enables debug severity. It does not retain
temporary document files.

## LLM tasks

`LLM_TASKS` in the `[pdf-wtf]` INI section defines optional LLM tasks.
`PDFWTF_LLM_TASKS` can override it. The value is a dictionary of task names.
A task name uses lowercase letters, digits, hyphens, or underscores. Each task
must have exactly these keys:

```ini
LLM_TASKS = {
    "clean_html": {
        "description": {"en": "Clean HTML output", "cs": "Vyčistit HTML výstup"},
        "commands": ["export"],
        "stage": "after_unit_html",
        "provider": "gemini",
        "template": "wp_html",
        "output": {"suffix": ".clean.html"}
    }
    }
```

- `description` maps language codes to non-empty labels. It must contain `en`.
- `stage` must be a stage defined in code. The only stage is
  `after_unit_html`.
- `commands` is a non-empty list of unique actions that the stage supports.
  `after_unit_html` supports only `export`. Export is currently the only
  implemented LLM stage. `enhance` and `partition` accept `--llm-task`, but
  no task can run for them yet. A configuration that lists `enhance` for
  `after_unit_html` is invalid. Change it to `["export"]`.
- `provider` is the `llm-call` provider name. pdf-wtf passes it to
  `llm_call.PROVIDERS`. The only provider is `gemini`.
- `template` names a module in `src/pdfwtf/templates/llm/`. The module must
  define non-empty `prompt` and `system` strings.
- `output` contains only `suffix`. The suffix uses lowercase letters, digits,
  dots, and hyphens, starts with a dot, and has at most 32 characters. For
  `after_unit_html`, it must end with `.html` and must differ from `.html`.
  Two tasks cannot use the same suffix.

Configuration loading rejects unknown keys, including the former `context`,
`pipeline`, and `document_types` keys. Invalid `LLM_TASKS` stops the process
with a configuration error.

The Gemini API key is a secret. Do not put it in the INI file. Configuration
loading reads `GEMINI_API_KEY` from the process environment or from
`PDFWTF_HOME/.env`. pdf-wtf uses the loaded key only to check that a key
exists. It does not pass the key to `llm-call` and does not change the
process environment.

`llm-call` gets the key independently. It reads `GEMINI_API_KEY`, or
`GOOGLE_API_KEY` as a fallback, from the process environment only. It does
not read `.env`. Because of this, a key that exists only in `PDFWTF_HOME/.env`
does not make tasks available. Set `GEMINI_API_KEY` in the operating-system
environment. `GEMINI_MODEL` in the process environment can select the model.
Otherwise `llm-call` uses its default model.

`LLM_TIMEOUT` in the `[pdf-wtf]` INI section sets the timeout for one LLM
request in seconds. `PDFWTF_LLM_TIMEOUT` can override it. The value must be a
positive number. The default is `180`. pdf-wtf passes the value to `llm-call`.
When a request times out, the unit is recorded as failed and the export
continues with the next unit. An invalid value stops an export that selects
an LLM task before processing starts.

A task is available when `LLM_TASKS` is valid and `GEMINI_API_KEY` is set both
in loaded configuration and in the process environment. Without a key,
ordinary processing continues, and a selected task is rejected before
processing.

## GUI host configuration

The approved Compose container reads `PDFWTF_HOME` from the host environment.
It bind-mounts `PDFWTF_HOME/instance/_data` at `/app/instance/_data`.
Host and container processes use the same runtime data.
The container uses the writable `/tmp` tmpfs for temporary processing files.
Compose stops with an error if `PDFWTF_HOME` is missing.

The standalone GUI host stores each uploaded document under the default input
directory in `instance/_data/in/<job-id>`. It does not use the configured
`input_dir`. It stores the corresponding approved plan under the configured
`output_dir` in `<job-id>/approved.plan.json`. The host creates these
directories when required. Both directories are outside static assets.
The standalone demo adapter stores the completed machine partition at
`<output_dir>/<job-id>/source.partition.json`. It publishes this file before
reporting the job as complete. A write failure causes the job to fail.
An optional prepared PDF is stored next to the upload as
`instance/_data/in/<job-id>/prepared.pdf`, and its enhancement report as
`<output_dir>/<job-id>/enhance.json`.

The reusable blueprint reads only settings in the `PDFWTF_GUI_*` namespace.
`PDFWTF_GUI_PARTITION_ADAPTER` supplies the start, status, result, and controlled
document operations. `start(job_id, source, document_type, *, llm_tasks=())`
receives the active PDF. The blueprint resolves the active PDF itself and
does not use `document_path` for routing. `PDFWTF_GUI_ACCESS_CHECK` is an optional callable for host
access control. `PDFWTF_GUI_BASE_TEMPLATE` selects the host layout integration
point. `PDFWTF_GUI_INPUT_DIR` and `PDFWTF_GUI_OUTPUT_DIR` supply the host's
resolved input and output directories to the blueprint. They are Flask
integration settings, not environment overrides.

`PDFWTF_GUI_PREPARE_ADAPTER` is optional. It supplies
`start_prepare(job_id, source, destination, *, options, llm_tasks=())` and
`prepare_status(job_id)`. `options` contains `pdf_type`, `languages`, and
`remove_background_flag`. The adapter must publish `destination` atomically,
raise `LookupError` while another operation for the job is active, and raise
`ValueError` when the selected LLM tasks cannot run. `prepare_status` raises
`KeyError` for an unknown job. Without this setting, the GUI hides the
preparation controls and returns HTTP 503 for the preparation endpoint.
The access callback uses `prepare` and `prepare_status` actions.

`PDFWTF_GUI_ANALYZE_ADAPTER` is optional. It supplies
`start_analysis(job_id, source, *, llm_tasks=())` and
`analysis_status(job_id)`. `source` is the active PDF. The adapter must
publish the report at `<output_dir>/<job-id>/analysis.json` atomically,
raise `LookupError` while preparation is active, and raise `ValueError`
when the selected LLM tasks cannot run. `analysis_status` raises
`KeyError` for an unknown job. Without this setting, the GUI hides the
Analyze button and returns HTTP 503 for the analysis endpoint. The access
callback uses `analyze`, `analyze_status`, `analysis_summary`, and
`download_analysis_report` actions. The former
`PDFWTF_GUI_ANALYSIS_ADAPTER` key named the partition adapter. It is not
read. Use `PDFWTF_GUI_PARTITION_ADAPTER` for partitioning.

The standalone host owns the secret key, Flask-Babel, CSRF protection, upload
limit, and the demo adapter for analysis, preparation, partitioning, and
export. The integrating apps must use its existing host extensions
and must supply its production access-control and processing integration. The
blueprint does not initialize global Flask extensions.

`PDFWTF_GUI_EXPORT_ADAPTER` supplies `start_export`, `export_status`, and
`export_path` operations. The standalone host uses its demo adapter for
preparation, partitioning, and export. A host that supplies a custom partition adapter must also
supply an export adapter to enable export processing. The access callback uses
`export`, `export_status`, and `download_export` actions for these operations.
Export endpoints require a valid source path within the configured input
directory. Export start requires CSRF protection. The `plan` argument of
`start_export` is the saved plan, or `None` when no plan is saved. With
`None`, the adapter exports the whole active PDF as one unit. A saved plan
for another PDF returns HTTP 409 before the adapter is called. Export download paths must remain within the output
directory.

The demo adapter snapshots the saved plan and validates it in the processing
worker before writing results. Plan saving and plan deletion return HTTP 409
while export is queued or running. Plan read, save, download, and delete paths
must remain within the configured output directory.
The demo adapter runs the shared export operation on the active PDF. It
does not run cleanup or OCR.
Each export uses an isolated results directory. It publishes
`<output_dir>/<job-id>/export.zip` after processing and ZIP creation succeed.
The archive contains unit results. With `no-pdf-out` off, it also contains
one `<unit-id>.pdf` for each included unit, using its selected source pages.
Unit results use the `_units` directory inside the GUI ZIP. The manifest
links each unit PDF with a `pdf` field. It always includes the complete
`upload.json` object under `upload`, immediately after `kind`. Missing or
invalid upload metadata causes export to fail. The archive excludes a
combined document PDF. The GUI-only `include-source` switch adds the active
PDF at the ZIP root, as `source.pdf` or `prepared.pdf`. It defaults to off and is independent
of `no-pdf-out`. The export adapter accepts `include_source=False` as an
optional keyword argument.
A successful rerun replaces this archive. Temporary export files are removed
after success or failure. Export status is held in memory and is not restored
after a host restart. The demo adapter is not a durable production queue.

New upload metadata includes a host-owned `created` timestamp.
Saved plans include host-owned `created` and `updated` timestamps.
Use ISO 8601 UTC values ending in `Z`. Preserve the original plan `created`
value on subsequent saves. Replace `updated` on each successful save.
Set both values on the first save or when saving an older plan without
`created`. Document-type updates preserve the upload `created` value.

The GUI-only `debug` export option defaults to off. When enabled, the adapter
copies `approved.plan.json` and `source.partition.json` to the ZIP root.
Copy the saved files without changing their bytes. With a saved plan, export
fails if either file is missing or escapes the job directory. Without a
saved plan, the ZIP contains only the generated one-unit plan. The adapter accepts
`debug=False` as an optional keyword argument. This option is independent
of the other export switches and does not enable diagnostic logging.

The GUI `get-meta` export switch defaults to off. When off, omit every
`*.metadata.json` file from the ZIP and set unit manifest `metadata` fields
to `null`. The manifest itself is always included. When on, include unit
metadata files and their manifest links. The adapter accepts `get_meta=False`
as an optional keyword argument. Other JSON files selected by `debug` are
independent of this option. CLI export metadata behavior does not change.

The standalone host passes its loaded `AppConfig` to the demo adapter with the
`config` keyword argument. The host does not copy the Gemini key into the Flask
configuration. The export adapter accepts `llm_tasks=()` as an optional keyword
argument. It raises `ValueError` when the selection cannot run, and the
blueprint then returns HTTP 400. The export start request accepts
`llm_tasks` as a JSON list of task names. An adapter can provide an optional
`llm_tasks(command="export")` operation that returns the selectable tasks for
one operation. An older `llm_tasks()` operation without an argument is used
for export only. Without it, the Export modal shows no LLM tasks. The demo
adapter passes the configuration and the selected task names to the shared
export operation. Preparation and partitioning adapters also accept
`llm_tasks=()` and validate it with their own action name before scheduling. When
a task fails, export status is `complete` and includes a localized `warning`.

## Application log files

The CLI appends operational records to `pdf-wtf-log.txt`.
The standalone GUI appends operational records to `pdf-wtf-gui-log.txt`.
Both files use UTF-8 and the configured `logs_dir`.
The default directory is `PDFWTF_HOME/instance/logs`.
Console logging remains enabled. CLI `--debug` enables debug severity.
The GUI logs request methods, endpoint names, status codes, and client addresses.
It does not log request query strings or bodies.
The demo container mounts `instance/logs` from the host.
Log files are not rotated automatically.
