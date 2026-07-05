const assert = require("assert");
const fs = require("fs");
const path = require("path");
const vm = require("vm");

const moduleShim = { exports: {} };
const context = {
  module: moduleShim,
  console,
  Date,
  JSON,
  Number,
  String,
  Object,
  Error,
  Math,
};
vm.runInNewContext(
  fs.readFileSync(path.join(__dirname, "../integrations/apps_script/Code.gs"), "utf8"),
  context
);
const script = moduleShim.exports;

class MockRange {
  constructor(sheet, row, column, rowCount, columnCount) {
    this.sheet = sheet;
    this.row = row;
    this.column = column;
    this.rowCount = rowCount;
    this.columnCount = columnCount;
  }

  getValues() {
    const values = [];
    for (let rowOffset = 0; rowOffset < this.rowCount; rowOffset += 1) {
      const source = this.sheet.rows[this.row - 1 + rowOffset] || [];
      const row = [];
      for (let columnOffset = 0; columnOffset < this.columnCount; columnOffset += 1) {
        const value = source[this.column - 1 + columnOffset];
        row.push(value === undefined ? "" : value);
      }
      values.push(row);
    }
    return values;
  }

  setValues(values) {
    values.forEach((valueRow, rowOffset) => {
      const targetRow = this.row - 1 + rowOffset;
      while (this.sheet.rows.length <= targetRow) {
        this.sheet.rows.push([]);
      }
      valueRow.forEach((value, columnOffset) => {
        this.sheet.rows[targetRow][this.column - 1 + columnOffset] = value;
      });
    });
  }
}

class MockSheet {
  constructor(headers = []) {
    this.rows = headers.length ? [headers.slice()] : [];
  }

  getLastColumn() {
    return this.rows.reduce((maximum, row) => Math.max(maximum, row.length), 0);
  }

  getLastRow() {
    return this.rows.length;
  }

  getRange(row, column, rowCount, columnCount) {
    return new MockRange(this, row, column, rowCount, columnCount);
  }

  appendRow(row) {
    this.rows.push(row.slice());
  }
}

class MockSpreadsheet {
  constructor() {
    this.sheets = {};
  }

  getSheetByName(name) {
    return this.sheets[name] || null;
  }

  insertSheet(name) {
    const sheet = new MockSheet();
    this.sheets[name] = sheet;
    return sheet;
  }
}

function useSpreadsheet(spreadsheet) {
  context.PropertiesService = {
    getScriptProperties() {
      return {
        getProperty(name) {
          return name === "JORDANA_SPREADSHEET_ID" ? "spreadsheet-id" : "test-api-key";
        },
      };
    },
  };
  context.SpreadsheetApp = {
    openById() {
      return spreadsheet;
    },
  };
}

function event(id) {
  return {
    calendar_event_id: id,
    event_title: "Sanitized Client | 60 | Telehealth",
    start_at: `2026-07-05T1${id}:00:00-04:00`,
    end_at: `2026-07-05T1${id}:50:00-04:00`,
  };
}

function runLogDataRows(sheet) {
  return sheet.rows.slice(1);
}

function cells(row, start, end) {
  return Array.prototype.slice.call(row, start, end);
}

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

let runLogSheet = new MockSheet(script.RUN_LOG_HEADERS);
context.SpreadsheetApp = {
  openById() {
    throw new Error("modern aggregate run_complete should not reread Raw_Event_Snapshots");
  },
};
let aggregateResponse = script.handleAggregateRunComplete_(
  runLogSheet,
  {
    run_id: "modern-run",
    batch_name: "normal",
    past_events_found: 2,
    past_events_received: 2,
    future_events_found: 3,
    future_events_received: 2,
  },
  "modern-run"
);
assert.strictEqual(aggregateResponse.status, "partial");
assert.deepStrictEqual(cells(runLogDataRows(runLogSheet)[0], 4, 9), [2, 2, 3, 2, "partial"]);

runLogSheet = new MockSheet(script.RUN_LOG_HEADERS);
aggregateResponse = script.handleAggregateRunComplete_(
  runLogSheet,
  {
    run_id: "zero-run",
    past_events_found: 0,
    past_events_received: 0,
    future_events_found: 0,
    future_events_received: 0,
  },
  "zero-run"
);
assert.strictEqual(aggregateResponse.status, "complete");
assert.deepStrictEqual(cells(runLogDataRows(runLogSheet)[0], 4, 9), [0, 0, 0, 0, "complete"]);

