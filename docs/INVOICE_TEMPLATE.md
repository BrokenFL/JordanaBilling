# Invoice Template

The ReportLab letter template contains a balanced two-column header: `INVOICE`, invoice number/date, and `BILL TO` on the left; the logo and centered provider block on the right. The line-item table, configured `TOTAL DUE`, and restrained footer-style payment block span the full content width below the header. The Bill To block shows the delivery destination implied by the invoice delivery method: mailing address for mail, `Via Email: ...` for email, or both in that order. The payment block shows the check instructions plus `Or send payment via Zelle to: ...`. It has no billing-period row, due date, clinical note, or treatment summary. Standard self-pay invoices do not include diagnosis codes or insurance coding; optional insurance coding may be added at finalization when required for administrative insurance billing (see Optional Insurance Coding Block below).

## PDF Layout (US Letter Portrait)

- **Page size**: US Letter (8.5" x 11"), portrait.
- **Margins**: 0.50" left/right, 0.50" top, 0.55" bottom — print-safe for desktop printers.
- **Content width**: 7.5" (540pt), used consistently by the header, line-item table, total bar, and payment section.
- **Header columns**: 3.65" left invoice/Bill To column + 3.85" right logo/provider column = 7.5".
- **Typography**: body 10.25pt / 13pt leading; small/meta 9pt / 11pt leading; `INVOICE` title 29pt / 31pt leading; total 14.5pt / 18pt leading.
- **Line-item table columns**: Date 1.12", Participants 1.65", Service 2.78", Duration 0.85", Amount 1.10" — sum exactly 7.5".
- **Date column**: 1.12" wide, sufficient for ordinary long-form dates like "June 22, 2026" without unnecessary wrapping.
- **Logo**: top-right PNG, up to 2.10" wide and 1.35" tall, preserving aspect ratio. The current approved asset uses no optical offset and reads centered over the provider block.
- **Header hierarchy**: `INVOICE` sits top-left with Invoice Number and Invoice Date directly beneath it in a tight label/value block. The Bill To block follows in the same left column. The logo sits top-right with the compact provider block directly beneath it. The session table starts beneath both columns with only a modest gap.
- **Table spacing**: 9pt top/bottom row padding with 6pt left/right cell padding for more readable printed rows.
- **Total section**: aligned to the 7.5" line-item table width, with top and bottom rules matching the table edges exactly and clean right alignment.
- **Payment footer**: full-width centered block, normal-size text, and alignment consistent with the invoice frame.
- **Short invoice balancing**: one-page invoices add flexible extra vertical space before the total/payment footer so short invoices sit lower on the sheet; multi-page invoices add no artificial gap and retain normal ReportLab splitting.
- **Design**: restrained, professional, suitable for printing and mailing.

SVG wrappers with embedded PNG/JPEG artwork and PNG images preserve aspect ratio. Missing/unreadable logos fall back to text. Full vector SVG rendering is used when optional local `svglib` is installed. Draft previews and finalized PDFs use the same server-side invoice render model for logo choice, header metadata, bill-to formatting, date formatting, and payment-block content. Draft PDF previews (`GET /api/invoices/{id}/draft-pdf`) use the same ReportLab template, are clearly marked DRAFT, do not assign an invoice number, and are generated in-memory without writing to disk or changing invoice state. Missing readiness information (e.g. missing address or email) may block finalization but does not block draft preview. Both draft PDF and final PDF endpoints use dedicated inline PDF response headers (`Content-Type: application/pdf`, `Content-Disposition: inline`) compatible with Safari. PDF responses use `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` but do not apply the `X-Frame-Options: DENY` or CSP headers used for HTML/JSON responses.

Multi-page invoices repeat headers, keep rows intact, identify invoice/page on every page, and show totals/payment instructions only on the last page. The payment footer remains above the invoice/page footer and is never intended to overlap it. New finalized files are atomic under the configured invoice root; installed releases use `~/Documents/Jordana Billing/Client Files/<Client Display Name>/<Month YYYY>/Invoice_<number>.pdf`. The frozen person code remains the stable internal filing identity and appears in the folder only when needed to disambiguate two different people with the same sanitized display name. Bill To organization names are not used as the folder when the invoice is filed under a client.

Existing finalized PDFs are immutable; layout and filing-path refinements apply only when a new invoice PDF is generated. Existing `pdf_path` and checksum values are preserved.

## Prior Unpaid Balance & Account Summary Layout

When an invoice contains prior unpaid balances or payments applied, the standard single-row "TOTAL DUE" block is replaced with a multi-row structured table displaying:
1. **Current Charges**: Total charges generated during the current period.
2. **Payments Applied**: Total payments allocated to the current invoice.
3. **Current Invoice Balance**: The remaining balance for the current invoice.
4. **Prior Unpaid Balance**: Unpaid balance from prior finalized non-void invoices.
5. **TOTAL AMOUNT DUE**: The final sum of current balance and prior unpaid balance (rendered with large, bold text).

Below the table, if there are prior unpaid invoices, a detailed right-aligned sub-list specifies each prior invoice number, date, and its remaining unpaid balance.

This layout is identical in both the HTML print preview and the ReportLab PDF rendering.

## Optional Insurance Coding Block

When insurance coding is enabled at finalization, a compact four-line block appears at the bottom-left of the invoice content area, after the payment section and before any notes:

```
Diagnosis Code: <value>
EIN: <value>
NPI: <value>
SW: <value>
```

- The block is rendered as a single `KeepTogether` unit with zero paragraph spacing and compact leading.
- There is no blank line or spacer between the Diagnosis Code line and the EIN line.
- The block appears only on the final page (it is part of the footer `KeepTogether`).
- EIN, NPI, and SW values come from Invoice Settings and are frozen into the finalized invoice snapshot at finalization time.
- The diagnosis code is entered or approved per-invoice during finalization and is never persisted on draft invoices.
- When insurance coding is unchecked, no block appears in preview or final PDF.
- Draft preview and finalized PDF render the block identically.
- Later changes to Invoice Settings do not alter existing finalized invoices.
- Diagnosis codes must never be inferred from calendar text, participant names, session descriptions, or other application data. Real diagnosis codes must never be committed to GitHub, fixtures, screenshots, logs, demo data, examples, or documentation.
