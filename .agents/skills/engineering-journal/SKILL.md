---
name: engineering-journal
description: Maintain engineering decision and execution-flow documentation when modifying, refactoring, debugging, or designing application code.
---

# Engineering Journal

Use this skill whenever application code is modified.

The purpose is to preserve two forms of engineering knowledge:

1. Why the system was designed or changed in a particular way.
2. How the system actually executes.

## decisions.md

Target file:

`docs/decisions.md`

If the file does not exist, create it.

Do not rewrite existing decision history.

Add a new decision only for meaningful engineering choices.

Use this format:

## DEC-YYYY-MM-DD-NN — Short decision title

**Status:** Accepted / Superseded / Experimental

**Context**
Explain the problem or constraint requiring a decision.

**Decision**
State what was chosen.

**Why**
Give concise technical reasons for choosing it.

**Alternatives considered**
List important realistic alternatives and why they were not selected.

**Trade-offs**
Document disadvantages, risks, or consequences.

**Affected code**
- file
- module
- class/function

**Verification**
Describe tests, commands, evidence, or behavior used to validate the decision.

---

Do not log trivial edits such as formatting, variable renaming, typo fixes,
or mechanical changes unless they affect architecture or behavior.

Do not expose private chain-of-thought.
Record engineering conclusions and concise rationale only.

# flow.md

Target file:

`docs/flow.md`

If the file does not exist, inspect the repository and establish an initial
execution-flow document from the actual implementation.

Never invent a call relationship.

Prefer concrete identifiers:

`module.py::function_name()`

rather than vague descriptions.

Maintain these sections:

# System Execution Flow

## Entry Points

Document how execution begins.

Example:

CLI / API / UI
    ↓
module_a.py::entry()
    ↓
module_b.py::process()
    ↓
module_c.py::retrieve()
    ↓
module_d.py::generate()

## Main Runtime Flows

For each important use case document:

1. entry point
2. function call
3. next module
4. transformation
5. storage/network/model call
6. returned result

## Module Responsibilities

Describe the role of important modules briefly.

## Active Modification Path

For the current task document:

**Task:**
<current task>

**Execution path affected:**

entry
→ function
→ function
→ changed function
→ downstream function
→ output

**Files/functions being modified:**
- path::function

**Reason this part of the flow is changing:**
<concise explanation>

## Flow Change History

Record significant execution-flow changes with their related decision ID when available.

# Workflow

Before modifying code:

1. Read relevant parts of `docs/decisions.md`.
2. Read relevant parts of `docs/flow.md`.
3. Trace the actual affected execution path in source code.
4. Update the Active Modification Path.

While implementing:

5. Record meaningful architectural/technical decisions.
6. Keep the documented flow aligned with what is actually implemented.

After implementing:

7. Re-read the final changed code.
8. Update the canonical runtime flow if behavior changed.
9. Update the decision log.
10. Ensure Active Modification Path describes the final implementation.
11. Run appropriate verification/tests.