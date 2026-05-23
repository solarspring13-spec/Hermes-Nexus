# 🤝 Contributing

Hermes-Nexus is an open-source project. We welcome contributions of all kinds — from bug reports to new adapters.

## Ways to Contribute

| Type | What to do |
|------|-----------|
| 🐛 **Bug Report** | Open a [Bug Report](https://github.com/solarspring13-spec/Hermes-Nexus/issues/new?template=bug_report.yml) |
| 💡 **Feature Request** | Open a [Feature Request](https://github.com/solarspring13-spec/Hermes-Nexus/issues/new?template=feature_request.yml) |
| 🔌 **New Adapter** | Build an adapter for a new agent platform |
| 📖 **Documentation** | Improve Wiki pages, README, or inline docs |
| 🧪 **Testing** | Write tests, run QA Sentinel locally |

## Development Setup

```bash
# Clone and install in dev mode
git clone https://github.com/solarspring13-spec/Hermes-Nexus.git
cd Hermes-Nexus
pip install -e .

# Run QA Sentinel locally
python3 tests/qa_sentinel/doc_fetcher.py --diff
```

## Project Structure

```
Hermes-Nexus/
├── memoria_engine/          # Core engine
│   ├── __init__.py          # Version + package init
│   ├── core/                # L0/L1/L2 logic
│   ├── search/              # FTS5 cross-session search
│   ├── scripts/             # CLI entry points
│   └── utils/               # Updater, helpers
├── adapters/                # Platform adapters
│   ├── workbuddy/           # WorkBuddy (CodeBuddy CN)
│   └── openclaw/            # OpenClaw (Nous Research)
├── tests/                   # Test suite
│   └── qa_sentinel/         # CI quality gate
├── .github/                 # CI/CD + Issue Templates
│   ├── workflows/           # GitHub Actions
│   └── ISSUE_TEMPLATE/      # Bug/Feature forms
├── install.sh               # Universal installer
└── README.md                # Project overview
```

## Building a New Adapter

1. Create `adapters/<platform>/` directory
2. Write `SKILL.md` following the thin-shell pattern
3. Write `install.sh` for platform-specific setup
4. Test with a fresh install on the target platform
5. Submit a PR

## Code Style

- Python: Follow PEP 8
- Shell: Use `#!/bin/bash`, no bashisms
- Markdown: Use reference-style links
- Docstrings: Google style

## PR Process

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'feat: add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

**Commit convention**: Use [Conventional Commits](https://www.conventionalcommits.org/):
- `feat:` New feature
- `fix:` Bug fix
- `docs:` Documentation
- `test:` Testing
- `chore:` Maintenance

## QA Sentinel CI

All PRs are automatically checked by QA Sentinel (runs Mon/Wed/Fri at 02:00 UTC, or on `workflow_dispatch`):

- **Security scan**: Regex + AST-based sensitivity detection
- **Import integrity**: Verifies no broken relative imports
- **Path migration**: Checks config.MEMORIA_HOME consistency

Run locally before submitting:

```bash
python3 tests/qa_sentinel/doc_fetcher.py --diff --exit-on-breaking
```

## License

By contributing, you agree that your contributions will be licensed under the BSL 1.1 License.

---

**[⬆ Back to Home](./Home.md)**
