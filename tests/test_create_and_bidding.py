import json
import unittest

from _bootstrap import *  # noqa: F401,F403
from _bootstrap import (ALICE, BIDDERS, BOB, BOND, BUDGET, CAROL, CLIENT, CLOCK, GOOD, STRANGER, BidRoom,
                        PaymentRejected, UserError, World, assert_balanced, call_payable, create_project,
                        default_project_args, get, make_contract, place_bid, set_caller, withdraw)


class TestCreate(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()

    def test_create_records_everything_and_locks_the_budget(self):
        pid = create_project(self.c)
        self.assertEqual(pid, "project_0")
        p = get(self.c)
        self.assertEqual(p["status"], "bidding")
        self.assertEqual(p["client"], CLIENT)
        self.assertEqual(p["budget"], str(BUDGET))
        self.assertEqual(p["bond"], str(BOND))
        self.assertEqual(p["locked"], str(BUDGET))
        self.assertEqual(p["criteria"], ["Shows relevant past work", "Gives a clear plan with milestones"])
        self.assertEqual(p["weights"], [3, 2])
        self.assertEqual([m["bps"] for m in p["milestones"]], [6000, 4000])
        self.assertEqual(len(p["criteria_digest"]), 64)
        self.assertEqual(p["history"][0]["event"], "project_created")
        self.assertEqual(self.c.total_projects(), 1)
        assert_balanced(self, self.c)

    def test_ids_increase(self):
        create_project(self.c)
        self.assertEqual(create_project(self.c), "project_1")

    def test_invalid_inputs_are_refunded_not_reverted(self):
        bad = [
            dict(title="x"),
            dict(brief="short"),
            dict(criteria=[]),
            dict(criteria=["a"] * 7, weights=[1] * 7),
            dict(weights=[1]),
            dict(weights=[0, 3]),
            dict(weights=[3, 6]),
            dict(milestone_bps=[6000, 3000]),
            dict(milestone_bps=[9800, 200]),
            dict(milestone_titles=["only one"]),
            dict(milestone_titles=["a b c"] * 6, milestone_acceptance=["acceptance text"] * 6, milestone_bps=[1000] * 6),
            dict(max_delivery_minutes=5),
            dict(bond_bps=99),
            dict(bond_bps=3001),
            dict(bidding_minutes=4),
            dict(accept_minutes=4),
            dict(review_minutes=10081),
            dict(grace_minutes=0),
        ]
        for over in bad:
            before = int(self.c.balance)
            with self.assertRaises(PaymentRejected, msg=str(over)):
                create_project(self.c, **over)
            self.assertEqual(int(self.c.balance), before + BUDGET)
        self.assertEqual(self.c.total_projects(), 0)
        self.assertEqual(int(self.c.get_pending_withdrawal(CLIENT)), BUDGET * len(bad))
        assert_balanced(self, self.c)

    def test_budget_below_minimum_is_refunded(self):
        BidRoom.MIN_BUDGET = 500
        try:
            with self.assertRaises(PaymentRejected):
                create_project(self.c, budget=499)
        finally:
            BidRoom.MIN_BUDGET = 1

    def test_zero_value_invalid_call_reverts(self):
        set_caller(CLIENT)
        with self.assertRaises(UserError):
            call_payable(self.c, "create_project", 0, **default_project_args())

    def test_rejected_create_can_be_withdrawn(self):
        with self.assertRaises(PaymentRejected):
            create_project(self.c, title="x")
        self.assertEqual(withdraw(self.c, CLIENT), BUDGET)
        assert_balanced(self, self.c)

    def test_string_numbers_are_accepted_for_integers(self):
        pid = create_project(self.c, bond_bps="1000", weights=["3", "2"], bidding_minutes="60")
        self.assertEqual(get(self.c, pid)["bond"], str(BOND))

    def test_boolean_and_fractional_numbers_are_rejected(self):
        with self.assertRaises(PaymentRejected):
            create_project(self.c, bond_bps=True)
        with self.assertRaises(PaymentRejected):
            create_project(self.c, bond_bps=1000.5)


class TestBidding(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        create_project(self.c)

    def test_bid_is_recorded_and_bond_locked(self):
        self.assertEqual(place_bid(self.c, ALICE), "0")
        p = get(self.c)
        bid = p["bids"][0]
        self.assertEqual((bid["bidder"], bid["price"], bid["minutes"], bid["status"]), (ALICE, "5000", 300, "active"))
        self.assertEqual(p["locked"], str(BUDGET + BOND))
        assert_balanced(self, self.c)

    def test_bond_must_match_exactly(self):
        for value in (BOND - 1, BOND + 1):
            with self.assertRaises(PaymentRejected):
                place_bid(self.c, ALICE, value=value)
        with self.assertRaises(UserError):
            place_bid(self.c, ALICE, value=0)
        self.assertEqual(len(get(self.c)["bids"]), 0)
        self.assertEqual(int(self.c.get_pending_withdrawal(ALICE)), 2 * BOND)
        assert_balanced(self, self.c)

    def test_client_cannot_bid(self):
        with self.assertRaises(PaymentRejected):
            place_bid(self.c, CLIENT)

    def test_one_active_bid_per_address(self):
        place_bid(self.c, ALICE)
        with self.assertRaises(PaymentRejected):
            place_bid(self.c, ALICE)

    def test_withdraw_then_rebid(self):
        place_bid(self.c, ALICE)
        set_caller(ALICE)
        self.assertEqual(self.c.withdraw_bid("project_0"), "withdrawn")
        self.assertEqual(get(self.c)["bids"][0]["status"], "withdrawn")
        self.assertEqual(int(self.c.get_pending_withdrawal(ALICE)), BOND)
        self.assertEqual(place_bid(self.c, ALICE, price=4000), "1")
        assert_balanced(self, self.c)

    def test_withdraw_without_bid_fails(self):
        set_caller(ALICE)
        with self.assertRaises(UserError):
            self.c.withdraw_bid("project_0")

    def test_price_and_time_limits(self):
        for price, minutes in ((0, 300), (BUDGET + 1, 300), (5000, 0), (5000, 601)):
            with self.assertRaises(PaymentRejected, msg=(price, minutes)):
                place_bid(self.c, ALICE, price=price, minutes=minutes)
        place_bid(self.c, ALICE, price=BUDGET, minutes=600)

    def test_price_is_in_whole_units(self):
        BidRoom.WEI_PER_UNIT = 100
        try:
            place_bid(self.c, ALICE, price=100)
            self.assertEqual(get(self.c)["bids"][0]["price"], "10000")
            with self.assertRaises(PaymentRejected):
                place_bid(self.c, BOB, price=101)
        finally:
            BidRoom.WEI_PER_UNIT = 1

    def test_proposal_length_rules(self):
        for text in ("too short", "x" * 801):
            with self.assertRaises(PaymentRejected):
                place_bid(self.c, ALICE, proposal=text)

    def test_window_closes(self):
        CLOCK.minutes(61)
        with self.assertRaises(PaymentRejected):
            place_bid(self.c, ALICE)
        set_caller(ALICE)

    def test_bid_at_the_deadline_second_is_still_allowed(self):
        CLOCK.minutes(60)
        place_bid(self.c, ALICE)

    def test_maximum_active_bids(self):
        for who in BIDDERS[:12]:
            place_bid(self.c, who)
        with self.assertRaises(PaymentRejected):
            place_bid(self.c, BIDDERS[12])
        set_caller(BIDDERS[0])
        self.c.withdraw_bid("project_0")
        place_bid(self.c, BIDDERS[12])

    def test_unknown_project(self):
        set_caller(ALICE)
        with self.assertRaises(PaymentRejected):
            call_payable(self.c, "place_bid", BOND, "project_9", 5000, 300, GOOD)

    def test_withdraw_closed_after_deadline(self):
        place_bid(self.c, ALICE)
        CLOCK.minutes(61)
        set_caller(ALICE)
        with self.assertRaises(UserError):
            self.c.withdraw_bid("project_0")


class TestCancelAndExpire(unittest.TestCase):
    def setUp(self):
        self.c = make_contract()
        create_project(self.c)

    def test_cancel_returns_the_budget(self):
        set_caller(CLIENT)
        self.assertEqual(self.c.cancel_project("project_0"), "cancelled")
        self.assertEqual(int(self.c.get_pending_withdrawal(CLIENT)), BUDGET)
        self.assertEqual(get(self.c)["status"], "cancelled")
        self.assertEqual(withdraw(self.c, CLIENT), BUDGET)
        self.assertEqual(int(self.c.balance), 0)

    def test_only_client_can_cancel(self):
        set_caller(ALICE)
        with self.assertRaises(UserError):
            self.c.cancel_project("project_0")

    def test_cannot_cancel_with_active_bids_but_can_after_all_withdrawn(self):
        place_bid(self.c, ALICE)
        set_caller(CLIENT)
        with self.assertRaises(UserError):
            self.c.cancel_project("project_0")
        set_caller(ALICE)
        self.c.withdraw_bid("project_0")
        set_caller(CLIENT)
        self.c.cancel_project("project_0")
        assert_balanced(self, self.c)

    def test_expire_unbid_after_the_window(self):
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.expire_unbid("project_0")
        CLOCK.minutes(61)
        self.c.expire_unbid("project_0")
        p = get(self.c)
        self.assertEqual((p["status"], p["end_reason"]), ("expired", "no_bids"))
        self.assertEqual(int(self.c.get_pending_withdrawal(CLIENT)), BUDGET)
        assert_balanced(self, self.c)

    def test_expire_unbid_refuses_when_bids_exist(self):
        place_bid(self.c, ALICE)
        CLOCK.minutes(61)
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.expire_unbid("project_0")

    def test_expire_unselected_refunds_everyone_after_the_grace_period(self):
        place_bid(self.c, ALICE)
        place_bid(self.c, BOB)
        CLOCK.minutes(61)
        set_caller(STRANGER)
        with self.assertRaises(UserError):
            self.c.expire_unselected("project_0")
        CLOCK.minutes(1440)
        self.c.expire_unselected("project_0")
        p = get(self.c)
        self.assertEqual((p["status"], p["end_reason"]), ("expired", "selection_timeout"))
        self.assertEqual(int(self.c.get_pending_withdrawal(ALICE)), BOND)
        self.assertEqual(int(self.c.get_pending_withdrawal(BOB)), BOND)
        self.assertEqual(int(self.c.get_pending_withdrawal(CLIENT)), BUDGET)
        self.assertEqual(p["locked"], "0")
        assert_balanced(self, self.c)

    def test_nothing_works_on_a_closed_project(self):
        set_caller(CLIENT)
        self.c.cancel_project("project_0")
        with self.assertRaises(UserError):
            self.c.cancel_project("project_0")
        with self.assertRaises(PaymentRejected):
            place_bid(self.c, ALICE)


if __name__ == "__main__":
    unittest.main()
