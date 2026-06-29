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
assert.strictEqual(script.isSupportedCaptureWindow_("past_7_days"), true);
assert.strictEqual(script.isSupportedCaptureWindow_("next_2_days"), true);
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
    run_id: "run-1",
    capture_window: "past_3_days",
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
assert.strictEqual(row[script.RAW_HEADERS.indexOf("capture_window")], "past_3_days");

console.log("Apps Script helper tests passed");
