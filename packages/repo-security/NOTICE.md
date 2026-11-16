# Source and dependency notices

The local `code-security-template` was migrated at the user's explicit request
on 2026-11-17 for this personal repository. Its source directory is not a Git
repository and contains no LICENSE or NOTICE file. No public redistribution
license for that source, or for this repository, is inferred by this migration.
Public publication requires resolving that license declaration first; M1 builds
and validates local artifacts and does not publish them.

`src/repo_security/templates/manifest.json` records each selected source file,
its original SHA-256, its shipped resource/destination and current SHA-256.
It intentionally uses a placeholder source location, not a personal absolute
path. Scan and PII runtime, hooks and local rules are retained from the source.
The installer is adapted to select a separate Python, verify every downloaded
Gitleaks archive, preflight hooks and self-test scanners before activation.
The scanner project adds an independent uv workspace boundary. `selftest.py`
is new. The GitHub workflow is preserved as a reference resource only in M1;
it is not generated or described as an approved production workflow yet.

Third-party executables and Python dependencies are **not vendored or bundled**.
The installer obtains the Gitleaks 8.30.1 release directly from its official
GitHub release URL and validates the platform archive against embedded SHA-256
values. Semgrep 1.185.0 and Presidio Analyzer 2.2.366 are obtained using the
copied uv lock, including dependency hashes. Preserve installed license files
and notices when using or redistributing those dependencies.

The reference environment's installed package metadata reports:

| Distribution | Version | License expression |
|---|---|---|
| semgrep | 1.185.0 | LGPL-2.1-or-later |
| presidio-analyzer | 2.2.366 | MIT |
| tldextract | 5.4.0 | BSD-3-Clause |

These are metadata observations, not a complete transitive license audit or a
claim that every Semgrep component uses the same license. Gitleaks archive
license/notice files are retained alongside its executable by the installer.
M1 does not republish third-party packages, archives or binaries.
