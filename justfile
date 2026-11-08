# Instances live outside the repository: one env file per Jira site in $MCP_JIRA_HOME.
config_dir := env("MCP_JIRA_HOME", home_directory() / ".config" / "mcp-jira")
data_dir := env("MCP_JIRA_DATA", home_directory() / ".local" / "share" / "mcp-jira")

default:
	@just --list --unsorted

# ---- Instances ----

[group('instances')]
[doc('create an instance env file (name: a-z0-9-, port: free localhost port)')]
init name port:
	#!/usr/bin/env bash
	set -euo pipefail
	[[ "{{name}}" =~ ^[a-z0-9][a-z0-9-]{0,30}$ ]] || { echo "name must match [a-z0-9-]"; exit 1; }
	file="{{config_dir}}/{{name}}.env"
	[ -e "$file" ] && { echo "$file already exists"; exit 1; }
	mkdir -p "{{config_dir}}" && chmod 700 "{{config_dir}}"
	sed -e "s/^MCP_PORT=.*/MCP_PORT={{port}}/" -e "s/^JIRA_INSTANCE_NAME=.*/JIRA_INSTANCE_NAME={{name}}/" \
		.env.example > "$file"
	chmod 600 "$file"
	echo "created $file; fill in JIRA_SITE_URL, ATLASSIAN_CLIENT_ID, ATLASSIAN_CLIENT_SECRET, then: just up {{name}}"

[group('instances')]
[doc('build and start an instance in the background')]
up name: (_compose name "up -d --build --wait")

[group('instances')]
[doc('stop an instance (keeps its OAuth state volume)')]
down name: (_compose name "down")

[group('instances')]
[doc('follow an instance log')]
logs name: (_compose name "logs -f")

[group('instances')]
[doc('list running instances')]
ps:
	docker ps --filter "name=mcp-jira-" --format "table {{{{.Names}}\t{{{{.Status}}\t{{{{.Ports}}"

_compose name args:
	#!/usr/bin/env bash
	set -euo pipefail
	file="{{config_dir}}/{{name}}.env"
	[ -f "$file" ] || { echo "no $file; run: just init {{name}} <port>"; exit 1; }
	port=$(grep -E '^MCP_PORT=' "$file" | cut -d= -f2)
	downloads="{{data_dir}}/{{name}}/downloads"
	mkdir -p "$downloads"
	MCP_INSTANCE="{{name}}" MCP_PORT="$port" MCP_ENV_FILE="$file" MCP_DOWNLOADS="$downloads" \
		docker compose -p "mcp-jira-{{name}}" {{args}}

# ---- Development ----

[group('dev')]
[doc('resolve and lock dependencies (inside Docker, no local uv needed)')]
lock:
	docker run --rm -v "$PWD":/src -w /src ghcr.io/astral-sh/uv:0.13.7-python3.15-trixie-slim uv lock

[group('dev')]
[doc('lint + format check + tests, as in CI')]
tests:
	docker compose --profile dev run --rm --build test

[group('dev')]
[doc('apply lint fixes and formatting')]
lint:
	docker compose --profile dev run --rm --build test docker/lint.sh

[group('dev')]
[doc('build the runtime image')]
build:
	docker compose build mcp-jira
