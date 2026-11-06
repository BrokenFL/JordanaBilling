import {test} from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import fs from 'node:fs';

const source = fs.readFileSync(new URL('../app/jordana_invoice/static/review.js', import.meta.url), 'utf8');

function buttonHarness(button, extras = {}) {
  const nodes = new Map([['target', button]]);
  return {
    nodes,
    context: {
      $: id => nodes.get(id) || extras[id] || null,
      document: {body: {contains: node => node === button}},
      sanitizeUiErrorMessage: message => String(message || 'Unexpected error'),
      ...extras,
    },
  };
}

function extract(startMarker, endMarker) {
  const start = source.indexOf(startMarker);
  assert.notEqual(start, -1, `missing source marker: ${startMarker}`);
  const end = source.indexOf(endMarker, start);
  assert.notEqual(end, -1, `missing source marker: ${endMarker}`);
  return source.slice(start, end);
}

test('client-directory create stays busy during a slow request and ignores duplicate clicks', async () => {
  const button = {disabled: false, textContent: 'Create Client'};
  let resolveRequest;
  let calls = 0;
  const harness = buttonHarness(button, {
    prompt: () => 'Jack Burkhead',
    api: async () => { calls += 1; return await new Promise(resolve => { resolveRequest = resolve; }); },
    loadPeople: async () => {},
    location: {hash: ''},
  });
  harness.context.$ = id => id === 'newPersonBtn' ? button : null;
  vm.runInNewContext(extract('$("newPersonBtn").onclick = async () => {', 'document.getElementById("syncNowBtn")'), harness.context);
  const handler = button.onclick;
  const first = handler();
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Creating client…');
  const second = handler();
  assert.equal(calls, 1);
  resolveRequest({person_id: 'person-1'});
  await Promise.all([first, second]);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Create Client');
  assert.equal(harness.context.location.hash, 'people/person-1');
});

test('client-directory create reports an error and can be retried', async () => {
  const button = {disabled: false, textContent: 'Create Client'};
  let calls = 0;
  const alerts = [];
  const harness = buttonHarness(button, {
    prompt: () => 'Jack Burkhead',
    alert: message => alerts.push(message),
    api: async () => { calls += 1; throw new Error('Could not create this client'); },
    loadPeople: async () => {},
    location: {hash: ''},
  });
  harness.context.$ = id => id === 'newPersonBtn' ? button : null;
  vm.runInNewContext(extract('$("newPersonBtn").onclick = async () => {', 'document.getElementById("syncNowBtn")'), harness.context);
  await button.onclick();
  assert.equal(calls, 1);
  assert.deepEqual(alerts, ['Could not create this client']);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Create Client');
});

test('Review Confirm Client(s) stays busy during a slow save and restores after failure', async () => {
  const button = {disabled: false, textContent: 'Confirm Client(s)'};
  let rejectSave;
  const errors = [];
  const harness = buttonHarness(button, {
    clearReviewActionError: () => {},
    showReviewActionError: message => errors.push(message),
    saveRelationshipSection: async () => await new Promise((_, reject) => { rejectSave = reject; }),
  });
  harness.context.$ = id => id === 'saveRelationshipBtn' ? button : null;
  vm.runInNewContext(extract('if ($("saveRelationshipBtn")) $("saveRelationshipBtn").onclick = async () => {', 'if ($("changeClientsBtn"))'), harness.context);
  const pending = button.onclick();
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Saving clients…');
  rejectSave(new Error('database busy'));
  await pending;
  assert.deepEqual(errors, ['database busy']);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Confirm Client(s)');
});

test('Review can approve an older session after confirming its client off the current queue page', async () => {
  const candidateId = 'older-candidate';
  const participant = {person_id: 'example-person', display_name: 'Example Client', is_primary: true};
  const detail = {session: {candidate_id: candidateId}, participants: [participant]};
  const calls = [], errors = [];
  const context = {
    state: {selected: candidateId, detail, participants: [participant], editSteps: {}, dirty: new Set(), limit: 25, offset: 0},
    approvalState: {submitting: false, candidateId: null},
    $: () => ({value: ''}),
    URLSearchParams,
    prepareReviewCalendar: async () => {},
    api: async (path, options) => {
      if (!options) return {items: [{candidate_id: 'newer-candidate'}]};
      calls.push(path);
      if (path.endsWith('/save-relationship')) return detail;
      if (path !== `/api/review/candidates/${candidateId}/approve`) throw new Error('Review candidate not found.');
      return {};
    },
    resolveTypedSelections: async () => {},
    collectParticipants: () => [participant], collectSessionDraftValues: () => ({}),
    participantState: p => p, collectPayload: () => ({}),
    renderStatus() {}, renderRows() {}, renderInspector() {}, restoreSessionDraftValues() {}, markSaved() {},
    clearReviewActionError() {}, completeReviewOverlayAction() {}, validateCancellationBillingChoice() {},
    reviewOverlayCtrl: null,
    showReviewActionError: message => errors.push(message), sanitizeUiErrorMessage: message => message,
    showReviewSuccess() {}, readInvoiceSessionReturnContext: () => null,
    document: {getElementById: () => ({hidden: true})},
  };
  vm.createContext(context);
  vm.runInContext([
    extract('async function loadList()', 'function renderStatus('),
    extract('async function saveRelationshipSection()', 'async function saveBillingSection()'),
    extract('async function save(approve)', 'function validateCancellationBillingChoice()'),
  ].join('\n'), context);
  await vm.runInContext('saveRelationshipSection()', context);
  await vm.runInContext('save(true)', context);
  assert.deepEqual(calls, [
    `/api/review/candidates/${candidateId}/save-relationship`,
    `/api/review/candidates/${candidateId}/approve`,
  ]);
  assert.deepEqual(errors, []);
});

