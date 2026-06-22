# Identity Correction And Merging

Jordana can correct an incomplete suggested person without creating a duplicate.

Example:

1. Calendar suggests `Fred`.
2. Jordana edits the selected person to `Fred Colin`.
3. The same `person_id` is retained.
4. `Fred` is saved as an approved calendar alias.
5. Future `Fred` events can prefill Fred Colin.
6. Audit history records old value, new value, timestamp, and source.

## Duplicate Prevention

When typing a new name, the system searches exact names, first-name matches, and known aliases. If `Fred` exists and Jordana types `Fred Colin`, the UI offers to update Fred rather than silently creating a second person.

## Merge

If duplicates already exist, the merge workflow:

- chooses one surviving `person_id`
- transfers session participant links
- transfers account memberships
- transfers billing-party links
- transfers aliases
- marks the duplicate inactive/merged
- records audit history

Merged people are not deleted blindly.
