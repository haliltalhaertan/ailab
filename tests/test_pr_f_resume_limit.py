from lab.iteration_control import resume_iteration_limit


def test_single_resume_stops_at_current_partial_iteration():
    assert resume_iteration_limit(6, 1, 2, only_one_iteration=True) == 2


def test_single_resume_advances_one_when_no_current_iteration_is_recorded():
    assert resume_iteration_limit(6, 0, 0, only_one_iteration=True) == 1
    assert resume_iteration_limit(6, 2, 2, only_one_iteration=True) == 3


def test_full_resume_keeps_configured_target():
    assert resume_iteration_limit(6, 1, 2, only_one_iteration=False) == 6


def test_single_resume_never_exceeds_configured_target():
    assert resume_iteration_limit(2, 1, 7, only_one_iteration=True) == 2
