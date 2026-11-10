"""
Shared test bootstrap for BidRoom.

Wires up the offline genlayer SDK stub, loads contract.py (or the file named
by the BR_CONTRACT environment variable), replaces the contract's single
clock with a controllable one, and provides a scripted stand-in for the
model and the web fetcher so every consensus path can be exercised offline.
"""
import copy
import datetime as _dt
import importlib.util
import json
import os
import sys
from unittest.mock import patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_STUB_DIR = os.path.join(_THIS_DIR, "genlayer_stub")
if _STUB_DIR not in sys.path:
    sys.path.insert(0, _STUB_DIR)

CONTRACT_PATH = os.environ.get("BR_CONTRACT") or os.path.join(os.path.dirname(_THIS_DIR), "contract.py")
_spec = importlib.util.spec_from_file_location("bidroom_contract", CONTRACT_PATH)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)

m = _module
BidRoom = m.BidRoom
gl = m.gl
Address = m.Address
u256 = m.u256
UserError = gl.vm.UserError

BidRoom.MIN_BUDGET = 1
BidRoom.WEI_PER_UNIT = 1

UTC = _dt.timezone.utc
BASE = _dt.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
MIN = 60
HOUR = 3600


class Clock:
    def __init__(self):
        self.now = BASE

    def reset(self):
        self.now = BASE

    def advance(self, seconds):
        self.now = self.now + _dt.timedelta(seconds=seconds)

    def minutes(self, count):
        self.advance(count * 60)


CLOCK = Clock()
BidRoom._now_utc = staticmethod(lambda: CLOCK.now)

CLIENT = "0x" + "c1" * 20
ALICE = "0x" + "a1" * 20
BOB = "0x" + "b2" * 20
CAROL = "0x" + "d3" * 20
DAVE = "0x" + "e4" * 20
STRANGER = "0x" + "99" * 20
BIDDERS = ["0x" + hex(0x30 + i)[2:].rjust(2, "0") * 20 for i in range(16)]

BUDGET = 10000
BOND_BPS = 1000
BOND = BUDGET * BOND_BPS // 10000

CRITERIA = ["Shows relevant past work", "Gives a clear plan with milestones"]
WEIGHTS = [3, 2]
MS_TITLES = ["Draft", "Final"]
MS_ACCEPT = ["The page contains a complete first draft of the guide", "The page contains the final polished guide"]
MS_BPS = [6000, 4000]

GOOD = "I built three similar onboarding guides last year and will deliver in two clear steps, a draft then a final."
OK = "I have some related experience and will deliver the work in two steps with a short review in between."
WEAK = "I can do this quickly and cheaply, trust me, the result will be fine for everybody involved here."

DELIVERY_URL = "https://raw.githubusercontent.com/acme/guide/abc123/GUIDE.md"
PAGE_TEXT = "Onboarding guide. Section one covers setup. Section two covers daily use and troubleshooting steps."


def make_contract():
    CLOCK.reset()
    gl.evm.transfers.clear()
    FakeWorld.reset()
    set_caller(CLIENT)
    return BidRoom()


def set_caller(address):
    gl.message.sender_address = Address(address)


def call_payable(contract, method, value, *args, **kwargs):
    gl.message.value = u256(value)
    contract.balance = u256(int(contract.balance) + value)
    before = (dict(contract.projects), int(contract.project_count))
    try:
        result = getattr(contract, method)(*args, **kwargs)
    except Exception:
        contract.balance = u256(int(contract.balance) - value)
        raise
    finally:
        gl.message.value = u256(0)
    if isinstance(result, str) and result.startswith('{"refunded_wei"'):
        data = json.loads(result)
        assert data["refunded_wei"] == str(value), data
        assert (dict(contract.projects), int(contract.project_count)) == before, "a rejected payable call changed state"
        raise PaymentRejected(data["rejected"])
    return result


class PaymentRejected(UserError):
    pass


def default_project_args(**over):
    args = dict(
        title="Write the onboarding guide",
        brief="We need a clear onboarding guide for new engineers joining the platform team.",
        criteria=list(CRITERIA),
        weights=list(WEIGHTS),
        milestone_titles=list(MS_TITLES),
        milestone_acceptance=list(MS_ACCEPT),
        milestone_bps=list(MS_BPS),
        max_delivery_minutes=600,
        bond_bps=BOND_BPS,
        bidding_minutes=60,
        accept_minutes=30,
        review_minutes=30,
        grace_minutes=20,
    )
    args.update(over)
    return args


