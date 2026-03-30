---
name: linear-workflow
description: Linear task management for church budget tool — project structure, labels, milestones, issue patterns
metadata:
  internal: false
---

# Linear Workflow Patterns

Perry's skill for managing the Church Budget Tool project in Linear.

## Workspace Configuration

- **Team**: Valid Agenda
- **Team Key**: CHA
- **Project**: Church Budget Tool
- **Project Slug**: `lev` (Levi — Matthew the tax collector)

## Status Workflow

```
Backlog → Todo → In Progress → Done
                              → Canceled
                              → Duplicate
```

## Label Conventions

### Agent Assignment Labels
Use agent name labels to indicate who owns the work:
- `Perry` — orchestration, coordination tasks
- `Felicity` — engineering, code implementation
- `Jimmy` — UI design, tokens, visual specs
- `Superman` — analytics, charts, data visualization
- `Kryptonite` — security reviews, audits
- `Lois` — research tasks
- `Clark` — documentation tasks
- `Tony` — deployment, Docker, infrastructure
- `Human` — requires manual human action (e.g. Xero app creation)

### Type Labels
- `Feature` — new capability
- `Bug` — defect
- `Improvement` — enhancement to existing feature

### Repo Label
- `repo:lev` — work in the Church Budget Tool repo

## Milestone Structure

| Milestone | Name | Maps to PRD Phase |
|-----------|------|-------------------|
| M1 | Foundation (MVP) | Phase 1 |
| M2 | Reporting & Property | Phase 2 |
| M3 | Budget Planning | Phase 3 |
| M4 | Auth & Automation | Phase 4 |

## Issue Creation Pattern

**Title**: Short, imperative verb phrase
**Labels**: Agent label + Type label + `repo:lev`
**Milestone**: Assign to appropriate phase
**Description template**:

```markdown
## What
[One sentence describing the deliverable]

## Why
[Business context — what does this enable for the church?]

## Acceptance Criteria
- [ ] [Specific, verifiable criterion]
- [ ] [Another criterion]

## Dependencies
- Blocked by: #[issue number] (if any)
- Blocks: #[issue number] (if any)

## Agent Notes
[Any context the assigned agent needs]
```

## Perry's Workflow

1. **Triage**: New issues go to Backlog, Perry assigns labels and milestone
2. **Sprint**: Move priority items to Todo, assign agent labels
3. **Track**: When an agent starts work, move to In Progress
4. **Close**: When work is verified, move to Done
5. **Sync**: Update `state_of_play.md` (via Clark) when milestone status changes

## Comment-First Execution

From Perry's agent definition — feedback lives in issue comments:
- Progress updates as comments on the issue
- Blockers flagged as comments with `@Human` mention
- Completion evidence (test results, screenshots) as comments before closing

## State of Play Sync Triggers

Update `00_context/memory/state_of_play.md` when:
- A milestone moves to a new status
- A blocker appears or resolves
- A significant deliverable completes
- Team assignments change

Perry coordinates the update; Clark writes it.
