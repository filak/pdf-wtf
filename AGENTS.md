# pdf-wtf agent contributor instructions

## Purpose and document hierarchy

This file defines how contributors and coding agents change this repository. It is
the top-level project instruction file. The files in `specs` define the product
and its approved implementation constraints.

Use this top-down authority structure:

1. `AGENTS.md` defines the change process, repository policy, and quality rules.
2. `specs/PROJECT.md` defines product scope, domain concepts, and component
   boundaries.
3. `specs/CONFIGURATION.md` defines application configuration.
4. `specs/DEPLOYMENT.md` defines approved production deployment modes.

A specialized specification can add detail within its ownership boundary. It
must not override a higher-level document or a sibling specification. Report a
conflict before making a material change.

## Required context

Before you plan, review, or modify this project, read these files in order:

1. Read `specs/PROJECT.md` for the product purpose, scope, domain concepts,
   component responsibilities, development model, and repository policy.
2. Read `specs/CONFIGURATION.md` when the task concerns `PDFWTF_HOME`, configuration
   sources, secrets, validation.
3. Read `specs/DEPLOYMENT.md` when the task concerns production installation,
   Windows services, NSSM, WSL deployment, or Linux deployment.

## General rules

- Use only technologies approved by the specification that owns the affected
  component.
- Do not replace or extend the approved stack without explicit user approval.
- Do not add a production dependency without explicit user approval.
- Use versions defined in `pyproject.toml` and `uv.lock`.
- Preserve the product boundaries in `specs/PROJECT.md` and the runtime and
  deployment context in this file.
- Prefer a simple design to an unnecessary abstraction.
- Record durable architectural decisions in project documentation, not only in chat history.

## Command execution

- Use `uv run --locked <tool> ...` consistently for development tools.
- Execute each verification command as a separate tool invocation.
- Avoid combining tests, linting, Git inspection, and searches into one
  PowerShell script.
- Avoid explicit nested `pwsh.exe -Command` wrappers where the execution
  tool can run the command directly.
- Use the tool's working-directory parameter instead of prepending `cd`.
- Use `rg` for searches and `Get-Content` for reading files.
- Do not modify Codex rule files unless explicitly requested.
- When approval is needed, prefer an existing reusable command prefix
  over proposing approval for a whole script.

## Dependency management

- `uv`
- `uv_build`
- `pyproject.toml`
- `uv.lock`
- Use `uv` for dependency management and virtual environments.
- Define dependencies in `pyproject.toml`.
- Commit `uv.lock`.
- Do not use `pip` directly for normal project dependency management.
- Do not create or maintain `requirements.txt` unless deployment requires it.
- Keep development dependencies separate from production dependencies.

The approved shared supporting Python libraries are:

- Arrow;
- mmh3;
- PyUCA;
- tqdm.

Use the versions defined in `pyproject.toml` and `uv.lock`.

## Runtime and deployment context

- Use Python 3.12.
- Support Microsoft Windows for development.
- Support Microsoft Windows Server for native production deployment.
- Support container deployment on Docker-capable hosts, including Linux.
- Use Docker Desktop with the WSL 2 backend and Docker Compose for local
  development infrastructure.
- Run application and background-worker processes on the Windows host during
  initial development.
- Keep each development computer's Docker environment and data independent.
- Use a reverse proxy for public routing and TLS.
- Do not require Docker Desktop in production.

The approved production modes and their current status are defined in
`specs/DEPLOYMENT.md`. Backup, retention, recovery, and logging and monitoring
technologies are not selected.
## Logging context

- Use the Python standard `logging` package.
- Write operational logs to standard output and standard error.
- Native Windows deployments can also write log files in the directory defined
  by `specs/CONFIGURATION.md`.
- Let container infrastructure collect standard output and standard error.
- Include timestamps, severity, and process names.
- Include request or job correlation identifiers when available.
- Do not log credentials, session identifiers, uploaded document contents, or
  complete LLM payloads.

Console logging is always available. Debug mode can
add a separate debug log. Log records include component, client address, and
correlation identifier fields when available.

## Python conventions and quality checks

- Format Python with Black.
- Check Python with Flake8.
- Use `pytest` for automated testing.
- Use `pip-audit` for dependency vulnerability checks.
- Add type annotations to new public functions.
- Prefer `pathlib.Path` to `os.path`.
- Use application factories where practical.
- Keep route handlers small.
- Put business logic in service modules.
- Keep database access outside templates and presentation code.
- Preserve the background-processing boundary in `specs/PROJECT.md`.
- Use `tests/unit` for tests without external services.
- Use `tests/conftest.py` for shared pytest fixtures.

## Infrastructure change rules

- Do not add a Compose service or containerize an application component without explicit approval.
- Keep commands compatible with standard Windows shells where practical. Label shell-specific commands.
- Add health checks for services that need readiness detection.
- Validate Compose changes with `docker compose config`.
- Do not run `docker compose down --volumes` unless the user explicitly asks to delete local development data.

## Security rules

- Preserve the trust boundaries and data principles in `specs/PROJECT.md`.
- Apply the authorization rules in `specs/WORKFLOW.md`.
- Apply component-specific security requirements from the applicable
  specification.
- Never commit or disclose a secret.

## Technical writing

Use Simplified Technical English style for documentation, interface text, and operating instructions.

- Use short, direct sentences.
- Use active voice where possible.
- Give one instruction in each sentence.
- Give one topic in each paragraph.
- Use the same term for the same concept.
- Avoid idioms, slang, metaphors, and ambiguous pronouns.
- Put conditions before actions.
- Preserve exact identifiers, commands, API names, and error messages.
- Describe text as `STE-style` unless formal ASD-STE100 compliance was verified.

## Repository policy

- Commit source code, documentation, `pyproject.toml`, `uv.lock`, Compose configuration, and safe example configuration.
- Do not commit secrets, `.env`, local databases, Docker volumes, uploaded images, logs, build output, or production credentials.
- Keep development credentials separate from production credentials.
- Do not put real credentials in `compose.yaml` or committed files.
- Commit `.env.example` with safe placeholders.

## Verification

After an implementation change:

1. Format Python with Black.
2. Run Flake8.
3. Run the relevant automated tests when they exist.
4. Report checks that could not run.
5. Summarize changed files and important decisions.

## Change control

- Make the smallest change that completes the task.
- Preserve existing public interfaces unless the task requires a change.
- Do not resolve an open architectural decision without user approval.
- Do not create commits, tags, releases, deployments, or pushes unless requested.
- Never discard uncommitted user changes to complete a Git operation.

## PyInstaller specifications

- PyInstaller `.spec` files are local and are not committed.
- Generate or maintain them independently on each build machine.
- Do not remove the `*.spec` rule from `.gitignore`.

## Running project commands

`PDFWTF_HOME` is configured in the environment inherited by VS Code.
Use direct uv commands:

- uv run

Do not prepend PDFWTF_HOME assignments or wrap these commands in
PowerShell -Command, Measure-Command, or Out-Host.
If PDFWTF_HOME is missing, report it instead of setting it automatically.
