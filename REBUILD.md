In order to do a clean build of the system, do the following:

* Completely delete the `butler/` and `bin/` directories with `rm -rf butler/ bin/`
* Rebuild the app according to the specs in `spec/`. This is an agent-driven process where the agent uses the markdown files in `spec/` as the requirements for generating the implementation.
