# Relationship Model

The review UI separates four ideas that may look similar in a calendar title:

- `people`: actual humans, such as Fred Colin, Bobsey Colin, or Simon
- `session_participants`: who attended one session
- `billing_parties`: who should receive/pay the bill
- `client_accounts`: optional backend relationship or shared-billing groups, such as a household, family, or couple

One session can have multiple participants and still be one charge.

Routine review shows Clients in this session and Bill to. It does not require a visible household or family billing relationship.

## Examples

`Fred 830`

- participant: Fred Colin
- billing party: Fred Colin
- account: optional backend relationship record only

`Bobsey and Fred 6`

- participants: Fred Colin and Bobsey Colin
- billing party: Fred Colin
- account: optional backend relationship record only

`Simon 2`

- participant: Simon
- billing party: parent or family payer
- account: optional backend relationship record only

The payer does not need to be a participant.
