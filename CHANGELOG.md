# Changelog

Follows [Keep a Changelog](https://keepachangelog.com/) and semantic versioning.

## [Unreleased]

First public release in preparation.

### Added
- `tools/ask_ava_specialist.py`: generic delegation tool, one copy per specialist.
- `filters/ava_context_injector.py`: name placeholders, date context, conversation snapshot.
- `filters/intent_router.py`: follow-up rewriting and specialist suggestion.
- System prompts for the orchestrator and the legal, HR, meeting-minutes and coding specialists.
- Build guide in two parts: on a laptop, then in production.
- Documentation: models, operations, website chatbot, meeting minutes.
- `.env.example` and Apache Tika in the local stack.
- `deploy/secret.example.yaml`, and production settings in `deploy/values.example.yaml`.
