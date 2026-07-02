# Write Endpoint Contracts

This document inventories every HTTP endpoint that can cause a state change in the
Jordana Invoice application. It describes **observed current behavior**, not desired
future behavior. This is a contract map for Round 4A.1; it does not standardize
validation or response envelopes.

All write endpoints share these common request requirements:

- **Method**: POST (PUT, PATCH, DELETE return 404)
- **Content-Length**: required (411 if missing, 400 if invalid/negative)
- **Content-Type**: `application/json` (415 if missing or unsupported; charset suffix accepted; case-insensitive)
- **X-Jordana-Write-Token**: required (403 if missing or incorrect; constant-time comparison)
- **Body size**: max 1 MiB / 1,048,576 bytes (413 if exceeded)
- **Host header**: required, must be localhost/127.0.0.1/[::1] (400 if missing/invalid)
- **Origin header** (for mutating methods): if present, must be `http://` + valid localhost (403 if invalid)
- **Malformed JSON**: 400 with `{"ok": false, "error": "Malformed JSON in request body."}`

Common error response shapes:

- Safe validation error: `{"ok": false, "error": "<safe message>"}`, status 400 (or 404 for "Account not found."/"Billing party not found.")
- `BillingPartyNotFoundError`: status 404
- `BillingPartyTypeError`: status 400
- `DatabaseBusyError`: status 503, `"Database is busy, please try again."`
- `SyncError`: status 503, sanitized message
- Unsafe/unknown exception: status 500 (GET) or 400 (POST), `"An unexpected error occurred."`

POST handlers use `default_status=400` for unknown exceptions; GET handlers use `default_status=500`.

---

## 1. Session Review and Approval

### POST /api/review/candidates/{id}/save

- **Handler**: inline in `do_POST`
- **Service**: `save_interpretation(conn, candidate_id, data)`
- **Auth**: write token required
- **Accepted fields**: participants (list), account_id, billing_party_id, approved_duration_minutes, service_mode, time_category, approved_rate, payment_status, billing_treatment, and all fields accepted by the interpretation saver
- **Required fields**: none explicitly enforced at the HTTP layer; service-level validation applies
- **Optional fields**: all fields are optional at the HTTP layer (defaults applied by service)
- **Success status**: 200
- **Success response**: full candidate detail dict (from `get_review_candidate`)
- **Error status codes**: 400 (safe validation error), 400 (unsafe exception sanitized)
- **DB tables**: `calendar_event_candidates`, `sessions`, `session_participants`, `audit_log`, `review_items`
- **Idempotent**: yes — saving the same interpretation again produces the same state
- **Existing tests**: `test_review_services.py` (service-level), `test_review_server.py` (sanitization)
- **Missing contract coverage**: HTTP-level success shape, missing-field behavior at HTTP boundary

### POST /api/review/candidates/{id}/save-person

- **Handler**: inline in `do_POST`
- **Service**: `save_person_section(conn, candidate_id, data)`
- **Accepted fields**: `person` (dict) or top-level person fields (`person_id`, `first_name`, `last_name`, `display_name`, `display_name`)
- **Required fields**: none at HTTP layer
- **Optional fields**: all
- **Success status**: 200
- **Success response**: full candidate detail dict
- **DB tables**: `people`, `session_participants`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/review/candidates/{id}/save-relationship

- **Handler**: inline in `do_POST`
- **Service**: `save_relationship_section(conn, candidate_id, data)`
- **Accepted fields**: `participants` (list of dicts with `person_id`, `display_name`, `is_primary`, `is_proposed`)
- **Required fields**: `participants` (enforced at service level)
- **Optional fields**: individual participant fields
- **Success status**: 200
- **Success response**: full candidate detail dict
- **DB tables**: `people`, `session_participants`, `calendar_aliases`, `audit_log`
- **Idempotent**: yes — repeated saves with same participants produce same state
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape, missing participants behavior

### POST /api/review/candidates/{id}/save-billing

- **Handler**: inline in `do_POST`
- **Service**: `save_billing_section(conn, candidate_id, data)`
- **Accepted fields**: `billing_party_id`
- **Required fields**: `billing_party_id` (enforced at service level)
- **Optional fields**: none
- **Success status**: 200
- **Success response**: full candidate detail dict
- **DB tables**: `sessions`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/review/candidates/{id}/save-session

- **Handler**: inline in `do_POST`
- **Service**: `save_session_draft(conn, candidate_id, data)`
- **Accepted fields**: `approved_duration_minutes`, `duration_choice`, `billing_session_type`, `custom_service_description`, `custom_service_code`, `approved_rate` / `approved_rate_cents`, `rate_change_scope`, `rate_rule_participant_person_id`, `payment_status`, `billing_treatment`, `amount_received`, `payment_date`, `payment_method`, `reference_number`, `administrative_note`
- **Required fields**: none at HTTP layer (service validates contextually)
- **Optional fields**: all
- **Success status**: 200
- **Success response**: full candidate detail dict
- **DB tables**: `sessions`, `rate_rules`, `rate_rule_participants`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/review/candidates/{id}/approve

- **Handler**: inline in `do_POST` (with post-approval staging logic)
- **Service**: `approve_candidate(conn, candidate_id, data)` then `stage_approved_sessions_to_monthly_drafts`
- **Accepted fields**: `participants`, `billing_party_id`, `approved_duration_minutes`, `service_mode`, `time_category`, `approved_rate`, `payment_status`, `billing_treatment`, `amount_received`, `payment_date`, `payment_method`, `reference_number`, `administrative_note`
- **Required fields**: `participants`, `billing_party_id`, `approved_duration_minutes`, `service_mode`, `time_category`, `approved_rate_cents` (enforced at service level via `REQUIRED_APPROVAL_FIELDS`)
- **Optional fields**: `billing_treatment` (required for cancelled/no-show), paid-at-session fields (required when `payment_status` is `paid_at_session`)
- **Success status**: 200
- **Success response**: `{...candidate detail, "session": {"id": "...", ...}, "invoice_staging": {"status": "success"|"warning"|"not_required"|"unavailable"|"error", "summary": {...}}}`
- **Error status codes**: 400 (safe validation error, e.g. "Cannot approve until required fields are complete: ...")
- **DB tables**: `sessions`, `session_participants`, `calendar_aliases`, `audit_log`, `review_items`, `invoices`, `invoice_line_items`, `payments`, `payment_allocations` (for paid-at-session)
- **Idempotent**: yes — re-approving an already-approved candidate returns current state without duplicating
- **Invoice staging**: for invoice billing, staging is attempted after approval commit; staging warnings do not roll back approval; staging errors are sanitized via `sanitize_staging_error_message`
- **Paid-at-session**: approval creates/validates payment and allocation transactionally; staging reports `not_required`
- **Existing tests**: `test_review_services.py`, `test_staging_api.py`, `test_approval_staging.py`, `test_paid_at_session_apply.py`
- **Missing contract coverage**: HTTP-level approval success shape including `invoice_staging` envelope; idempotent re-approval at HTTP level

### POST /api/review/candidates/{id}/mark

- **Handler**: inline in `do_POST`
- **Service**: `mark_candidate(conn, candidate_id, classification=data.get("classification", "personal"), reason=data.get("reason", ""))`
- **Accepted fields**: `classification` (default "personal"), `reason` (default "")
- **Enum-like values for classification**: `personal`, `administrative`, `nonbillable`, `duplicate`, `client_session`
- **Success status**: 200
- **Success response**: full candidate detail dict
- **DB tables**: `calendar_event_candidates`, `sessions`, `calendar_aliases`, `audit_log`, `review_items`
- **Idempotent**: yes — marking again updates classification
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/review/candidates/{id}/restore

