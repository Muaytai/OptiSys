# AI Agent Rules

> These rules apply to ALL AI assistants working in this repository.
> They are written in English for precision. **Communication with the user is ALWAYS in Russian.**

## 1. Language (MANDATORY)
- All replies, questions, plans, summaries and code comments MUST be in **Russian**.
- Identifiers (variables, functions, classes), commit messages and technical terms stay in English.
- Switch language only if the user explicitly asks.
- **This includes ALL user-visible tool metadata**: command titles/summaries (`toolSummary`), subagent roles, task names, artifact summaries, question options, progress/status messages. Example: `toolSummary: "Проверка версии Python"`, NOT `"Python version check"`.
- The only English allowed in the UI is the literal command/code itself.

## 2. User Profile
- The user is an experienced developer: **Python (Django REST Framework, FastAPI) + React / Next.js (TypeScript)**.
- Communicate dev-to-dev. Skip basic explanations, tutorials and obvious caveats.
- When the stack is unspecified, default to this stack and its conventions.

## 3. Communication & Token Economy
- Be concise. No filler, no restating the request, no long intros/outros.
- Explain only what is non-obvious: decisions, trade-offs, risks.
- After making changes, report briefly: what changed, where (file links), what to check.
- **Sync over assumptions:** if a request is ambiguous, ask short targeted questions BEFORE coding.
  - Prefer multiple-choice options (2–4 variants with a recommended one marked).
  - Batch related questions into one message.
- For non-trivial tasks, propose a short plan (3–7 bullets) and wait for confirmation.

## 4. Tools
- Actively use all available tools: **MCP servers, skills, web search, documentation lookup**.
- Before implementing with an external library/API, verify current versions and APIs via search/docs — do not guess.
- Check for a relevant skill or MCP tool before improvising a workaround.
- Use subagents for broad research to keep the main context small.

## 5. File Integrity (STRICT)
- **Do NOT modify, rename or delete existing files** unless the user explicitly asks for that specific file.
- Work only in the requested files, or create new isolated files/modules.
- If a fix requires touching another file — stop, explain why, and ask for permission first.
- Preserve existing comments, docstrings and formatting unrelated to the change.
- Never mass-reformat or refactor beyond the scope of the task.

## 6. Code Quality
### Backend (Python)
- Type hints everywhere; Pydantic v2 for FastAPI schemas; DRF serializers/viewsets per conventions.
- Follow PEP 8; keep business logic out of views (services/selectors layer).
- Async where appropriate in FastAPI; avoid blocking calls in async code.
### Frontend
- TypeScript with strict types; functional components and hooks.
- Next.js: follow App Router conventions unless the project uses Pages Router.
### General
- Clean, readable, maintainable code; small focused functions.
- Handle errors explicitly; no silent `except`/`catch`.
- Never hardcode secrets — use environment variables.

## 7. Commands & Environment (Windows)
- OS is **Windows / PowerShell**. Use compatible commands and paths.
- **Run routine terminal commands immediately without asking for confirmation** (diagnostics, file checks, running project scripts/tests, building the project, creating `venv`, installing project dependencies).
- **Ask for confirmation ONLY before serious actions** that affect system security, OS stability, or irreversible data loss (e.g., changing boot/BIOS/registry/services, deleting non-temp data, global OS-level installs/uninstalls).
- Avoid destructive or unnecessary commands (`rm -rf`, force pushes, registry edits).
- Prefer virtual environments (`venv`) and project-local dependencies.

## 8. Autonomous Subagent Pipeline (Security, Validation & QA)
- Proactively spawn and chain autonomous subagents (`define_subagent` / `invoke_subagent`) for non-trivial code changes:
  1. **Implementation** — write or update the requested code.
  2. **Validation & Static Checks** — verify types, schemas, edge cases and runtime imports.
  3. **Security Audit** — check for vulnerabilities, unsafe inputs, privilege/permission issues and secret leaks.
  4. **Testing & Auto-Fix Loop** — run tests/smoke checks, catch bugs, and **fix them immediately** before reporting back.
- Keep the loop autonomous: resolve discovered bugs and test failures internally without bothering the user.
- **Involve the user ONLY when necessary:** ambiguous product requirements, architectural trade-offs, permission to touch unrequested existing files (Rule 5), or high-impact system actions (Rule 7).
