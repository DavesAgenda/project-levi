---
name: project-docs
description: Documentation patterns for state_of_play, decisions_log, API docs, config schemas, deployment runbooks
metadata:
  internal: false
---

# Project Documentation Patterns

Clark's skill for maintaining project records and documentation.

## State of Play (`00_context/memory/state_of_play.md`)

Living project status document. Updated by Clark when Perry signals a status change.

### Format

```markdown
# State of Play
**Last updated**: YYYY-MM-DD

## Current Phase
Phase N: [Name] — [Status: Active / Blocked / Complete]

## Active Milestone
M[N]: [Name] — [X/Y issues complete]
Linear: [project URL]

## Team Assignments
| Agent | Current Focus | Status |
|-------|--------------|--------|
| Felicity | [task] | Active / Idle / Blocked |
| Jimmy | [task] | Active / Idle / Blocked |
| Superman | [task] | Active / Idle / Blocked |
| Tony | [task] | Active / Idle / Blocked |
| Kryptonite | [task] | Active / Idle / Blocked |
| Lois | [task] | Active / Idle / Blocked |
| Clark | [task] | Active / Idle / Blocked |

## Blockers
- [Blocker description] — Owner: [agent/human] — Since: YYYY-MM-DD

## Next Actions
1. [Immediate tactical action]
2. [Next priority]

## Strategic Check
[1-2 sentences: Are we on track? Any course corrections needed?]
```

## Decisions Log (`00_context/memory/decisions_log.md`)

Permanent record of architectural and process decisions.

### Format

```markdown
# Decisions Log

## YYYY-MM-DD — [Short Decision Title]
**Decision**: [What was decided]
**Rationale**: [Why — include alternatives considered]
**Agent**: [Who recommended/decided]
**Impact**: [What this affects going forward]

---
```

### Rules
- Entries are append-only — never delete, only add corrections
- Use absolute dates (ISO 8601), never relative
- Include business context ("helps the treasurer reconcile" not just "simplifies code")
- Tag the agent who made or recommended the decision

## API Documentation

When Felicity completes a router, Clark documents:

```markdown
## [Router Name]

### GET /endpoint
**Purpose**: [What it returns]
**Auth**: Required / Not required
**Parameters**:
| Name | Type | Required | Description |
|------|------|----------|-------------|
| param | string | yes | [Description] |

**Response**: [Description of response format]
**htmx**: Returns partial HTML when `HX-Request` header present
```

## Config Schema Documentation

For each YAML config file, document:
- Purpose and who maintains it
- Every field with type, required/optional, and meaning
- Example values
- Relationship to other config files
- How changes affect computed values (e.g. property weekly_rate → annual budget)

## Deployment Runbook

```markdown
## Deployment Runbook

### Prerequisites
- [What needs to be in place]

### Deploy
1. [Step-by-step commands]

### Verify
- [Health check URL]
- [Smoke test steps]

### Rollback
1. [How to revert]

### Troubleshooting
| Symptom | Likely Cause | Fix |
|---------|-------------|-----|
```

## Phase Summary (End of Milestone)

When a milestone completes:

```markdown
## Phase [N] Summary: [Name]
**Completed**: YYYY-MM-DD

### Delivered
- [Feature 1]
- [Feature 2]

### Changed from Plan
- [Deviation and reason]

### Known Issues / Tech Debt
- [Issue and severity]

### Setup Instructions
- [Anything new that needs configuration]
```
