from qabuddy.ingest.pipeline import needs_history


def test_history_rebuilt_when_jenkins_changed_or_full():
    assert needs_history(full=True, jenkins_changed=False, jenkins_removed=False,
                         jenkins_indexed=False, history_recorded=False)
    assert needs_history(full=False, jenkins_changed=True, jenkins_removed=False,
                         jenkins_indexed=True, history_recorded=True)
    assert needs_history(full=False, jenkins_changed=False, jenkins_removed=True,
                         jenkins_indexed=True, history_recorded=True)


def test_history_rebuilt_when_previous_run_was_interrupted():
    # Jenkins files were stored, but the run stopped before the history chunks were indexed.
    assert needs_history(full=False, jenkins_changed=False, jenkins_removed=False,
                         jenkins_indexed=True, history_recorded=False)


def test_history_skipped_when_nothing_jenkins_changed():
    assert not needs_history(full=False, jenkins_changed=False, jenkins_removed=False,
                             jenkins_indexed=True, history_recorded=True)
    assert not needs_history(full=False, jenkins_changed=False, jenkins_removed=False,
                             jenkins_indexed=False, history_recorded=False)
