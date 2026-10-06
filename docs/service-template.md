# Service template: inheriting platform-bom

How a BiciKletka Java service (root `pom.xml`, Dockerfile, compose, CI, Dependabot) consumes
`BiciKletka/platform-bom`. The repository is public, but GitHub Packages needs a token even to read, so every
place that builds a service needs credentials; the sections below name each one. CI reads with the workflow's own
`GITHUB_TOKEN`; Dependabot and laptops use a token with `read:packages` (`gh auth token` after
`gh auth refresh -s read:packages`, or a classic personal access token).

## Parent and registry

The root `pom.xml` names `platform-parent` by a literal, published version (a `v*` tag of this repository) and
the registry it comes from. A POM's own `<repositories>` resolves its parent on Maven 3.9, so `settings.xml`
needs only the credential.

```xml
<parent>
  <groupId>io.github.bicikletka</groupId>
  <artifactId>platform-parent</artifactId>
  <version>0.1.1</version> <!-- bumped by Dependabot -->
  <relativePath/>
</parent>

<repositories>
  <!-- Central first: POM-declared repositories are tried before the super-POM's central, so without this
       every artifact is looked up (and mostly 404s) in the GitHub registry first. Resolution is identical,
       with about 4x fewer registry lookups on a cold repository. -->
  <repository>
    <id>central</id>
    <url>https://repo.maven.apache.org/maven2</url>
    <snapshots><enabled>false</enabled></snapshots>
  </repository>
  <repository>
    <id>github</id>
    <url>https://maven.pkg.github.com/BiciKletka/platform-bom</url>
    <snapshots><enabled>false</enabled></snapshots>
  </repository>
</repositories>
```

Remove from the service's POM everything the parent now owns: the `spring-boot-dependencies`, Jackson and AWS
BOM imports, the Tomcat entries, the Lombok, MapStruct, springdoc, Paho and ArchUnit versions, and the
compiler, resources, Surefire and `spring-boot-maven-plugin` management. Remove the leftover
`maven.compiler.source` / `maven.compiler.target` properties too (root and modules): the parent sets
`maven.compiler.release`. Keep only service-specific pins,
each with a comment saying why, and module plugins (executions, `argLine`). A module that must target another
Java release (the bifromq plugin) redefines the `java.version` property; one that must not inherit Boot's
versions pins its own.

A module that declares its own compiler `annotationProcessorPaths` replaces the parent's list **by position**
(Maven merges the elements by index), and a `<path>` without a `<version>` takes the version of the parent's entry
at the same index: a lone `mapstruct-processor` would be resolved at Lombok's `1.18.46` and fail. Always give
every module-level path an explicit version, for example `${mapstruct.version}` (the parent defines
`lombok.version` and `mapstruct.version`). `annotationProcessorPathsUseDepMgmt` was tried and does not change
this: with it on, the same consumer still asked for `mapstruct-processor:1.18.46`.

Commit `.mvn/ci-settings.xml`, a credential template and never a credential:

```xml
<settings xmlns="http://maven.apache.org/SETTINGS/1.0.0">
  <servers>
    <server>
      <id>github</id>
      <username>${env.GITHUB_ACTOR}</username>
      <password>${env.GITHUB_TOKEN}</password>
    </server>
  </servers>
</settings>
```

Docker builds pass `-s .mvn/ci-settings.xml` with the two variables set. GitHub Actions jobs use the settings
`actions/setup-java` generates (`server-id`, `server-username`, `server-password`, see CI below) and need no
`-s`. A laptop or omen keeps the same `<server>` in `~/.m2/settings.xml`.

## Dockerfile

The parent comes from the registry, so the image build needs the token. It enters as a BuildKit secret, never
an `ARG` (an `ARG` is recorded in the image history). Every Dockerfile stage that runs Maven needs the mount, not
only the main service's: mqtt-gateway's `bifromq-auth-plugin/Dockerfile` `builder` stage runs
`mvn -f bifromq-auth-plugin/pom.xml`, which reads the root POM and so the parent, and its build context must
include the root `pom.xml` and `.mvn/ci-settings.xml`:

```dockerfile
# syntax=docker/dockerfile:1
FROM maven:3.9-eclipse-temurin-21 AS builder
WORKDIR /build
ARG GITHUB_ACTOR=docker-build
COPY .mvn/ci-settings.xml .mvn/ci-settings.xml
COPY pom.xml .
# ... COPY each module's pom.xml
RUN --mount=type=secret,id=gh_token,env=GITHUB_TOKEN \
    mvn -B -s .mvn/ci-settings.xml -pl <svc>-api -am dependency:go-offline
# ... COPY each module's src
RUN --mount=type=secret,id=gh_token,env=GITHUB_TOKEN \
    mvn -B -s .mvn/ci-settings.xml -pl <svc>-api -am clean package -DskipTests
```

`docker build --secret id=gh_token,env=GITHUB_TOKEN --build-arg GITHUB_ACTOR=<your-login> .` builds it by
hand; CI passes the real actor (below) rather than relying on the registry ignoring the username. In `docker-compose.yml`:

