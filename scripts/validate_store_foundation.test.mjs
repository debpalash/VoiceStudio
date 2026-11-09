import assert from "node:assert/strict";
import { test } from "node:test";

import { bundledOnlyRuntimeBlockers } from "./validate_store_foundation.mjs";

test("bundled-only rejects executable runtime downloads regardless of approval status", () => {
  const blockers = bundledOnlyRuntimeBlockers([
    { id: "review", delivery: "runtime-download", status: "needs-review" },
    { id: "approved-download", delivery: "runtime-download", status: "approved" },
    { id: "excluded-model", delivery: "runtime-download", status: "not-bundled" },
    { id: "approved-bundle", delivery: "bundled", status: "approved" },
  ]);
  assert.deepEqual(
    blockers.map((component) => component.id),
    ["review", "approved-download"],
  );
});
