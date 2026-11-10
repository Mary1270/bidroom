# BidRoom

A freelance project auction on GenLayer. A client locks a budget and writes how the winner will be chosen. Freelancers bid. A blind AI panel scores every bid against the written criteria only and stores its reasoning. Then the work is paid milestone by milestone, with approval, dispute or refund.

Single Intelligent Contract (`contract.py`), no-build frontend (`index.html`), offline test suite (`tests/`).

- Contract on GenLayer Studio: `0x6e15D62F640a8D0B3AD360207380134bFfCA52E2` ([explorer](https://explorer-studio.genlayer.com/address/0x6e15D62F640a8D0B3AD360207380134bFfCA52E2))
- Live app: https://mary1270.github.io/bidroom/

## Roles

| Role | Who | Can do |
| --- | --- | --- |
| Client | Creates the project | Locks the budget, writes criteria and milestones, approves or disputes each delivery, cancels while there are no bids |
| Bidder | Any other wallet | Locks a bond, bids (price, time, proposal), withdraws until bidding closes |
| Winner | The selected bidder | Confirms or declines the award, submits one link per milestone |
| Anyone | Any wallet | Triggers selection, rulings, auto-approval, refunds and expiries that nobody can block |

## Phases

`bidding` -> `selection` -> `delivery` <-> `dispute` -> closed (`completed`, `failed`, `cancelled`, `expired`)

1. **Bidding.** The client's budget is locked. Each bidder locks a bond (a fixed share of the budget). Bids are public and can be withdrawn until the window closes.
2. **Selection.** Anyone triggers it after the window closes. Every validator asks the model to score each bid 0 to 3 on each written criterion. Bidders appear only as labels (B0, B1, ...), never as addresses. The best weighted total wins; ties go to the lower price, then the earlier bid. If the best total is below 34% of the maximum, nobody is awarded and everyone is refunded. Scores, totals, reasoning and digests of the criteria and bids are stored.
3. **Award.** The winner confirms within the client's window, or declines (half the bond to the client), or stays silent (all of the bond to the client). Losers' bonds and the unused part of the budget are credited immediately.
4. **Delivery.** Milestones are sequential. The winner submits an https link. The client approves, or stays silent for the review window, which counts as approval. A missed deadline plus grace lets anyone refund the client.
5. **Dispute.** The client may contest a submitted milestone inside the review window. An AI arbiter fetches the delivered page and judges it against the acceptance text written before bidding opened. It must quote the page verbatim.

## Money

| Situation | Winner | Client |
| --- | --- | --- |
| Milestone approved, auto-approved or ACCEPTED | Full milestone amount | - |
| Dispute PARTIAL | Half the milestone | Other half |
| Dispute REJECTED the first time | One redo | - |
| REJECTED twice, or a milestone never delivered | Nothing more, bond lost | Unpaid milestones plus the bond |
| No ruling within 72 hours | Half | Half |
| Award never confirmed | Bond lost | Whole price plus the bond |
| Award declined | Half the bond back | Whole price plus half the bond |
| All milestones settled | Bond returned | Budget minus price (credited at selection) |
| No bids, selection never run, or no qualifying bid | - | Full budget, every bidder gets the bond back |

Nothing is sent automatically. Every payout is credited to a withdrawable balance and the owner calls `withdraw()`. The ledger is zeroed before the transfer.

## Contract interface

Writes: `create_project` (payable), `cancel_project`, `place_bid` (payable), `withdraw_bid`, `select_winner`, `expire_unbid`, `expire_unselected`, `confirm_award`, `decline_award`, `void_award`, `submit_milestone`, `approve_milestone`, `claim_unreviewed`, `dispute_milestone`, `rule_dispute`, `expire_dispute`, `refund_undelivered`, `withdraw`.

Read-only: `get_project`, `list_projects`, `total_projects`, `get_phase`, `get_actions` (roles and the exact actions an address may take right now), `get_bids`, `get_milestones`, `get_history`, `get_selection`, `get_stats`, `get_profile`, `get_address_projects` (every project an address took part in, with its roles, bids and payouts), `get_activity` (the latest events across all projects), `get_pending_withdrawal`, `get_contract_balance`, `get_accounting`.

Every arbitration ruling stores the evidence it judged: the delivery link, the freelancer's note, the client's complaint, the verbatim quote, the reasoning, timestamps and the address that triggered it.

`get_accounting` proves the books: contract balance equals GEN locked in projects plus claimable balances.

## Consensus design

Both AI steps run through `gl.vm.run_nondet_unsafe` with a leader function and a validator function that only close over plain data.

- **Selection.** The validator re-scores every bid itself and requires the same winner and the same qualified/not-qualified decision as the leader. It also recomputes the leader's totals from the leader's scores. Exact scores may differ.
- **Ruling.** The validator fetches the page itself, asks the model itself and requires the same ruling. A ruling without a quote that appears verbatim on the page is invalid.
- A step that cannot be completed records nothing and can be retried by anyone.

## Frontend

`index.html` is a single no-build page that talks to the contract through `genlayer-js`. Everything it shows is read from the contract; nothing is simulated.

- **Explore:** live contract activity, escrow totals with a balance check, project cards with phase, countdown and budget, search, sort, filters, grid or list.
- **Project:** phase tracker with live countdown, a "Your move" panel built from `get_actions`, who-can-do-what for every role, an escrow ledger that reconciles every GEN in and out, the AI selection report (score matrix, weights, qualifying line, reasoning, digests), bids with each bidder's on-chain track record, milestones with payment splits, an arbitration report for every ruling (written rule, delivered evidence, verbatim quote, both parties' claims, reasoning, payout effect), and the on-chain event log.
- **Public profiles** (`#/u/<address>`): reliability, earnings, escrowed budgets, rulings and a record of every project the address took part in.
- **Post a project:** four-step wizard with templates and a live preview.
- **Transactions:** a status panel that follows the transaction from wallet signature to finalization and shows the node's consensus status while validators work.

Open it with `?contract=0xYourAddress` once, or paste the address in the setup card. It is remembered in the browser.

## Tests

```
python3 -m unittest discover -s tests -v
```

No installs needed. The suite uses a small offline stand-in for the GenLayer SDK (`tests/genlayer_stub`). It covers every path above, includes 400 seeded random walks that check money conservation after every call, checks that `get_actions` matches what the contract actually accepts for every role and state, and checks the Studio file format (two header lines, then code).

The stub does not run real validators or a real model. Live behaviour must be confirmed on GenLayer Studio.

## Limits

- The model's scores can differ between validators on close bids. Then the call fails and can be repeated; after 24 hours past the close, anyone can refund everyone.
- The arbiter reads the delivery page at ruling time. Use links that cannot change (a file at a commit).
- Prices are whole GEN. Studio value fields take whole GEN, so choose a budget and bond percentage that give a whole bond.
- Bids are public on-chain text. Do not put secrets in a proposal.

## License

MIT
