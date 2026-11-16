# Actual pre-checkpoint editor snapshots

These synthetic fixtures were captured from the real `ProductionScreen` on main
`2bc` before draft checkpoints, through `SaveableStateRegistry.performSave()`.
The legacy screen source SHA-256 was
`a3d3c15007890acc0b4b58fc147a4462aca351a28841984d58ead191e2bb9da5`.
The capture harness source SHA-256 was
`77c4c3621e33b0c31ac02a0d90b2fff308b897a9b042766e9f1ef3d818b4d50e`.

The unchanged harness called the old and current production screens at the same
composition callsite in separate Gradle compilations. It cleared the project
name and entered the short synthetic manuscript through the actual text fields;
the manuscript was absent from the named-project library. Both old-screen
capture tests and both current-screen restoration tests passed. The subsequent
public-registry probe confirmed that the real old project value was consumed,
then saved as its UUID. No production key compatibility patch was needed.

The complete public map is encoded as portable JSON, preserving list order,
primitive types, and the public `MutableState` envelope. No registry key or
project value was fabricated. UUIDs and timestamps belong only to this synthetic
capture. There are no user documents, credentials, device paths or model files.

`ProductionUpgradeSavedStateTest` also checks an empty registry and omission of
only the typed old project value, proving that disk or retained ViewModels cannot
supply the expected manuscript. Keep its composition callsite unchanged when
editing the harness. The byte prefix through that callsite has SHA-256
`b37f556050503ced604d350f4dcb51d96e83c0337cb1e988662e54fd7334542b`.

To regenerate, compile that same harness against the actual legacy screen at its
original package/path in an isolated test tree, then run the filtered class with
`VOICESTUDIO_CAPTURE_PRODUCTION_UPGRADE` pointing to a temporary directory.
Copy both generated JSON files here, unset the capture variable, compile the
current screen, and run the class again. Do not substitute a helper-only Saver or
predict the compiler-generated key. These tests use Robolectric, not an emulator,
model inference, or real application data.
