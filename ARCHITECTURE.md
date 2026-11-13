# BidRoom architecture

## State machine

Stored status: `bidding`, `awarded`, `delivery`, `dispute`, `completed`, `failed`, `cancelled`, `expired`.
Derived phase (view `get_phase`): `bidding` while the window is open, `selection` after it closes and while an award waits for confirmation, `delivery`, `dispute`, `closed`. Time moves the phase without a transaction, so the views take the clock into account.

Milestone status: `pending` -> `submitted` -> `approved` | `disputed` -> `approved` | `split` | back to `pending` (one redo).

## Accounting

Each project keeps `locked` (GEN it still holds) and `released` by tag (`winner`, `client`, `bidders`, `winner_bond`, `forfeit`). Every outgoing amount goes through one function that lowers `locked`, raises `released` and credits a withdrawable balance. Invariants, enforced by tests after every call of 400 random walks:

- released + locked = budget + all bonds ever locked
- contract balance = sum of `locked` + sum of withdrawable balances (`get_accounting`)
- a closed project has `locked = 0`

A payable call that fails validation while GEN is attached credits that GEN back to the sender and returns the reason instead of reverting, so GEN can never sit in the contract without an owner.

## Selection

Input to the model: title, brief (context only), numbered weighted criteria, and the active bids as `B<index>` with price, time and proposal. No addresses. Proposals are marked as untrusted data, and a proposal that addresses the evaluator is scored 0. Output must be integers 0 to 3 for every bid and criterion; anything else is invalid. Code, not the model, computes weighted totals, applies the qualification threshold (34% of the maximum) and breaks ties (lower price, earlier index).

## Ruling

Input: milestone title, acceptance text, the winner's note and the client's complaint (both marked as claims), and up to 12,000 characters of the delivered page. Output: `ACCEPTED`, `PARTIAL` or `REJECTED` plus a verbatim quote. Page fetch failure, a short page, an invalid ruling or a quote that is not on the page all yield "no result": nothing is recorded.

## Timing

All windows are chosen per project in minutes. Milestone deadlines are sequential: when one settles, the next one starts its own clock. A redo after a rejection gets a fresh deadline equal to the grace period.

## Exclusive settlement

Each deadline separates exactly two settlement paths: the normal one is accepted while `now <= deadline`, the timeout one only while `now > deadline`. The pairs are bidding/selection, selection/expire_unselected, confirm or decline/void_award, submit_milestone/refund_undelivered, approve or dispute/claim_unreviewed and rule_dispute/expire_dispute. The guard is checked before any state change or model call, and `get_actions` derives its list from the same comparisons.

## Known limits

Listed in the README. In short: model variance on close bids, mutable delivery links, public bids, whole-GEN prices.
