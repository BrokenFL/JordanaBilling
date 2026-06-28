# Invoice Template

The ReportLab letter template contains a logo or business fallback, `INVOICE`, number/date/period, `BILL TO`, Date/Participants/Service/Duration/Amount columns, configured `TOTAL DUE`, and one centered payment block. The Bill To block shows the delivery destination implied by the invoice delivery method: mailing address for mail, `Via Email: ...` for email, or both in that order. The payment block now shows the check instructions plus `Or send payment via Zelle to: ...`. It has no due date, diagnosis, clinical note, insurance code, or treatment summary.

## PDF Layout (US Letter Portrait)

- **Page size**: US Letter (8.5" x 11"), portrait.
- **Margins**: 0.50" left/right, 0.50" top, 0.55" bottom — print-safe for desktop printers.
- **Content width**: 7.5" (540pt), used consistently by the header, line-item table, total bar, and payment section.
- **Header columns**: 4.8" (logo/sender) + 2.7" (invoice metadata) = 7.5".
- **Line-item table columns**: Date 1.00", Participants 1.65", Service 2.90", Duration 0.85", Amount 1.10" — sum exactly 7.5".
- **Date column**: 1.00" wide, sufficient for long-form dates like "June 22, 2026" on one line at 9pt Helvetica.
- **Logo**: up to 1.25" x 0.87" with aspect ratio preserved; slightly enlarged from the prior 1.05" x 0.73".
- **Vertical spacing**: tightened around the header, Bill To block, table, total bar, and payment instructions to reduce unnecessary whitespace.
- **Total bar**: 6.40" label + 1.10" amount = 7.5", with a top rule line.
- **Design**: restrained, professional, suitable for printing and mailing.

SVG wrappers with embedded PNG/JPEG artwork and PNG images preserve aspect ratio. Missing/unreadable logos fall back to text. Full vector SVG rendering is used when optional local `svglib` is installed. Draft previews and finalized PDFs use the same server-side invoice render model for logo choice, header metadata, bill-to formatting, date formatting, billing-period labels, and payment-block content.

Multi-page invoices repeat headers, keep rows intact, identify invoice/page on every page, and show totals/payment instructions only on the last page. Files are atomic at `Invoices/<year>/Invoice_<number>.pdf`; filenames never contain client names.

Existing finalized PDFs are immutable; layout refinements apply only when a new invoice PDF is generated.