- **Handler**: inline in `do_POST`
- **Service**: `restore_candidate(conn, candidate_id, reason=data.get("reason", ""))`
- **Accepted fields**: `reason` (default "")
- **Success status**: 200
- **Success response**: full candidate detail dict; if the post-restore suggestion refresh fails, an additive `warning` field is included with the sanitized message `"Candidate was restored, but suggestions could not be refreshed."`
- **Error status codes**: 400/404 ("No session found for this candidate; only session-backed candidates can be restored.")
- **DB tables**: `calendar_event_candidates`, `sessions`, `audit_log`, `review_items`
- **Transaction boundary**: the restore (candidate/session/audit/review-item updates) commits atomically before the suggestion refresh is attempted; the refresh is a secondary operation whose failure cannot roll back the committed restore
- **Idempotency**: no — restoring an already-needs-review candidate resets fields; repeat requests produce additional audit entries but do not create duplicate sessions
- **Success-with-warning convention**: follows the same pattern used by the approve endpoint when invoice staging warns — the primary operation succeeds and the secondary failure is reported as an additive field on the success response rather than as an error status
- **Existing tests**: `test_routine_queue_filter.py` (service-level), `test_write_endpoint_contracts.py` (HTTP-level), `test_request_validation.py` (HTTP-level regression)

### POST /api/review/candidates/{id}/send-to-review

- **Handler**: inline in `do_POST`
- **Service**: `promote_candidate_to_review(conn, candidate_id, reason=data.get("reason", ""))`
- **Accepted fields**: `reason` (default "")
- **Success status**: 200
- **Success response**: full candidate detail dict
- **DB tables**: `calendar_event_candidates`, `sessions`, `audit_log`, `review_items`
- **Idempotent**: yes — promoting an already-in-review candidate is a no-op
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/review/candidates/{id}/refresh

- **Handler**: inline in `do_POST`
- **Service**: `refresh_candidate_suggestions(conn, candidate_id)` then `get_review_candidate`
- **Accepted fields**: none (body is read but not used)
- **Success status**: 200
- **Success response**: full candidate detail dict
- **DB tables**: `calendar_event_candidates`, `sessions` (suggestion fields refreshed)
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/review/recalc-rates

- **Handler**: inline in `do_POST`
- **Service**: `recalc_unapproved_session_rates(conn)`
- **Accepted fields**: none
- **Success status**: 200
- **Success response**: `{"ok": true, "sessions_updated": <int>}`
- **DB tables**: `sessions` (rate suggestion fields for unapproved sessions)
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level response shape

### POST /api/review/reparse-candidates

- **Handler**: inline in `do_POST`
- **Service**: `reparse_unapproved_candidates(conn)`
- **Accepted fields**: none
- **Success status**: 200
- **Success response**: `{"ok": true, ...result}` (result contains counts of reparsed/updated candidates)
- **DB tables**: `calendar_event_candidates`, `sessions`
- **Idempotent**: yes
- **Existing tests**: `test_reparse_candidates.py`
- **Missing contract coverage**: HTTP-level response shape

---

## 2. People and Identity

### POST /api/people

- **Handler**: inline in `do_POST`
- **Service**: `create_person(conn, data)`
- **Accepted fields**: `display_name` (string or dict), `first_name`, `last_name`
- **Required fields**: `display_name` (enforced at service level: "Display name is required.")
- **Optional fields**: `first_name`, `last_name`
- **Success status**: 200
- **Success response**: `{"ok": true, "person_id": "...", "display_name": "...", ...}`
- **Error status codes**: 400 ("Display name is required.")
- **DB tables**: `people`
- **Idempotent**: no — each call creates a new person (duplicate names allowed)
- **Existing tests**: `test_review_server.py` (sanitization, write token, content type), `test_review_services.py`
- **Missing contract coverage**: HTTP-level success shape

### POST /api/people/{id}

- **Handler**: inline in `do_POST`
- **Service**: `update_person(conn, person_id, data)`
- **Accepted fields**: `display_name`, `first_name`, `last_name`, `active`, `administrative_notes`, `person_code`
- **Required fields**: none at HTTP layer
- **Optional fields**: all
- **Success status**: 200
- **Success response**: updated person dict
- **Error status codes**: 400/404 ("Person not found.")
- **DB tables**: `people`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape, not-found error

### POST /api/people/{id}/aliases

- **Handler**: inline in `do_POST`
- **Service**: `save_person_alias(conn, person_id, raw_alias=data.get("raw_alias", ""), approved_by_user=bool(data.get("approved_by_user", True)), alias_id=data.get("alias_id"))`
- **Accepted fields**: `raw_alias` (default ""), `approved_by_user` (default true), `alias_id` (optional, for updates)
- **Required fields**: none at HTTP layer
- **Optional fields**: all
- **Success status**: 200
- **Success response**: alias dict
- **DB tables**: `calendar_aliases`, `audit_log`
- **Idempotent**: yes (with `alias_id`)
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/people/{id}/merge

- **Handler**: inline in `do_POST`
- **Service**: `merge_people(conn, survivor_id, data["duplicate_person_id"], data.get("reason", ""))`
- **Accepted fields**: `duplicate_person_id` (required, accessed via `data["duplicate_person_id"]` — **KeyError if missing**), `reason` (default "")
- **Required fields**: `duplicate_person_id`
- **Optional fields**: `reason`
- **Success status**: 200
- **Success response**: merge result dict
- **Error status codes**: 400 ("Cannot merge a person into itself.", "Both people must exist before merging.")
- **DB tables**: `people`, `session_participants`, `billing_parties`, `calendar_aliases`, `rate_rules`, `audit_log`
- **Idempotent**: no — merging is a one-way operation
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape, missing `duplicate_person_id` behavior (KeyError → sanitized to 400)

---

## 3. Billing Relationships and Accounts

### POST /api/accounts

- **Handler**: inline in `do_POST`
- **Service**: `create_account(conn, data["account_name"], data.get("account_type", "individual"))`
- **Accepted fields**: `account_name` (required, accessed via `data["account_name"]` — **KeyError if missing**), `account_type` (default "individual")
- **Required fields**: `account_name`
- **Optional fields**: `account_type`
- **Success status**: 200
- **Success response**: account dict
- **DB tables**: `client_accounts`, `audit_log`
- **Idempotent**: no — duplicate account names are blocked at service level
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape, missing `account_name` behavior

### POST /api/accounts/from-client

- **Handler**: inline in `do_POST`
- **Service**: `create_account_or_return_existing(conn, data["person_id"], data["account_name"], data.get("account_type", "individual"))`
- **Accepted fields**: `person_id` (required), `account_name` (required), `account_type` (default "individual")
- **Required fields**: `person_id`, `account_name`
- **Optional fields**: `account_type`
- **Success status**: 200 (new) or 409 (existing)
- **Success response (new)**: `{"ok": true, "existing": false, "account_id": "...", "account_name": "..."}`
- **Existing response (409)**: `{"ok": false, "existing": true, "error": "A billing relationship already exists for this client.", "account_id": "...", "account_name": "..."}`
- **DB tables**: `client_accounts`, `account_members`, `audit_log`
- **Idempotent**: yes — returns existing if duplicate
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level 409 shape

### POST /api/accounts/{id}

- **Handler**: inline in `do_POST` (falls through to `update_account`)
- **Service**: `update_account(conn, account_id, data)`
- **Accepted fields**: `account_name`, `account_type`
- **Success status**: 200
- **Success response**: updated account dict
- **Error status codes**: 404 ("Account not found.")
- **DB tables**: `client_accounts`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/accounts/{id}/deactivate

- **Handler**: inline in `do_POST`
- **Service**: `deactivate_account(conn, account_id)`
- **Accepted fields**: none
- **Success status**: 200
- **Success response**: updated account dict
- **Error status codes**: 404 ("Account not found.")
- **DB tables**: `client_accounts`, `audit_log`
- **Idempotent**: yes — deactivating an already-inactive account is a no-op (no audit entry)
- **Existing tests**: `test_review_server.py` (404 test), `test_review_services.py`
- **Missing contract coverage**: HTTP-level success shape

### POST /api/accounts/{id}/reactivate

- **Handler**: inline in `do_POST`
- **Service**: `reactivate_account(conn, account_id)`
- **Accepted fields**: none
- **Success status**: 200
- **Success response**: updated account dict
- **Error status codes**: 404 ("Account not found.")
- **DB tables**: `client_accounts`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/accounts/{id}/update-billing-relationship