test('Record Payment reads the submitted invoice form even when hidden Review fields remain', async () => {
  const visible = {
    payment_date: '2026-11-05', amount_received: '175.00', payment_method: 'zelle',
    reference_number: ' Example reference ', received_from_name: ' Example Client ', administrative_note: '',
  };
  const form = {elements: Object.fromEntries(Object.entries(visible).map(([name, value]) => [name, {value}]))};
  const nodes = {
    paymentDateInput: {value: ''}, paymentAmountInput: {value: ''}, paymentMethodInput: {value: ''},
    paymentReferenceInput: {value: ''}, paymentReceivedFromInput: {value: visible.received_from_name},
    paymentAdministrativeNoteInput: {value: ''}, paymentSubmitBtn: {}, paymentCancelBtn: {},
    paymentOverlayClose: {}, paymentFormMessage: {},
  };
  let payload;
  const context = {
    state: {unpaid: {submitting: false}}, $: id => nodes[id],
    parseMoneyToCents: value => Math.round(Number(value) * 100),
    api: async (path, options) => {
      assert.equal(path, '/api/invoices/example-invoice/payments');
      payload = JSON.parse(options.body);
    },
    closePaymentOverlay() {}, loadOutstandingInvoices: async () => {}, showUnpaidSuccess() {},
  };
  vm.createContext(context);
  vm.runInContext(extract('async function submitInvoicePayment(', 'function goToPreviousSession()'), context);
  await context.submitInvoicePayment({invoice_id: 'example-invoice'}, form);
  assert.deepEqual(payload, {
    payment_date: '2026-11-05', amount_cents: 17500, payment_method: 'zelle',
    reference_number: 'Example reference', received_from_name: 'Example Client', administrative_note: null,
  });
});

test('client rate save shows progress, blocks duplicate clicks, and restores for retry', async () => {
  const button = {disabled: false, textContent: 'Save Future Client Rate'};
  const message = {textContent: '', className: ''};
  let resolveRequest;
  let calls = 0;
  const harness = buttonHarness(button, {
    personRateMessage: message,
    personRateSessionType: {value: 'psychotherapy'},
    personRateDuration: {value: '60'},
    personRateTimeCategory: {value: 'standard'},
    personRateAmount: {value: '400.00'},
    personRateEffectiveFrom: {value: '2026-09-11'},
    api: async () => { calls += 1; return await new Promise(resolve => { resolveRequest = resolve; }); },
    personId: 'person-1',
    showAllSessions: false,
    openPersonRecord: async () => {},
    defaultFutureEffectiveDate: () => '2026-09-11',
  });
  harness.context.$ = id => {
    if (id === 'savePersonRateRule') return button;
    if (id === 'personRateMessage') return message;
    return {
      personRateSessionType: {value: 'psychotherapy'},
      personRateDuration: {value: '60'},
      personRateTimeCategory: {value: 'standard'},
      personRateAmount: {value: '400.00'},
      personRateEffectiveFrom: {value: '2026-09-11'},
    }[id] || null;
  };
  vm.runInNewContext(extract('if ($("savePersonRateRule")) $("savePersonRateRule").onclick = async () => {', 'if ($("savePersonAlias")'), harness.context);
  const first = button.onclick();
  assert.equal(button.disabled, true);
  assert.equal(button.textContent, 'Saving rate…');
  const second = button.onclick();
  assert.equal(calls, 1);
  resolveRequest({});
  await Promise.all([first, second]);
  assert.equal(button.disabled, false);
  assert.equal(button.textContent, 'Save Future Client Rate');
  assert.equal(message.textContent, 'Future client rate saved.');
});
