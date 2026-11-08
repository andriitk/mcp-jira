# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-08

### Added
- Tools: `get_issue`, `get_attachment`, `search_issues`, `whoami`, `add_comment`, `create_issue`.
- Per-user Atlassian OAuth 2.0 (3LO) for MCP clients, with transparent token refresh.
- Attachment previews: images inline, video and animated GIF frames, text files.
- Markdown to Atlassian Document Format for comments and descriptions, and back for reading.
- One container per Jira site; `just init/up/down/logs/ps` for local instances.
- Optional Redis storage for OAuth state across replicas.
