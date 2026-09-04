from lab.ui_tool_availability import tool_availability_caption, tool_availability_rows


def test_tool_availability_rows_use_effective_snapshot_first():
    snapshot = {
        "declared_tool_availability": {
            "lean_draft": {"available": True, "reason": "declared"},
            "z3": {"available": True, "reason": "declared"},
        },
        "runtime_tool_availability": {
            "lean_draft": {"available": False, "reason": "runtime missing"},
            "z3": {"available": True, "reason": "runtime"},
        },
        "effective_tool_availability": {
            "lean_draft": {"available": False, "reason": "runtime daralması"},
            "z3": {"available": True, "reason": "z3-solver kullanılabilir"},
        },
    }

    rows = {row["name"]: row for row in tool_availability_rows(snapshot)}
    assert rows["lean_draft"]["label"] == "Lean"
    assert rows["lean_draft"]["available"] is False
    assert rows["lean_draft"]["reason"] == "runtime daralması"
    assert rows["z3"]["available"] is True


def test_tool_availability_caption_explains_resume_no_widen():
    snapshot = {
        "resumed_snapshot": True,
        "effective_tool_availability": {
            "z3": {"available": True, "reason": "ok"},
        },
    }
    caption = tool_availability_caption(snapshot)
    assert "aynı run" in caption
    assert "genişletmez" in caption


def test_tool_availability_caption_before_first_run():
    assert "İlk theorem run" in tool_availability_caption({})
