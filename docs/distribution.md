# Mouvadah distribution

Mouvadah Community is a self-hosted human-agent control and memory plane. The
current release stage is alpha. Version `0.1.1` is available as a checksummed
GitHub Release, a Homebrew-installed release command, and a separately
installable Python MCP bridge.

## Editions and licenses

| Offering | Description | License or terms |
| --- | --- | --- |
| Mouvadah Community | Complete self-hosted API and web application | AGPL-3.0-only |
| Mouvadah MCP | Connector for local and hosted agent harnesses | Apache-2.0 |
| Mouvadah Cloud | Operated hosting, upgrades, backups, integrations, and support | Service terms when available |

The repository’s root [`LICENSE`](../LICENSE) applies to the API, web
application, deployment materials, and other files unless a file says
otherwise. The contents of [`mcp/`](../mcp/) use the separate
[`mcp/LICENSE`](../mcp/LICENSE). The software licenses grant copyright
permissions, not rights to Mouvadah branding; see
[`TRADEMARKS.md`](../TRADEMARKS.md).

## Hosted use — no installation

To use Claude on web or mobile, connect to `https://mouvadah.com/mcp` and
sign in through OAuth. No package, API key, or local server is required.
Follow the [hosted connector guide](../mcp/README.md#hosted-connector--claude-web-desktop-and-mobile).

## Install the packaged Community release

The full application requires Docker with Compose v2:

```bash
brew install andrewb1234/tap/mouvadah
mouvadah install --email you@example.com --name "Your Name"
```

The same `mouvadah` command is available as a checksummed asset on the
[`v0.1.1` GitHub Release](https://github.com/andrewb1234/mouvadah/releases/tag/v0.1.1).
It downloads a checksummed Compose manifest and versioned API/web images, binds
both services to loopback, and preserves data on uninstall.

Desktop clients that use stdio can install the MCP bridge independently.
It requires an API key from the local or hosted server it connects to:

```bash
pipx install mouvadah-mcp==0.1.1
```

## Install from source

The supported alpha path requires Python 3.12 or newer and Node.js 20 or
newer:

```bash
git clone https://github.com/andrewb1234/mouvadah.git
cd mouvadah
python3 bootstrap.py
```

The bootstrap creates an isolated environment, applies migrations, provisions
a local owner and API key, and prints commands for starting the API, web
application, and MCP bridge. See the repository [`README`](../README.md) for
the full local and Docker workflows.

## Packaged release contents

Each version tag builds:

- the `mouvadah-mcp` wheel and source archive;
- a local installer and Docker Compose manifest;
- multi-architecture (`linux/amd64` and `linux/arm64`) API and web container
  images, referenced by immutable digest in the published Compose manifest;
- license, trademark, and third-party notices; and
- SHA-256 checksums, SBOMs, and build-provenance attestations.

`npx` is not a primary channel because the connector is Python and the
application is a multi-container service.

## Retraction

Published open-source copies cannot be recalled. A release or container tag
can be removed from its original channel, and a Python release can be yanked,
but existing downloads and valid license grants remain. Security or packaging
problems are corrected with a documented advisory and a new version rather
than silently replacing an immutable release.
