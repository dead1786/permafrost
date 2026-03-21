# Changelog

## v0.2.1 (2026-03-21)

### Fixed
- Brain writes heartbeat immediately on start (prevents watchdog false restart)
- PID lock prevents duplicate brain processes
- All provider SDKs included in requirements.txt (users no longer hit missing module errors)

## v0.2.0 (2026-03-21)

### Added
- 4-layer security system: tool whitelist, file ACL, prompt injection detection (20+ patterns), rate limiter
- AI-guided persona wizard: 8-question interactive setup, bilingual (EN/ZH)
- 5-step setup wizard: model -> channels -> persona -> security -> launch
- Docker deployment: multi-stage build, non-root, healthcheck, secrets management
- 159 unit + E2E tests across 6 test files
- Security audit logging

### Changed
- Restructured Brain core for multi-provider support (Anthropic, OpenAI, Google, OpenRouter)
- Improved channel abstraction for Telegram, Discord, and Web

## v0.1.0 (2026-03-15)

### Added
- Initial release
- Brain core: message processing + provider routing
- Channel system: Telegram, Discord, Web support
- Provider abstraction: Anthropic Claude, OpenAI GPT, Google Gemini, OpenRouter
- Configuration via JSON
- Basic persona system
