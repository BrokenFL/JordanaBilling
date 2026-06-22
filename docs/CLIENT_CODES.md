# Client Codes

Internal UUIDs remain the real primary keys. Human-readable codes are secondary helpers for Jordana and CSV review.

## Person Codes

Person codes are generated only when a real person has confirmed first and last names.

Format:

```text
first initial + first three usable letters of last name + numeric suffix
```

Examples:

- Fred Colin -> `FCOL-001`
- Leah Grossman -> `LGRO-001`
- Rebecca Colon -> `RCOL-001`

Rules:

- Uppercase.
- Remove punctuation, spaces, apostrophes, and diacritics.
- Do not assign a code to a parser-only provisional candidate.
- Do not silently change an existing code when a name changes.
- If a prefix collides, increment the suffix.
- Code regeneration must be explicit and audited.

Participant and bill-to workflows do not change these rules. A provisional parser candidate still does not receive a code until a real person is confirmed with first and last name.

## Account Codes

Accounts use a separate sequence:

```text
ACCT-0001
ACCT-0002
```

Account codes are assigned when a real account is created. Reusing the same account name returns the existing account instead of creating a duplicate.
