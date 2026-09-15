from agent_system.risk import classify_risk


def test_risk_levels():
    assert classify_risk("Update README docs") == "low"
    assert classify_risk("Fix login bug") == "medium"
    assert classify_risk("Change authentication architecture") == "high"
    assert classify_risk("Deploy production migration") == "critical"
