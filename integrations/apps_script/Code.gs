const RAW_SHEET_NAME = "Raw_Event_Snapshots";
const RUN_LOG_SHEET_NAME = "Run_Log";
const INGEST_API_KEY_PROPERTY = "INGEST_API_KEY";
const SPREADSHEET_ID_PROPERTY = "JORDANA_SPREADSHEET_ID";
const DEFAULT_SYNC_LIMIT = 500;
const EMPTY_SYNC_CURSOR = "1970-01-01T00:00:00.000Z";
const EMPTY_SNAPSHOT_KEY = "";
const BACKFILL_CAPTURE_WINDOW = "backfill_2026_06_01_through_2026_06_14";
const BACKFILL_CAPTURE_WINDOW_ALIASES = ["june_2026_backfill"];
const NOOP_CAPTURE_WINDOWS = ["june_2026_backfill_noop"];

const RAW_HEADERS = [
  "ingested_at",
  "snapshot_key",
  "run_id",
  "batch_name",
  "capture_window",
  "captured_at",
  "window_start",
  "window_end",
  "source_device",
  "timezone",
  "calendar_event_id",
  "event_fingerprint",
  "event_title",
  "start_at",
  "end_at",
  "duration_minutes",
  "location",
  "notes",
  "calendar",
  "payload_version",
  "raw_json",
];

const RUN_LOG_HEADERS = [
  "run_id",
  "batch_name",
  "started_at",
  "completed_at",
  "past_found",
  "past_received",
  "future_found",
  "future_received",
  "status",
  "error_message",
  "updated_at",
];

function doPost(e) {
  try {
    const payload = parsePayload_(e);
    authorize_(payload);
    const recordType = String(payload.record_type || "");
    if (recordType === "calendar_batch") {
      return jsonResponse_(handleCalendarBatch_(payload));
    }
    if (recordType === "run_complete") {
      return jsonResponse_(handleRunComplete_(payload));
    }
    if (recordType === "sync_request") {
      return jsonResponse_(handleSyncRequest_(payload));
    }
    return jsonResponse_({ ok: false, error: "Unsupported record type." });
  } catch (error) {
    const message = error && error.safeMessage ? error.safeMessage : "Request failed.";
    return jsonResponse_({ ok: false, error: message });
  }
}

function parsePayload_(e) {
  const contents = e && e.postData && e.postData.contents;
  if (!contents) {
    throw safeError_("Missing request body.");
  }
  try {
    const payload = JSON.parse(contents);
    if (!payload || typeof payload !== "object" || Array.isArray(payload)) {
      throw new Error("invalid");
    }
    return payload;
  } catch (error) {
    throw safeError_("Invalid JSON request.");
  }
}

function authorize_(payload) {
  const expected = scriptProperty_(INGEST_API_KEY_PROPERTY);
  if (!expected) {
    throw safeError_("Server configuration is incomplete.");
  }
  if (!payload || String(payload.api_key || "") !== expected) {
    throw safeError_("Unauthorized request.");
  }
}

function scriptProperty_(name) {
  return PropertiesService.getScriptProperties().getProperty(name);
}

function spreadsheet_() {
  const spreadsheetId = scriptProperty_(SPREADSHEET_ID_PROPERTY);
  if (!spreadsheetId) {
    throw safeError_("Server configuration is incomplete.");
  }
  return SpreadsheetApp.openById(spreadsheetId);
}

function handleCalendarBatch_(payload) {
  const captureWindow = canonicalCaptureWindow_(payload.capture_window);
  if (!isSupportedCaptureWindow_(captureWindow)) {
    return { ok: false, error: "Unsupported capture window." };
  }
  if (isNoopCaptureWindow_(captureWindow)) {
    return {
      ok: true,
      record_type: "calendar_batch_response",
      run_id: runId_(payload),
      capture_window: captureWindow,
      received: 0,
    };
  }
  const events = Array.isArray(payload.events) ? payload.events : [];
  const sheet = ensureSheet_(spreadsheet_(), RAW_SHEET_NAME, RAW_HEADERS);
  const existingKeys = existingSnapshotKeys_(sheet);
  const ingestedAt = new Date().toISOString();
  const rows = [];
  const normalizedPayload = Object.assign({}, payload, { capture_window: captureWindow });
  events.forEach((event, index) => {
    const row = rawRow_(normalizedPayload, event || {}, index, ingestedAt);
    const snapshotKey = row[RAW_HEADERS.indexOf("snapshot_key")];
    if (snapshotKey && existingKeys[snapshotKey]) {
      return;
    }
    rows.push(row);
    if (snapshotKey) {
      existingKeys[snapshotKey] = true;
    }
  });
  appendRows_(sheet, rows);
  return {
    ok: true,
    record_type: "calendar_batch_response",
    run_id: runId_(payload),
    capture_window: captureWindow,
    received: rows.length,
  };
}