def create_project(contract, budget=BUDGET, who=CLIENT, **over):
    set_caller(who)
    return call_payable(contract, "create_project", budget, **default_project_args(**over))


def place_bid(contract, who, price=5000, minutes=300, proposal=GOOD, project="project_0", value=None):
    set_caller(who)
    pj = json.loads(contract.projects[project])
    return call_payable(contract, "place_bid", int(pj["bond"]) if value is None else value,
                        project, price, minutes, proposal)


def get(contract, project="project_0"):
    return json.loads(contract.projects[project])


def accounting(contract):
    return json.loads(contract.get_accounting())


def assert_balanced(test, contract):
    acc = accounting(contract)
    test.assertTrue(acc["balanced"], acc)


def withdraw(contract, who):
    set_caller(who)
    gl.evm.transfers.clear()
    contract.withdraw()
    paid = sum(int(t["value"]) for t in gl.evm.transfers)
    contract.balance = u256(int(contract.balance) - paid)
    return paid


class FakeWorld:
    pages = {}
    scores = {}
    ruling = "ACCEPTED"
    quote = "Section two covers daily use"
    select_calls = 0
    rule_calls = 0
    last_select_prompt = ""
    last_rule_prompt = ""
    select_override = None
    rule_override = None

    @classmethod
    def reset(cls):
        cls.pages = {DELIVERY_URL: PAGE_TEXT}
        cls.scores = {}
        cls.ruling = "ACCEPTED"
        cls.quote = "Section two covers daily use"
        cls.select_calls = 0
        cls.rule_calls = 0
        cls.last_select_prompt = ""
        cls.last_rule_prompt = ""
        cls.select_override = None
        cls.rule_override = None

    @classmethod
    def exec_prompt(cls, prompt, response_format="text"):
        if "neutral evaluator" in prompt:
            cls.select_calls += 1
            cls.last_select_prompt = prompt
            if cls.select_override is not None:
                value = cls.select_override(cls.select_calls)
                if isinstance(value, Exception):
                    raise value
                return value
            return {"scores": cls.scores, "reasoning": "Scored against C1 and C2 only."}
        if "neutral arbiter" in prompt:
            cls.rule_calls += 1
            cls.last_rule_prompt = prompt
            if cls.rule_override is not None:
                value = cls.rule_override(cls.rule_calls)
                if isinstance(value, Exception):
                    raise value
                return value
            return {"ruling": cls.ruling, "quote": cls.quote, "reasoning": "Checked against the acceptance criteria."}
        raise AssertionError("unexpected prompt")

    @classmethod
    def render(cls, url, mode="text"):
        if url not in cls.pages:
            raise RuntimeError("404")
        return cls.pages[url]


def install_world():
    return (
        patch.object(gl.nondet, "exec_prompt", FakeWorld.exec_prompt),
        patch.object(gl.nondet.web, "render", FakeWorld.render),
    )


def snapshot(contract):
    return (copy.deepcopy(dict(contract.projects)), int(contract.project_count),
            copy.deepcopy(dict(contract.pending_withdrawals)), int(contract.pending_total), int(contract.balance))


def restore(contract, snap):
    projects, count, pending, total, balance = snap
    contract.projects.clear()
    contract.projects.update(projects)
    contract.project_count = u256(count)
    contract.pending_withdrawals.clear()
    contract.pending_withdrawals.update(pending)
    contract.pending_total = u256(total)
    contract.balance = u256(balance)


def to_selection(contract, bids=((ALICE, 5000, 300, GOOD), (BOB, 3000, 400, OK)), scores=None, **over):
    """Creates project_0, places the bids, closes the window and returns the project id."""
    create_project(contract, **over)
    for who, price, minutes, text in bids:
        place_bid(contract, who, price, minutes, text)
    CLOCK.minutes(61)
    FakeWorld.scores = scores if scores is not None else {"B0": [3, 3], "B1": [1, 1]}
    return "project_0"


def to_delivery(contract, **over):
    pid = to_selection(contract, **over)
    set_caller(STRANGER)
    contract.select_winner(pid)
    set_caller(ALICE)
    contract.confirm_award(pid)
    return pid


class World:
    """Context manager that patches the model and the fetcher for a test."""

    def __enter__(self):
        self.patches = install_world()
        for p in self.patches:
            p.start()
        return FakeWorld

    def __exit__(self, *exc):
        for p in self.patches:
            p.stop()
        return False
