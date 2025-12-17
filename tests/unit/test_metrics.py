import dspy  # type: ignore[import-not-found]  # pyright: ignore[reportMissingTypeStubs]

from src.backend_common.dspy_common.metrics import edit_distance_metric


def test_edit_distance_metric_perfect_score() -> None:
    gold = dspy.Example(text="hello world")
    pred = dspy.Prediction(text="hello world")
    metric = edit_distance_metric

    score: float = metric(gold, pred, "text")

    assert score == 1.0


def test_edit_distance_metric_penalizes_errors() -> None:
    gold = dspy.Example(text="hello world")
    pred = dspy.Prediction(text="hello word")
    metric = edit_distance_metric

    score: float = metric(gold, pred, "text")

    assert 0.0 < score < 1.0
