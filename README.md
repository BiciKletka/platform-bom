# platform-bom

Shared Maven build-of-materials for the BiciKletka Java services (bici-core, mqtt-gateway, bici-media,
remotemonit-gateway). One reactor, three published artifacts, one version:

- **`platform-bom`**: the reactor root.
- **`platform-dependencies`**: a pure BOM (`dependencyManagement` only). It imports `spring-boot-dependencies`
  and the Jackson and AWS SDK BOMs, and pins Tomcat, springdoc, Lombok, MapStruct, ArchUnit and Paho on top. Each
  version is one property; the comment next to it says why it is not the Boot default.
- **`platform-parent`**: the parent POM every service's root `pom.xml` inherits. It imports
  `platform-dependencies` and centralizes plugin management (compiler with `-parameters` and the Lombok and
  MapStruct annotation processors, Surefire, Failsafe, `spring-boot-maven-plugin`, enforcer, CycloneDX).

`docs/service-template.md` shows how a service inherits it (parent, registry, Dockerfile, CI, Dependabot).

## Versions

Versions are `0.<series>.<n>` (for example `0.1.3`). `.platform-series` holds the `MAJOR.MINOR` pair, and every
push to `main` that changes more than docs publishes `<series>.<last+1>` to this repository's GitHub Packages
Maven registry and pushes the tag `v<version>` (`publish.yml`). Pushes that only touch `docs/**`, `*.md`,
`.gitattributes`, `.gitignore` or `.dockerignore` publish nothing.

- The tags are the record of what shipped (`git ls-remote --tags origin 'v*'`); the Packages tab shows the latest.
- Publishing is serialized. After several quick merges GitHub cancels the pending middle run; the last run
  contains those commits, so no change is lost, but a version number is skipped. That is expected.
- While the platform is at `0.x`, treat a series bump as breaking: edit `.platform-series` in the same PR as the
  breaking change. The first version of a new series is `<series>.1`.

## Upgrading a service

Merge the Dependabot PR that bumps `platform-parent` (each service has a `.github/dependabot.yml` limited to it).
Every other version bump (a Tomcat patch, Boot, Jackson) is made once, here, and reaches the services that way.

## CI

- `ci.yml` (pull requests): builds and installs the reactor with a throwaway revision, checks the installed
  parent carries literals, and runs the OSV gate against the BOM's resolved set (a throwaway consumer that depends on
  everything the BOM manages; `.github/scripts/osv_severity_gate.py`, allowlist in `osv-scanner.toml`).
  Publishes nothing.
- `publish.yml` (push to `main`): computes the version from the tags, runs `mvn -Drevision=<version> clean deploy`,
  then tags `v<version>`. If the tag push fails after a successful deploy, a re-run computes the same number and
  fails with `409`: do not re-run, push the missing tag by hand
  (`git tag -a v<version> -m "platform-bom <version>" <sha> && git push origin v<version>`), then re-run.
  `-DdeployAtEnd=true` uploads only after every module built, but the upload itself is still module by module.
  If it fails halfway (for example `platform-parent` after `platform-bom` went up), a re-run computes the same
  number and gets `409` on the modules already uploaded. Recover the same way: push the tag `v<version>` for the
  failed run's SHA by hand, then re-run, which publishes the next number. The half-published
  version may be incomplete, so a service must not adopt it (Dependabot reads the registry, so it can offer it:
  close that PR).

## Dependabot and the synced properties

`lombok.version` and `mapstruct.version` in `platform-parent` are referenced only inside
`annotationProcessorPaths`, which Dependabot's Maven parser may not treat as a dependency. A Lombok or MapStruct
bump can then touch only `platform-dependencies` and fail `scripts/check-synced-properties.sh`. CI catches it and
nothing ships wrong, but the PR needs a manual edit of the same property in `platform-parent/pom.xml`.

## Local token setup

GitHub's Maven registry needs a token even to read (anonymous requests get `401`, also for public repositories).
Use a classic personal access token with `read:packages`:

```bash
gh auth refresh -h github.com -s read:packages        # interactive, once
export GITHUB_ACTOR=<your-login> GITHUB_TOKEN="$(gh auth token)"
```

and a `github` server entry in `~/.m2/settings.xml`:

```xml
<server>
  <id>github</id>
  <username>${env.GITHUB_ACTOR}</username>
  <password>${env.GITHUB_TOKEN}</password>
</server>
```

The repository is public, so any classic token with `read:packages` reads it. A consuming repository's CI needs
no setup: it reads with its own `GITHUB_TOKEN` and `packages: read`. Dependabot cannot use that token and takes a
classic personal access token as a repository-level Dependabot secret (see `docs/service-template.md`).