- **Handler**: inline in `do_POST`
- **Service**: `update_billing_relationship(conn, account_id, data)`
- **Accepted fields**: `payer_kind`, `payer_person_id`, `organization_billing_party_id`, `covered_client_ids`, `default_billing_party_id`, `filing_owner_kind`, `filing_owner_record_id`, `filing_owner_explicit`, legacy `default_filing_owner_person_id`, `billing_delivery` (dict: billing name/email/phone/address/delivery method/admin notes), `delivery_contact` (dict: `person_id` for existing contact or `person` for new contact creation)
- **Required fields**: context-dependent (service-level validation)
- **Success status**: 200
- **Success response**: updated account/relationship dict
- **Error status codes**: 400 (various safe validation messages), 404 ("Account not found.")
- **DB tables**: `client_accounts`, `account_members`, `billing_parties`, `billing_relationship_keys`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_billing_setup_edit.py`, `test_billing_relationships_round*.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/accounts/{id}/remove-member

- **Handler**: inline in `do_POST`
- **Service**: `remove_account_member(conn, account_id, data["person_id"])`
- **Accepted fields**: `person_id` (required, accessed via `data["person_id"]` — **KeyError if missing**)
- **Success status**: 200
- **Success response**: `{"ok": true}`
- **Error status codes**: 400/404 ("This client is not a member of this billing relationship.")
- **DB tables**: `account_members`, `audit_log`
- **Idempotent**: no — removing a non-member raises an error
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/account-members

- **Handler**: inline in `do_POST`
- **Service**: `add_account_member(conn, data["account_id"], data["person_id"], data.get("relationship_role", "primary"), bool(data.get("is_primary")))`
- **Accepted fields**: `account_id` (required), `person_id` (required), `relationship_role` (default "primary"), `is_primary` (default false)
- **Required fields**: `account_id`, `person_id`
- **Success status**: 200
- **Success response**: `{"account_member_id": "..."}`
- **Error status codes**: 400 ("This client is already included in this billing relationship.")
- **DB tables**: `account_members`, `audit_log`
- **Idempotent**: no — adding a duplicate member raises an error
- **Existing tests**: `test_review_services.py` (service-level)
- **Missing contract coverage**: HTTP-level shape

### POST /api/billing-relationships/setup

- **Handler**: inline in `do_POST`
- **Service**: `setup_billing_relationship(conn, data)`
- **Accepted fields**: `payer_kind`, `payer_person_id`, `organization_billing_party_id`, `covered_client_ids`, `billing_name`, `billing_email`, `billing_address_line_1`, `billing_city`, `billing_state`, `billing_postal_code`, `preferred_delivery_method`
- **Required fields**: `payer_kind`, covered clients (service-level validation)
- **Success status**: 200
- **Success response**: relationship setup result dict
- **Error status codes**: 400 (various safe validation messages)
- **DB tables**: `client_accounts`, `account_members`, `billing_parties`, `billing_relationship_keys`, `audit_log`
- **Idempotent**: yes — exact active duplicates are blocked or reused
- **Existing tests**: `test_billing_relationships_round*.py`, `test_billing_relationship_duplicate_prevention.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/billing-relationships/normalize-payer

- **Handler**: inline in `do_POST`
- **Service**: `normalize_duplicate_payer_billing_parties(conn, person_id, canonical_billing_party_id=canonical_bp_id)`
- **Accepted fields**: `person_id` (required — 400 if missing), `canonical_billing_party_id` (optional)
- **Required fields**: `person_id`
- **Success status**: 200
- **Success response**: `{"ok": true, ...result}`
- **Error status codes**: 400 ("person_id is required.")
- **DB tables**: `billing_parties`, `sessions`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_organization_billing_record.py`
- **Missing contract coverage**: HTTP-level shape

---

## 4. Billing Parties

### POST /api/billing-parties

- **Handler**: inline in `do_POST`
- **Service**: `create_billing_party(conn, data)`
- **Accepted fields**: `billing_name`, `billing_party_type` ("person", "organization"), `person_id`, `billing_email`, `billing_address_line_1`, `billing_city`, `billing_state`, `billing_postal_code`, `preferred_delivery_method` ("email", "mail", "both", "none")
- **Required fields**: `billing_name` (enforced: "Billing name is required.")
- **Optional fields**: all others
- **Success status**: 200
- **Success response**: billing party dict
- **Error status codes**: 400 (various safe validation messages)
- **DB tables**: `billing_parties`, `audit_log`
- **Idempotent**: no — each call creates a new party
- **Existing tests**: `test_review_services.py`, `test_organization_billing_record.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/billing-parties/{id}

- **Handler**: inline in `do_POST`
- **Service**: `update_billing_party(conn, billing_party_id, data)`
- **Accepted fields**: same as create plus `active`
- **Success status**: 200
- **Success response**: updated billing party dict
- **Error status codes**: 404 ("Billing party not found." via `BillingPartyNotFoundError`), 400 (type errors via `BillingPartyTypeError`)
- **DB tables**: `billing_parties`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_review_services.py`, `test_organization_billing_record.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/billing-parties/{id}/copy-contact

- **Handler**: inline in `do_POST`
- **Service**: `apply_copy_contact_details(conn, target_id, source_id, confirmed_fields=..., copy_delivery_method=...)`
- **Accepted fields**: `source_billing_party_id` (required), `confirmed_fields` (optional), `copy_delivery_method` (default false)
- **Success status**: 200
- **Success response**: result dict with updated fields
- **DB tables**: `billing_parties`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_organization_billing_record.py`
- **Missing contract coverage**: HTTP-level shape

---

## 5. Rate Rules

### POST /api/rate-rules

- **Handler**: inline in `do_POST`
- **Service**: `create_rate_rule_from_payload(conn, data)`
- **Accepted fields**: `scope` ("global", "person", "participant_combination", "billing_relationship"), `person_id`, `participant_person_ids`, `billing_relationship_account_id`, `amount_cents` / `amount_dollars`, `effective_from`, `effective_through`, `service_mode`, `billing_session_type`, `appointment_status`
- **Success status**: 200
- **Success response**: serialized rate rule dict
- **Error status codes**: 400 (various safe validation messages)
- **DB tables**: `rate_rules`, `rate_rule_participants`, `audit_log`
- **Idempotent**: no — creates a new rule
- **Existing tests**: `test_rate_card_default.py`, `test_review_services.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/rate-rules/preview

- **Handler**: inline in `do_POST`
- **Service**: `preview_rate_suggestion(conn, data)`
- **Accepted fields**: same as rate rule creation (used for preview)
- **Success status**: 200
- **Success response**: rate suggestion preview dict
- **DB tables**: none (read-only preview)
- **Idempotent**: yes
- **Existing tests**: `test_rate_card_default.py`
- **Missing contract coverage**: HTTP-level shape
- **Note**: This endpoint is technically read-only but uses POST because it accepts a complex body

### POST /api/rate-rules/{id}/replace

- **Handler**: inline in `do_POST`
- **Service**: `replace_rate_rule_from_payload(conn, rule_id, data)`
- **Accepted fields**: same as create
- **Success status**: 200
- **Success response**: new serialized rate rule dict
- **DB tables**: `rate_rules` (ends old rule, creates new), `rate_rule_participants`, `audit_log`
- **Idempotent**: no
- **Existing tests**: `test_rate_card_default.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/rate-rules/{id}/end

- **Handler**: inline in `do_POST`
- **Service**: `end_rate_rule(conn, rule_id, data.get("effective_through") or "")`
- **Accepted fields**: `effective_through` (default "")
- **Success status**: 200
- **Success response**: updated rate rule dict
- **DB tables**: `rate_rules`, `audit_log`
- **Idempotent**: yes — ending an already-ended rule is a no-op
- **Existing tests**: `test_rate_card_default.py`
- **Missing contract coverage**: HTTP-level shape

---

## 6. Business Profile

### POST /api/business-profile

