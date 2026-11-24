# Publishing Indy Hub 1.19.0

Release preparation and publication are separate. Merging the preparation PR
does not publish a package. The maintainer creates and pushes the final tag
manually after reviewing the PR and its passing checks.

## Prepared release files

- Python, npm and lockfile versions are `1.19.0`.
- The dated `1.19.0` changelog section supplies the GitHub release body.
- README and upgrade notes describe Python 3.12+, ESI 10.2+, SDE 2,
  migrations `0117`–`0119` and worker requirements.
- CI checks source versions, release notes and actual wheel/sdist metadata.
  The same checks run again for the tag before uploading anything.

## Maintainer's final step

After merging the preparation PR, wait for the Tests workflow on the resulting
`main` commit to pass. Confirm the repository's existing `PYPI_APIKEY` Actions
secret remains valid for the `indy-hub` project. Never put the token in source
files, commands, release notes or workflow logs.

From the up-to-date `main` checkout, create the tag only when ready to publish:

```bash
git switch main
git pull --ff-only origin main
git tag -a 1.19.0 -m "Indy Hub 1.19.0"
git push origin 1.19.0
```

The Release workflow then:

1. Requires the final tag to equal the package version (`1.19.0` or `v1.19.0`).
1. Builds one wheel and one source archive, checks their contents and versions,
   and runs strict Twine metadata validation.
1. Uploads those checked artifacts to PyPI using the existing repository secret.
1. Creates the GitHub release for the existing tag with the matching changelog
   section and the same wheel/source attachments.

Do not push both tag spellings for the same version. Prerelease tags are rejected;
the workflow currently publishes final versions only. Publishing a GitHub release
does not trigger another upload, so there is no second release-event publisher.
If upload fails, no GitHub release is created. Correct the cause and rerun failed
jobs; an already successful PyPI job need not run again when only the GitHub
release job failed. Never move a published version tag to different code.

## Migration 0025 compatibility review

The release preparation does not edit existing migrations. A correction already
on `main` since commit `85ded0052b1d9a2993b66f51b87d8a329f182a1c`
changed migration `0025` to supply `location_ids=None`, `force_refresh=True` and
`dry_run=False` explicitly when queuing location-name population. These arguments
are required by the existing QueueOnce key configuration; the old empty dispatch
could fail and enter synchronous fallback during installation (issue #163).

The migration dependencies, `RunPython` operation, reverse no-op and database
schema are unchanged. Django skips the migration when it is already recorded as
applied, so upgraded installations do not replay the backfill. Fresh installations
retain the same population scope and forced refresh; queue publication now works,
and broker failures retain the existing synchronous fallback. The enqueue and
fallback regression tests cover both paths. Do not fake, reset or reapply this
migration on an existing installation.

## Deployment after publication

Publication does not change an installed AA instance. Follow
[the upgrade notes](../UPGRADE_NOTES.md#upgrading-from-1183-to-1190), including
backup, migrations, SDE loading, `collectstatic` and coordinated process restarts.
