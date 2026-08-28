# Abralia public-repository instructions

## Repository scope

- This directory is the root of the public Abralia source repository.
- `abralia/firmware/` contains firmware source.
- `abralia/desktop/` is the root for desktop-side host source. Do not choose its
  language, runtime, packaging, or UI framework until the maintainer resolves
  the corresponding project decisions.
- `experiments/` contains bounded prototypes and does not select a production
  host language or architecture.

## License boundaries

- Follow `LICENSE.md` and the full texts in `LICENSES/`.
- Abralia-authored firmware under `abralia/firmware/` is
  `GPL-3.0-or-later`, except where a file preserves another compatible
  upstream notice.
- Abralia-authored desktop, host, protocol, experiment, documentation, and
  root-level project files are `Apache-2.0`, unless a file states otherwise.
- Do not change these license assignments or introduce an additional license
  without explicit maintainer approval and a corresponding decision-record
  update.

## Source-file notices

- New Abralia-authored firmware `.c`, `.h`, and source include files must use:

  ```c
  // Copyright <year> blue_lobster
  // SPDX-License-Identifier: GPL-3.0-or-later
  ```

- New Abralia-authored desktop, host-tool, protocol, and experiment source
  files must use the comment syntax appropriate to the language and:

  ```text
  Copyright <year> blue_lobster
  SPDX-License-Identifier: Apache-2.0
  ```

- Keep shebangs as the first line of executable scripts and place the notice
  immediately after the shebang.
- Formats that do not permit comments, including strict JSON, are covered by
  the directory rules in `LICENSE.md`; do not add invalid comment syntax.

## Upstream and GPL preservation

- Never remove or replace Keychron, QMK, ChibiOS, or other third-party
  copyright, attribution, warranty, or license notices.
- Add a `blue_lobster` modification notice only to files containing
  copyrightable Abralia changes. Do not claim ownership of unchanged upstream
  material.
- Keep firmware dependencies GPL-compatible. Do not copy GPL-covered firmware
  implementation code into the Apache-2.0 desktop tree.
- Desktop software may communicate with firmware through the documented USB
  protocol. If code is copied, shared, linked, or generated across the
  firmware/desktop license boundary, stop and review the resulting license
  obligations before continuing.
- Do not publish a firmware binary unless the exact Abralia source, pinned
  upstream source revision and submodules, build instructions, retained
  notices, and other required corresponding-source material are available for
  that release.

## Documentation

- Every public README that describes a component must state its applicable
  license or link to the repository-root `LICENSE.md`.
- Record third-party code and assets with their source, version, license, and
  retained notice when they are added.