- **Handler**: inline in `do_POST`
- **Service**: `save_business_profile(conn, data)`
- **Accepted fields**: `business_name`, `provider_display_name`, `address_line_1`, `city`, `state`, `postal_code`, `phone`, `email`, `payee_name`, `payment_address_line_1`, `payment_city`, `payment_state`, `payment_postal_code`, `zelle_recipient`, `invoice_total_label`, `invoice_number_format`, `business_insurance_code`, `business_insurance_name`
- **Required fields**: `business_name` (enforced: "Business name is required.")
- **Success status**: 200
- **Success response**: saved business profile dict
- **DB tables**: `business_profile`
- **Idempotent**: yes — overwrites the single active profile
- **Existing tests**: `test_review_services.py`, `test_staging_api.py`
- **Missing contract coverage**: HTTP-level shape

---

## 7. Invoices

### POST /api/invoices

- **Handler**: inline in `do_POST`
- **Service**: `create_invoice_draft(conn, data)`
- **Accepted fields**: `bill_to_party_id`, `billing_month`, `period_start`, `period_end`, `session_ids`
- **Required fields**: `bill_to_party_id` (enforced: "Select an active bill-to party.")
- **Success status**: 200
- **Success response**: invoice draft dict
- **Error status codes**: 400 (various safe validation messages)
- **DB tables**: `invoices`, `invoice_line_items`, `audit_log`
- **Idempotent**: yes — monthly billing identity prevents duplicate drafts for same party/month
- **Existing tests**: `test_invoice_lifecycle.py`, `test_staging_api.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/invoices/stage

- **Handler**: inline in `do_POST`
- **Service**: `stage_approved_sessions_to_monthly_drafts(conn, session_ids=session_ids)`
- **Accepted fields**: `session_ids` (optional list of strings; if omitted, stages all eligible)
- **Required fields**: none
- **Validation**: if `session_ids` is present, must be a list of non-empty strings
- **Success status**: 200
- **Success response**: `{"drafts_created": int, "drafts_reused": int, "sessions_staged": int, "sessions_already_staged": int, "sessions_moved": int, "sessions_removed_ineligible": int, "sessions_skipped": int, "errors": [...]}`
- **DB tables**: `invoices`, `invoice_line_items`, `audit_log`
- **Idempotent**: yes — re-staging creates no duplicate drafts or lines
- **Existing tests**: `test_staging_api.py` (comprehensive)
- **Missing contract coverage**: none significant

### POST /api/invoices/{id}

- **Handler**: inline in `do_POST` (falls through to `update_invoice_draft`)
- **Service**: `update_invoice_draft(conn, invoice_id, data)`
- **Accepted fields**: `billing_month`, `period_start`, `period_end`, `expected_revision`
- **Success status**: 200
- **Success response**: updated invoice dict
- **Error status codes**: 400 ("Only a draft invoice can be changed.", "Invoice has changed. Please reload and try again.")
- **DB tables**: `invoices`, `audit_log`
- **Idempotent**: yes (with same revision)
- **Existing tests**: `test_invoice_lifecycle.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/invoices/{id}/update-line

- **Handler**: inline in `do_POST`
- **Service**: `update_invoice_line_item(conn, invoice_id, line_id=data["invoice_line_item_id"], description=data["description"], amount_cents=data["amount_cents"], amount_scope=data["amount_scope"], reason=data["reason"], expected_revision=data["expected_revision"])`
- **Accepted fields**: `invoice_line_item_id` (required), `description` (required), `amount_cents` (required, int), `amount_scope` (required when amount changes: "invoice_line_only" or "invoice_line_and_session"), `reason` (required when amount changes), `expected_revision` (required, int)
- **Required fields**: all accessed via `data["..."]` — **KeyError if any missing**
- **Success status**: 200
- **Success response**: updated invoice dict
- **Error status codes**: 400 (various safe validation messages)
- **DB tables**: `invoice_line_items`, `invoice_line_item_corrections`, `sessions` (if session-scoped), `audit_log`
- **Idempotent**: no — revision locking prevents concurrent edits
- **Existing tests**: `test_invoice_line_editing.py`
- **Missing contract coverage**: HTTP-level shape, missing required field behavior

### POST /api/invoices/{id}/add-sessions

- **Handler**: inline in `do_POST`
- **Service**: `add_sessions_to_draft(conn, invoice_id, data.get("session_ids") or [])`
- **Accepted fields**: `session_ids` (list of strings)
- **Success status**: 200
- **Success response**: updated invoice dict
- **Error status codes**: 400 ("Session is already included in this draft.", "Source session was not found.", "All invoice sessions must use the selected bill-to party.", "Session is outside the invoice billing period.")
- **DB tables**: `invoice_line_items`, `audit_log`
- **Idempotent**: yes — adding already-included sessions is handled
- **Existing tests**: `test_invoice_lifecycle.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/invoices/{id}/remove-line

- **Handler**: inline in `do_POST`
- **Service**: `remove_line_from_draft(conn, invoice_id, data["invoice_line_item_id"])`
- **Accepted fields**: `invoice_line_item_id` (required — **KeyError if missing**)
- **Success status**: 200
- **Success response**: updated invoice dict
- **DB tables**: `invoice_line_items`, `audit_log`
- **Idempotent**: no — removing a non-existent line raises an error
- **Existing tests**: `test_invoice_lifecycle.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/invoices/{id}/preview-finalize

- **Handler**: inline in `do_POST`
- **Service**: `preview_finalization(conn, invoice_id, data=data)`
- **Accepted fields**: optional draft-like fields for backward compatibility plus `insurance_coding_included`, `insurance_diagnosis_code`
- **Success status**: 200
- **Success response**: finalization preview dict
- **DB tables**: none mutated; read-only readiness/preview response
- **Idempotent**: yes
- **Existing tests**: `test_invoice_lifecycle.py`, `test_invoice_readiness.py`, `test_payment_and_finalization.py`, `test_write_endpoint_contracts.py`
- **Note**: Review & Finalize uses this endpoint for readiness/revision only. The visual approval preview is the side-effect-free canonical PDF from `POST /api/invoices/{id}/draft-pdf`.

### POST /api/invoices/{id}/finalize

- **Handler**: inline in `do_POST`
- **Service**: `finalize_invoice(conn, invoice_id, expected_revision=data.get("expected_revision"), insurance_coding_included=bool(data.get("insurance_coding_included")), insurance_diagnosis_code=str(data.get("insurance_diagnosis_code") or ""))`
- **Accepted fields**: `confirmed` (required, must be true), `expected_revision`, `insurance_coding_included`, `insurance_diagnosis_code`
- **Required fields**: `confirmed` (enforced: "Explicit finalization confirmation is required.")
- **Success status**: 200
- **Success response**: finalized invoice dict with snapshot and PDF path
- **Error status codes**: 400 (various safe validation messages)
- **DB tables**: `invoices` (status → finalized, snapshot frozen), `invoice_line_items` (frozen), `invoice_sequences`, `audit_log`, PDF file written
- **Idempotent**: no — finalizing a non-draft raises "Only a draft invoice can be changed."
- **Existing tests**: `test_invoice_lifecycle.py`, `test_payment_and_finalization.py`
- **Missing contract coverage**: HTTP-level shape, missing `confirmed` behavior

### POST /api/invoices/{id}/void

- **Handler**: inline in `do_POST`
- **Service**: `void_invoice(conn, invoice_id, data.get("reason") or "")`
- **Accepted fields**: `reason` (required, non-empty)
- **Success status**: 200
- **Success response**: voided invoice dict
- **Error status codes**: 400 ("A void reason is required.", "Only a finalized invoice can be voided.")
- **DB tables**: `invoices` (status → void), `audit_log`
- **Idempotent**: no — voiding a non-finalized invoice raises an error
- **Existing tests**: `test_invoice_lifecycle.py`, `test_payment_and_finalization.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/invoices/{id}/filing-owner

- **Handler**: inline in `do_POST`
- **Service**: `update_invoice_filing_owner(conn, invoice_id, data.get("person_id"))`
- **Accepted fields**: `person_id` (optional, can be None to clear)
- **Success status**: 200
- **Success response**: updated invoice dict
- **DB tables**: `invoices`, `audit_log`
- **Idempotent**: yes
- **Existing tests**: `test_invoice_lifecycle.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/invoices/{id}/document-action

