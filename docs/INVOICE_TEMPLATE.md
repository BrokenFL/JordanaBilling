# Invoice Template

The ReportLab letter template contains a logo or business fallback, `INVOICE`, number/date/period, `BILL TO`, Date/Participants/Service/Duration/Amount columns, configured `TOTAL DUE`, and two payment instructions. It has no due date, diagnosis, clinical note, insurance code, or treatment summary.

SVG wrappers with embedded PNG/JPEG artwork and PNG images preserve aspect ratio. Missing/unreadable logos fall back to text. Full vector SVG rendering is used when optional local `svglib` is installed.

Multi-page invoices repeat headers, keep rows intact, identify invoice/page on every page, and show totals/payment instructions only on the last page. Files are atomic at `Invoices/<year>/Invoice_<number>.pdf`; filenames never contain client names.
