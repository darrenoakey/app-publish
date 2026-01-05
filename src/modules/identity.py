# ##################################################################
# identity module
# ai-generated app name, descriptions, keywords
# uses claude to analyze project and generate app name, subtitle, description, keywords, and category suggestions
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    llm_json,
    write_file,
    read_file,
    ensure_dir,
)


def gather_project_context(project_path: Path, state: ProjectState) -> str:
    # ##################################################################
    # gather project context
    # gather project files for llm analysis
    context_parts = []

    # Add project name
    context_parts.append(f"Project name: {state.project_name}")
    context_parts.append(f"Project type: {state.project_type}")
    context_parts.append("")

    # Read key files based on project type
    if state.project_type == "web":
        files_to_read = [
            "index.html",
            "src/index.html",
            "package.json",
            "README.md",
        ]
        # Also read main JS files
        js_files = list(project_path.glob("*.js"))[:3]
        files_to_read.extend([f.name for f in js_files])
    else:  # swift
        files_to_read = [
            "README.md",
            "Package.swift",
        ]
        # Read main Swift files
        swift_files = list(project_path.glob("**/*.swift"))[:5]
        files_to_read.extend([str(f.relative_to(project_path)) for f in swift_files])

    for file_path in files_to_read:
        full_path = project_path / file_path
        if full_path.exists() and full_path.is_file():
            try:
                content = full_path.read_text()
                # Limit size
                if len(content) > 5000:
                    content = content[:5000] + "\n... (truncated)"
                context_parts.append(f"=== {file_path} ===")
                context_parts.append(content)
                context_parts.append("")
            except Exception:
                pass

    return "\n".join(context_parts)


def generate_identity(project_path: Path, state: ProjectState) -> dict[str, any] | None:
    # ##################################################################
    # generate identity
    # use ai to generate app identity
    context = gather_project_context(project_path, state)

    prompt = f"""Analyze this iOS app project and generate App Store metadata.

{context}

Generate a JSON response with:
1. "app_name": A catchy, memorable app name (max 30 chars). Should be different from project folder name if that's not user-friendly.
2. "subtitle": App Store subtitle (max 30 chars) - brief value proposition
3. "description": Full App Store description (2-3 paragraphs, ~500 words). Focus on benefits, features, and what makes it unique. Use bullet points for features.
4. "keywords": Array of 10-15 relevant search keywords (max 100 chars total when comma-separated)
5. "primary_category": Main App Store category (e.g., "Games", "Productivity", "Utilities", "Entertainment", "Education")
6. "secondary_category": Secondary category
7. "promotional_text": Short promotional text (max 170 chars) for featuring

Respond with ONLY valid JSON, no other text.
"""

    result = llm_json(prompt)
    if not result:
        print_warning("Failed to generate identity via AI, using defaults")
        return {
            "app_name": state.project_name.replace("-", " ").title(),
            "subtitle": "Your new favorite app",
            "description": f"{state.project_name} is an innovative iOS application.",
            "keywords": [state.project_name.lower()],
            "primary_category": "Utilities",
            "secondary_category": "Entertainment",
            "promotional_text": f"Try {state.project_name} today!",
        }

    return result


def save_metadata_files(project_path: Path, identity: dict[str, any]) -> None:
    # ##################################################################
    # save metadata files
    # save identity to fastlane metadata files
    metadata_dir = project_path / "fastlane" / "metadata" / "en-US"
    ensure_dir(metadata_dir)

    # Name
    write_file(metadata_dir / "name.txt", identity.get("app_name", ""))

    # Subtitle
    write_file(metadata_dir / "subtitle.txt", identity.get("subtitle", ""))

    # Description
    write_file(metadata_dir / "description.txt", identity.get("description", ""))

    # Keywords (comma-separated, max 100 chars total per App Store requirement)
    keywords = identity.get("keywords", [])
    if isinstance(keywords, list):
        # Build keywords string, respecting 100 char limit
        keywords_parts = []
        total_len = 0
        for kw in keywords:
            kw = str(kw).strip()
            # Add 2 for ", " separator (except first)
            sep_len = 2 if keywords_parts else 0
            if total_len + sep_len + len(kw) <= 100:
                keywords_parts.append(kw)
                total_len += sep_len + len(kw)
            else:
                break
        keywords_str = ", ".join(keywords_parts)
    else:
        keywords_str = str(keywords)[:100]
    write_file(metadata_dir / "keywords.txt", keywords_str)

    # Promotional text
    write_file(metadata_dir / "promotional_text.txt", identity.get("promotional_text", ""))

    # Release notes (placeholder for first release)
    write_file(metadata_dir / "release_notes.txt", "Initial release")

    print_success("Metadata files saved to fastlane/metadata/en-US/")


def run(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # run identity generation step
    # sets state.app_name, state.app_subtitle, state.app_description, state.app_keywords, and state.metadata["categories"]
    print_info("Analyzing project to generate App Store identity...")

    # Generate identity using AI
    identity = generate_identity(project_path, state)
    if not identity:
        return False

    # Store in state
    state.app_name = identity.get("app_name", state.project_name)
    state.app_subtitle = identity.get("subtitle", "")
    state.app_description = identity.get("description", "")
    state.app_keywords = identity.get("keywords", [])
    state.metadata["primary_category"] = identity.get("primary_category", "Utilities")
    state.metadata["secondary_category"] = identity.get("secondary_category", "")

    print_success(f"App name: {state.app_name}")
    print_info(f"Subtitle: {state.app_subtitle}")
    print_info(f"Category: {state.metadata['primary_category']}")
    print_info(f"Keywords: {len(state.app_keywords)} keywords generated")

    # Save to fastlane metadata files
    save_metadata_files(project_path, identity)

    return True