- **Handler**: inline in `do_POST`
- **Service**: `trusted_invoice_document_action(conn, invoice_id, data.get("action") or "")`
- **Accepted fields**: `action` (string)
- **Success status**: 200
- **Success response**: action result dict
- **DB tables**: may write files (PDF open/reveal actions)
- **Idempotent**: yes
- **Existing tests**: `test_invoice_lifecycle.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/invoices/{id}/print-preview

- **Handler**: inline in `do_POST`
- **Service**: `build_print_preview_html` (read-only rendering)
- **Accepted fields**: `insurance_coding_included`, `insurance_diagnosis_code`
- **Success status**: 200 (HTML content)
- **Error status codes**: 400 ("Print preview is only available for draft invoices.")
- **DB tables**: none (read-only)
- **Note**: POST version supports insurance coding payload; GET version does not

### POST /api/invoices/{id}/draft-pdf

- **Handler**: inline in `do_POST`
- **Service**: `generate_draft_pdf_bytes` (read-only rendering)
- **Accepted fields**: `insurance_coding_included`, `insurance_diagnosis_code`
- **Success status**: 200 (PDF content)
- **Error status codes**: 400 ("Draft PDF preview is only available for draft invoices.")
- **DB tables**: none (read-only)
- **Note**: POST version supports insurance coding payload; GET version does not

### POST /api/invoices/{id}/finalization-preview-token

- **Handler**: inline in `do_POST`
- **Service**: short-lived in-memory preview token creation
- **Accepted fields**: `insurance_coding_included`, `insurance_diagnosis_code`
- **Success status**: 200 (JSON with `preview_pdf_url`)
- **Error status codes**: 400 ("Finalization PDF preview is only available for draft invoices.")
- **DB tables**: none (read-only; no SQLite writes)
- **Note**: The token avoids putting diagnosis-code preview values in the iframe URL.

### GET /api/invoices/{id}/finalization-preview-pdf

- **Handler**: inline in `do_GET`
- **Service**: `generate_draft_pdf_bytes` (read-only rendering)
- **Accepted fields**: optional `token` query parameter from `finalization-preview-token`
- **Success status**: 200 (PDF content)
- **Error status codes**: 400 ("Finalization PDF preview is only available for draft invoices.")
- **DB tables**: none (read-only)
- **Note**: Review & Finalize embeds this same-origin PDF URL directly for Safari instead of using a blob URL.

---

## 8. Payments

### POST /api/invoices/{id}/payments

- **Handler**: inline in `do_POST`
- **Service**: `record_invoice_payment(conn, invoice_id=invoice_id, payment_date=data.get("payment_date") or "", amount_cents=data.get("amount_cents"), payment_method=data.get("payment_method") or "", reference_number=data.get("reference_number"), received_from_name=data.get("received_from_name"), administrative_note=data.get("administrative_note"))`
- **Accepted fields**: `payment_date` (required), `amount_cents` (required, int, > 0), `payment_method` (required), `reference_number`, `received_from_name`, `administrative_note`
- **Required fields**: `payment_date`, `amount_cents`, `payment_method`
- **Enum-like values for payment_method**: validated by `_validate_payment_method` (supported methods)
- **Success status**: 200
- **Success response**: `{"invoice": balance_summary, "payment": payment_dict, "allocations": [...]}`
- **Duplicate response**: `{"invoice": ..., "payment": ..., "allocations": [...], "duplicate_submission_ignored": true}` (returns 200, not an error)
- **Error status codes**: 400 (various safe validation messages)
- **DB tables**: `payments`, `payment_allocations`, `audit_log`
- **Idempotent**: yes — duplicate detection returns existing payment with `duplicate_submission_ignored: true`
- **Existing tests**: `test_payment_api.py`, `test_payment_services.py`
- **Missing contract coverage**: HTTP-level shape, duplicate submission behavior

### POST /api/payments/allocations/{id}/reverse

- **Handler**: inline in `do_POST`
- **Service**: `reverse_allocation(conn, allocation_id, reason=data.get("reason") or "", idempotency_key=data.get("idempotency_key"))`
- **Accepted fields**: `reason` (required), `idempotency_key` (optional)
- **Success status**: 200
- **Success response**: reversal result dict
- **Error status codes**: 400 ("A reversal reason is required.", "Allocation was not found.", "Allocation is already reversed.")
- **DB tables**: `payment_allocations`, `audit_log`
- **Idempotent**: yes (with `idempotency_key`)
- **Existing tests**: `test_payment_api.py`, `test_payment_services.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/payments/{id}/apply-funds