function handleRunComplete_(payload) {
  const captureWindow = canonicalCaptureWindow_(payload.capture_window);
  if (captureWindow && !isSupportedCaptureWindow_(captureWindow)) {
    return { ok: false, error: "Unsupported capture window." };
  }
  const sheet = ensureSheet_(spreadsheet_(), RUN_LOG_SHEET_NAME, RUN_LOG_HEADERS);
  const runId = runId_(payload);
  if (!runId) {
    return { ok: false, error: "Missing run id." };
  }
  if (!captureWindow && hasAggregateRunComplete_(payload)) {
    return handleAggregateRunComplete_(sheet, payload, runId);
  }

  const found = numberValue_(payload.events_found);
  const received =
    payload.events_received === undefined
      ? countRowsForRunWindow_(ensureSheet_(spreadsheet_(), RAW_SHEET_NAME, RAW_HEADERS), runId, captureWindow)
      : numberValue_(payload.events_received);
  const previous = findRunLog_(sheet, runId);
  const row = previous.values || blankRunLog_(runId);
  row[0] = runId;
  row[1] = String(payload.batch_name || row[1] || "");
  row[2] = row[2] || String(payload.started_at || payload.captured_at || "");
  row[3] = new Date().toISOString();
  if (isFutureCaptureWindow_(captureWindow)) {
    row[6] = found;
    row[7] = received;
  } else {
    row[4] = found;
    row[5] = received;
  }
  row[8] = runStatus_(row, captureWindow);
  row[9] = row[8] === "complete" ? "" : "Received count did not match found count.";
  row[10] = new Date().toISOString();

  if (previous.rowNumber) {
    sheet.getRange(previous.rowNumber, 1, 1, RUN_LOG_HEADERS.length).setValues([row]);
  } else {
    sheet.appendRow(row);
  }
  return { ok: true, record_type: "run_complete_response", run_id: runId, status: row[8] };
}

function handleAggregateRunComplete_(sheet, payload, runId) {
  const rawSheet = ensureSheet_(spreadsheet_(), RAW_SHEET_NAME, RAW_HEADERS);
  const previous = findRunLog_(sheet, runId);
  const row = previous.values || blankRunLog_(runId);
  const backfillReceived = countRowsForRunWindow_(rawSheet, runId, BACKFILL_CAPTURE_WINDOW);
  const pastFound = numberValue_(payload.past_events_found);
  const futureFound = numberValue_(payload.future_events_found);
  const totalFound = pastFound + futureFound;

  row[0] = runId;
  row[1] = String(payload.batch_name || row[1] || "");
  row[2] = row[2] || String(payload.started_at || payload.captured_at || "");
  row[3] = new Date().toISOString();
  if (backfillReceived > 0 || isBackfillBatchName_(payload.batch_name)) {
    row[4] = totalFound;
    row[5] = backfillReceived;
    row[6] = 0;
    row[7] = 0;
    row[8] = runStatus_(row, BACKFILL_CAPTURE_WINDOW);
  } else {
    row[4] = pastFound;
    row[5] = countRowsForRunWindow_(rawSheet, runId, "past_3_days");
    row[6] = futureFound;
    row[7] = countRowsForRunWindow_(rawSheet, runId, "next_7_days");
    row[8] = row[4] === row[5] && row[6] === row[7] ? "complete" : "partial";
  }
  row[9] = row[8] === "complete" ? "" : "Received count did not match found count.";
  row[10] = new Date().toISOString();

  if (previous.rowNumber) {
    sheet.getRange(previous.rowNumber, 1, 1, RUN_LOG_HEADERS.length).setValues([row]);
  } else {
    sheet.appendRow(row);
  }
  return { ok: true, record_type: "run_complete_response", run_id: runId, status: row[8] };
}

function handleSyncRequest_(payload) {
  const limit = Math.max(1, Math.min(numberValue_(payload.limit) || DEFAULT_SYNC_LIMIT, 1000));
  const cursor = syncCursorFromPayload_(payload);
  const spreadsheet = spreadsheet_();
  const rawSheet = ensureSheet_(spreadsheet, RAW_SHEET_NAME, RAW_HEADERS);
  const rows = syncRows_(sheetObjects_(rawSheet, RAW_HEADERS), cursor.ingested_at, cursor.snapshot_key);
  const page = rows.slice(0, limit);
  const nextCursor = syncNextCursor_(page, cursor);
  return {
    ok: true,
    record_type: "sync_response",
    rows: page,
    next_cursor: nextCursor,
    has_more: rows.length > page.length,
    timestamp: new Date().toISOString(),
  };
}

