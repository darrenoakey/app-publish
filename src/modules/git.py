# ##################################################################
# git/github module
# initializes git repo and creates private github repository
# handles git initialization, .gitignore creation, github repo creation, and initial commit/push
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from state import ProjectState
from config import GITHUB_USER
from utils import (
    print_info,
    print_success,
    print_warning,
    print_error,
    run as exec_cmd,
    run_silent,
    is_git_repo,
    git_init,
    git_add_all,
    git_commit,
    git_remote_exists,
    gh_repo_exists,
    gh_create_repo,
    write_file,
    file_exists,
)


def create_gitignore(project_path: Path, project_type: str) -> None:
    # ##################################################################
    # create gitignore
    # create or update .gitignore
    gitignore_path = project_path / ".gitignore"

    # Base ignores
    ignores = [
        "# OS",
        ".DS_Store",
        "Thumbs.db",
        "",
        "# IDE",
        ".idea/",
        ".vscode/",
        "*.swp",
        "*.swo",
        "",
        "# Build artifacts",
        "*.ipa",
        "*.app",
        "*.dSYM.zip",
        "*.dSYM",
        "",
        "# Xcode",
        "build/",
        "DerivedData/",
        "*.xcuserstate",
        "xcuserdata/",
        "*.xcworkspace/xcuserdata/",
        "",
        "# Fastlane",
        "fastlane/report.xml",
        "fastlane/Preview.html",
        "fastlane/screenshots/**/",
        "fastlane/test_output/",
        "",
    ]

    # Web-specific ignores
    if project_type == "web":
        ignores.extend([
            "# Node",
            "node_modules/",
            "npm-debug.log*",
            "yarn-error.log*",
            ".npm",
            "",
            "# Capacitor",
            "ios/App/Pods/",
            "ios/.build/",
            "",
        ])

    # Swift-specific ignores
    if project_type == "swift":
        ignores.extend([
            "# Swift",
            ".build/",
            "Packages/",
            "*.playground/",
            "Pods/",
            "",
        ])

    content = "\n".join(ignores)

    # If .gitignore exists, append our content
    if file_exists(gitignore_path):
        existing = gitignore_path.read_text()
        # Only add if our marker isn't there
        if "# app-publish managed" not in existing:
            content = existing + "\n\n# app-publish managed\n" + content
        else:
            return  # Already managed
    else:
        content = "# app-publish managed\n" + content

    write_file(gitignore_path, content)
    print_info("Updated .gitignore")


def run(project_path: Path, state: ProjectState) -> bool:
    # ##################################################################
    # run git/github step
    # sets state.metadata["github_repo"]
    repo_name = project_path.name
    full_repo = f"{GITHUB_USER}/{repo_name}"

    # Initialize git if needed
    if not is_git_repo(project_path):
        print_info("Initializing git repository...")
        if not git_init(project_path):
            print_error("Failed to initialize git repository")
            return False
        print_success("Git repository initialized")
    else:
        print_info("Git repository already exists")

    # Create/update .gitignore
    create_gitignore(project_path, state.project_type)

    # Check if GitHub repo exists
    if gh_repo_exists(full_repo):
        print_info(f"GitHub repository exists: {full_repo}")
        state.metadata["github_repo"] = full_repo
    else:
        # Create private GitHub repo
        print_info(f"Creating private GitHub repository: {full_repo}")
        if not gh_create_repo(full_repo, private=True):
            print_error("Failed to create GitHub repository")
            return False
        print_success("GitHub repository created")
        state.metadata["github_repo"] = full_repo

    # Set up remote if not present
    if not git_remote_exists(project_path):
        print_info("Adding git remote...")
        ret_code, _ = exec_cmd(
            ["git", "remote", "add", "origin", f"https://github.com/{full_repo}.git"],
            cwd=project_path,
        )
        if ret_code != 0:
            print_warning("Failed to add remote, may already exist")

    # Stage all changes
    git_add_all(project_path)

    # Check if there are changes to commit
    ret_code, status = exec_cmd(["git", "status", "--porcelain"], cwd=project_path)
    if status.strip():
        print_info("Committing changes...")
        if not git_commit(project_path, "Initial app-publish setup"):
            print_warning("Commit failed or nothing to commit")
    else:
        print_info("No changes to commit")

    # Push to GitHub
    print_info("Pushing to GitHub...")
    ret_code, output = exec_cmd(
        ["git", "push", "-u", "origin", "main"],
        cwd=project_path,
    )
    if ret_code != 0:
        # Try with force if it's a new repo with existing commits
        ret_code, output = exec_cmd(
            ["git", "push", "-u", "origin", "main", "--force-with-lease"],
            cwd=project_path,
        )
        if ret_code != 0:
            print_warning(f"Push warning: {output}")
            # Don't fail - repo might not have changes

    print_success(f"Repository: https://github.com/{full_repo}")

    return True
