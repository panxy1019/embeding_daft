---
mode: agent
---
Given the English documentation file in `AgentHeaven-docs/en/source/**/*.md`, verify its alignment with the current source code in `AgentHeaven-dev/src/`. If discrepancies are found—such as outdated APIs, incorrect parameter descriptions, missing features, or behavior that contradicts the actual implementation—flag them for human review and propose specific updates to bring the documentation into agreement with the code.

- Always prioritize the source code as the single source of truth. When evaluating accuracy, follow this hierarchy:  
    **source code > test cases > existing English documentation > Chinese documentation**.

- For each documentation file:
    - Cross-check every described function, class, configuration option, CLI command, or workflow against the corresponding code.
    - Validate parameter names, types, default values, return types, exceptions, and side effects.
    - Ensure all examples in code blocks reflect actual, runnable usage based on the current codebase.
    - Confirm that architectural diagrams, data flows, or system behaviors described in text match the implemented logic.

- Do **not** automatically modify the English documentation. Instead:
    - Generate a clear, line-specific change proposal (including file path, original line(s), and suggested revision).
    - Include a justification referencing the relevant code (e.g., function signature in `AgentHeaven-dev/src/core/engine.py`, CLI argument parser in `AgentHeaven-dev/src/cli/main.py`, etc.).
    - Submit the proposal for human review and approval before any edit is made.

- If the human explicitly requests an update or approves a proposal, apply the change precisely:
    - Preserve the original Markdown structure, section numbering, and formatting.
    - Maintain the "Further Exploration" section with correct English links.
    - Keep the "Quick Navigation" grid and "Contents" toctree consistent with the documentation architecture.
    - All main section titles must be numbered (except structural sections like "Quick Navigation"). Sections must end with `<br/>` (only sections, not paragraphs or titles).

- When updating, ensure terminology remains consistent with `docs/i18n.md` (for shared concepts) and other English documentation files.

- If a documentation section is found to be entirely obsolete (e.g., describing a removed feature), propose its removal or replacement with a deprecation notice, pending human confirmation.

- Never alter code or tests—this agent’s scope is strictly documentation alignment with code, not code correction.

- Log all proposed changes in a machine-readable format (e.g., JSON patch or diff-style report) for traceability and integration with CI/CD workflows.

- Check the documentation wording, make sure it is logically fluent and intuitive to read.
