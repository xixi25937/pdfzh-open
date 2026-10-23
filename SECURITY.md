# Security and privacy

Do not post API keys, settings, caches or private PDFs in Issues. Revoke exposed keys at DeepSeek immediately; deleting a key from the latest commit does not remove Git history.

Keys are saved outside the repository using Windows DPAPI. DEEPSEEK_API_KEY overrides saved settings. Translation text goes to the DeepSeek API; model downloads also contact upstream hosts. DPAPI does not protect against malware running as the same Windows user.

Treat PDF and LaTeX as untrusted input. No-shell-escape is not a complete sandbox. Keep dependencies updated. This experimental tool has not had a security audit.

Before public release, configure a private vulnerability-reporting channel on GitHub.
