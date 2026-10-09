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

## GUI host configuration

The standalone GUI host stores each uploaded document under the default input
directory in `instance/_data/in/<job-id>`. It does not use the configured
`input_dir`. It stores the corresponding approved plan under the configured
`output_dir` in `<job-id>/approved-plan.json`. The host creates these
directories when required. Both directories are outside static assets.

The reusable blueprint reads only settings in the `PDFWTF_GUI_*` namespace.
`PDFWTF_GUI_ANALYSIS_ADAPTER` supplies the start, status, result, and controlled
document operations. `PDFWTF_GUI_ACCESS_CHECK` is an optional callable for host
access control. `PDFWTF_GUI_BASE_TEMPLATE` selects the host layout integration
point. `PDFWTF_GUI_INPUT_DIR` and `PDFWTF_GUI_OUTPUT_DIR` supply the host's
resolved input and output directories to the blueprint. They are Flask
integration settings, not environment overrides.

The standalone host owns the secret key, Flask-Babel, CSRF protection, upload
limit, and the demo analysis adapter. BMF must use its existing host extensions
and must supply its production access-control and processing integration. The
blueprint does not initialize global Flask extensions.
