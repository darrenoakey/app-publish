# utility functions for app-publish
# shared helpers for console output, command execution, file operations,
# llm integration, git operations, and xcode builds
import asyncio
import os
import sys
import subprocess
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Optional
from colorama import init, Fore, Style

SECRET_FILE_ARGUMENT = "__APP_PUBLISH_SECRET_FILE_0__"


def secret_file_argument(index: int) -> str:
    """Return the nonsecret argv marker for one anonymous secret pipe."""
    return f"__APP_PUBLISH_SECRET_FILE_{index}__"


# initialize colorama
init(autoreset=True)


# ##################################################################
# cprint
# print colored messages using colorama with optional bold style
def cprint(msg: str, color: str = "green", bold: bool = False) -> None:
    colors = {
        "green": Fore.GREEN,
        "red": Fore.RED,
        "yellow": Fore.YELLOW,
        "cyan": Fore.CYAN,
        "magenta": Fore.MAGENTA,
        "blue": Fore.BLUE,
        "white": Fore.WHITE,
    }
    style = Style.BRIGHT if bold else Style.NORMAL
    print(f"{style}{colors.get(color, Fore.WHITE)}{msg}{Style.RESET_ALL}")


# ##################################################################
# print header
# print a formatted section header with lines above and below
def print_header(title: str, color: str = "cyan") -> None:
    line = "=" * 60
    cprint(f"\n{line}", color, bold=True)
    cprint(f"  {title.upper()}", color, bold=True)
    cprint(f"{line}", color, bold=True)


# ##################################################################
# print step
# print a step header showing progress through pipeline
def print_step(step_num: int, total: int, name: str) -> None:
    cprint(f"\n[{step_num}/{total}] {name}", "cyan", bold=True)
    cprint("-" * 40, "cyan")


# ##################################################################
# print success
# print success message with ok prefix
def print_success(msg: str) -> None:
    cprint(f"  [OK] {msg}", "green")


# ##################################################################
# print error
# print error message with error prefix in bold red
def print_error(msg: str) -> None:
    cprint(f"  [ERROR] {msg}", "red", bold=True)


# ##################################################################
# print warning
# print warning message with warn prefix
def print_warning(msg: str) -> None:
    cprint(f"  [WARN] {msg}", "yellow")


# ##################################################################
# print info
# print info message in cyan
def print_info(msg: str) -> None:
    cprint(f"  {msg}", "cyan")


# ##################################################################
# print done
# print message for a step that was already completed
def print_done(msg: str) -> None:
    cprint(f"  [DONE] {msg}", "yellow")


# ##################################################################
# run
# run a command and return exit code and output
def run(
    cmd: list[str],
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    capture: bool = True,
    timeout: Optional[int] = None,
    secret_file: str | bytes | None = None,
    secret_files: Sequence[str | bytes] = (),
) -> tuple[int, str]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    read_descriptors: list[int] = []
    write_descriptors: list[int] = []
    actual_cmd = cmd
    try:
        values = ((secret_file,) if secret_file is not None else ()) + tuple(secret_files)
        for index, secret_value in enumerate(values):
            read_descriptor, write_descriptor = os.pipe()
            read_descriptors.append(read_descriptor)
            write_descriptors.append(write_descriptor)
            secret_bytes = secret_value.encode("utf-8") if isinstance(secret_value, str) else secret_value
            os.write(write_descriptor, secret_bytes)
            os.close(write_descriptor)
            write_descriptors.remove(write_descriptor)
            marker = secret_file_argument(index)
            secret_path = f"/dev/fd/{read_descriptor}"
            actual_cmd = [argument.replace(marker, secret_path) for argument in actual_cmd]
        p = subprocess.run(
            actual_cmd,
            cwd=cwd,
            capture_output=capture,
            text=True,
            env=full_env,
            timeout=timeout,
            pass_fds=tuple(read_descriptors),
        )
        output = p.stdout.strip() if p.stdout else ""
        if p.stderr and p.stderr.strip():
            output = f"{output}\n{p.stderr.strip()}" if output else p.stderr.strip()
        return p.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "Command timed out"
    except Exception as e:
        return 1, str(e)
    finally:
        for descriptor in write_descriptors:
            os.close(descriptor)
        for descriptor in read_descriptors:
            os.close(descriptor)