function syncCursorFromPayload_(payload) {
  return {
    ingested_at: String((payload && payload.after_ingested_at) || EMPTY_SYNC_CURSOR),
    snapshot_key: String((payload && payload.after_snapshot_key) || EMPTY_SNAPSHOT_KEY),
  };
}

function syncNextCursor_(page, fallbackCursor) {
  if (!page.length) {
    return {
      ingested_at: String(fallbackCursor.ingested_at || EMPTY_SYNC_CURSOR),
      snapshot_key: String(fallbackCursor.snapshot_key || EMPTY_SNAPSHOT_KEY),
    };
  }
  const row = page[page.length - 1];
  return {
    ingested_at: String(row.ingested_at || EMPTY_SYNC_CURSOR),
    snapshot_key: String(row.snapshot_key || EMPTY_SNAPSHOT_KEY),
  };
}

function syncRows_(rawRows, afterIngestedAt, afterSnapshotKey) {
  const cursor = {
    ingested_at: String(afterIngestedAt || EMPTY_SYNC_CURSOR),
    snapshot_key: String(afterSnapshotKey || EMPTY_SNAPSHOT_KEY),
  };
  return rawRows
    .filter((row) => isRowAfterCursor_(row, cursor))
    .sort((a, b) => {
      const byTime = String(a.ingested_at || "").localeCompare(String(b.ingested_at || ""));
      return byTime || String(a.snapshot_key || "").localeCompare(String(b.snapshot_key || ""));
    });
}

function isRowAfterCursor_(row, cursor) {
  const ingestedAt = String(row.ingested_at || "");
  const snapshotKey = String(row.snapshot_key || "");
  return (
    ingestedAt > cursor.ingested_at ||
    (ingestedAt === cursor.ingested_at && snapshotKey > cursor.snapshot_key)
  );
}

function rawRow_(payload, event, index, ingestedAt) {
  const runId = runId_(payload);
  const captureWindow = canonicalCaptureWindow_(payload.capture_window);
  const eventId = String(event.calendar_event_id || event.event_id || "");
  const fingerprint = String(event.event_fingerprint || event.fingerprint || "");
  const startAt = String(event.start_at || event.start_date || "");
  const endAt = String(event.end_at || event.end_date || "");
  const snapshotKey = String(
    event.snapshot_key ||
      [runId, captureWindow, eventId || fingerprint || index, startAt, endAt].join("|")
  );
  const raw = Object.assign({}, event);
  delete raw.api_key;
  return [
    ingestedAt,
    snapshotKey,
    runId,
    String(payload.batch_name || ""),
    captureWindow,
    String(payload.captured_at || ""),
    String(payload.window_start || ""),
    String(payload.window_end || ""),
    String(payload.source_device || ""),
    String(payload.timezone || ""),
    eventId,
    fingerprint,
    String(event.event_title || event.title || ""),
    startAt,
    endAt,
    String(event.duration_minutes || ""),
    String(event.location || ""),
    String(event.notes || ""),
    String(event.calendar || event.calendar_name || ""),
    String(payload.payload_version || event.payload_version || ""),
    JSON.stringify(raw),
  ];
}

function runStatus_(row, captureWindow) {
  const pastFound = numberValue_(row[4]);
  const pastReceived = numberValue_(row[5]);
  const futureFound = numberValue_(row[6]);
  const futureReceived = numberValue_(row[7]);
  if (isBackfillCaptureWindow_(captureWindow)) {
    return pastFound === pastReceived ? "complete" : "partial";
  }
  const pastOk = pastFound === pastReceived;
  const futureOk = futureFound === futureReceived;
  const hasPast = pastFound > 0 || pastReceived > 0;
  const hasFuture = futureFound > 0 || futureReceived > 0;
  return hasPast && hasFuture && pastOk && futureOk ? "complete" : "partial";
}

function isPastCaptureWindow_(captureWindow) {
  return ["past_3_days", "past_7_days", BACKFILL_CAPTURE_WINDOW].indexOf(canonicalCaptureWindow_(captureWindow)) !== -1;
}

function isFutureCaptureWindow_(captureWindow) {
  return ["next_7_days", "next_2_days"].indexOf(canonicalCaptureWindow_(captureWindow)) !== -1;
}

function isBackfillCaptureWindow_(captureWindow) {
  return canonicalCaptureWindow_(captureWindow) === BACKFILL_CAPTURE_WINDOW;
}

