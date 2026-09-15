## Git History and Collaboration Rules

Maintain a clean, chronological, and professional Git history.

- Before each phase or logically complete feature, inspect `git status` and review the current diff.
- Commit only coherent, tested changes. Do not create vague, oversized, or unrelated commits.
- Use concise but informative Conventional Commit messages:
  - `feat: add workflow proposal schema`
  - `fix: resume failed runs from checkpoints`
  - `test: add evaluation regression cases`
  - `docs: document NVIDIA model routing`
  - `refactor: isolate model provider adapter`
- Each commit message must explain the completed change, not the development intention.
- Run relevant tests before committing. Never commit known failing code unless explicitly documenting an intentional checkpoint.
- Do not commit secrets, `.env` files, credentials, generated junk, or large untracked artifacts.
- After each completed implementation phase, create a commit.
- Push completed commits to the configured remote branch after verification.
- Before pushing, confirm the branch, remote, commit contents, and test status.
- Never force-push, rewrite history, squash commits, or delete branches unless explicitly instructed.
- If authentication, permissions, merge conflicts, or protected-branch rules block a push, stop and report the exact issue.
- At each checkpoint, report the commit hash, commit message, files changed, tests run, and push status.
