# Copyright 2026 blue_lobster
# SPDX-License-Identifier: Apache-2.0

"""Console entry for the macOS app's bundled management helper."""
from multiprocessing import freeze_support
import sys

if __name__ == '__main__':
    freeze_support()
    mode = sys.argv[1] if len(sys.argv) > 1 else None
    if mode == 'dev':
        from abralia.developer.cli import main
        raise SystemExit(main(sys.argv[2:]))
    elif mode == 'terminal-focus':
        from abralia.backend.terminal_focus import main
        raise SystemExit(main())
    elif mode == 'mcp':
        from abralia.backend.mcp import main
        raise SystemExit(main(sys.argv[2:]))
    elif mode == 'codex-hook':
        from abralia.backend.codex_hook import main
        raise SystemExit(main(sys.argv[2:]))
    elif mode == 'serve':
        # Legacy terminal CLI retains its command name in sys.argv.
        from abralia.backend.cli import main
        raise SystemExit(main())
    else:
        from abralia.backend.gui_host import main
        main()
