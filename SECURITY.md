# Security Policy

Famulus can read personal data and act on external services when a user enables
the corresponding skills. Its current permissions, data flows, credential
locations, confirmation rules, and known limitations are documented in
[Security and privacy](docs/security-and-privacy.md).

## Supported versions

Famulus does not yet have a promoted stable release. Security reports against
the current repository state are welcome. A version support policy will be
added with the first promoted release.

## Report a vulnerability privately

Email `officina.feedback@gmail.com` with `SECURITY` in the subject. Do not put a
vulnerability, credential, token, private document, or personal data in a
public GitHub issue.

Include, when available:

- the affected commit or installed version and host platform;
- the affected skill or command;
- the security impact and the minimum steps needed to reproduce it; and
- a redacted example that contains no live credentials or private data.

Do not send a live secret as evidence. Revoke or rotate an exposed credential
through its provider first. Reports are handled on a best-effort basis; no
response-time or disclosure-time guarantee is currently offered.

Non-sensitive bugs may be reported through the repository's
[public issue tracker](https://github.com/MoeenNehzati/famulus/issues).