- **Handler**: inline in `do_POST`
- **Service**: `apply_available_funds(conn, payment_id, invoice_id=data.get("invoice_id") or "", amount_cents=data.get("amount_cents"), idempotency_key=data.get("idempotency_key"))`
- **Accepted fields**: `invoice_id` (required), `amount_cents` (required, int, > 0), `idempotency_key` (optional)
- **Success status**: 200
- **Success response**: allocation result dict
- **Error status codes**: 400 (various safe validation messages)
- **DB tables**: `payment_allocations`, `audit_log`
- **Idempotent**: yes (with `idempotency_key`)
- **Existing tests**: `test_payment_api.py`, `test_payment_services.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/payments/{id}/void

- **Handler**: inline in `do_POST`
- **Service**: `void_payment(conn, payment_id, reason=data.get("reason") or "", idempotency_key=data.get("idempotency_key"))`
- **Accepted fields**: `reason` (required), `idempotency_key` (optional)
- **Success status**: 200
- **Success response**: voided payment dict
- **Error status codes**: 400 ("A void reason is required.", "Payment was not found.", "Payment is already void.", "Cannot void a payment with active allocations. Reverse all allocations first.")
- **DB tables**: `payments`, `payment_allocations`, `audit_log`
- **Idempotent**: yes (with `idempotency_key`)
- **Existing tests**: `test_payment_api.py`, `test_payment_services.py`
- **Missing contract coverage**: HTTP-level shape

---

## 9. Receipts

### POST /api/payments/{id}/receipt

- **Handler**: inline in `do_POST`
- **Service**: `create_payment_receipt(conn, payment_id, filing_owner_person_id=data.get("filing_owner_person_id"))`
- **Accepted fields**: `filing_owner_person_id` (optional)
- **Success status**: 200
- **Success response**: receipt dict
- **DB tables**: `payment_receipts`, `receipt_sequences`, `audit_log`, PDF file written
- **Idempotent**: yes — creating a receipt for a payment that already has one returns the existing receipt
- **Existing tests**: `test_payment_receipts.py`
- **Missing contract coverage**: HTTP-level shape

### POST /api/payments/{id}/receipt-document-action

- **Handler**: inline in `do_POST`
- **Service**: `trusted_receipt_document_action(conn, receipt_id, data.get("action") or "")`
- **Accepted fields**: `action` (string)
- **Success status**: 200
- **Success response**: action result dict
- **Error status codes**: 404 ("Receipt was not found.")
- **DB tables**: may write files (PDF open/reveal actions)
- **Idempotent**: yes
- **Existing tests**: `test_payment_receipts.py`
- **Missing contract coverage**: HTTP-level shape

---

## 10. Calendar Sync and Import

### POST /api/sync/run

- **Handler**: inline in `do_POST`
- **Service**: `sync_calendar_automatically(review_sync_config(database_path), transport=REVIEW_SYNC_TRANSPORT)`
- **Accepted fields**: none (body is read but not used)
- **Success status**: 200
- **Success response**: `{"rows_fetched": int, "rows_imported": int, "duplicate_snapshots_skipped": int, "review_items_changed": int, "mode": "full"|"incremental", "status": sync_status_payload}`
- **Error status codes**: 503 (SyncError, sanitized)
- **DB tables**: `raw_calendar_snapshots`, `calendar_event_candidates`, `sessions`, `sync_state`, `review_items`; after successful non-dry-run sync, idempotent monthly invoice staging may also update draft `invoices` and `invoice_line_items` for previously approved sessions that have become eligible
- **Idempotent**: yes — snapshot_key uniqueness prevents duplicate imports, and invoice staging reuses existing monthly drafts/lines
- **Existing tests**: `test_review_server.py` (mocked), `test_sync.py`, `test_manual_sync_integration.py`
- **Missing contract coverage**: HTTP-level shape (mocked tests exist)

### POST /api/sync/rebuild

- **Handler**: inline in `do_POST`
- **Service**: `rebuild_calendar_data_from_sheet(review_sync_config(database_path), transport=REVIEW_SYNC_TRANSPORT)`
- **Accepted fields**: `confirmed` (required, must be true)
- **Required fields**: `confirmed` (enforced: "Explicit rebuild confirmation is required.")
- **Success status**: 200
- **Success response**: `{"rows_fetched": int, "rows_imported": int, "duplicate_snapshots_skipped": int, "review_items_changed": int, "mode": "full", "backup_created": bool, "status": sync_status_payload}`
- **DB tables**: `raw_calendar_snapshots`, `calendar_event_candidates`, `sessions`, `sync_state`, `review_items`; after successful rebuild sync, idempotent monthly invoice staging may also update draft `invoices` and `invoice_line_items` for previously approved sessions that have become eligible
- **Idempotent**: yes — snapshot_key uniqueness prevents duplicates, invoice staging reuses existing monthly drafts/lines, and rebuild creates a backup first
- **Existing tests**: `test_review_server_sync.py`
- **Missing contract coverage**: HTTP-level shape, missing `confirmed` behavior

---

## 11. Service Catalog

### POST /api/service-catalog/{id}/{activate|deactivate}

- **Handler**: inline in `do_POST`
- **Service**: `set_service_active(conn, service_id, active=parts[3] != "deactivate")`
- **Accepted fields**: none (action encoded in URL path)
- **Success status**: 200
- **Success response**: updated service dict
- **DB tables**: `service_catalog`
- **Idempotent**: yes
- **Existing tests**: none directly
- **Missing contract coverage**: HTTP-level shape

---

## 12. Reports Download (GET, but writes CSV to response)

### GET /api/reports/download

- **Handler**: inline in `do_GET`
- **Service**: `generate_report_csv(conn, report_type, year)`
- **Note**: This is a GET endpoint that generates a CSV download. It does not mutate SQLite but writes a CSV response. Included for completeness.
- **Success status**: 200 (CSV content)
- **Error status codes**: 400 (invalid year)
- **Existing tests**: `test_report_api.py`

---

## Summary of Write Endpoints

| # | Method | Path | Service | Category |
|---|--------|------|---------|----------|
| 1 | POST | /api/review/candidates/{id}/save | save_interpretation | Review |
| 2 | POST | /api/review/candidates/{id}/save-person | save_person_section | Review |
| 3 | POST | /api/review/candidates/{id}/save-relationship | save_relationship_section | Review |
| 4 | POST | /api/review/candidates/{id}/save-billing | save_billing_section | Review |
| 5 | POST | /api/review/candidates/{id}/save-session | save_session_draft | Review |
| 6 | POST | /api/review/candidates/{id}/approve | approve_candidate + staging | Review |
| 7 | POST | /api/review/candidates/{id}/mark | mark_candidate | Review |
| 8 | POST | /api/review/candidates/{id}/restore | restore_candidate | Review |
| 9 | POST | /api/review/candidates/{id}/send-to-review | promote_candidate_to_review | Review |
| 10 | POST | /api/review/candidates/{id}/refresh | refresh_candidate_suggestions | Review |
| 11 | POST | /api/review/recalc-rates | recalc_unapproved_session_rates | Review |
| 12 | POST | /api/review/reparse-candidates | reparse_unapproved_candidates | Review |
| 13 | POST | /api/people | create_person | People |
| 14 | POST | /api/people/{id} | update_person | People |
| 15 | POST | /api/people/{id}/aliases | save_person_alias | People |
| 16 | POST | /api/people/{id}/merge | merge_people | People |
| 17 | POST | /api/accounts | create_account | Accounts |
| 18 | POST | /api/accounts/from-client | create_account_or_return_existing | Accounts |
| 19 | POST | /api/accounts/{id} | update_account | Accounts |
| 20 | POST | /api/accounts/{id}/deactivate | deactivate_account | Accounts |
| 21 | POST | /api/accounts/{id}/reactivate | reactivate_account | Accounts |
| 22 | POST | /api/accounts/{id}/update-billing-relationship | update_billing_relationship | Accounts |
| 23 | POST | /api/accounts/{id}/remove-member | remove_account_member | Accounts |
| 24 | POST | /api/account-members | add_account_member | Accounts |
| 25 | POST | /api/billing-relationships/setup | setup_billing_relationship | Relationships |
| 26 | POST | /api/billing-relationships/normalize-payer | normalize_duplicate_payer_billing_parties | Relationships |
| 27 | POST | /api/billing-parties | create_billing_party | Billing Parties |
| 28 | POST | /api/billing-parties/{id} | update_billing_party | Billing Parties |
| 29 | POST | /api/billing-parties/{id}/copy-contact | apply_copy_contact_details | Billing Parties |
| 30 | POST | /api/rate-rules | create_rate_rule_from_payload | Rates |
| 31 | POST | /api/rate-rules/preview | preview_rate_suggestion | Rates (read-only) |
| 32 | POST | /api/rate-rules/{id}/replace | replace_rate_rule_from_payload | Rates |
| 33 | POST | /api/rate-rules/{id}/end | end_rate_rule | Rates |
| 34 | POST | /api/business-profile | save_business_profile | Business Profile |
| 35 | POST | /api/invoices | create_invoice_draft | Invoices |
| 36 | POST | /api/invoices/stage | stage_approved_sessions_to_monthly_drafts | Invoices |
| 37 | POST | /api/invoices/{id} | update_invoice_draft | Invoices |
| 38 | POST | /api/invoices/{id}/update-line | update_invoice_line_item | Invoices |
| 39 | POST | /api/invoices/{id}/add-sessions | add_sessions_to_draft | Invoices |
| 40 | POST | /api/invoices/{id}/remove-line | remove_line_from_draft | Invoices |
| 41 | POST | /api/invoices/{id}/preview-finalize | preview_finalization | Invoices |
| 42 | POST | /api/invoices/{id}/finalize | finalize_invoice | Invoices |
| 43 | POST | /api/invoices/{id}/void | void_invoice | Invoices |
| 44 | POST | /api/invoices/{id}/filing-owner | update_invoice_filing_owner | Invoices |
| 45 | POST | /api/invoices/{id}/document-action | trusted_invoice_document_action | Invoices |
| 46 | POST | /api/invoices/{id}/print-preview | build_print_preview_html | Invoices (read-only) |
| 47 | POST | /api/invoices/{id}/draft-pdf | generate_draft_pdf_bytes | Invoices (read-only) |
| 48 | POST | /api/invoices/{id}/payments | record_invoice_payment | Payments |
| 49 | POST | /api/payments/allocations/{id}/reverse | reverse_allocation | Payments |
| 50 | POST | /api/payments/{id}/apply-funds | apply_available_funds | Payments |
| 51 | POST | /api/payments/{id}/void | void_payment | Payments |
| 52 | POST | /api/payments/{id}/receipt | create_payment_receipt | Receipts |
| 53 | POST | /api/payments/{id}/receipt-document-action | trusted_receipt_document_action | Receipts |
| 54 | POST | /api/sync/run | sync_calendar_automatically | Sync |
| 55 | POST | /api/sync/rebuild | rebuild_calendar_data_from_sheet | Sync |
| 56 | POST | /api/service-catalog/{id}/{activate\|deactivate} | set_service_active | Service Catalog |

**Total write endpoints: 56** (including 3 read-only POSTs that use POST for complex body acceptance)

---

## Boundary-Hardening Gaps for Round 4A.2

The following gaps were identified during this inventory. They are documented here for
future boundary-hardening work and are **not implemented in this round**.

### Ad Hoc Payload Parsing

- **Multiple endpoints** access required fields via `data["key"]` (direct dict access) rather than `.get()`, causing unhandled `KeyError` exceptions that are sanitized to "An unexpected error occurred." instead of a meaningful 400 response. Affected endpoints:
  - `POST /api/people/{id}/merge` — `data["duplicate_person_id"]`
  - `POST /api/accounts` — `data["account_name"]`, `data["person_id"]`
  - `POST /api/accounts/from-client` — `data["person_id"]`, `data["account_name"]`
  - `POST /api/accounts/{id}/remove-member` — `data["person_id"]`
  - `POST /api/account-members` — `data["account_id"]`, `data["person_id"]`
  - `POST /api/invoices/{id}/update-line` — `data["invoice_line_item_id"]`, `data["description"]`, `data["amount_cents"]`, `data["amount_scope"]`, `data["reason"]`, `data["expected_revision"]`
  - `POST /api/invoices/{id}/remove-line` — `data["invoice_line_item_id"]`

### Missing Explicit Type Checks

- `amount_cents` in payment endpoints is passed through without explicit HTTP-layer type validation (the service checks `isinstance(amount_cents, int)` but a string from JSON would raise a `TypeError`, not a `ValueError`)
- `expected_revision` in invoice line editing is passed without type checking at the HTTP layer
- `confirmed` in finalize/rebuild is checked with `data.get("confirmed") is not True` / `not data.get("confirmed")` — truthy non-boolean values may behave unexpectedly
- `approved_by_user` in alias saving uses `bool(data.get(...))` which coerces strings/numbers unpredictably

### Inconsistent Required-Field Handling

- Some endpoints use `data.get("key") or ""` (defaulting to empty string), others use `data["key"]` (raising KeyError), and others use `data.get("key")` (defaulting to None) — no consistent pattern
- `payment_date` in payment recording uses `data.get("payment_date") or ""` while `amount_cents` uses `data.get("amount_cents")` (None if missing, caught by service)
- `reason` fields default to `""` via `data.get("reason") or ""` in some endpoints but are required at service level, creating a two-layer validation pattern

### Inconsistent Status Codes

- POST handlers use `default_status=400` for unknown exceptions while GET handlers use `default_status=500`
- `BillingPartyNotFoundError` returns 404 but `ValueError("Person not found.")` also returns 404 only because it's in the safe messages list — other "not found" ValueErrors return 400
- `Account not found.` returns 404 via special-case in `send_error_response` but other "not found" errors return 400

### Inconsistent Success Envelopes

- Most endpoints return the service function result directly (various shapes)
- `POST /api/account-members` wraps in `{"account_member_id": ...}`
- `POST /api/accounts/from-client` returns `{"ok": true/false, "existing": bool, ...}` with a different shape for new vs. existing
- `POST /api/review/recalc-rates` returns `{"ok": true, "sessions_updated": int}`
- `POST /api/review/reparse-candidates` returns `{"ok": true, ...result}`
- `POST /api/billing-relationships/normalize-payer` returns `{"ok": true, ...result}`
- `POST /api/accounts/{id}/remove-member` returns `{"ok": true}`
- Some endpoints include `"ok": true` in the service return, others do not

### Inconsistent Error Envelopes

- All errors use `{"ok": false, "error": "..."}` but the `ok` key is not present in all success responses
- Some success responses include `"ok": true` while others do not — the envelope is not standardized

### Direct Exposure Risk from Exception Messages

- The `is_safe_validation_error` function uses a hardcoded set of safe message strings and prefix patterns. Any new `ValueError` message not in this set will be sanitized, but the list is manually maintained and could fall out of sync with service code
- `KeyError` exceptions from direct dict access are not in the safe list and are sanitized to "An unexpected error occurred." — the user gets no indication which field was missing
- `TypeError` from type mismatches (e.g., passing a string where int is expected) is not handled as a safe validation error

### Duplicated Validation Logic

- `session_ids` validation (list of non-empty strings) is done inline in the `/api/invoices/stage` handler rather than in the service function
- `confirmed` check for finalize is done inline in the handler while `confirmed` for rebuild is also done inline — same pattern in two places
- The `is_safe_validation_error` function maintains a large hardcoded message set that duplicates validation logic spread across multiple service files

### Endpoints with Weak or Missing Idempotency Protection

- `POST /api/people` — no idempotency; duplicate names create duplicate people
- `POST /api/billing-parties` — no idempotency; duplicate billing names create duplicate parties
- `POST /api/accounts` — duplicate account names are blocked at service level but not via an idempotency key
- `POST /api/invoices` — monthly billing identity provides natural idempotency but no explicit idempotency key
- `POST /api/rate-rules` — no idempotency; duplicate rules can be created
- `POST /api/business-profile` — idempotent by nature (single active profile) but no key
- Payment endpoints (`record_invoice_payment`, `reverse_allocation`, `void_payment`, `apply_available_funds`) support optional `idempotency_key` but do not require it
- `POST /api/review/candidates/{id}/approve` — idempotent for re-approval but no idempotency key for concurrent requests

### Write Handlers That Mix Request Parsing, Business Logic, and Persistence

- `POST /api/review/candidates/{id}/approve` — the handler contains post-approval staging logic (checking payment status, calling staging, sanitizing staging errors) that should be in a service function
- `POST /api/invoices/stage` — `session_ids` validation is done in the handler rather than the service
- `POST /api/sync/run` and `POST /api/sync/rebuild` — handler constructs sync config and calls sync functions directly
- `POST /api/billing-relationships/normalize-payer` — handler does `person_id` validation inline

### Tests That Cannot Yet Assert Rollback or Partial-Write Safety

- The handler test pattern uses `object.__new__` and mock connections, making it difficult to verify transaction rollback behavior at the HTTP level
- No test currently verifies that a failed staging after successful approval does not leave partial state
- No test currently verifies that a failed payment allocation does not leave a partial payment record
- No test currently verifies that a failed merge does not leave partial identity state
- The `finalize_invoice` transaction safety is tested at the service level but not through the HTTP handler

---

## Round 4A.1 Test Results

Test commands and results are recorded in the completion report for this round.

This round documents current behavior and does not yet standardize validation,
response envelopes, or error handling.

---

## Round 4A.2: Request Validation Helpers for High-Risk Review Write Endpoints

Round 4A.2 adds small, explicit request-parsing and validation helpers for the
four highest-risk review write workflows. All existing endpoint paths, payload
keys, response shapes, status codes, business rules, and user-visible behavior
are preserved exactly.

### New Module

`app/jordana_invoice/request_validation.py`

This module provides frozen dataclass request types and explicit parser
functions that validate request shape and supported primitive values only.
Business validation (database-backed and billing-domain decisions) remains
in the service layer.

### Request Types

| Dataclass | Frozen | Purpose |
|-----------|--------|---------|
| `ApproveSessionRequest` | yes | Wraps validated approval payload |
| `SaveSectionRequest` | yes | Wraps validated section-level save payload |
| `MarkCandidateRequest` | yes | Wraps validated mark/duplicate-resolution payload with extracted `classification` and `reason` |
| `RestoreCandidateRequest` | yes | Wraps validated restore payload with extracted `reason` |

### Parser Functions

| Parser | Endpoint | Validates |
|--------|----------|-----------|
| `parse_approve_session_request` | POST `/api/review/candidates/{id}/approve` | participants list-of-dicts, billing_party_id non-empty str, duration int (not bool), service_mode str, time_category str, approved_rate str-or-int (not bool), payment_status str, billing_treatment str, amount_received str-or-int, payment_date str, payment_method str, reference_number str, administrative_note str, billing_session_type str, rate_scope str, rate_override_reason str, account_id str, billable_status str, duration_choice str, custom_duration_minutes int, custom_service_description str, custom_service_code str, suggested_rate str-or-int, appointment_method str |
| `parse_save_interpretation_request` | POST `/api/review/candidates/{id}/save` | Same field set as approval (general save accepts all session fields) |
| `parse_save_person_section_request` | POST `/api/review/candidates/{id}/save-person` | person dict, person_id non-empty str, first_name str, last_name str, display_name str |
| `parse_save_relationship_section_request` | POST `/api/review/candidates/{id}/save-relationship` | participants list-of-dicts, account_id non-empty str, primary_person_id non-empty str, default_billing_party_id non-empty str, billing_party_id non-empty str |
| `parse_save_billing_section_request` | POST `/api/review/candidates/{id}/save-billing` | billing_party_id non-empty str, bill_to_person_id non-empty str, billing_party dict |
| `parse_save_session_draft_request` | POST `/api/review/candidates/{id}/save-session` | approved_duration_minutes int, duration_minutes int, duration_choice str, custom_duration_minutes int, billing_session_type str, custom_service_description str, custom_service_code str, approved_rate str-or-int, suggested_rate str-or-int, rate_scope str, rate_override_reason str, payment_status str, billing_treatment str, billable_status str, amount_received str-or-int, payment_date str, payment_method str, reference_number str, administrative_note str, service_mode str |
| `parse_mark_candidate_request` | POST `/api/review/candidates/{id}/mark` | classification str from allowed set (personal, administrative, nonbillable, duplicate, client_session), reason str; defaults classification to "personal" and reason to "" |
| `parse_restore_candidate_request` | POST `/api/review/candidates/{id}/restore` | reason str; defaults to "" |

### Legacy Aliases Preserved

All legacy aliases continue to be accepted by the service layer. The parsers
validate the alias keys with the same type rules as the primary keys:

- `duration_minutes` accepted alongside `approved_duration_minutes`
- `approved_rate` accepted as string dollars or integer cents (not boolean)
- `suggested_rate` accepted as string or integer
- `amount_received` accepted as string or integer
- `payment_status` accepted as string (service normalizes via `_LEGACY_PAYMENT_MAP`)

### Error Handling

`RequestValidationError` is a subclass of `ValueError`. The
`is_safe_validation_error` function in `review_server.py` recognizes it as a
safe validation error, so parser error messages are returned to the client as
400 responses with `{"ok": false, "error": "..."}` — not sanitized to
"An unexpected error occurred."

### Unknown Field Behavior

Unknown fields are passed through silently by all parsers, preserving the
current behavior where the service layer ignores unrecognized keys.

### Write-Token Enforcement

Write-token enforcement occurs before request body parsing in the handler,
so validation helpers are only called after the token check passes. A missing
or incorrect token returns 403 before any validation runs.

### No Persistence on Validation Failure

When a parser raises `RequestValidationError`, the handler returns a 400
response immediately. The service function is never called, so no database
writes occur on validation failure.

### Preserved Contracts

All 59 existing write-endpoint contract tests in
`tests/test_write_endpoint_contracts.py` continue to pass without
modification. No endpoint paths, payload keys, response shapes, status codes,
business rules, or user-visible behavior have changed.

### New Tests

`tests/test_request_validation.py` — 102 focused tests covering:

- valid payload acceptance for each parser
- wrong top-level JSON type rejection
- wrong field type rejection (string vs int vs list vs dict)
- empty identifier rejection
- invalid enum-like value rejection (mark classification)
- boolean incorrectly supplied where integer expected
- legacy alias acceptance (duration_minutes, approved_rate)
- unknown field pass-through
- sanitized error behavior (messages preserved, not generic)
- no persistence on validation failure (service function not called)
- unchanged success response contracts (status 200, response shape)
- unchanged failure response contracts (status 400, ok=false, error key)
- write-token enforcement before validation (403 before 400)
- restore endpoint success-with-warning behavior

### Restore Endpoint Success-With-Warning Behavior

The `restore_candidate` service function commits the restore before attempting
the secondary suggestion refresh. If `refresh_candidate_suggestions` fails after
the committed restore, the response remains a normal 200 success and includes an
additive `warning` field with the sanitized message `"Candidate was restored,
but suggestions could not be refreshed."` The warning does not roll back the
restore and does not change endpoint paths, payload keys, schemas, or primary
business rules.