# ##################################################################
# run check
# run command and exit on failure with error details
def run_check(
    cmd: list[str],
    cwd: Optional[Path] = None,
    error_msg: str = "Command failed",
    env: Optional[dict[str, str]] = None,
) -> str:
    ret_code, output = run(cmd, cwd=cwd, env=env)
    if ret_code != 0:
        print_error(error_msg)
        cprint(f"    Command: {' '.join(cmd)}", "yellow")
        cprint(f"    Output: {output}", "yellow")
        sys.exit(1)
    return output


# ##################################################################
# run silent
# run command silently, return true if successful
def run_silent(cmd: list[str], cwd: Optional[Path] = None) -> bool:
    ret_code, _ = run(cmd, cwd=cwd)
    return ret_code == 0


# ##################################################################
# ensure dir
# ensure directory exists, create if needed
def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


# ##################################################################
# file exists
# check if file exists and is a file
def file_exists(path: Path) -> bool:
    return path.exists() and path.is_file()


# ##################################################################
# dir exists
# check if directory exists and is a directory
def dir_exists(path: Path) -> bool:
    return path.exists() and path.is_dir()


# ##################################################################
# find files
# find files matching any of the glob patterns
def find_files(directory: Path, patterns: list[str]) -> list[Path]:
    files = []
    for pattern in patterns:
        files.extend(directory.glob(pattern))
    return files


# ##################################################################
# read file
# read file contents, return none if not found or error
def read_file(path: Path) -> Optional[str]:
    try:
        return path.read_text()
    except Exception:
        return None


