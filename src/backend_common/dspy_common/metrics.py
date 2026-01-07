import dspy
import jiwer


def edit_distance_metric(
    gold: dspy.Example,
    pred: dspy.Prediction,
    key: str,
    trace: str | None = None,  # Note to myself: We are not sure if it is realy a string :)
) -> float:
    """
    Calculate the word erro rate (WER) and character error rate (CER) between the predicted and reference values for a given key.
    Then combine the two with equal weight, invert the score and return it.
    Inversion is necessary because DSPy maximizes the score.

    Args:
        gold: The gold standard example.
        pred: The predicted example.
        key: The key of the value to calculate the edit distance for.
        trace: The trace of the prediction.

    Returns:
        The score (1 - ((WER + CER) / 2)) between 0 and 1, where 1 is the best score.
    """
    if key not in pred:
        raise ValueError(f"Key {key} not found in pred. Available keys: {pred.keys()}")
    if key not in gold:
        raise ValueError(f"Key {key} not found in gold. Available keys: {gold.keys()}")
    predicted = pred[key]
    reference = gold[key]

    wer_value = jiwer.wer(reference, predicted)
    cer_value = jiwer.cer(reference, predicted)

    # Combine WER and CER with equal weight
    combined_error = (wer_value + cer_value) / 2.0
    # DSPy maximizes the score, so we need to invert it
    score = max(0.0, 1.0 - combined_error)
    return score
