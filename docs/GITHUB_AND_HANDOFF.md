# GitHub And Handoff

Target repository:

```text
https://github.com/BrokenFL/JordanaBilling
```

Before pushing, verify the repository is private and run:

```bash
scripts/git_safety_check.sh
```

Do not commit:

- `.env`
- API keys
- live SQLite databases
- real CSV reports
- Google credentials
- invoice PDFs
- logs
- screenshots containing client data
- shortcut backups
- raw Google Sheet exports

Commit:

- source code
- schema and migrations
- tests
- documentation
- `.env.example`
- sanitized sample fixtures

Use `git status --ignored --short` before pushing to confirm sensitive files are ignored.
