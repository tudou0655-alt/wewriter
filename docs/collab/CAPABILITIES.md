# Desktop capability notes

Verified: 2026-10-16

## Codex

OpenAI documents `AGENTS.md` as an optional file recognized by Codex and used as persistent repository guidance. The surrounding requirements/plan/task/handoff files in this template are workflow conventions, not mandatory Codex filenames.

- https://developers.openai.com/cookbook/examples/codex/iterating-development-workflows-with-codex

## Claude Desktop and Claude Code

Anthropic documents Claude Code as an agent that reads files, runs commands, and makes multi-file edits. It also documents `CLAUDE.md` as project context read at session start. Claude Desktop supports Code sessions with a selected working directory on Windows.

- https://support.claude.com/en/articles/14553240-give-claude-context-claude-md-and-better-prompts
- https://support.claude.com/en/articles/14554922-claude-code-user-faq
- https://support.claude.com/en/articles/14729294-open-claude-desktop-with-a-link

## Important distinction

Anthropic's ordinary chat can create downloadable artifacts and work with uploaded content. Direct local project access instead depends on an explicitly authorized local surface such as Claude Code, Cowork with a mounted folder, or a desktop extension. Therefore this template never treats an ordinary chat as having implicit write access to the repository.

- https://support.claude.com/en/articles/11725091-when-to-use-desktop-and-web-connectors
- https://support.claude.com/en/articles/10065433-install-claude-desktop
