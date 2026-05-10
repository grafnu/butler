DO NOT ASK FOR CLARIFICATION. YOU ARE AN AUTONOMOUS AGENT WITHOUT THE ABILITY
TO SEEK ADVICE OR EXTERNAL INPUT.

In order to do a clean build of the system, do the following:

* Completely delete the `butler/` and `bin/` directories with `rm -rf butler/ bin/`
* Rebuild the app according to the specs in `spec/`.

This is an agent-driven process where the agent uses the markdown files in `spec/` as the requirements for generating the implementation.

Do not ask how to complete the task, and do not check if you should proceed.
Do not ask for clarification on any missing elements. If necessary,
add a "ASSUMED:" tag in the spec file where it was ambigous and you
needed to make a guess at the right thing to do.
Your job is to figure everything out yourself and then generate a commit
or PR that encapsulates the end result. Don't ask for permission or
if anything is acceptable.
