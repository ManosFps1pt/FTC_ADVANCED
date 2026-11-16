# Java recorder fixture

`java-v3.ftclog` was emitted by ftclogger's `LocalMatchRecorderTest`, using the
production Java recorder rather than the Python writer. It contains a v3 hello
for `java-fixture` with record timestamp 123 and a valid CRC.

Regenerate by running `testReleaseUnitTest` in ftclogger and copying
`build/fixtures/java.ftclog` here. The Python release-safety tests read this file,
plus a separately mutated legacy-header fixture and a corrupted-CRC case.
