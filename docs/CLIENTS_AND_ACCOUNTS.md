# Billing Relationships

Billing Relationships is the CRM-style home for backend relationship and shared-billing structures: households, families, individual accounts, billing parties, rates, aliases, administrative notes, and session history.

Routine session review should not force Jordana to choose a backend account or family record. Use Clients in this session and Bill to first. Open Billing Relationships only when a family/couple/default-payer/shared-rate relationship needs maintenance.

The list shows account code, name, type, primary person, members, billing party, default rate, outstanding balance, last session, and active status.

Billing relationship records show:

- Header details and active status
- Members and relationship roles
- Default billing party and contact information
- Account-specific rates
- Calendar aliases
- Session history
- Audit history through the backend service

Quick client and payer choices can be made in the Review Queue inspector. Deeper billing setup belongs here.

Do not automatically create a permanent shared account merely because two names appear in one calendar title.

Invoice grouping uses the confirmed billing party, not a required visible household account. Bill-to delivery preference is `email`, `mail`, `both`, or `unresolved`; a draft may override it and finalization freezes the selected method and destination.
