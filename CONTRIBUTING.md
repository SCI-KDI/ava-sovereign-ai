# Contributing

Thank you for helping. Before a large change, please open an issue so we can check that it fits.

- Contributions are accepted under the Apache License 2.0. There is no CLA.
- Never include personal data, internal hostnames or credentials, including in tests, examples and screenshots. Use `example.org` hosts and fictitious names.
- Do not copy Open WebUI code into this repository.
- Run `./scripts/preflight.sh` before opening a pull request.
- Useful contributions: bug fixes, tests, documentation, and tools or prompts adapted to other cantons or countries.

## Changing a tool or filter

- Follow the [file conventions](tools/README.md#file-conventions): Open WebUI header, everything deployment-specific in valves, no content in logs.
- Test it in the local stack (`docker compose up -d`), with the version of Open WebUI pinned in `compose.yaml`. Say in the pull request what you tested.
- Check that it compiles: `python -m py_compile tools/*.py filters/*.py`.

## Changing a prompt

- Describe the behaviour you want to change, with an example question and the answer before and after.
- Keep the rules listed in [conventions that matter](system-prompts/README.md#conventions-that-matter), unless the pull request is about one of them.
