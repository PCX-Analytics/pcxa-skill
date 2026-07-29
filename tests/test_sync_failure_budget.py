"""--max-failures must budget failure EVENTS, not failed files (pcxa#1699 / #1730).

A bulk-register batch fault fails every file in the flush at once. Counting
those as independent failures meant one brief backend blip — a 5xx, or a
server-side DB connection drop — could exhaust a 100-failure budget in two
flushes and abort a multi-hour sync that was otherwise perfectly healthy.

``summary["error"]`` still counts files (that many really didn't land, and it
stays the number shown to the user). ``summary["failure_events"]`` is what the
budget consults.
"""

from pcxa.commands.sync import _budget_exhausted, _record_batch_failure


def _summary():
    return {"created": 0, "duplicate": 0, "error": 0, "failure_events": 0, "failures": []}


def _items(n):
    return [{"original_filename": f"f-{i}.jpg"} for i in range(n)]


class TestRecordBatchFailure:
    def test_one_batch_fault_is_one_event_but_many_failed_files(self):
        """The load-bearing invariant."""
        summary = _summary()
        _record_batch_failure(summary, _items(50), "bulk-register: 503")

        assert summary["failure_events"] == 1, "a single fault must cost one unit of budget"
        assert summary["error"] == 50, "but all 50 files genuinely didn't land"
        assert len(summary["failures"]) == 50, "and each is still listed for diagnostics"

    def test_every_affected_file_is_still_named(self):
        """Collapsing the budget must not collapse the diagnostics."""
        summary = _summary()
        _record_batch_failure(summary, _items(3), "bulk-register: boom")

        assert [f["name"] for f in summary["failures"]] == ["f-0.jpg", "f-1.jpg", "f-2.jpg"]
        assert {f["error"] for f in summary["failures"]} == {"bulk-register: boom"}

    def test_repeated_faults_accumulate_one_event_each(self):
        summary = _summary()
        for _ in range(3):
            _record_batch_failure(summary, _items(50), "bulk-register: 503")

        assert summary["failure_events"] == 3
        assert summary["error"] == 150

    def test_missing_filename_falls_back_rather_than_raising(self):
        summary = _summary()
        _record_batch_failure(summary, [{}], "bulk-register: boom")

        assert summary["failures"][0]["name"] == "?"
        assert summary["failure_events"] == 1


class TestBudgetSemantics:
    """The behaviour change, stated against a --max-failures=100 budget."""

    MAX_FAILURES = 100

    def _exhausted(self, summary):
        # Calls the REAL guard used by _run_uploads — not a copy of it, so
        # changing the production predicate turns these tests red.
        return _budget_exhausted(summary, self.MAX_FAILURES)

    def test_budget_of_zero_disables_the_guard(self):
        summary = _summary()
        for _ in range(500):
            _record_batch_failure(summary, _items(50), "bulk-register: 503")
        assert not _budget_exhausted(summary, 0)

    def test_two_batch_faults_no_longer_exhaust_a_100_failure_budget(self):
        """Two 50-file flush faults used to total exactly 100 'failures' and
        abort the run. That is the #1699 report."""
        summary = _summary()
        _record_batch_failure(summary, _items(50), "bulk-register: 503")
        _record_batch_failure(summary, _items(50), "bulk-register: 503")

        assert summary["error"] >= self.MAX_FAILURES, "the old counter would have tripped here"
        assert not self._exhausted(summary), "but two blips must not abort the sync"

    def test_a_genuinely_bad_run_still_aborts(self):
        """The budget must not become unenforceable — 100 real per-file
        failures still stop the run."""
        summary = _summary()
        for i in range(self.MAX_FAILURES):
            summary["error"] += 1
            summary["failure_events"] += 1
            summary["failures"].append({"name": f"bad-{i}.jpg", "error": "403"})

        assert self._exhausted(summary)

    def test_sustained_batch_faults_still_abort_eventually(self):
        """One event per fault, so a persistently broken backend still trips
        the budget — it just takes 100 faults instead of two."""
        summary = _summary()
        for _ in range(self.MAX_FAILURES):
            _record_batch_failure(summary, _items(50), "bulk-register: 503")

        assert self._exhausted(summary)
