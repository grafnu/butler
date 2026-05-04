Prefer python unless otherwise specified.
mqtt and mosquitto are available on the system.

If the BUTLER_CONN_SPEC env variable is defined, it should use that as the connectivity specificaiton passed in to all tools.
The tools should not use BUTLER_CONN_SPEC directly, but rather the caller should explicitly add it to the command line.
It would conform to the `uufi.md` spec as defined. Otherwise, the tests should use `mqtt://<branchname>@localhost/` as
the specification, where `<branchname>` is the current git branch (defaulting to `unknown` if not in a git directory).

* For `mqtt` connections, the only valid hostname for testing is `localhost`
  * The `setup` utility should perform a connectivity check and start a local mqtt server if necessary.
* For `pubsub` connections, it can be assumed that the necessary authentication and cloud resources will already be setup.
  * The `setup` utility should perform a connectivity check but not try to change anything in the cloud.

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
