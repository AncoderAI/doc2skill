# npm release process

Releases are driven by Git tags. A pushed `vX.Y.Z` tag starts
`.github/workflows/npm-publish.yml`, which validates the tag and both package
versions, runs the Python and npm release gates, and publishes to npm.

## One-time npm configuration

The `book-to-skill` package must trust this exact GitHub Actions workflow:

```bash
npm trust github book-to-skill \
  --repository AncoderAI/doc2skill \
  --file npm-publish.yml \
  --allow-publish \
  --yes

npm trust list book-to-skill
```

Use npm 11.5.1 or newer for these commands. npm requires maintainer
authentication and may open a browser for two-factor verification. Configure
only the workflow filename, not `.github/workflows/npm-publish.yml`.

The workflow uses GitHub OIDC (`id-token: write`) and does not require an
`NPM_TOKEN`. Repository-level Actions secrets cannot be read or inherited by a
different repository. If token authentication is ever required as a temporary
fallback, create a new repository secret or an organization secret explicitly
shared with this repository; do not copy a token into the workflow file.

## Cut a release

1. Update the same semantic version in `package.json`, `package-lock.json`, and
   `pyproject.toml`.
2. Move the `Unreleased` changelog entries under `## [X.Y.Z] - YYYY-MM-DD`.
3. Run the local release gates and commit the release.
4. Push the release commit, create the matching annotated tag, then push it.

```bash
npm run verify:npm
pytest tests/ -q
ruff check --select E9,F --target-version py310 book_to_skill/ scripts/ tests/ tools/
python3 tools/validate_skill.py SKILL.md
npm pack --dry-run --json

git push origin master
git tag -a vX.Y.Z -m "book-to-skill vX.Y.Z"
git push origin vX.Y.Z
```

The workflow rejects a tag that does not equal `v` plus the package version, a
Python/npm version mismatch, or a version already present in the npm registry.
Stable versions use the `latest` dist-tag. A version such as `1.5.0-beta.1`
uses the `beta` dist-tag and does not move `latest`.

## Verify the public release

A pushed tag is not release proof. Wait for the tag workflow and verify the
registry artifact independently:

```bash
gh run list --repo AncoderAI/doc2skill --workflow npm-publish.yml --limit 5
npm view book-to-skill@X.Y.Z version dist-tags repository dist --json

release_tmp="$(mktemp -d)"
npm pack book-to-skill@X.Y.Z --pack-destination "$release_tmp" --json
npm exec --yes --package="$release_tmp/book-to-skill-X.Y.Z.tgz" -- \
  book-to-skill-skill version
```

For a complete acceptance check, install that exact tarball into a temporary
skills root, run `doctor`, and exercise `scripts/extract.py` against a small
document. Do not rely only on `npx` from the project root because npm can resolve
the local package instead of the registry artifact.
