# Reporting a security issue

Please do not disclose credentials, tokens, session keys or exploitation details in a public issue.

Use GitHub's **Report a vulnerability** link on the repository's **Security** tab when available, or contact the repository owner privately through an existing channel.

The Discord bot and draft-coach relay are private friend-group tools, not publicly supported APIs. Riot and Northflank credentials must be stored in a private server secret store or GitHub Actions secrets, never in code, packaged Windows binaries, logs, or distributed loadfiles.

If a token has been exposed, revoke it at its issuer and issue a new one. Removing it from the latest commit is not sufficient when it remains in Git history.
