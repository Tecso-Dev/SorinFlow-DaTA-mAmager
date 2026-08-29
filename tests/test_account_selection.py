"""
چرخش شماره — which Divar account carries the next reveal.

Companion to test_account_rotation.py, which pins *when* a rotation happens.
This one pins *which account it lands on*, and how the budget is remembered
between jobs.

Three faults made one account absorb everything, which is what the constant
SMS was:

  1. A job picked the most recently *updated* account, and saving a session on
     rotation bumps updated_at — so the account just used was always the one
     picked next.
  2. The reveal counter lived on the scraper, and a fresh scraper is built per
     job, so it restarted every run while the account's real spend kept
     climbing. «۱۰۰ per number» could never happen across short jobs.
  3. Up to three jobs run at once and each picked independently, so they all
     landed on the same number.

All three are the same decision — «which account next» — so they are tested
together here against a stand-in for the cookies table.
"""
import pytest


class Account:
    def __init__(self, phone, reveals=0, last_used=0, valid=True):
        self.phone, self.reveals, self.last_used, self.valid = phone, reveals, last_used, valid
    def __repr__(self):
        return f"{self.phone}(r={self.reveals},t={self.last_used})"


def pick(accounts):
    """Mirror of the ORDER BY: least spent, oldest used breaking the tie."""
    usable = [a for a in accounts if a.valid]
    if not usable:
        return None
    return sorted(usable, key=lambda a: (a.reveals, a.last_used))[0]


def pick_old(accounts):
    """What it used to do: most recently updated wins."""
    usable = [a for a in accounts if a.valid]
    return sorted(usable, key=lambda a: -a.last_used)[0] if usable else None


def should_rotate(account_reveals, job_counter, every, forced=False):
    """Mirror of the threshold in maybe_rotate_account."""
    if forced:
        return True
    if every <= 0:
        return False
    return max(account_reveals, job_counter) >= every


class TestSelection:
    def test_picks_the_least_spent(self):
        accs = [Account("A", reveals=90), Account("B", reveals=10), Account("C", reveals=50)]
        assert pick(accs).phone == "B"

    def test_oldest_used_breaks_a_tie(self):
        accs = [Account("A", reveals=0, last_used=500), Account("B", reveals=0, last_used=100)]
        assert pick(accs).phone == "B"

    def test_invalid_accounts_are_never_picked(self):
        accs = [Account("A", reveals=0, valid=False), Account("B", reveals=80)]
        assert pick(accs).phone == "B"

    def test_none_when_nothing_is_valid(self):
        assert pick([Account("A", valid=False)]) is None

    def test_the_old_order_kept_returning_the_same_account(self):
        """Using an account bumped its timestamp, so it won every next pick."""
        accs = [Account("A", reveals=0, last_used=10), Account("B", reveals=0, last_used=20)]
        chosen = pick_old(accs)
        assert chosen.phone == "B"
        chosen.last_used = 999          # a job ran and saved its session
        assert pick_old(accs).phone == "B"   # …and it is picked again
        # the new order moves on instead
        assert pick(accs).phone == "A"

    def test_three_concurrent_jobs_spread_out(self):
        """Each job charges what it takes, so the next one sees it."""
        accs = [Account("A"), Account("B"), Account("C")]
        chosen = []
        for _ in range(3):
            a = pick(accs)
            a.reveals += 40           # this job's share
            a.last_used = len(chosen)
            chosen.append(a.phone)
        assert sorted(chosen) == ["A", "B", "C"], chosen

    def test_the_old_order_put_all_three_on_one_number(self):
        accs = [Account("A"), Account("B"), Account("C")]
        chosen = []
        for i in range(3):
            a = pick_old(accs)
            a.reveals += 40
            a.last_used = 100 + i     # using it makes it the most recent
            chosen.append(a.phone)
        assert len(set(chosen)) == 1, chosen


class TestThreshold:
    def test_rotates_once_the_account_has_spent_its_budget(self):
        assert should_rotate(account_reveals=100, job_counter=0, every=100) is True

    def test_a_short_job_still_sees_the_accounts_history(self):
        """The account was at 98 from earlier jobs. This job reveals two more —
        each one is charged to the account first — and the second crosses the
        line, even though this job has only done two."""
        account = 98
        for job_counter in (1, 2):
            account += 1                       # _charge_reveal writes it through
            rotated = should_rotate(account, job_counter, every=100)
        assert account == 100
        assert rotated is True

    def test_the_old_per_job_counter_missed_it(self):
        """Counting only this job's reveals, 2 < 100, so it never rotated —
        and the account sailed past its budget job after job."""
        assert should_rotate(account_reveals=0, job_counter=2, every=100) is False

    def test_does_not_rotate_early(self):
        assert should_rotate(account_reveals=40, job_counter=10, every=100) is False

    def test_zero_disables_the_threshold(self):
        assert should_rotate(account_reveals=9999, job_counter=9999, every=0) is False

    def test_but_a_challenge_still_rotates_with_the_threshold_off(self):
        assert should_rotate(account_reveals=0, job_counter=0, every=0, forced=True) is True

    def test_a_challenge_beats_any_count(self):
        assert should_rotate(account_reveals=1, job_counter=1, every=100, forced=True) is True


class TestSpendAndRest:
    def test_a_challenged_account_is_banked_as_spent(self):
        """Otherwise «least spent» hands it straight back."""
        a = Account("A", reveals=12)
        a.reveals = max(a.reveals, 100)      # _mark_account_spent
        others = [a, Account("B", reveals=30)]
        assert pick(others).phone == "B"

    def test_a_new_round_starts_when_every_account_is_spent(self):
        accs = [Account("A", reveals=100), Account("B", reveals=100)]
        assert all(a.reveals >= 100 for a in accs)
        for a in accs:                        # _rest_all_accounts
            a.reveals = 0
        assert pick(accs).reveals == 0

    def test_a_full_run_across_two_accounts(self):
        """100 each, then a new round — not 200 on one number."""
        accs = [Account("A"), Account("B")]
        active = pick(accs)
        used = {}
        for _ in range(400):
            active.reveals += 1
            used[active.phone] = used.get(active.phone, 0) + 1
            if should_rotate(active.reveals, 0, every=100):
                if all(a.reveals >= 100 for a in accs):
                    for a in accs:
                        a.reveals = 0
                active = pick(accs)
        assert used["A"] == used["B"] == 200, used
