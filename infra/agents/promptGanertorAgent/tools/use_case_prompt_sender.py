import json
import os

def build_prompt_agent_request(use_case: dict) -> dict:
    use_case_id = use_case.get("id") or use_case.get("useCaseId")
    prompt = use_case.get("prompt")

    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Use case must contain a non-empty prompt")

    return {
        "useCaseId": use_case_id,
        "prompt": prompt.strip(),
        "expectedResult": use_case.get("expectedResult"),
    }


def send_prompt_from_use_case(use_case: dict, prompt_agent) -> dict:
    request = build_prompt_agent_request(use_case)
    return prompt_agent(request)


def load_json_file(file_path: str) -> dict:
    with open(file_path, "r", encoding="utf-8") as file:
        return json.load(file)

def load_use_case_from_catalog_entry(catalog_entry: dict, catalog_dir: str) -> dict:
    relative_path = catalog_entry.get("path")

    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Catalog entry must contain a non-empty path")

    use_case_path = os.path.join(catalog_dir, relative_path)
    use_case = load_json_file(use_case_path)

    return use_case        


def send_prompt_from_catalog_entry(catalog_entry: dict, catalog_dir: str, prompt_agent) -> dict:
    use_case = load_use_case_from_catalog_entry(catalog_entry, catalog_dir)
    return send_prompt_from_use_case(use_case, prompt_agent)    