# Resource Steward

**Language:** English · [العربية](Resource-Steward/docs/guides/README.ar.md) · [Français](Resource-Steward/docs/guides/README.fr.md) · [Español](Resource-Steward/docs/guides/README.es.md)

Resource Steward is a lightweight Python utility for monitoring resource pressure in Linux-based Arena-like sessions, checking conditions before expensive tasks, supervising commands it launches, and organizing reviews of large files.

> **Scope and safety:** This is an independent, unofficial tool—not a built-in Arena or Google Drive feature. It does not automatically upload or delete files. Review commands and permissions before running them.

## What it does

- Reads visible Linux cgroup v2 memory information, host available memory, and free disk space.
- Estimates workspace file counts and sizes that may be included in a session snapshot.
- Runs preflight checks and reports resource pressure or recovery.
- Can supervise a child command and stop its process group when configured limits are reached or monitoring fails.
- Maintains a local SQLite queue for reviewing large files.
- Can verify the size and MD5 checksum of an existing Google Drive file when configured with a read token.
- Includes a sample demonstrating application-level checkpoints and resumption.

## Requirements

- Linux with `/proc` and cgroup v2.
- Python 3.10 or newer.
- A POSIX-compatible shell and Git.
- No third-party Python packages are required for the core utility.

The project targets Linux/Arena-like environments; it is not documented as a general-purpose Android/Termux, macOS, or Windows utility.

## Getting started

Clone the repository:

```bash
git clone https://github.com/supermorkat/Resource-Steward.git
cd Resource-Steward
```

Inspect the available commands and options before use:

```bash
python3 --version
python3 -m unittest discover -s resource-steward/tests -v
```

Run commands from the project directory using the repository's documented entry point and options. Check the source/help output for the exact CLI syntax for your checked-out version; do not assume that monitoring alone guarantees a task can be safely stopped or resumed.

## Important operational notes

- Resource measurements are limited to what the current environment exposes; they are estimates, not guarantees.
- A supervised command may be terminated when configured thresholds are reached or monitoring becomes unavailable. Use this only for commands you are prepared to stop.
- Checkpoints are application-level: a task must explicitly save and restore its own progress.
- Google Drive verification checks metadata for a file that already exists; it does not perform the upload.
- Keep access tokens out of source control and logs. Use the environment-variable mechanism documented by the project.
- Review large-file queue entries before acting on them; do not treat a file as disposable just because it is large.

## Development and testing

Run the test suite from the repository root:

```bash
python3 -m unittest discover -s resource-steward/tests -v
```

Before contributing, review the current implementation, tests, and repository history. Update tests when changing resource thresholds, process supervision, persistence, or Drive verification.

## Project status

See the repository for current implementation details, issues, and changes: [supermorkat/Resource-Steward](https://github.com/supermorkat/Resource-Steward).

**License:** No license is selected in the documentation at the time this README was prepared. Do not assume the project is open source or that reuse, modification, or redistribution is permitted until a license is added.
