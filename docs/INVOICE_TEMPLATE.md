# Invoice Template

The ReportLab letter template contains a logo or business fallback, `INVOICE`, number/date/period, `BILL TO`, Date/Participants/Service/Duration/Amount columns, configured `TOTAL DUE`, and one restrained footer-style payment block. The Bill To block shows the delivery destination implied by the invoice delivery method: mailing address for mail, `Via Email: ...` for email, or both in that order. The payment block shows the check instructions plus `Or send payment via Zelle to: ...`. It has no due date, diagnosis, clinical note, insurance code, or treatment summary.

## PDF Layout (US Letter Portrait)

- **Page size**: US Letter (8.5" x 11"), portrait.
- **Margins**: 0.50" left/right, 0.50" top, 0.55" bottom — print-safe for desktop printers.
- **Content width**: 7.5" (540pt), used consistently by the header, line-item table, total bar, and payment section.
- **Header columns**: 4.55" (logo/sender) + 2.95" (invoice metadata) = 7.5".
- **Typography**: body 10.25pt / 13pt leading; small/meta 9pt / 11pt leading; `INVOICE` title 29pt / 31pt leading; total 14.5pt / 18pt leading.
- **Line-item table columns**: Date 1.12", Participants 1.65", Service 2.78", Duration 0.85", Amount 1.10" — sum exactly 7.5".
- **Date column**: 1.12" wide, sufficient for ordinary long-form dates like "June 22, 2026" without unnecessary wrapping.
- **Logo**: up to 1.50" wide and 1.05" tall, preserving aspect ratio.
- **Header hierarchy**: larger logo, stronger sender/meta spacing, right-aligned metadata labels and values, and a more distinct Bill To break below the header.
- **Table spacing**: 9pt top/bottom row padding with 6pt left/right cell padding for more readable printed rows.
- **Total section**: 6.15" label + 1.35" amount = 7.5", with a full-width top rule and clean right alignment.
- **Payment footer**: full-width block with a subtle separator rule, normal-size text, and alignment consistent with the invoice frame.
- **Short invoice balancing**: one-page invoices add flexible extra vertical space before the total/payment footer so short invoices sit lower on the sheet; multi-page invoices add no artificial gap and retain normal ReportLab splitting.
- **Design**: restrained, professional, suitable for printing and mailing.

SVG wrappers with embedded PNG/JPEG artwork and PNG images preserve aspect ratio. Missing/unreadable logos fall back to text. Full vector SVG rendering is used when optional local `svglib` is installed. Draft previews and finalized PDFs use the same server-side invoice render model for logo choice, header metadata, bill-to formatting, date formatting, billing-period labels, and payment-block content.

Multi-page invoices repeat headers, keep rows intact, identify invoice/page on every page, and show totals/payment instructions only on the last page. The payment footer remains above the invoice/page footer and is never intended to overlap it. Files are atomic at `Invoices/<year>/Invoice_<number>.pdf`; filenames never contain client names.

Existing finalized PDFs are immutable; layout refinements apply only when a new invoice PDF is generated.
