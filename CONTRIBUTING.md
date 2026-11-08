# Contributing

Thanks for your interest. Bug reports, fixes and focused improvements are welcome.

## Ground rules

- **Keep the surface small.** New tools need a clear use case that the existing six cannot cover. Edit, transition
  and delete operations are out of scope by design.
- **No secrets or private data** in code, tests, fixtures or issues. Use placeholders such as `acme.atlassian.net`.
- **Behaviour is tested through the MCP interface.** Add or adjust tests alongside every change.

## Workflow

```bash
just tests     # lint, format check and the full test suite in Docker
just lint      # apply fixes and formatting
```

If you change a tool's parameters or descriptions, the schema snapshot test fails on purpose. Review the change,
then regenerate the snapshot:

```bash
pytest tests/test_mcp.py --inline-snapshot=fix
```

Commit messages explain why, what and how the change was verified. Open a pull request against `main`. CI runs the
same checks as `just tests` and builds the runtime image.
