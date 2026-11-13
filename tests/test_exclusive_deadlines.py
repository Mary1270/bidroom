"""Every deadline is a hard switch between two settlement paths.

Up to and including the deadline only the normal path may execute; one
second later only the timeout path may execute. Each test checks both sides
of the boundary against the contract itself and against get_actions."""
import datetime
import json
import unittest

import _bootstrap as b
from _bootstrap import (ALICE, BOB, CLIENT, CLOCK, DELIVERY_URL, STRANGER, FakeWorld, UserError, World, create_project,
                        get, make_contract, place_bid, set_caller, to_delivery, to_selection)


def at(iso, seconds=0):
    CLOCK.now = datetime.datetime.fromisoformat(iso) + datetime.timedelta(seconds=seconds)


class Exclusive(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        self.world = World()
        self.world.__enter__()
        self.addCleanup(self.world.__exit__)

    def listed(self, who):
        return {a["action"] for a in json.loads(self.c.get_actions("project_0", who))["actions"]}

    def everyone_listed(self):
        names = set()
        for who in (CLIENT, ALICE, BOB, STRANGER):
            names |= self.listed(who) - {"withdraw"}
        return names

    def call(self, who, method, *args):
        set_caller(who)
        snap = b.snapshot(self.c)
        try:
            getattr(self.c, method)(*args)
            ok = True
        except UserError:
            ok = False
        b.restore(self.c, snap)
        return ok

    def assert_switch(self, deadline, normal, timeout):
        """normal and timeout are (who, method, args) tuples."""
        at(deadline)
        self.assertTrue(self.call(*normal[:2], *normal[2]), f"{normal[1]} must work at the deadline")
        self.assertFalse(self.call(*timeout[:2], *timeout[2]), f"{timeout[1]} must not work at the deadline")
        self.assertIn(normal[1], self.everyone_listed())
        self.assertNotIn(timeout[1], self.everyone_listed())
        at(deadline, 1)
        self.assertFalse(self.call(*normal[:2], *normal[2]), f"{normal[1]} must not work after the deadline")
        self.assertTrue(self.call(*timeout[:2], *timeout[2]), f"{timeout[1]} must work after the deadline")
        self.assertNotIn(normal[1], self.everyone_listed())
        self.assertIn(timeout[1], self.everyone_listed())

    def test_selection_versus_expire_unselected(self):
        to_selection(self.c)
        end = (datetime.datetime.fromisoformat(get(self.c)["bidding_deadline"]) + datetime.timedelta(minutes=1440)).isoformat()
        self.assert_switch(end, (STRANGER, "select_winner", ("project_0",)), (STRANGER, "expire_unselected", ("project_0",)))

    def test_decline_versus_void_award(self):
        to_selection(self.c)
        set_caller(STRANGER)
        self.c.select_winner("project_0")
        deadline = get(self.c)["accept_deadline"]
        self.assert_switch(deadline, (ALICE, "decline_award", ("project_0",)), (STRANGER, "void_award", ("project_0",)))
        at(deadline)
        self.assertTrue(self.call(ALICE, "confirm_award", "project_0"))
        at(deadline, 1)
        self.assertFalse(self.call(ALICE, "confirm_award", "project_0"))

    def test_submission_versus_refund_undelivered(self):
        to_delivery(self.c)
        deadline = get(self.c)["milestones"][0]["hard_deadline"]
        self.assert_switch(deadline, (ALICE, "submit_milestone", ("project_0", 0, DELIVERY_URL, "n")),
                           (STRANGER, "refund_undelivered", ("project_0",)))

    def test_submission_versus_refund_after_a_rejected_redo(self):
        to_delivery(self.c)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 0, DELIVERY_URL, "n")
        set_caller(CLIENT)
        self.c.dispute_milestone("project_0", 0, "The delivered page does not match the rule")
        FakeWorld.ruling = "REJECTED"
        set_caller(STRANGER)
        self.c.rule_dispute("project_0", 0)
        deadline = get(self.c)["milestones"][0]["hard_deadline"]
        self.assert_switch(deadline, (ALICE, "submit_milestone", ("project_0", 0, DELIVERY_URL, "n")),
                           (STRANGER, "refund_undelivered", ("project_0",)))

    def test_review_versus_claim_unreviewed(self):
        to_delivery(self.c)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 0, DELIVERY_URL, "n")
        deadline = get(self.c)["milestones"][0]["review_deadline"]
        self.assert_switch(deadline, (CLIENT, "approve_milestone", ("project_0", 0)), (STRANGER, "claim_unreviewed", ("project_0", 0)))
        self.assert_switch(deadline, (CLIENT, "dispute_milestone", ("project_0", 0, "The delivered page does not match the rule")),
                           (STRANGER, "claim_unreviewed", ("project_0", 0)))

    def test_ruling_versus_expire_dispute(self):
        to_delivery(self.c)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 0, DELIVERY_URL, "n")
        set_caller(CLIENT)
        self.c.dispute_milestone("project_0", 0, "The delivered page does not match the rule")
        deadline = get(self.c)["milestones"][0]["ruling_deadline"]
        self.assert_switch(deadline, (STRANGER, "rule_dispute", ("project_0", 0)), (STRANGER, "expire_dispute", ("project_0", 0)))

    def test_cancel_versus_expire_unbid(self):
        create_project(self.c)
        deadline = get(self.c)["bidding_deadline"]
        self.assert_switch(deadline, (CLIENT, "cancel_project", ("project_0",)), (STRANGER, "expire_unbid", ("project_0",)))

    def test_bidding_versus_selection(self):
        create_project(self.c)
        place_bid(self.c, BOB)
        FakeWorld.scores = {"B0": [3, 3]}
        deadline = get(self.c)["bidding_deadline"]
        at(deadline)
        self.assertTrue(self.call(BOB, "withdraw_bid", "project_0"))
        self.assertFalse(self.call(STRANGER, "select_winner", "project_0"))
        at(deadline, 1)
        self.assertFalse(self.call(BOB, "withdraw_bid", "project_0"))
        self.assertTrue(self.call(STRANGER, "select_winner", "project_0"))

    def test_late_ruling_attempt_records_nothing(self):
        to_delivery(self.c)
        set_caller(ALICE)
        self.c.submit_milestone("project_0", 0, DELIVERY_URL, "n")
        set_caller(CLIENT)
        self.c.dispute_milestone("project_0", 0, "The delivered page does not match the rule")
        at(get(self.c)["milestones"][0]["ruling_deadline"], 1)
        calls = FakeWorld.rule_calls
        with self.assertRaises(UserError):
            set_caller(STRANGER)
            self.c.rule_dispute("project_0", 0)
        self.assertEqual(FakeWorld.rule_calls, calls, "the model must not even be asked after the deadline")
        self.assertEqual(get(self.c)["milestones"][0]["rulings"], [])


if __name__ == "__main__":
    unittest.main()
