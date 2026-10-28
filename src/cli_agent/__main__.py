import sys

from cli_agent.cli import main

# The guard matters: search_files spawns a child process, which re-imports the
# main module and must not run the CLI again.
if __name__ == "__main__":
    sys.exit(main())
