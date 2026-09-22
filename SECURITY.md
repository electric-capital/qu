# Security Policy

## Reporting a vulnerability

Please do not open a public issue for security problems.

Report vulnerabilities privately through GitHub's
[private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)
for this repository (the **Security** tab, then **Report a vulnerability**). Include the affected
component, steps to reproduce, and the impact you believe it has.

We will acknowledge the report, work with you on a fix, and credit you in the advisory unless you
prefer otherwise.

## Scope

Quest's security model is described in the [Security section of the README](README.md#security)
and in the architecture docs (start with
[public projects](docs/architecture/public-projects.md),
[script runner](docs/architecture/script-runner.md),
[action requests](docs/architecture/action-requests.md), and
[encryption at rest](docs/architecture/encryption-at-rest.md)).

In scope:

- Any way for a conversation to reach data or services it should not (cross-user access,
  escaping the private/public split, bypassing an `authed_get` / `authed_post` allow-list).
- Any way for the model or a sandboxed script to perform a write without the human approval
  step, or to reach the network from the restricted sandbox.
- Authentication and session issues, credential leakage, and encryption-at-rest weaknesses.
- Path traversal or code execution reachable through workspace files, uploads, or plugin
  inputs.

Out of scope:

- Issues that require a compromised host or a compromised admin account (see the README's
  hardened-host assumption).
- Model behaviour that stays within the guarantees above (for example, the model producing a
  wrong or unhelpful answer).
- Vulnerabilities in upstream services or third-party dependencies with no Quest-specific
  impact; report those upstream.

## Supported versions

Only the `main` branch receives security fixes.
