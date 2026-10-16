# Claude Code project instructions

This repository is shared with Codex. Collaboration happens through Git and `docs/collab/`, not through assumed shared chat memory.

## Before editing

1. Read `docs/collab/REQUIREMENTS.md`, `PLAN.md`, `TASKS.md`, and `HANDOFF.md`.
2. Inspect Git status and diff. Preserve pre-existing changes.
3. Confirm one approved task and mark it `in-progress — Claude` in `TASKS.md`.

## Rules

- Limit changes to the accepted task and its tests/docs.
- Do not invent requirements; record questions or assumptions in the task and handoff.
- Run relevant verification and report actual output. Do not describe an unrun test as passing.
- Do not overwrite another agent's work or edit a file currently assigned to Codex.
- Before stopping, update `HANDOFF.md` and the task status.

## Git and machine safety

- No destructive Git operations, force pushes, repository cleaning, history rewrites, commits, pushes, merges, tags, releases, deployments, or branch deletion without explicit human approval.
- Do not read, print, copy, or commit credentials. Never place secrets in prompts or collaboration files.
- Ask before installing/upgrading dependencies, changing lockfiles broadly, running migrations, writing to external services, or using administrator privileges.
- Prefer serial handoffs. If parallel work is approved, use separate branches/worktrees and disjoint file ownership.

## Important capability boundary

These instructions are for **Claude Code in Claude Desktop** with this local folder selected. A normal Claude chat should not claim it directly changed this repository unless a local-folder tool was explicitly attached and authorized.
