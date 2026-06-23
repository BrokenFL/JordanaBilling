# Invoice Model

`sessions` remains authoritative for approved occurrence facts and actual charged amount. `billing_parties` provides current payer/contact defaults. `business_profile` stores one active local invoice identity. `service_catalog` stores reusable current labels. `invoices` and `invoice_line_items` store lifecycle plus frozen snapshots.

All IDs are UUIDs and all money is integer cents. Drafts have no permanent number. Finalized and void invoices are immutable. Current person, payer, profile, service, or rate changes never rewrite finalized snapshots.

`invoice_sequences` stores the last number used per year. The configurable default is `YYYY-NNNN`; numbers are assigned inside finalization and never reused.

`sessions.service_mode` remains historical text while `service_catalog_id` is additive. Legacy client/rate tables remain untouched.

## Invoice Line Descriptions

Invoice line descriptions use the **billing session type labels**:

- Psychotherapy Session
- Psychotherapy Session / House Call
- Psychotherapy Session / Weekend
- Psychotherapy Session / Evening
- Custom (uses saved custom description)

Office, Phone, FaceTime, Unknown, or legacy service names are **never** generated for new invoice lines. These are appointment methods, not billing session types.

## Invoice Line Item Snapshots

Each invoice line item stores frozen snapshots at finalization:

- `billing_session_type_snapshot` — The billing session type at finalization
- `custom_service_description_snapshot` — Custom description if applicable
- `custom_service_code_snapshot` — Custom code if applicable

Finalized invoice snapshots remain immutable. Current session or catalog changes never rewrite finalized line items.