# ##################################################################
# write file
# write content to file, return true if successful
def write_file(path: Path, content: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        return True
    except Exception as e:
        print_error(f"Failed to write {path}: {e}")
        return False


# ##################################################################
# llm chat
# use daz_agent_sdk to get llm response with retries
def llm_chat(prompt: str, max_retries: int = 2) -> str:
    from daz_agent_sdk import agent, Tier

    for attempt in range(max_retries + 1):
        try:
            response = asyncio.run(agent.ask(prompt, tier=Tier.HIGH))
            return response.text.strip()
        except Exception as e:
            if attempt < max_retries:
                print_warning(f"LLM call failed, retrying... ({attempt + 1}/{max_retries})")
            else:
                print_error(f"LLM call failed after {max_retries + 1} attempts: {e}")
                return ""

    return ""


# ##################################################################
# llm json
# get json response from llm, extracting from markdown if needed
def llm_json(prompt: str) -> Optional[dict[str, any]]:
    response = llm_chat(prompt)
    if not response:
        return None

    try:
        # extract json from response (claude might include markdown)
        if "```json" in response:
            response = response.split("```json")[1].split("```")[0].strip()
        elif "```" in response:
            response = response.split("```")[1].split("```")[0].strip()

        return json.loads(response)
    except (json.JSONDecodeError, IndexError) as e:
        print_warning(f"Failed to parse LLM JSON response: {e}")
        return None


# ##################################################################
# claude agent task
# run daz_agent_sdk to perform autonomous code generation tasks
def claude_agent_task(
    task: str,
    project_path: Path,
    allowed_tools: Optional[list[str]] = None,
    timeout: int = 600,
) -> tuple[bool, str]:
    from daz_agent_sdk import agent, Tier

    print_info(f"Starting Claude agent for: {task[:60]}...")

    # default tools for code generation tasks
    if allowed_tools is None:
        allowed_tools = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

    # ##################################################################
    # run agent
    # async function to execute the agent task
    async def run_agent() -> tuple[bool, str]:
        try:
            response = await agent.ask(
                task,
                tier=Tier.HIGH,
                tools=allowed_tools,
                cwd=project_path,
                max_turns=50,
                timeout=float(timeout),
            )
            return True, response.text
        except Exception as e:
            import traceback

            return False, f"Agent error: {e}\n{traceback.format_exc()}"

    try:
        success, output = asyncio.run(run_agent())

        if success:
            print_success("Agent task completed")
        else:
            print_warning("Agent task may have issues")

        return success, output

    except Exception as e:
        print_error(f"Failed to run agent: {e}")
        return False, str(e)


# ##################################################################
# is git repo
# check if path is inside a git repository
def is_git_repo(path: Path) -> bool:
    return (path / ".git").exists() or run_silent(["git", "rev-parse", "--git-dir"], cwd=path)


# ##################################################################
# git init
# initialize git repository with main branch
def git_init(path: Path) -> bool:
    return run_silent(["git", "init", "-b", "main"], cwd=path)


# ##################################################################
# git add all
# stage all changes including untracked files
def git_add_all(path: Path) -> bool:
    return run_silent(["git", "add", "-A"], cwd=path)


# ##################################################################
# git commit
# commit staged changes with message
def git_commit(path: Path, message: str) -> bool:
    return run_silent(["git", "commit", "-m", message], cwd=path)


# ##################################################################
# git has changes
# check if there are uncommitted changes including untracked files
def git_has_changes(path: Path) -> bool:
    git_add_all(path)  # stage first to detect untracked files
    ret_code, output = run(["git", "status", "--porcelain"], cwd=path)
    return bool(output.strip())


# ##################################################################
# git remote exists
# check if remote exists
def git_remote_exists(path: Path, remote: str = "origin") -> bool:
    ret_code, output = run(["git", "remote", "get-url", remote], cwd=path)
    return ret_code == 0


# ##################################################################
# gh repo exists
# check if a github repo exists using gh cli
def gh_repo_exists(repo: str) -> bool:
    return run_silent(["gh", "repo", "view", repo, "--json", "name"])


# ##################################################################
# gh create repo
# create a github repository using gh cli
def gh_create_repo(repo: str, private: bool = True) -> bool:
    visibility = "--private" if private else "--public"
    return run_silent(["gh", "repo", "create", repo, visibility, "--confirm"])


# ##################################################################
# gh get user
# get current github username from gh cli
def gh_get_user() -> str:
    ret_code, output = run(["gh", "api", "user", "--jq", ".login"])
    return output.strip() if ret_code == 0 else ""


# ##################################################################
# xcode build
# build xcode project with specified scheme and configuration
def xcode_build(
    project_path: Path,
    scheme: str,
    configuration: str = "Release",
    destination: str = "generic/platform=iOS",
) -> tuple[bool, str]:
    cmd = [
        "xcodebuild",
        "-project",
        str(project_path),
        "-scheme",
        scheme,
        "-configuration",
        configuration,
        "-destination",
        destination,
        "build",
    ]
    ret_code, output = run(cmd, timeout=600)
    return ret_code == 0, output


# ##################################################################
# xcode archive
# archive xcode project for app store distribution
def xcode_archive(
    project_path: Path,
    scheme: str,
    archive_path: Path,
    configuration: str = "Release",
) -> tuple[bool, str]:
    cmd = [
        "xcodebuild",
        "-project",
        str(project_path),
        "-scheme",
        scheme,
        "-configuration",
        configuration,
        "-archivePath",
        str(archive_path),
        "archive",
    ]
    ret_code, output = run(cmd, timeout=600)
    return ret_code == 0, output


# ##################################################################
# parse schemes
# extract scheme names from `xcodebuild -list` output (in order)
def parse_schemes(xcodebuild_list_output: str) -> list[str]:
    schemes: list[str] = []
    in_schemes = False
    for line in xcodebuild_list_output.split("\n"):
        stripped = line.strip()
        if stripped == "Schemes:":
            in_schemes = True
            continue
        if in_schemes:
            if not stripped:
                break  # blank line terminates the Schemes block
            schemes.append(stripped)
    return schemes


# ##################################################################
# pick scheme
# choose the scheme to build/deploy from `xcodebuild -list`. xcodebuild
# returns schemes alphabetically and includes Xcode-autocreated schemes for
# local Swift package products (e.g. a vendored "kokoro-bench" executable)
# and test bundles — so "the first scheme" is routinely wrong. The reliable
# signal is the scheme whose name matches the project itself; fall back to a
# non-test scheme that starts with / contains the project name, and only as
# a last resort the first listed scheme.
def pick_scheme(schemes: list[str], project_name: str) -> str:
    if not schemes:
        return project_name

    def norm(s: str) -> str:
        return "".join(c for c in s.lower() if c.isalnum())

    def is_test(s: str) -> bool:
        n = s.lower()
        return n.endswith("tests") or n.endswith("test") or "uitest" in n

    target = norm(project_name)

    # 1. exact (separator-insensitive) match to the project name
    for s in schemes:
        if norm(s) == target:
            return s
    # 2. non-test scheme whose name starts with the project name
    for s in schemes:
        if not is_test(s) and target and norm(s).startswith(target):
            return s
    # 3. non-test scheme containing the project name
    for s in schemes:
        if not is_test(s) and target and target in norm(s):
            return s
    # 4. last resort: original behaviour (first listed scheme)
    return schemes[0]
