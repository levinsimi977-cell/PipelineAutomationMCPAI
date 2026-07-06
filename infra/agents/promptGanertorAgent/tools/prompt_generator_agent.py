def prompt_generator_agent(request: dict) -> dict:
    prompt = request.get("prompt")

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt agent request must contain a non-empty prompt")

    return {
        "useCaseId": request.get("useCaseId"),
        "originalPrompt": prompt.strip(),
        "expectedResult": request.get("expectedResult"),
        "steps": [],
    }