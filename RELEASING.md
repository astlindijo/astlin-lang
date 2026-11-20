# Releasing Astlin

A local build is not a public release. A release is public only after its source tag
and assets have been uploaded to the selected hosting account and the release URL
has been verified.

## Prepare artifacts

Use the Python and C compiler versions supported by the release. Run from the repository root:

```sh
python3 -m astlin doctor
python3 -m unittest discover -s tests -v
python3 -m astlin test --sanitize
python3 tools/prepare_release.py
```

The preparation command checks the source release version and notes, builds the wheel
and source archive, verifies their embedded versions and required files, and writes
`dist/SHA256SUMS`. Rebuild after any source, documentation, or license change.

The license and copyright holder must be selected before publishing. Include the
selected `LICENSE` in the repository and distributions and keep package metadata in sync.
Do not include account tokens, local credentials, caches, or generated executables in Git.

## Publish on GitHub

The following commands are for an existing, authenticated repository. Choose the
destination explicitly and check the staged files before the initial source commit.
The release command refuses to create a missing remote tag or overwrite an existing release.

```sh
gh auth status
git status --short
git tag -a v0.2.0 -m "Astlin v0.2.0"
git push origin main
git push origin v0.2.0
gh release create v0.2.0 dist/astlin_lang-0.2.0-py3-none-any.whl dist/astlin_lang-0.2.0.tar.gz dist/SHA256SUMS --verify-tag --title "Astlin v0.2.0" --notes-file releases/v0.2.0.md
gh release view v0.2.0 --json url,tagName,isDraft,assets
```

This version is a development release. Its notes and README must continue to describe
its experimental status. Do not advertise PyPI installation until an actual PyPI upload
has succeeded and the exact distribution/version has been verified there.

## Future versions

Update `VERSION` in `astlin/compiler.py`, the project/demo version, editor metadata,
changelog, and versioned release notes together. The Python distribution version is
derived from the compiler constant. CI verifies syntax, native behavior, sanitizer
tests, package installation, and release preparation; passing remote CI is separate
from successful local checks.
