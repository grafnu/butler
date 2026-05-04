Prefer python unless otherwise specified.
mqtt and mosquitto are available on the system.

If the BUTLER_CONN_SPEC env variable is defined, it should use that as the connectivity specificaiton for all tools.
It would conform to the `uufi.md` spec as defined. Otherwise, the tests should use `mqtt://<branchname>@localhost/` as
the specification, where `<branchname>` is the current git branch (defaulting to `unknown` if not in a git directory).

The top-level files should not be removed or altered.
* README.md
* AGENTS.md
* BUTLER.md
* MERGER.md
* .wincolor
* .gitignore

The result of all work should be contained within the following directories:

* bin/
* butler/