function isSupportedCaptureWindow_(captureWindow) {
  return (
    isPastCaptureWindow_(captureWindow) ||
    isFutureCaptureWindow_(captureWindow) ||
    isNoopCaptureWindow_(captureWindow) ||
    String(captureWindow || "") === "legacy"
  );
}

function canonicalCaptureWindow_(captureWindow) {
  const value = String(captureWindow || "");
  if (BACKFILL_CAPTURE_WINDOW_ALIASES.indexOf(value) !== -1) {
    return BACKFILL_CAPTURE_WINDOW;
  }
  return value;
}

function isNoopCaptureWindow_(captureWindow) {
  return NOOP_CAPTURE_WINDOWS.indexOf(String(captureWindow || "")) !== -1;
}

function runId_(payload) {
  return String((payload && (payload.run_id || payload.client_run_key)) || "");
}

function hasAggregateRunComplete_(payload) {
  return (
    payload &&
    (payload.past_events_found !== undefined ||
      payload.future_events_found !== undefined ||
      payload.past_events_received !== undefined ||
      payload.future_events_received !== undefined)
  );
}

function isBackfillBatchName_(batchName) {
  return String(batchName || "").toLowerCase().indexOf("backfill") !== -1;
}

function countRowsForRunWindow_(sheet, runId, captureWindow) {
  const rows = sheetObjects_(sheet, RAW_HEADERS);
  const canonical = canonicalCaptureWindow_(captureWindow);
  return rows.filter((row) => {
    return (
      String(row.run_id || "") === String(runId || "") &&
      canonicalCaptureWindow_(row.capture_window) === canonical
    );
  }).length;
}

function ensureSheet_(spreadsheet, name, headers) {
  let sheet = spreadsheet.getSheetByName(name);
  if (!sheet) {
    sheet = spreadsheet.insertSheet(name);
  }
  const current = sheet.getLastColumn()
    ? sheet.getRange(1, 1, 1, Math.max(sheet.getLastColumn(), headers.length)).getValues()[0]
    : [];
  const missing = headers.some((header, index) => current[index] !== header);
  if (missing) {
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  }
  return sheet;
}

function existingSnapshotKeys_(sheet) {
  const result = {};
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    return result;
  }
  const values = sheet.getRange(2, 2, lastRow - 1, 1).getValues();
  values.forEach((row) => {
    const key = String(row[0] || "");
    if (key) {
      result[key] = true;
    }
  });
  return result;
}

function appendRows_(sheet, rows) {
  if (!rows.length) {
    return;
  }
  sheet.getRange(sheet.getLastRow() + 1, 1, rows.length, rows[0].length).setValues(rows);
}

function findRunLog_(sheet, runId) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    return { rowNumber: null, values: null };
  }
  const values = sheet.getRange(2, 1, lastRow - 1, RUN_LOG_HEADERS.length).getValues();
  for (let i = 0; i < values.length; i += 1) {
    if (String(values[i][0] || "") === runId) {
      return { rowNumber: i + 2, values: values[i] };
    }
  }
  return { rowNumber: null, values: null };
}

function blankRunLog_(runId) {
  return [runId, "", "", "", 0, 0, 0, 0, "partial", "", ""];
}

function completeRunIds_(sheet) {
  const result = {};
  const rows = sheetObjects_(sheet, RUN_LOG_HEADERS);
  rows.forEach((row) => {
    if (String(row.status || "") === "complete") {
      result[String(row.run_id || "")] = true;
    }
  });
  return result;
}

function sheetObjects_(sheet, headers) {
  const lastRow = sheet.getLastRow();
  if (lastRow < 2) {
    return [];
  }
  return sheet.getRange(2, 1, lastRow - 1, headers.length).getValues().map((values) => {
    const row = {};
    headers.forEach((header, index) => {
      row[header] = values[index];
    });
    return row;
  });
}

function numberValue_(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? number : 0;
}

function safeError_(message) {
  const error = new Error(message);
  error.safeMessage = message;
  return error;
}

function jsonResponse_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload)).setMimeType(
    ContentService.MimeType.JSON
  );
}

if (typeof module !== "undefined") {
  module.exports = {
    BACKFILL_CAPTURE_WINDOW,
    RAW_HEADERS,
    RUN_LOG_HEADERS,
    isPastCaptureWindow_,
    isFutureCaptureWindow_,
    isBackfillCaptureWindow_,
    isSupportedCaptureWindow_,
    canonicalCaptureWindow_,
    countRowsForRunWindow_,
    handleAggregateRunComplete_,
    runStatus_,
    rawRow_,
    syncCursorFromPayload_,
    syncNextCursor_,
    syncRows_,
    isRowAfterCursor_,
  };
}
