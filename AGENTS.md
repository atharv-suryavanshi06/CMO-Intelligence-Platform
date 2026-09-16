# Engineering Documentation Rules

For every task that modifies application code:

1. Use the `engineering-journal` skill.
2. Before modifying code, inspect the relevant existing execution path.
3. Maintain:
   - `docs/decisions.md`
   - `docs/flow.md`

If either document does not exist, create it.

If it already exists, update it rather than replacing its useful existing content.

## Decision documentation

Record meaningful engineering decisions, including:

- what was decided
- why the decision was necessary
- the chosen approach
- important alternatives considered
- why the chosen approach was preferred
- important trade-offs
- libraries/frameworks/algorithms selected and why
- affected modules/functions/files

Do not record private chain-of-thought or low-value implementation trivia.
Record concise engineering rationale that would be useful to another developer.

## Execution-flow documentation

`docs/flow.md` must reflect the actual codebase.

Before documenting a flow, inspect the implementation. Do not guess.

Document:

- entry points
- module-to-module flow
- function-to-function calls
- important data transformations
- external services/storage involved
- order of execution
- the exact execution path affected by the current change

For every code-changing task, identify the section of the execution path being modified.

If the implementation changes the execution flow, update the canonical flow accordingly.

## Before finishing a task

Before reporting completion:

1. Verify the code change.
2. Update `docs/decisions.md` when a meaningful engineering decision was made.
3. Update `docs/flow.md` when code or execution behavior was touched.
4. Ensure documentation matches the final implementation, not the original plan.

## Platform layout

- `apps/frontend/` contains the React/Vite client and is the only maintained frontend.
- `services/api/` contains the FastAPI launcher and API package.
- `packages/agents/`, `packages/web-search/`, `packages/rag-core/`, `packages/ingestion/`, and `packages/security/` contain the coordinated Python components.
- All component source roots contribute to the established `multimodal_rag.*` namespace package.
- Runtime artifacts live under `runtime-data/`; user-provided source material remains under `Data/`.
- The canonical virtual environment is `.venv`, and Python checks use `.venv\Scripts\python.exe`.
- Do not modify protected runtime artifacts, credentials, or user source files during source changes.