const legacySpreadsheet = new MockSpreadsheet();
legacySpreadsheet.sheets.Raw_Event_Snapshots = new MockSheet(script.RAW_HEADERS);
legacySpreadsheet.sheets.Raw_Event_Snapshots.appendRow(
  script.rawRow_(
    { run_id: "legacy-run", capture_window: "past_3_days", batch_name: "legacy" },
    event("1"),
    0,
    "2026-07-05T12:00:00.000Z"
  )
);
legacySpreadsheet.sheets.Raw_Event_Snapshots.appendRow(
  script.rawRow_(
    { run_id: "legacy-run", capture_window: "next_7_days", batch_name: "legacy" },
    event("2"),
    0,
    "2026-07-05T12:00:01.000Z"
  )
);
useSpreadsheet(legacySpreadsheet);
runLogSheet = new MockSheet(script.RUN_LOG_HEADERS);
aggregateResponse = script.handleAggregateRunComplete_(
  runLogSheet,
  {
    run_id: "legacy-run",
    batch_name: "legacy",
    past_events_found: 1,
    future_events_found: 1,
  },
  "legacy-run"
);
assert.strictEqual(aggregateResponse.status, "complete");
assert.deepStrictEqual(cells(runLogDataRows(runLogSheet)[0], 4, 9), [1, 1, 1, 1, "complete"]);

aggregateResponse = script.handleAggregateRunComplete_(
  runLogSheet,
  {
    run_id: "legacy-run",
    batch_name: "legacy-retry",
    past_events_found: 1,
    future_events_found: 1,
  },
  "legacy-run"
);
assert.strictEqual(aggregateResponse.status, "complete");
assert.strictEqual(runLogDataRows(runLogSheet).length, 1);
assert.strictEqual(runLogDataRows(runLogSheet)[0][1], "legacy-retry");

const batchSpreadsheet = new MockSpreadsheet();
useSpreadsheet(batchSpreadsheet);
let batchResponse = script.handleCalendarBatch_({
  run_id: "batch-run",
  batch_name: "normal",
  capture_window: "past_3_days",
  captured_at: "2026-07-05T12:00:00.000Z",
  events: [event("1"), event("2")],
});
assert.strictEqual(batchResponse.received, 2);
let batchRunLogRows = runLogDataRows(batchSpreadsheet.sheets.Run_Log);
assert.strictEqual(batchRunLogRows.length, 1);
assert.deepStrictEqual(cells(batchRunLogRows[0], 4, 9), [0, 2, 0, 0, "partial"]);
assert.strictEqual(batchRunLogRows[0][9], "Awaiting final run_complete.");

batchResponse = script.handleCalendarBatch_({
  run_id: "batch-run",
  batch_name: "normal",
  capture_window: "next_7_days",
  captured_at: "2026-07-05T12:00:10.000Z",
  events: [event("3")],
});
assert.strictEqual(batchResponse.received, 1);
batchRunLogRows = runLogDataRows(batchSpreadsheet.sheets.Run_Log);
assert.deepStrictEqual(cells(batchRunLogRows[0], 4, 9), [0, 2, 0, 1, "partial"]);

batchResponse = script.handleCalendarBatch_({
  run_id: "batch-run",
  batch_name: "normal",
  capture_window: "past_3_days",
  captured_at: "2026-07-05T12:00:20.000Z",
  events: [event("1"), event("2")],
});
assert.strictEqual(batchResponse.received, 0);
assert.strictEqual(batchSpreadsheet.sheets.Raw_Event_Snapshots.getLastRow(), 4);
batchRunLogRows = runLogDataRows(batchSpreadsheet.sheets.Run_Log);
assert.deepStrictEqual(cells(batchRunLogRows[0], 4, 9), [0, 2, 0, 1, "partial"]);

const syncResponse = script.handleSyncRequest_({
  after_ingested_at: "1970-01-01T00:00:00.000Z",
  limit: 10,
});
assert.strictEqual(syncResponse.record_type, "sync_response");
assert.strictEqual(syncResponse.rows.length, 3);
assert.strictEqual(syncResponse.rows[0].run_id, "batch-run");

const missingRunLogSpreadsheet = new MockSpreadsheet();
missingRunLogSpreadsheet.sheets.Raw_Event_Snapshots = batchSpreadsheet.sheets.Raw_Event_Snapshots;
useSpreadsheet(missingRunLogSpreadsheet);
const missingRunLogSyncResponse = script.handleSyncRequest_({
  after_ingested_at: "1970-01-01T00:00:00.000Z",
  limit: 10,
});
assert.strictEqual(missingRunLogSyncResponse.rows.length, 3);
assert.strictEqual(missingRunLogSpreadsheet.sheets.Run_Log, undefined);

console.log("Apps Script helper tests passed");
