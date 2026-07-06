# Invoice Template

The ReportLab letter template contains a balanced two-column header. The
right-side invoice block shows exactly `INVOICE`, the invoice date as an
unlabeled uppercase short date such as `JUL 1, 2025`, and the invoice number as
an unlabeled value such as `10-2025`. Billing Period is not displayed on the
invoice. The line-item table, configured `TOTAL DUE`, and restrained
footer-style payment block span the full content width below the header. The
Bill To block shows the delivery destination implied by the invoice delivery
method: mailing address for mail, `Via Email: ...` for email, or both in that
order. The payment block shows the check instructions plus `Or send payment via
Zelle to: ...`. It has no due date, clinical note, or treatment summary.
Standard self-pay invoices do not include diagnosis codes or insurance coding;
optional insurance coding may be added at finalization when required for
administrative insurance billing (see Optional Insurance Coding Block below).

## PDF Layout (US Letter Portrait)

- **Page size**: US Letter (8.5" x 11"), portrait.
- **Margins**: 0.50" left/right, 0.50" top, 0.55" bottom — print-safe for desktop printers.
- **Content width**: 7.5" (540pt), used consistently by the header, line-item table, total bar, and payment section.
- **Header columns**: 3.65" left invoice/Bill To column + 3.85" right logo/provider column = 7.5".
- **Typography**: body 10.25pt / 13pt leading; small/meta 9pt / 11pt leading; `INVOICE` title 29pt / 31pt leading; total 14.5pt / 18pt leading.
- **Line-item table columns**: Date 1.12", Participants 1.65", Service 2.78", Duration 0.85", Amount 1.10" — sum exactly 7.5".
- **Date column**: 1.12" wide, sufficient for ordinary long-form dates like "June 22, 2026" without unnecessary wrapping.
- **Logo**: top-right PNG, up to 2.10" wide and 1.35" tall, preserving aspect ratio. The current approved asset uses no optical offset and reads centered over the provider block.
- **Header hierarchy**: The invoice metadata block shows `INVOICE`, then the unlabeled invoice date, then the unlabeled invoice number or draft placeholder. `Invoice Number:`, `Invoice Date:`, and `Billing Period:` labels are not rendered. The invoice-number line is aligned with the final line of the left-side address block. The session table starts beneath both columns with only a modest gap.
- **Line order**: Session rows are chronological by service date, then source start time, then stable line UUID. Import, approval, insertion, and database row order do not control display order.
- **Table spacing**: 9pt top/bottom row padding with 6pt left/right cell padding for more readable printed rows.
- **Total section**: aligned to the 7.5" line-item table width, with top and bottom rules matching the table edges exactly and clean right alignment.
- **Payment footer**: full-width centered block, normal-size text, and alignment consistent with the invoice frame.
- **Short invoice balancing**: one-page invoices add flexible extra vertical space before the total/payment footer so short invoices sit lower on the sheet; multi-page invoices add no artificial gap and retain normal ReportLab splitting.
- **Design**: restrained, professional, suitable for printing and mailing.

SVG wrappers with embedded PNG/JPEG artwork and PNG images preserve aspect ratio. Missing/unreadable logos fall back to text. Full vector SVG rendering is used when optional local `svglib` is installed. Draft previews and finalized PDFs use the same server-side invoice render model for logo choice, header metadata, bill-to formatting, date formatting, and payment-block content. Draft PDF previews (`GET /api/invoices/{id}/draft-pdf`) use the same ReportLab template, are clearly marked DRAFT, do not assign an invoice number, and are generated in-memory without writing to disk or changing invoice state. Missing readiness information (e.g. missing address or email) may block finalization but does not block draft preview. Both draft PDF and final PDF endpoints use dedicated inline PDF response headers (`Content-Type: application/pdf`, `Content-Disposition: inline`) compatible with Safari. PDF responses use `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer` but do not apply the `X-Frame-Options: DENY` or CSP headers used for HTML/JSON responses.

### Canonical Shared Renderer

Both draft preview (`generate_draft_pdf_bytes`) and finalized invoice PDF generation (`generate_invoice_pdf`) delegate to a single shared canonical rendering function (`_generate_invoice_pdf_bytes`). This ensures that typography, spacing, BILL TO dynamic alignment, provider block, table, TOTAL DUE, payment section, footer, insurance/coding block, and late-cancellation rendering are always identical between preview and finalization. The only intended differences are:

- DRAFT watermark/label versus finalized invoice number
- Any explicitly approved final metadata (e.g. frozen account-summary snapshot)

