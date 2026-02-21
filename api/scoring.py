"""
scoring.py

User proficiency vector update logic.
"""

LEARNING_RATE = 0.15
HINT_PENALTY = 0.1
ATTEMPT_PENALTY = 0.15


def compute_final_score(correctness: float, hints_used: int, attempts: int) -> float:
    """
    Compute the final score after penalties for hints and extra attempts.

    correctness: 0.0–1.0 from the GPT judge
    hints_used:  number of hints the user requested
    attempts:    which attempt this is (1-based)
    """
    penalty = HINT_PENALTY * hints_used + ATTEMPT_PENALTY * max(0, attempts - 1)
    return max(0.0, correctness - penalty)


def update_user_vector(
    user_vector: list[float],
    problem_vector: list[float],
    final_score: float,
) -> list[float]:
    """
    Update the user's proficiency vector based on their performance.

    For each topic that the problem covers (problem_vector[i] > 0.1),
    nudge the user's score upward proportional to how well they did.

    Returns the new user vector.
    """
    new_vector = list(user_vector)
    for i in range(len(new_vector)):
        if problem_vector[i] > 0.1:
            delta = LEARNING_RATE * final_score * problem_vector[i]
            new_vector[i] = min(1.0, new_vector[i] + delta)
    return [round(v, 4) for v in new_vector]
