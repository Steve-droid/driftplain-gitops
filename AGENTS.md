# Repository instructions

## Release versioning (September 22, 2026)

Follow [SemVer 2.0.0](https://semver.org/) and the
[release policy](https://github.com/Steve-droid/driftplain/blob/main/RELEASE-POLICY.md).
These rules replace older per-slice tagging rules and fixed next-version suggestions.

- **PATCH:** compatible bug fixes or dependency/security/packaging fixes needing a new artifact.
- **MINOR:** new backward-compatible functionality or deprecation with continued compatibility.
- **MAJOR:** a breaking supported API, CLI, configuration, user workflow or operational upgrade contract.
- **No release:** documentation, comments, tests or internal tooling/refactoring alone, unless a
  changed distributable is needed. Compatible internal/build changes that require an image get a patch.
- Evaluate all relevant changes since the last release of that component. Use the highest bump;
  reset patch for a minor, and minor/patch for a major. Commit prefixes and task numbers do not
  choose the version. Record `previous -> next`, category and compatibility reason in the PR or release.
- Fetch fresh tags and check published versions before choosing a number. Backend, frontend,
  agents, infrastructure and GitOps have independent sequences. Both agents share one
  `agent-vX.Y.Z` sequence; other component repos use `vX.Y.Z`. Keep existing 1.x sequences.
- A completed task does not automatically need a tag. For an intentional release, tag the
  reviewed main commit and create a GitHub Release even for patch/minor versions. Check for
  concurrent releases before tagging. Publication and deployment are separate actions.
- Never move, delete or overwrite a published tag/image to fix an incorrect bump. Backend
  `1.1.1` remains published; its added public APIs warranted a minor. The next backend release
  must be at least `1.2.0`, adjusted for any newer releases or breaking changes.
- Continue versions across the image rename. Preserve old packages, current deployment pins
  and all operational approval requirements. This policy itself requires no release tag.

GitOps compatibility covers Helm values, resource/storage identities and deployed app contracts.
A compatible image fix is patch; a new app/chart feature is minor; breaking deployed behavior
or required values/storage migration is major. Use GitOps' own version sequence, not the app's.

## Application image names (September 22, 2026)

New releases use `ghcr.io/steve-droid/driftplain-backend`, `driftplain-frontend`,
`driftplain-agent` and `driftplain-agent-security`. Follow the
[image naming policy](https://github.com/Steve-droid/driftplain/blob/main/IMAGE-NAMING.md).
Continue each existing version sequence; do not reset versions, reuse published tags or
delete old `modelmatch-*` packages. The verified starting points are backend 1.1.1,
frontend 1.1.0 and agents 1.1.3; check fresh tags before choosing the next version.

Keep existing production image pins until the new packages are published, public and
verified by an anonymous pull. Update both repository and digest for the first deployment
under a new name. Preserve Kubernetes, database, volume and CI credential/environment names.
This policy overrides older image-naming statements below; it does not authorize a deployment.

See [CLAUDE.md](CLAUDE.md) for the repository's other working instructions.
