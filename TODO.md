# Paragents TODO Roadmap

This document contains the detailed engineering roadmap that was previously in `README.md`.

## P0: IM Integration and Recoverability

### 0) Telegram-first IM gateway
- Goal: trigger local Paragents from IM, support multi-session routing.
- Routing key: `chat_id + session_ref`.
- Minimum commands: `/new`, `/prompt`, `/switch`, `/list`.
- Output protocol: prefix all IM responses with `[S:<session_ref>][P:<prompt_ref>][status]`.

### 1) Atomic state writes
- Convert `JsonlSessionStateStore` writes to `tmp -> fsync -> rename`.
- Keep last good state on failure.

### 2) Concurrency protection
- Add serialized write lock for state mutations and flushes.

### 3) Schema evolution
- Add `schema_version` and backward-compatible loading with defaults.

### 4) Checkpoint lifecycle clarity
- Separate `turn_checkpoint` and `session_checkpoint`.
- Log recovery source in debug traces.

## P1: Context Quality, Policy, and Session Runtime

### 5) Multi-dimensional compaction triggers
- Expand `should_compact()` using turn count, character size, estimated tokens, and tool-output bloat.

### 6) Structured compact notes
- Standardize to: `done / pending / constraints / next`.

### 7) Prompt layering profiles
- Add toggles for runtime block, memory summary, compact notes, and recent turns.
- Allow session-level profile override.

### 8) History pollution control
- Summarize large tool outputs before they enter `recent_turns`.

### 9) Unified policy semantics
- Converge into one rule layer with precedence `deny > ask > allow`.

### 10) Better output conflict detection
- Keep `out:*` as primary signal.
- Add directory-level and temp-artifact pattern support.

### 11) Conflict decision UX improvements
- `/override` and `/cancel` must display overwritten paths and owner `session_ref`.
- Clear conflict highlights immediately after decision.

### 12) One-session-one-agent invariant checks
- Add assertions and metrics for session->agent uniqueness.

### 13) Full `turn_done` convergence
- Remove remaining `final`-path compatibility.

### 14) Session worker health probes
- Expose queue length, wait duration, and heartbeat metrics.

### 19) Skill support system
- Add `SkillSpec`: `name`, `version`, `inputs`, `outputs`, `permissions`, `entrypoint`.
- Build local skill registry/loader (remote index optional later).
- Add commands: `/skills`, `/skill use <name>`, `/skill info <name>`.
- Skill execution must pass existing permission policy checks.

## P2: Observability and Regression Hardening

### 15) Structured debug events
- Emit structured fields for `compact_notes`, `memory_summary`, `recent_turns`.

### 16) End-to-end trace IDs
- Propagate one `trace_id` through preflight -> approval -> run -> turn_done.

### 17) Regression scenario suite
- Cover conflict, approval, pause/resume, and long multi-turn context continuity.

### 18) Unified command+conflict UI state machine
- Unify candidate logic for `/switch`, `/override`, `/cancel`.
- Manage conflict-highlight lifecycle from one state machine.

## Suggested Two-Week Order

### Week 1
1. P0 IM minimal loop (`TODO-00` ~ `TODO-00C`)
2. P0 reliability (`TODO-01` ~ `TODO-04`)
3. P1 compaction trigger baseline (`TODO-05`)

### Week 2
1. P1 context quality (`TODO-06` ~ `TODO-08`)
2. P1 policy/conflict UX (`TODO-09` ~ `TODO-11`)
3. P2 observability/regression (`TODO-15` ~ `TODO-17`)

## File-Level TODO Index

- `TODO-00` ~ `TODO-00C`: IM gateway, routing, readability protocol, command UX
- `TODO-01` ~ `TODO-04`: atomic persistence, lock, schema migration, checkpoint layering
- `TODO-05` ~ `TODO-08`: compaction and prompt quality
- `TODO-09` ~ `TODO-11`: policy unification and conflict handling
- `TODO-12` ~ `TODO-14`: session runtime invariants and worker health
- `TODO-15` ~ `TODO-18`: observability, regressions, UI state machine
- `TODO-19`: skill framework integration
