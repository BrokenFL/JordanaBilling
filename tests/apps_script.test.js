const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const moduleShim = { exports: {} };
vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, "../integrations/apps_script/Code.gs"), "utf8"),
  {
    module: moduleShim,
    console,
    Date,
    JSON,
    Number,
    String,
    Object,
    Error,
  }
);
const script = moduleShim.exports;

assert.strictEqual(script.isPastCaptureWindow_("past_3_days"), true);
assert.strictEqual(script.isFutureCaptureWindow_("next_7_days"), true);
assert.strictEqual(script.isBackfillCaptureWindow_(script.BACKFILL_CAPTURE_WINDOW), true);
assert.strictEqual(script.isBackfillCaptureWindow_("june_2026_backfill"), true);
assert.strictEqual(
  script.canonicalCaptureWindow_("june_2026_backfill"),
  script.BACKFILL_CAPTURE_WINDOW
);
assert.strictEqual(script.isSupportedCaptureWindow_("past_7_days"), true);
assert.strictEqual(script.isSupportedCaptureWindow_("next_2_days"), true);
assert.strictEqual(script.isSupportedCaptureWindow_("june_2026_backfill_noop"), true);
assert.strictEqual(script.isSupportedCaptureWindow_("legacy"), true);
assert.strictEqual(script.isSupportedCaptureWindow_("unsupported"), false);

assert.strictEqual(script.runStatus_(["run", "", "", "", 2, 2, 3, 3], "next_7_days"), "complete");
assert.strictEqual(script.runStatus_(["run", "", "", "", 2, 1, 3, 3], "next_7_days"), "partial");
assert.strictEqual(
  script.runStatus_(["run", "", "", "", 2, 2, 0, 0], script.BACKFILL_CAPTURE_WINDOW),
  "complete"
);
assert.strictEqual(
  script.runStatus_(["run", "", "", "", 2, 1, 0, 0], script.BACKFILL_CAPTURE_WINDOW),
  "partial"
);

const row = script.rawRow_(
  {
    client_run_key: "run-1",
    capture_window: "june_2026_backfill",
    batch_name: "batch",
    timezone: "America/New_York",
    payload_version: "2",
  },
  {
    calendar_event_id: "event-1",
    event_fingerprint: "fp-1",
    event_title: "Demo Client | 60 | Phone",
    start_at: "2026-06-12T17:00:00-04:00",
    end_at: "2026-06-12T18:00:00-04:00",
    api_key: "must-not-persist",
  },
  0,
  "2026-06-22T02:00:00.000Z"
);
const rawJson = row[script.RAW_HEADERS.indexOf("raw_json")];
assert.strictEqual(rawJson.includes("must-not-persist"), false);
assert.strictEqual(row[script.RAW_HEADERS.indexOf("run_id")], "run-1");
assert.strictEqual(
  row[script.RAW_HEADERS.indexOf("capture_window")],
  script.BACKFILL_CAPTURE_WINDOW
);

const syncRows = script.syncRows_(
  [
    {
      ingested_at: "2026-06-22T02:03:00.000Z",
      snapshot_key: "partial-run-row",
      run_id: "run-without-complete-log",
    },
    {
      ingested_at: "2026-06-22T02:01:00.000Z",
      snapshot_key: "older-row",
      run_id: "older-run",
    },
    {
      ingested_at: "2026-06-22T02:03:00.000Z",
      snapshot_key: "another-row-same-time",
      run_id: "another-run",
    },
  ],
  "2026-06-22T02:00:00.000Z"
);
assert.deepStrictEqual(
  syncRows.map((syncRow) => syncRow.snapshot_key),
  ["older-row", "another-row-same-time", "partial-run-row"]
);

const sharedTimestamp = "2026-06-22T02:03:00.000Z";
const sharedTimestampRows = Array.from({ length: 625 }, (_, index) => ({
  ingested_at: sharedTimestamp,
  snapshot_key: `same-time-${String(index).padStart(4, "0")}`,
  run_id: "shared-timestamp-run",
}));
let cursor = script.syncCursorFromPayload_({
  after_ingested_at: "1970-01-01T00:00:00.000Z",
});
const fetchedKeys = [];
while (true) {
  const remainingRows = script.syncRows_(
    sharedTimestampRows,
    cursor.ingested_at,
    cursor.snapshot_key
  );
  const page = remainingRows.slice(0, 500);
  fetchedKeys.push(...page.map((syncRow) => syncRow.snapshot_key));
  cursor = script.syncNextCursor_(page, cursor);
  if (remainingRows.length <= page.length) {
    break;
  }
}
assert.strictEqual(fetchedKeys.length, sharedTimestampRows.length);
assert.strictEqual(new Set(fetchedKeys).size, sharedTimestampRows.length);
assert.deepStrictEqual(
  fetchedKeys,
  sharedTimestampRows.map((syncRow) => syncRow.snapshot_key)
);
assert.strictEqual(cursor.ingested_at, sharedTimestamp);
assert.strictEqual(cursor.snapshot_key, "same-time-0624");

const legacyTimestampOnlyRows = script.syncRows_(
  [
    {
      ingested_at: sharedTimestamp,
      snapshot_key: "same-time-a",
    },
    {
      ingested_at: sharedTimestamp,
      snapshot_key: "same-time-b",
    },
    {
      ingested_at: "2026-06-22T02:04:00.000Z",
      snapshot_key: "later-row",
    },
  ],
  sharedTimestamp
);
assert.deepStrictEqual(
  legacyTimestampOnlyRows.map((syncRow) => syncRow.snapshot_key),
  ["same-time-a", "same-time-b", "later-row"]
);

const pageCursor = script.syncNextCursor_(
  [
    {
      ingested_at: sharedTimestamp,
      snapshot_key: "cursor-key",
    },
  ],
  { ingested_at: "1970-01-01T00:00:00.000Z", snapshot_key: "" }
);
assert.strictEqual(pageCursor.ingested_at, sharedTimestamp);
assert.strictEqual(pageCursor.snapshot_key, "cursor-key");

const localeSensitiveRows = [
  {
    ingested_at: sharedTimestamp,
    snapshot_key: "prefix|2026-06-01",
  },
  {
    ingested_at: sharedTimestamp,
    snapshot_key: "prefix:2026-06-02",
  },
  {
    ingested_at: sharedTimestamp,
    snapshot_key: "prefix|2026-06-03",
  },
];
const localeSensitiveFirstPage = script.syncRows_(
  localeSensitiveRows,
  "1970-01-01T00:00:00.000Z",
  ""
).slice(0, 2);
const localeSensitiveCursor = script.syncNextCursor_(
  localeSensitiveFirstPage,
  { ingested_at: "1970-01-01T00:00:00.000Z", snapshot_key: "" }
);
const localeSensitiveSecondPage = script.syncRows_(
  localeSensitiveRows,
  localeSensitiveCursor.ingested_at,
  localeSensitiveCursor.snapshot_key
);
assert.deepStrictEqual(
  localeSensitiveFirstPage
    .concat(localeSensitiveSecondPage)
    .map((syncRow) => syncRow.snapshot_key),
  ["prefix:2026-06-02", "prefix|2026-06-01", "prefix|2026-06-03"]
);
assert.strictEqual(
  new Set(
    localeSensitiveFirstPage
      .concat(localeSensitiveSecondPage)
      .map((syncRow) => syncRow.snapshot_key)
  ).size,
  localeSensitiveRows.length
);

console.log("Apps Script helper tests passed");