```yaml
services:
  <svc>:
    build:
      context: .
      secrets: [gh_token]
secrets:
  gh_token:
    environment: GITHUB_TOKEN        # or `file: ${GH_TOKEN_FILE}` on a host that keeps the token in a file
```

## CI (GitHub Actions)

The workflow's own `GITHUB_TOKEN` reads the package: this repository is public, so no per-package or
per-repository grant exists or is needed. The job needs `packages: read` (`write` where it also pushes to ghcr).

```yaml
permissions:
  contents: read
  packages: read
steps:
  - uses: actions/setup-java@v5
    with:
      distribution: temurin
      java-version: '21'
      cache: maven
      server-id: github
      server-username: GITHUB_ACTOR
      server-password: GITHUB_TOKEN
  - run: mvn -B verify
    env:
      GITHUB_ACTOR: ${{ github.actor }}
      GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
  - uses: docker/build-push-action@v6   # v7 also accepts `secrets:`; bump or keep v6
    with:                              # every build-push-action step, see below
      build-args: |
        GITHUB_ACTOR=${{ github.actor }}
      secrets: |
        gh_token=${{ secrets.GITHUB_TOKEN }}
```

Workflows to change in each service: `pr-validation` (build, OSV and Qodana jobs), `staging-deployment`, and
`dependency-graph.yml` (mqtt-gateway, bici-media, remotemonit-gateway). Add the `build-args` and `secrets` above
to **every** `docker/build-push-action` step: staging builds an image on every merge (`build-and-push`) and has a
second step for the release-tag fallback (`release-tag`); a step left out fails with `401` the first time it runs.
`production-deployment` promotes by digest and builds nothing, so it needs no change. The dependency graph job runs Maven with only `contents: write` today; it also needs
`packages: read` and the `setup-java` server credential above, or it fails with `401` on `platform-parent`.

### Qodana

The `qodana` job's IntelliJ Maven import also needs `platform-parent`, and the container has no credential (the
registry answers `401` anonymously), so the import cannot resolve it and "No new problems" says less than it
looks. Give the job `packages: read`, pass the credential into the container, and install the settings file there:

```yaml
  qodana:
    permissions:
      contents: read
      packages: read
      pull-requests: write
      checks: write
    steps:
      - uses: JetBrains/qodana-action@v2026.1
        with:
          args: --env,GITHUB_ACTOR=${{ github.actor }},--env,GITHUB_TOKEN=${{ secrets.GITHUB_TOKEN }}
```

```yaml
# qodana.yaml
bootstrap: mkdir -p ~/.m2 && cp .mvn/ci-settings.xml ~/.m2/settings.xml
```

The log does not say whether the import resolved the parent (`upload-result: false`), which is why this is
applied without a before/after report. A repository whose Qodana job already shows a resolved import can skip it.

## Dependabot

Each service keeps `platform-parent` current with `.github/dependabot.yml`, read from the default branch. A
Dependabot run has no `GITHUB_TOKEN` it can read packages with, so the registry credentials are
**repository-level Dependabot secrets** `PACKAGES_READ_USER` and `PACKAGES_READ_TOKEN` (a classic personal
access token with `read:packages`), set in each service repository (Settings, Secrets and variables, Dependabot).
Do not use org-level secrets: they are unreliable for private repositories on the Free plan.

```yaml
version: 2
registries:
  platform-bom:
    type: maven-repository
    url: https://maven.pkg.github.com/BiciKletka/platform-bom
    username: ${{secrets.PACKAGES_READ_USER}}
    password: ${{secrets.PACKAGES_READ_TOKEN}}
updates:
  - package-ecosystem: maven
    directory: /
    registries:
      - platform-bom
    schedule:
      interval: daily
    allow:
      - dependency-name: io.github.bicikletka:platform-parent
    open-pull-requests-limit: 2
    commit-message:
      prefix: build
    labels:
      - dependencies
```

`allow` limits it to the parent: every other version bump is made once, in platform-bom. A series bump
(`0.1` to `0.2`) arrives like any other version and is breaking while the platform is at `0.x`; its CI result is
the signal.

## Local setup (laptop, omen)

```bash
gh auth refresh -h github.com -s read:packages          # laptop, once
export GITHUB_ACTOR=<your-login> GITHUB_TOKEN="$(gh auth token)"
```

and the `github` `<server>` entry above in `~/.m2/settings.xml` (the token needs `read:packages`). On omen the token lives in a file the user
creates and agents never read; the build runner and `redeploy.sh` load it into `GITHUB_TOKEN` for the Maven
and `docker compose build` calls. Check access (prints `200`):

```bash
curl -s -o /dev/null -w '%{http_code}\n' -u "$GITHUB_ACTOR:$GITHUB_TOKEN" \
  https://maven.pkg.github.com/BiciKletka/platform-bom/io/github/bicikletka/platform-parent/0.1.1/platform-parent-0.1.1.pom
```

Hosts that only run services (the Hetzner staging box) pull the images CI pushed to ghcr and need no
`read:packages` token.
