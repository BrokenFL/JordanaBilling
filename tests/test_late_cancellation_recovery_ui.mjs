import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../app/jordana_invoice/static/review.js', import.meta.url), 'utf8');
function extract(start, end) {
  const from = source.indexOf(start);
  const to = source.indexOf(end, from);
  assert.ok(from >= 0 && to > from);
  return source.slice(from, to);
}

test('Every Sessions row opens in Review, with separate status-specific recovery', () => {
  const nodes = {sessionsResultCount: {}, sessionsRows: {querySelectorAll: () => []}};
  const context = {
    state: {sessions: {offset: 0, selectedIds: new Set()}}, $: id => nodes[id],
    fmt: x => x || '', money: () => '', paymentHandlingLabel: x => x || '',
    escapeHtml: x => x, escapeAttr: x => x,
  };
  vm.createContext(context);
  vm.runInContext(extract('function renderSessions(', 'async function restoreSessionRow('), context);
  for (const status of ['needs_classification', 'needs_billing_treatment', 'needs_person_match', 'ready_for_approval']) {
    context.rows = [{candidate_id: 'candidate-example', review_status: status}];
    vm.runInContext('renderSessions(rows, 1)', context);
    assert.match(nodes.sessionsRows.innerHTML, /send-session-to-review-btn/, status);
    assert.match(nodes.sessionsRows.innerHTML, /open-session-review-btn/, status);
    context.rows[0].session_id = 'session-example';
    vm.runInContext('renderSessions(rows, 1)', context);
    assert.match(nodes.sessionsRows.innerHTML, /open-session-review-btn/, status);
    assert.doesNotMatch(nodes.sessionsRows.innerHTML, /send-session-to-review-btn/);
  }
  for (const [status, action] of [['approved', 'return-approved-session-btn'], ['excluded', 'restore-session-btn']]) {
    context.rows = [{candidate_id: 'candidate-example', session_id: 'session-example', review_status: status}];
    vm.runInContext('renderSessions(rows, 1)', context);
    assert.ok(nodes.sessionsRows.innerHTML.includes(action));
    assert.match(nodes.sessionsRows.innerHTML, /open-session-review-btn/);
    assert.doesNotMatch(nodes.sessionsRows.innerHTML, /send-session-to-review-btn/);
  }
  for (const status of ['', 'source_change_warning', 'legacy_status']) {
    context.rows = [{candidate_id: 'candidate-example', review_status: status}];
    vm.runInContext('renderSessions(rows, 1)', context);
    assert.match(nodes.sessionsRows.innerHTML, /open-session-review-btn/, status);
  }
});

function promotionHarness(api) {
  const button = {disabled: false, textContent: 'Send to Review'};
  const alerts = [], opened = [];
  const context = {
    promoteState: {submitting: false, candidateId: null}, api,
    CSS: {escape: x => x}, document: {querySelector: () => button, body: {contains: () => true}},
    location: {hash: '#sessions'}, loadSessions: async () => {}, showReviewWorkbench: async () => {},
    selectCandidate: async id => opened.push(id), alert: message => alerts.push(message),
    sanitizeUiErrorMessage: () => 'Safe recovery error',
  };
  vm.createContext(context);
  vm.runInContext(extract('async function sendSessionRowToReview(', 'function persistInvoiceSessionReturnContext('), context);
  return {context, button, alerts, opened};
}

test('Send to Review blocks duplicate requests and opens the recovered appointment', async () => {
  let finish, calls = 0;
  const harness = promotionHarness(async () => { calls++; await new Promise(resolve => { finish = resolve; }); });
  const pending = vm.runInContext('sendSessionRowToReview("candidate-example")', harness.context);
  assert.equal(harness.button.disabled, true);
  assert.equal(harness.button.textContent, 'Sending…');
  await vm.runInContext('sendSessionRowToReview("candidate-example")', harness.context);
  assert.equal(calls, 1);
  finish();
  await pending;
  assert.deepEqual(harness.opened, ['candidate-example']);
  assert.deepEqual(harness.alerts, []);
  assert.equal(harness.context.promoteState.submitting, false);
  assert.equal(harness.button.disabled, false);
});

test('Failed recovery sanitizes its error and permits retry', async () => {
  let calls = 0;
  const harness = promotionHarness(async () => { calls++; if (calls === 1) throw Error('private database details'); });
  await vm.runInContext('sendSessionRowToReview("candidate-example")', harness.context);
  assert.deepEqual(harness.alerts, ['Safe recovery error']);
  assert.equal(harness.button.disabled, false);
  assert.equal(harness.button.textContent, 'Send to Review');
  await vm.runInContext('sendSessionRowToReview("candidate-example")', harness.context);
  assert.equal(calls, 2);
  assert.deepEqual(harness.opened, ['candidate-example']);
});

test('Opening any record navigates to its review detail without changing its status', async () => {
  let mutations = 0;
  const harness = promotionHarness(async () => { mutations++; });
  await vm.runInContext('openSessionRowInReview("excluded-example")', harness.context);
  assert.equal(mutations, 0);
  assert.deepEqual(harness.opened, ['excluded-example']);
  assert.equal(harness.context.location.hash, '');
});

test('Excluded and approved details show saved values with explicit recovery controls', () => {
  const content = {};
  const context = {
    state: {editSteps: {}, participants: [], dirty: new Set()}, $: () => content,
    firstPresent: (...values) => values.find(value => value != null), centString: x => x || '',
    fmt: x => String(x || ''), money: x => x || '', escapeAttr: x => x || '', escapeHtml: x => x || '',
    startRange: () => '', calendarLabel: () => '', appointmentBadge: () => '',
    titleTimeWarning: () => '', calendarDispositionLabel: () => '',
    userFacingSessionLabel: () => 'Example service', safeList: () => [], billToSummary: () => '',
    isFutureAppointment: () => false, wireInspector() {}, renderParticipantChips() {}, renderRelationshipEditor() {},
    data: {session: {id: 'session-example', billing_session_type: 'psychotherapy', duration_minutes: 60},
      readiness: {clients_ready: true, billing_ready: true, session_ready: true, all_ready: true}, checklist: []},
  };
  vm.createContext(context);
  vm.runInContext(extract('function renderInspector(data)', 'function wireInspector()'), context);
  for (const [status, action] of [['excluded', 'restoreReviewBtn'], ['approved', 'editApprovedReviewBtn']]) {
    context.data.session.review_status = status;
    vm.runInContext('renderInspector(data)', context);
    assert.match(content.innerHTML, /<fieldset class="review-values" disabled>/);
    assert.ok(content.innerHTML.includes(`id="${action}"`));
    assert.doesNotMatch(content.innerHTML, /id="approveBtn"|id="excludeBtn"|id="duplicateBtn"/);
  }
  context.data.session.review_status = 'needs_billing_treatment';
  vm.runInContext('renderInspector(data)', context);
  assert.doesNotMatch(content.innerHTML, /<fieldset class="review-values" disabled>/);
  assert.match(content.innerHTML, /id="approveBtn"/);
});
