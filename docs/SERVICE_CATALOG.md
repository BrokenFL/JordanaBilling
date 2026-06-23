# Service Catalog

## Billing Session Types (Active)

The service catalog contains exactly **5 active billing session types**:

1. **Psychotherapy Session** (`psychotherapy`)
2. **Psychotherapy Session / House Call** (`psychotherapy_house_call`)
3. **Psychotherapy Session / Weekend** (`psychotherapy_weekend`)
4. **Psychotherapy Session / Evening** (`psychotherapy_evening`)
5. **Custom** (`custom`)

These are the only session types that may appear in active UI dropdowns. No other session type may ever be offered as a selectable option.

## Legacy Appointment Methods (Historical)

The following are **appointment methods**, not billing session types:

- Office
- Phone
- FaceTime
- House Call (as appointment method)
- Correspondence
- Preparation
- Mediation
- Other

These are marked with `catalog_type = 'appointment_method'` and `legacy_appointment_method = 1`. They remain readable for historical records but are not offered as session type choices.

## Custom Service Descriptions

Client-specific insurance descriptions belong in `custom_service_mappings`, not the global service catalog. When a user selects "Custom" as the session type, they can enter a custom description and optional code for that session. This does not create a new global service type.

## Service Learning Constraints

The `learn_service` function is constrained:

- New services are created with `catalog_type = 'appointment_method'`
- They cannot become billing session types
- This prevents arbitrary text from becoming a sixth global session type

## Catalog Management

Reviewed text is whitespace/case normalized. A new value creates an active catalog record and audit event; capitalization variants deduplicate. Usage metadata updates on approval/invoicing.

Listing and safe activation/deactivation are available through API and CLI. Deactivation never changes historical session text or finalized line snapshots. Rename/merge UI is deferred.
