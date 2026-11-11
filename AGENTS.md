
# AI Agent Instructions

## Maintaining This File
- Write instructions in clear, concise English.
- Keep this file under 100 lines and 6000 characters. Consolidate
  duplication before expanding it; ask before removing requirements.
- Put non-obvious, project-specific rules in the nearest applicable
  AGENTS.md. Do not duplicate discoverable documentation.
- Suggest updates for recurring user preferences; do not edit
  AGENTS.md unless requested.

## Agent Behavior
- Follow the user's request and established project conventions.
  Ask questions only when ambiguity materially affects correctness,
  compatibility, or safety; otherwise make reasonable assumptions.
- Answer questions and perform reviews without modifying files
  unless implementation was requested.
- Complete the requested task and necessary supporting changes.
  Avoid unrelated features, refactoring, and cleanup.
- Preserve user changes. Never rewrite Git history, push, deploy,
  publish, or perform destructive operations without authorization.
- Remove obsolete code within the affected scope after checking
  references. Preserve required compatibility and migration paths.
- Before adding third-party dependencies, explain the rationale
  and obtain approval.
- Verify version-sensitive behavior against installed APIs and
  official documentation when needed.
- Never expose secrets in source code, logs, tests, or output.

## Code Quality
- Keep `__init__.py` files limited to a package docstring, public
  re-exports, and `__all__`. Put implementation in focused modules.
- Separate distinct steps and loosely related groups of statements
  with a blank line. Keep tightly related statements together.
- Write simple, idiomatic, readable, maintainable Python.
- Follow existing conventions before introducing new patterns.
- Prefer direct solutions over speculative flexibility, layers,
  wrappers, configuration, and premature optimization.
- Introduce abstractions only for concrete benefits in clarity,
  reuse, testability, or architectural boundaries.
- Keep one-off logic local unless extraction improves the design.
- Prefer explicit control flow and descriptive naming.
- Write comments explaining non-obvious reasoning or constraints,
  not behavior already evident from the code.
- Validate untrusted inputs and handle realistic failures.
  Avoid redundant checks and unnecessary fallback paths.
- Do not silently suppress unexpected exceptions.
- In async code, avoid blocking the event loop and leaking tasks
  or resources; use appropriate cancellation and cleanup.
- Preserve public contracts and existing behavior unless changes
  are requested.

## TDD and Tests
- For features and bug fixes, use focused red-green-refactor cycles
  when practical. Confirm intended failures before fixing them;
  collection or environment errors do not count as valid red steps.
- Test observable behavior at established boundaries, including
  meaningful edge cases and regressions. Use independent expected
  results rather than reproducing implementation logic.
- Name the behavior under test and separate setup, action, and
  assertions with blank lines. Give parameterized cases explicit
  expectations and use fixtures for shared setup only.
- Use pytest and pytest-asyncio as configured in pyproject.toml.
  Async tests and fixtures use auto mode.
- Organize tests by level (unit, integration, e2e), then feature
  when useful. Use test_*.py files, descriptive test_* functions,
  and type annotations.
- Keep fixtures local until reuse justifies sharing them in the
  nearest conftest.py. Prefer function scope and reliable cleanup.
- Use parametrization for equivalent cases. Mock external
  boundaries; avoid implementation-coupled mocks.
- Keep default tests deterministic and independent of credentials
  and live external services.
- For changes without runtime behavior, use relevant checks
  instead of artificial failing tests.

## Documentation and Examples
- Maintain docs/README.md (English) and docs/README_RU.md (Russian).
  Keep them equivalent, with reciprocal translation links.
- Update both READMEs when public behavior, APIs, or commands change.
  Keep documentation concise, accurate, and reader-facing.
- Do not modify docs/TZ.md or docs/ROADMAP.md unless requested.
- Maintain runnable public-feature examples in examples/ with
  documented uv commands in both READMEs.
- Verify affected offline examples as subprocesses. Document
  credentials or services required for live examples.
  
## Verification Workflow
1. During development, run focused tests and relevant checks. Repeat affected checks after edits; avoid unnecessary full runs.
2. When dependencies change, run `uv lock` and `uv sync --locked`. Otherwise, preserve `uv.lock`.
3. Before completion, run the following checks:
   - `uv run --locked ruff check --fix .`
   - `uv run --locked ruff format .`
   - `uv run --locked pyright`
   - `uv run --locked pytest`
   Fix remaining issues introduced by the change without weakening checks or modifying unrelated code. Distinguish pre-existing failures.
4. For changes to packaging, metadata, dependencies, or the package README, run `uv build` and inspect the wheel and sdist. Verify clean installation when installation behavior changes.
5. Review the final diff and affected references; run `git diff --check`. Ensure only intended changes remain.
6. Report changes, checks performed, failures, and unverified behavior. Never claim unperformed checks passed.
