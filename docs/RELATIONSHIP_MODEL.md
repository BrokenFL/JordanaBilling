# Relationship Model

The review UI separates four ideas that may look similar in a calendar title:

- `people`: actual humans, such as Fred Colin, Bobsey Colin, or Simon
- `client_accounts`: the relationship or billing group, such as Fred Household or Simon Family Account
- `session_participants`: who attended one session
- `billing_parties`: who should receive/pay the bill

One session can have multiple participants and still be one charge.

## Examples

`Fred 830`

- participant: Fred Colin
- account: Fred Household
- billing party: Fred Colin

`Bobsey and Fred 6`

- participants: Fred Colin and Bobsey Colin
- account: Fred Household
- billing party: Fred Colin

`Simon 2`

- participant: Simon
- account: Simon Family Account
- billing party: parent or family payer

The payer does not need to be a participant.