## Round 4A.3: Remaining Write Endpoint Parsers

### Scope

All remaining write endpoints not covered in Round 4A.2 now have request
validation parsers in `request_validation.py` and are wired into
`review_server.py`. The parsers validate shape (JSON object, field types,
enum-like choices) before the service layer is called.

### New Parsers Added

**People, aliases, merges:**
- `parse_create_person_request` — POST /api/people
- `parse_update_person_request` — POST /api/people/{id}
- `parse_save_person_alias_request` — POST /api/people/{id}/aliases
- `parse_merge_people_request` — POST /api/people/{id}/merge

**Accounts:**
- `parse_create_account_request` — POST /api/accounts
- `parse_create_account_from_client_request` — POST /api/accounts/from-client
- `parse_update_account_request` — POST /api/accounts/{id}
- `parse_update_billing_relationship_request` — POST /api/accounts/{id}/update-billing-relationship
- `parse_remove_account_member_request` — POST /api/accounts/{id}/remove-member
- `parse_add_account_member_request` — POST /api/account-members

**Billing relationships and parties:**
- `parse_setup_billing_relationship_request` — POST /api/billing-relationships/setup
- `parse_normalize_payer_request` — POST /api/billing-relationships/normalize-payer
- `parse_create_billing_party_request` — POST /api/billing-parties
- `parse_update_billing_party_request` — POST /api/billing-parties/{id}
- `parse_copy_contact_request` — POST /api/billing-parties/{id}/copy-contact

