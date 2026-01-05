# utility functions for app-publish
# shared helpers for console output, command execution, file operations,
# llm integration, git operations, and xcode builds
import os
import sys
import subprocess
import json
from pathlib import Path
from typing import Optional
from colorama import init, Fore, Style

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
# print skip
# print skip message for already completed steps
def print_skip(msg: str) -> None:
    cprint(f"  [SKIP] {msg}", "yellow")


# ##################################################################
# run
# run a command and return exit code and output
def run(
    cmd: list[str],
    cwd: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    capture: bool = True,
    timeout: Optional[int] = None,
) -> tuple[int, str]:
    full_env = os.environ.copy()
    if env:
        full_env.update(env)

    try:
        p = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=capture,
            text=True,
            env=full_env,
            timeout=timeout,
        )
        output = p.stdout.strip() if p.stdout else ""
        if p.stderr and p.stderr.strip():
            output = f"{output}\n{p.stderr.strip()}" if output else p.stderr.strip()
        return p.returncode, output
    except subprocess.TimeoutExpired:
        return 1, "Command timed out"
    except Exception as e:
        return 1, str(e)


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
# use claude cli in headless mode to get llm response with retries
def llm_chat(prompt: str, max_retries: int = 2) -> str:
    cmd = ["claude", "--print"]

    for attempt in range(max_retries + 1):
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True)
        if p.returncode == 0:
            return p.stdout.strip()

        if attempt < max_retries:
            print_warning(f"LLM call failed, retrying... ({attempt + 1}/{max_retries})")
        else:
            print_error(f"LLM call failed after {max_retries + 1} attempts: {p.stderr}")
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
# run claude agent sdk to perform autonomous code generation tasks
# uses the official claude agent sdk with a persistent session
def claude_agent_task(
    task: str,
    project_path: Path,
    allowed_tools: Optional[list[str]] = None,
    timeout: int = 600
) -> tuple[bool, str]:
    import anyio

    print_info(f"Starting Claude agent for: {task[:60]}...")

    # default tools for code generation tasks
    if allowed_tools is None:
        allowed_tools = ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]

    # ##################################################################
    # run agent
    # async function to execute the agent task
    async def run_agent() -> tuple[bool, str]:
        try:
            from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

            options = ClaudeAgentOptions(
                cwd=str(project_path),
                allowed_tools=allowed_tools,
                permission_mode="acceptEdits",  # auto-accept file edits
                max_turns=50,  # allow multiple tool uses
            )

            output_parts = []
            success = True

            # use ClaudeSDKClient for persistent session with consistent conversation
            async with ClaudeSDKClient(options=options) as client:
                await client.query(task)

                async for message in client.receive_response():
                    # collect text responses
                    if hasattr(message, 'type'):
                        if message.type == 'text':
                            text = message.text if hasattr(message, 'text') else str(message)
                            output_parts.append(text)
                            print_info(f"  {text[:100]}..." if len(text) > 100 else f"  {text}")
                        elif message.type == 'result':
                            if hasattr(message, 'error') and message.error:
                                success = False
                                output_parts.append(f"Error: {message.error}")
                            elif hasattr(message, 'result'):
                                output_parts.append(str(message.result))
                        elif message.type == 'tool_use':
                            tool_name = getattr(message, 'name', 'unknown')
                            print_info(f"  [Tool: {tool_name}]")
                    elif isinstance(message, dict):
                        msg_type = message.get('type', '')
                        if msg_type == 'text':
                            text = message.get('text', '')
                            output_parts.append(text)
                        elif msg_type == 'result':
                            output_parts.append(str(message.get('result', '')))
                        elif msg_type == 'tool_use':
                            print_info(f"  [Tool: {message.get('name', 'unknown')}]")

            output = "\n".join(filter(None, output_parts))
            return success, output

        except ImportError as e:
            return False, f"Claude Agent SDK not installed. Run: pip install claude-agent-sdk\nError: {e}"
        except Exception as e:
            import traceback
            return False, f"Agent error: {e}\n{traceback.format_exc()}"

    try:
        success, output = anyio.run(run_agent)

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
    return (path / ".git").exists() or run_silent(
        ["git", "rev-parse", "--git-dir"], cwd=path
    )


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
