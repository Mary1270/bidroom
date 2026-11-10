import json
import random
import unittest

import _bootstrap as b
from _bootstrap import (ALICE, BOB, BUDGET, CAROL, CLIENT, CLOCK, DELIVERY_URL, GOOD, OK, STRANGER, WEAK, FakeWorld,
                        PaymentRejected, UserError, World, assert_balanced, call_payable, create_project, get,
                        make_contract, set_caller, withdraw)

ADDRESSES = [CLIENT, ALICE, BOB, CAROL, STRANGER]
TERMINAL = ("completed", "failed", "cancelled", "expired")
ACTION_NAMES = [
    "place_bid", "withdraw_bid", "cancel_project", "select_winner", "expire_unbid", "expire_unselected",
    "confirm_award", "decline_award", "void_award", "submit_milestone", "approve_milestone", "dispute_milestone",
    "claim_unreviewed", "rule_dispute", "expire_dispute", "refund_undelivered", "withdraw",
]


def perform(c, who, name, pid, index, rng):
    set_caller(who)
    if name == "place_bid":
        pj = get(c, pid)
        return call_payable(c, "place_bid", int(pj["bond"]), pid, rng.randint(max(1, int(pj["budget"]) // 10), int(pj["budget"])), rng.randint(50, 600),
                            rng.choice([GOOD, OK, WEAK]))
    if name == "withdraw":
        pending = int(c.get_pending_withdrawal(who))
        c.withdraw()
        b.gl.evm.transfers.clear()
        c.balance = b.u256(int(c.balance) - pending)
        return pending
    if name in ("submit_milestone",):
        return c.submit_milestone(pid, index, DELIVERY_URL, "note")
    if name in ("approve_milestone", "claim_unreviewed", "rule_dispute", "expire_dispute"):
        return getattr(c, name)(pid, index)
    if name == "dispute_milestone":
        return c.dispute_milestone(pid, index, "The delivered page does not match the criteria")
    return getattr(c, name)(pid)


class TestRandomWalks(unittest.TestCase):
    def run_walk(self, seed):
        rng = random.Random(seed)
        c = make_contract()
        scores = {}

        def select_fn(call):
            n = len(get(c)["bids"])
            for i in range(n):
                scores.setdefault(i, [rng.randint(0, 3), rng.randint(0, 3)])
            return {"scores": {f"B{i}": scores[i] for i in range(n)}, "reasoning": "ok"}

        def rule_fn(call):
            return {"ruling": ruling_choice[0], "quote": FakeWorld.quote, "reasoning": "ok"}

        ruling_choice = [rng.choice(["ACCEPTED", "PARTIAL", "REJECTED"])]
        with World():
            FakeWorld.select_override = select_fn
            FakeWorld.rule_override = rule_fn
            budget = rng.choice([1000, 10000, 12345])
            create_project(c, budget=budget, bond_bps=rng.choice([100, 1000, 3000]),
                           bidding_minutes=60, accept_minutes=30, review_minutes=30, grace_minutes=20)
            total_bonds_in = 0
            steps = 0
            while steps < 80:
                steps += 1
                pj = get(c)
                if pj["status"] in TERMINAL and all(int(c.get_pending_withdrawal(a)) == 0 for a in ADDRESSES):
                    break
                options = []
                for who in ADDRESSES:
                    info = json.loads(c.get_actions("project_0", who))
                    for act in info["actions"]:
                        options.append((who, act))
                if not options or rng.random() < 0.2:
                    CLOCK.minutes(rng.choice([5, 25, 31, 61, 200, 1500, 5000]))
                    ruling_choice[0] = rng.choice(["ACCEPTED", "PARTIAL", "REJECTED"])
                    continue
                who, act = rng.choice(options)
                try:
                    perform(c, who, act["action"], "project_0", act.get("index", 0), rng)
                except (UserError, PaymentRejected) as error:
                    if "could not be completed" in str(error) or "could not be judged" in str(error):
                        continue
                    self.fail(f"seed {seed}: listed action {act} for {who[:6]} failed: {error}")
                assert_balanced(self, c)
                pj = get(c)
                self.assertGreaterEqual(int(pj["locked"]), 0)
                bonds = sum(int(x["bond"]) for x in pj["bids"] if x["status"] != "withdrawn")
                bonds_withdrawn = sum(int(x["bond"]) for x in pj["bids"] if x["status"] == "withdrawn")
                released = sum(int(v) for v in pj["released"].values())
                self.assertEqual(released + int(pj["locked"]), int(pj["budget"]) + bonds + bonds_withdrawn,
                                 f"seed {seed}: conservation broken {pj['released']} {pj['locked']}")
            pj = get(c)
            if pj["status"] in TERMINAL:
                self.assertEqual(pj["locked"], "0", f"seed {seed}")
                for who in ADDRESSES:
                    if int(c.get_pending_withdrawal(who)) > 0:
                        withdraw(c, who)
                self.assertEqual(int(c.balance), 0, f"seed {seed}")
                self.assertEqual(int(c.pending_total), 0)
            return pj["status"]

    def test_random_walks_keep_the_money_exact(self):
        ends = {}
        for seed in range(400):
            end = self.run_walk(seed)
            ends[end] = ends.get(end, 0) + 1
        for needed in ("completed", "failed", "expired", "cancelled"):
            self.assertGreater(ends.get(needed, 0), 0, ends)


class TestPermissionsMatchTheActionList(unittest.TestCase):
    def reach(self, rng):
        c = make_contract()
        target = rng.choice(["bidding", "empty_closed", "bidding_closed", "bids", "selection_closed", "awarded", "late_award",
                             "delivery", "submitted", "submitted_late", "disputed", "disputed_late", "late_delivery",
                             "second", "done"])
        scores = {}

        def select_fn(call):
            n = len(get(c)["bids"])
            return {"scores": {f"B{i}": [3, 3] if i == 0 else [1, 1] for i in range(n)}, "reasoning": "ok"}

        FakeWorld.select_override = select_fn
        FakeWorld.rule_override = lambda n: {"ruling": "ACCEPTED", "quote": FakeWorld.quote, "reasoning": "ok"}
        create_project(c)
        if target == "bidding":
            return c
        if target == "empty_closed":
            CLOCK.minutes(61)
            return c
        if target in ("bids", "bidding_closed", "selection_closed", "awarded", "late_award", "delivery", "submitted",
                      "submitted_late", "disputed", "disputed_late", "late_delivery", "second", "done"):
            b.place_bid(c, ALICE)
            b.place_bid(c, BOB, price=3000)
        if target == "bids":
            return c
        CLOCK.minutes(61)
        if target == "bidding_closed":
            return c
        if target == "selection_closed":
            CLOCK.minutes(1500)
            return c
        set_caller(STRANGER)
        c.select_winner("project_0")
        if target == "awarded":
            return c
        if target == "late_award":
            CLOCK.minutes(31)
            return c
        set_caller(ALICE)
        c.confirm_award("project_0")
        if target == "delivery":
            return c
        if target == "late_delivery":
            CLOCK.minutes(400)
            return c
        c.submit_milestone("project_0", 0, DELIVERY_URL, "n")
        if target == "submitted":
            return c
        if target == "submitted_late":
            CLOCK.minutes(31)
            return c
        set_caller(CLIENT)
        c.dispute_milestone("project_0", 0, "The delivered page does not match the criteria")
        if target == "disputed":
            return c
        if target == "disputed_late":
            CLOCK.minutes(4400)
            return c
        set_caller(STRANGER)
        c.rule_dispute("project_0", 0)
        if target == "second":
            return c
        set_caller(ALICE)
        c.submit_milestone("project_0", 1, DELIVERY_URL, "n")
        set_caller(CLIENT)
        c.approve_milestone("project_0", 1)
        return c

    def test_an_action_works_exactly_when_it_is_listed(self):
        rng = random.Random(7)
        checked = 0
        listed_names = set()
        with World():
            for _ in range(160):
                c = self.reach(rng)
                snap = b.snapshot(c)
                cur = None
                pj = get(c)
                for i, m in enumerate(pj["milestones"]):
                    if m["status"] in ("pending", "submitted", "disputed"):
                        cur = i
                        break
                index = 0 if cur is None else cur
                for who in ADDRESSES:
                    info = json.loads(c.get_actions("project_0", who))
                    allowed = {a["action"] for a in info["actions"]}
                    listed_names |= allowed
                    for name in ACTION_NAMES:
                        r = random.Random(1)
                        try:
                            perform(c, who, name, "project_0", index, r)
                            ok = True
                        except (UserError, PaymentRejected):
                            ok = False
                        b.restore(c, snap)
                        self.assertEqual(ok, name in allowed,
                                         f"{name} by {who[:6]} in {pj['status']}: listed={name in allowed} worked={ok}")
                        checked += 1
        self.assertGreater(checked, 5000)
        for name in ACTION_NAMES:
            self.assertIn(name, listed_names, f"{name} was never listed in any reached state")


if __name__ == "__main__":
    unittest.main()
