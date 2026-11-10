# AI AGENT INSTRUCTIONS
Read and strictly adhere.

## Maintaining This File
1. Must be written entirely in clear, high-quality English.
2. Keep instructions brief, elegant, and directly actionable. Add verified project-specific rules and non-obvious constraints in scoped instructions; avoid duplicating discoverable documentation.
3. Must not exceed 100 lines. If this file exceeds 6000 characters (checked when editing AGENTS.md), ask the user if they want to compress it without losing any meaning or details.
4. Propose `AGENTS.md` updates if users repeatedly ask for specific behaviors.

## Agent Behavior
- Strict Adherence: Follow prompts exactly. Do not invent tasks or behavior. Ask clarifying questions only when ambiguity materially affects correctness, compatibility, or safety. Otherwise, use reasonable assumptions consistent with the codebase.
- Verify & Thoroughness: Verify facts in code, double-check affected code, and do not leave work incomplete. When renaming or removing symbols, check affected references using available search tools. Remove obsolete code introduced or superseded by the change.
- User Edits: Do not discard user changes, rewrite Git history, push, deploy, publish, or perform irreversible data operations without authorization.
- Complete Refactoring & Dead Code Removal: Remove superseded code within the affected scope, unless it is required for compatibility, migration, or an explicitly supported use case.
- Explicit Change Scope: Implement the requested outcome and its necessary supporting changes. Avoid unrelated features, refactoring, and cleanup. Ask before implementing proposed changes. Answer questions without modifying code; never infer implementation permission from an explanatory question.
- Avoid Premature Abstraction: Keep one-off code local unless extraction is justified by reuse, correctness, maintainability, or an explicit user requirement.
- Documentation: Keep project documentation in `docs/README.md` (English) and `docs/README_RU.md` (Russian), with matching content and reciprocal translation links. Write concise, reader-facing documentation. Leave the temporary `docs/TZ.md` and `docs/ROADMAP.md` unchanged unless explicitly requested.
- New Libraries: Before adding a library, explain its purpose and why it was chosen, and obtain user approval.

## TDD and Tests
- For features and bug fixes, work in small red-green-refactor cycles: write one behavior test, run it and confirm the intended failure, implement the minimum change, then refactor with tests green. Record the commands and outcomes; a collection or environment error is not a valid red step.
- Test public behavior at established boundaries. Clarify new boundaries only when ambiguity affects the contract. Use independent expected results, relevant edge cases, and regression tests; avoid tests coupled to private implementation.
- Use pytest and pytest-asyncio as configured in `pyproject.toml`. Group tests by level (`unit`, `integration`, `e2e`), then by feature as needed; create directories when tests exist. Use `test_*.py` files, descriptive `test_<behavior>` names and typed functions. Async tests and fixtures use auto mode.
- Keep fixtures local until shared; put shared fixtures in the nearest `conftest.py`. Prefer function scope, context managers or yield cleanup, built-in fixtures, and parametrization for equivalent cases. Mock external boundaries rather than internal collaborators; keep default tests independent of credentials and external services.
- For documentation or tooling changes without runtime behavior, use the relevant checks instead of artificial failing tests. Document any reason a red step cannot be reproduced.
- Keep runnable examples of implemented public features in `examples/`, with uv commands in both READMEs. Verify offline examples as subprocesses; document credentials and services required by any live example.

## Mandatory Workflow
Apply this workflow after every logical change to code or files that affect its behavior, dependencies, checks, or packaging. Repeat affected checks after further edits.

1. Inspect the diff and affected references. Confirm the requested behavior and preserve existing user changes.
2. Update relevant tests when behavior changes. Keep both README translations consistent with the implementation and validate their links and commands. For documentation-only changes, check content and links; run packaging checks if the package README changes.
3. When dependencies change, regenerate `uv.lock` with `uv lock` and synchronize the environment with `uv sync --locked`. Otherwise, preserve the lockfile and use `--locked` for checks.
4. Run `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, `uv run --locked pyright`, and `uv run --locked pytest`. Correct issues introduced by the change without weakening checks; distinguish unrelated existing failures.
5. When packaging, metadata, dependencies, or the package README change, run `uv build` and inspect the resulting wheel and source archive for the affected metadata and files. Verify installation in a clean environment when installation behavior changes.
6. Review the final diff, run `git diff --check`, and ensure the patch contains only intended changes. Report the result, checks actually run, and any failures or unverified behavior; claim completion only when the evidence supports it.