**Rate rules:**
- `parse_create_rate_rule_request` — POST /api/rate-rules
- `parse_preview_rate_request` — POST /api/rate-rules/preview
- `parse_replace_rate_rule_request` — POST /api/rate-rules/{id}/replace
- `parse_end_rate_rule_request` — POST /api/rate-rules/{id}/end

**Invoices:**
- `parse_create_invoice_draft_request` — POST /api/invoices
- `parse_stage_invoices_request` — POST /api/invoices/stage
- `parse_update_invoice_draft_request` — POST /api/invoices/{id}
- `parse_update_invoice_line_item_request` — POST /api/invoices/{id}/update-line
- `parse_add_sessions_to_draft_request` — POST /api/invoices/{id}/add-sessions
- `parse_remove_line_from_draft_request` — POST /api/invoices/{id}/remove-line
- `parse_preview_finalize_request` — POST /api/invoices/{id}/preview-finalize
- `parse_finalize_invoice_request` — POST /api/invoices/{id}/finalize
- `parse_void_invoice_request` — POST /api/invoices/{id}/void
- `parse_update_invoice_filing_owner_request` — POST /api/invoices/{id}/filing-owner
- `parse_document_action_request` — POST /api/invoices/{id}/document-action
- `parse_print_preview_request` — POST /api/invoices/{id}/print-preview and /draft-pdf

**Payments:**
- `parse_record_payment_request` — POST /api/invoices/{id}/payments
- `parse_reverse_allocation_request` — POST /api/payments/allocations/{id}/reverse
- `parse_apply_funds_request` — POST /api/payments/{id}/apply-funds
- `parse_void_payment_request` — POST /api/payments/{id}/void
- `parse_create_payment_receipt_request` — POST /api/payments/{id}/receipt
- `parse_document_action_request` (shared) — POST /api/payments/{id}/receipt-document-action

**Business profile:**
- `parse_save_business_profile_request` — POST /api/business-profile

**Sync:**
- `parse_sync_run_request` — POST /api/sync/run
- `parse_sync_rebuild_request` — POST /api/sync/rebuild

### Design Principles

- **Shape validation only**: Parsers validate JSON object type, field types,
  and enum-like choices. Business rules (e.g., "Payment date is required",
  "A void reason is required") remain in the service layer.
- **Unknown field pass-through**: Unknown fields are preserved in the payload
  dict and passed through to the service, matching existing behavior.
- **RequestValidationError is safe**: All parser errors raise
  `RequestValidationError`, which `send_error_response` maps to HTTP 400
  with the message preserved (not sanitized to "An unexpected error occurred.").
- **No behavioral change**: Existing success and failure response contracts
  are unchanged. Tests in `test_write_endpoint_contracts.py` verify this.

### Test Coverage

`tests/test_request_validation_round3.py` — 103 focused tests covering:

- valid payload acceptance for each parser
- missing required field rejection
- wrong top-level JSON type rejection
- wrong field type rejection (bool for int, non-string for string)
- enum-like value validation (payer_kind, delivery_method, billing_party_type)
- unknown field pass-through
- default values (confirmed=false, reason="", account_type="individual")
- `RequestValidationError` recognized as safe validation error
