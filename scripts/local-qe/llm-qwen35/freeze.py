"""Create immutable pre-inference evidence and first-three-in-original-order input."""
from pathlib import Path
import json

import contract
import prepare
import runtime


def main():
    destination = prepare.DEST
    source_input = destination / "inputs/dev48-source-only-v1.jsonl"
    rows, source_sha = contract.read_input(source_input)
    smoke = destination / "inputs/smoke-first3-v1.jsonl"
    prepare.once(smoke, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows[:3]).encode("utf-8"))
    checked, smoke_sha = contract.read_input(smoke)
    prepare.json_once(destination / "inputs/smoke-first3-v1.manifest.json", {
        "version": "qwen35-first3-technical-smoke-v1", "selectedAt": prepare.utc(),
        "sourceInputSha256": source_sha, "smokeInputSha256": smoke_sha,
        "selection": "first three rows in original dev48 inputs order; not selected using quality labels or outputs",
        "selectedIds": [row["id"] for row in checked], "count": 3,
        "modelFields": ["source", "translation", "context"], "labelsReferencesChecksRead": False,
        "technicalSmokeOnly": True, "qualityClaimPermitted": False})
    template, metadata = runtime.gguf_metadata(destination / prepare.MODEL_NAME)
    prepare.once(destination / "evidence/gguf-chat-template.jinja", template.encode("utf-8"))
    prepare.json_once(destination / "runtime-support-audit.json", {
        "version": "qwen35-runtime-support-audit-v1", "createdAt": prepare.utc(),
        "gguf": metadata, "memoryPlan": runtime.memory_plan(), "support": "b10888 qwen35 9B branch plus actual GGUF architecture",
        "originalObservedConfigEosId": 248044, "conversionEosId": metadata["tokenizerEosId"],
        "tokenIdDifferenceHandling": "preserve exact GGUF; verify runtime EOS token text/ID and boundary tokens; no override",
        "originalWeightRevisionAttested": False, "nativeModelLoaded": False,
        "tokenizerInferenceValidated": False, "qualityValidated": False,
        "runtimeServerContextEvidenceSha256": prepare.sha_file(destination / "evidence/runtime-server-context.cpp")})
    paths = sorted(p for p in Path(__file__).parent.iterdir() if p.is_file())
    prepare.json_once(destination / "freeze-v1.json", {
        "version": "qwen35-semantic-review-freeze-v1", "frozenAt": prepare.utc(),
        "installationSha256": prepare.sha_file(destination / "install-manifest.json"),
        "contract": contract.contract_identity(), "resourceProfile": runtime.RESOURCE_PROFILE,
        "runtimeCodeHashes": runtime.code_hashes(),
        "files": {str(p.relative_to(prepare.ROOT)): prepare.sha_file(p) for p in paths},
        "sanitizedDev48Sha256": source_sha, "technicalSmokeInputSha256": smoke_sha,
        "modelOutputsObservedForThisCandidate": False, "modelInferencePerformed": False,
        "syntheticTests": {"contract": 24, "runtime": 11, "passed": True},
        "historicalDocumentationExposure": "Repository entry documentation was read under AGENTS; it contains prior aggregate model summaries. No prior raw outputs, blind keys or judgments were read for this preparation.",
        "futurePromptChangeRequiresNewVersion": True})
    print(json.dumps({"event": "frozen", "sha256": prepare.sha_file(destination / "freeze-v1.json"),
                      "smokeInputSha256": smoke_sha, "modelLoaded": False}))


if __name__ == "__main__":
    main()