No legacy or alternative final-invoice renderer exists. Regression tests in `tests/test_invoice_pdf_layout.py` verify that both functions delegate to the shared renderer and that draft and finalized output share the same layout, content, and positioning.

Multi-page invoices repeat headers, keep rows intact, identify invoice/page on every page, and show totals/payment instructions only on the last page. The payment footer remains above the invoice/page footer and is never intended to overlap it. New finalized files are atomic under the configured invoice root; installed releases use `~/Documents/Jordana Billing/Client Files/<Client Display Name>/<Month YYYY>/Invoice_<number>.pdf`. The frozen person code remains the stable internal filing identity and appears in the folder only when needed to disambiguate two different people with the same sanitized display name. Bill To organization names are not used as the folder when the invoice is filed under a client.

Existing finalized PDFs are immutable; layout and filing-path refinements apply only when a new invoice PDF is generated. Existing `pdf_path` and checksum values are preserved.

## Review To Finalize Preview

The Review -> Finalize confirmation screen shows a clean in-app HTML invoice card built from the current canonical backend render model. The draft invoice editor and finalized/void invoice view use the same model-backed HTML card for the in-app reading experience. The exact PDF remains available through secondary actions (`Open Exact PDF`, download, and print) from the same-origin `GET /api/invoices/{id}/draft-pdf`, `GET /api/invoices/{id}/finalization-preview-pdf`, and `GET /api/invoices/{id}/final-pdf` endpoints. Preview requests are side-effect free: they do not assign a number, finalize, write `pdf_path` or checksum fields, update status/revision, or create audit records. Temporary insurance/coding preview values are carried in browser state for the HTML card and by a short-lived in-memory token for exact PDF preview rather than a blob URL or database mutation.

After confirmation, the workflow finalizes through `POST /api/invoices/{id}/finalize`, receives the stored finalized invoice record, and opens the versioned `final_pdf_url` returned on that record. The Invoices workspace shows the frozen render-model HTML card for finalized and void invoices while keeping `Open PDF`, download, Finder, client-folder, and print actions available below the preview. The official customer-facing artifact remains the PDF served by `GET /api/invoices/{id}/final-pdf`; the HTML card is an in-app view of the same canonical model, not an independent invoice source of truth.

The finalized PDF URL includes the stored PDF checksum as a cache-busting query value, while the stable stored filename remains `Invoice_<number>.pdf`. PDF responses use no-cache headers so Safari or another browser cannot show an older file for the same invoice endpoint. Repeated finalize submissions for an already-finalized invoice return the existing immutable invoice record and existing final PDF URL instead of regenerating or renumbering.

The July 2026 live workflow bugs were not caused by the source server importing `build/lib`: the source launcher sets `PYTHONPATH` to `app/`, and the running process imported `app/jordana_invoice/invoice_pdf.py`. Earlier in-app HTML previews drifted because they duplicated invoice fields and layout separately from the canonical PDF renderer. Current previews use one backend render model for both the polished HTML card and the exact PDF renderer. Release packaging clears stale Python `build/lib` output before wheel creation so packaged builds cannot accidentally reuse an older renderer.

ReportLab's PDF path imports Pillow (`PIL.Image`) at runtime. Pillow is an explicit production dependency and the release installer plus installed-app verifier import `PIL` alongside ReportLab so missing PDF dependencies are caught during installation instead of surfacing later as a draft-preview failure.

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

When insurance coding is enabled at finalization, a compact four-line block appears at the bottom-left of the invoice content area, below the payment block. The first coding line begins approximately four body-text lines below the final payment-information line, using a layout-driven spacer of `4 × body leading`. The block is left-aligned to the normal invoice content margin:

```
Diagnosis Code: <value>
EIN: <value>
NPI: <value>
SW: <value>
```

- The block is rendered as a single `KeepTogether` unit with zero paragraph spacing and compact leading.
- There is no blank line or spacer between the Diagnosis Code line and the EIN line.
- The spacer between the final payment-information line and the first coding line is `4 × body leading` (a layout-driven value, not a one-off pixel coordinate).
- The block appears only on the final page (it is part of the footer `KeepTogether`).
- EIN, NPI, and SW values come from Invoice Settings and are frozen into the finalized invoice snapshot at finalization time.
- The diagnosis code is entered or approved per-invoice during finalization and is never persisted on draft invoices.
- When insurance coding is unchecked, no block appears in preview or final PDF.
- Draft preview and finalized PDF render the block identically.
- Later changes to Invoice Settings do not alter existing finalized invoices.
- Diagnosis codes must never be inferred from calendar text, participant names, session descriptions, or other application data. Real diagnosis codes must never be committed to GitHub, fixtures, screenshots, logs, demo data, examples, or documentation.
