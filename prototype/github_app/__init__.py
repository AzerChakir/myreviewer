"""GitHub App integration (CodeRabbit-style).

`auth.py` signs the App JWT and mints per-installation access tokens;
`app.py` is the FastAPI webhook that tracks installs, reviews PRs, and answers
slash commands. Both are served from `<prototype>/api.py` on the same process.
"""