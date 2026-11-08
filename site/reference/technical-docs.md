---
title: Technical documentation
parent: Reference
nav_order: 5
description: Links to the maintained technical documents on GitHub, for administrators, reviewers and developers.
permalink: /reference/technical-docs/
---

# Technical documentation

Where to find the maintained technical documents behind this site.
{: .fs-5 .fw-300 }

This site is written for people using opncafyr. The documents below are written for administrators, reviewers and developers. They live in the project's GitHub repository and are kept up to date with the code. Some of them cite the project's internal issue numbers.

| Document | What it is for |
|---|---|
| [Specification](https://github.com/jprybylski/opncafyr/blob/main/docs/spec.md) | The design intent: what the workspace is for, how the parts fit together, and the rules it must keep. |
| [Architecture](https://github.com/jprybylski/opncafyr/blob/main/docs/architecture.md) | How the app is layered, its security boundaries, and the exact layout of a study folder and a run. |
| [Decision records](https://github.com/jprybylski/opncafyr/tree/main/docs/decisions) | Each accepted design decision and its reasons: platforms, data conventions (BLQ, units, exclusions, checks), templates and identifiable data, how opnca-r is installed, the interface stack. |
| [Installation](https://github.com/jprybylski/opncafyr/blob/main/docs/install.md) | Every installer command and option, what goes where, how an install runs and is verified, offline bundles and internal mirrors. |
| [Offline operation](https://github.com/jprybylski/opncafyr/blob/main/docs/offline.md) | Every point where installing or running opncafyr could reach the network, and how each is closed. |
| [Publishing](https://github.com/jprybylski/opncafyr/blob/main/docs/publishing.md) | How publications, description files (metadata sidecars), report renders and deck builds work, and what each records. |
| [Adding a tool](https://github.com/jprybylski/opncafyr/blob/main/docs/adding-a-tool.md) | For developers: how to add another tool to the workspace without changing its core. |
| [Dependency graph](https://github.com/jprybylski/opncafyr/blob/main/docs/dependency-graph.md) | Which component needs which runtime and package, and why, at the pinned versions. |
| [Changelog](https://github.com/jprybylski/opncafyr/blob/main/CHANGELOG.md) | What changed in each version, including changes not yet released. |

## The in-app Guide

The app has its own user guide: select **Guide** in the left navigation, or **Read the guide** on **Home**. It covers the same tasks as this site, ships with the app and works without a network connection. Its source is in [docs/user-guide](https://github.com/jprybylski/opncafyr/tree/main/docs/user-guide) on GitHub.

{: .research }
> **Research use only.** This workspace has not been validated for clinical or regulatory decisions.
