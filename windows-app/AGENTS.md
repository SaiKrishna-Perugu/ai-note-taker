# Repository Guidelines

## Project Structure & Module Organization

- `src/ai_note_taker/__init__.py` contains `main()`, the entry point for the `ai-note-taker` command. It currently prints a greeting; place reusable application code in this package.
- `gemini.py` is a standalone LangChain/LangGraph prototype that calls Gemini and exposes a sample weather tool.
- `pyproject.toml` defines package metadata, dependencies, the console command, and the `uv_build` backend. `uv.lock` records dependency resolution; `.python-version` selects Python 3.13.
- `README.md` is currently empty. No test or asset directories exist yet.

## Build, Test, and Development Commands

Run commands from the repository root with Python 3.13 or newer and uv installed:

- `uv sync`: create or update `.venv` from the project configuration and lockfile.
- `uv run ai-note-taker`: run the packaged console entry point.
- `uv build`: create source and wheel distributions in `dist/`.
- `uv run gemini.py`: run the prototype after declaring its dependencies and configuring `GEMINI_API_KEY`.

The dependency list is currently empty, although `gemini.py` imports `langchain-core`, `langchain-google-genai`, `langgraph`, and `typing-extensions`. Declare required dependencies with `uv add` and commit the updated manifest and lockfile together.

## Coding Style & Naming Conventions

Use four-space indentation, `snake_case` for functions and modules, and `PascalCase` for classes. Follow the existing type-annotation style and add annotations to new public functions. Keep reusable logic in `src/ai_note_taker/`. No formatter or linter is configured; keep formatting consistent with nearby code.

## Testing Guidelines

No test framework, test command, or coverage threshold is configured. For new automated tests, use a `tests/` directory and `test_*.py` filenames, and document the chosen runner. Mock Gemini calls so routine tests require neither credentials nor network access. Smoke-check package changes with `uv run ai-note-taker`.

## Commit & Pull Request Guidelines

There are no commits yet, so no established message convention exists. Use concise imperative subjects, such as `Add transcript parser`. Pull requests should explain the change, reference relevant issues, and list validation performed. Include sample output for CLI changes.

## Security & Configuration

Read `GEMINI_API_KEY` from the environment. The prototype does not load `.env` automatically. A local `.env` exists but is not ignored; do not commit it or credentials, and add an ignore rule before staging configuration files.
