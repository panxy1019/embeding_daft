---
mode: agent
---
Given the English documentation file in `AgentHeaven-docs/en/source/**/*.md`, translate it to Simplified Chinese to the corresponding `AgentHeaven-docs/zh/source/**/*.md` file with the same name. If a previous version of the Chinese file already exists, use it as reference to ensure consistency in terminology and style, but make sure you review word-by-word about the changes in the English file and update the Chinese file accordingly.

- Reference `AgentHeaven-docs/i18n.md` or other existing Chinese documentation files for consistent translation of technical terms.

- For system components, read code before translating to ensure accurate understanding. When conflict, always prioritize source code > EN documentation > test > ZH documentation.

- Strictly align the Chinese documentation format to English documentation format, they should always have the same number of lines.
    - All index pages must contain the "Quick Navigation" section with the same grid items in the same order. Keep the emojis in section titles and grid items consistent. And all index pages must contain the "Contents" section with the same toctree items in the same order.
    - For most content pages, it should have a "Further Exploration" section at the end with the same links in the same order. Make sure the links point to the Chinese version if applicable.
    - All main section titles must be numbered (except structural sections like "Quick Navigation"). Sections must end with `<br/>` (only sections, not paragraphs or titles).
    - Translate comments in code blocks, but do not translate code itself or the strings in code.
    - Remember to change image links or hyperlinks to point to the Chinese version if applicable. Images are placed under `_static` (after build).

- In general, use straightforward and concise language, the tone should be friendly and natural, avoid overly formal or technical language. Keep accuracy the first priority, only use simplifications when it does not compromise accuracy.

- If title or catalog changes, make sure to update the toctree in the markdown as well as the corresponding file names and titles in other related markdown files.

- If you find any terminology and confirm it is more accurate, please update it in `AgentHeaven-docs/i18n.md` as well as all existing Chinese documentation files for consistency. This requires human review; do not use automated search-and-replace.
