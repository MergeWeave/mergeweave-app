"""
Repository Manager for Git Operations.

Handles cloning, git operations, and cleanup for GitHub repositories.
Designed for conflict detection and resolution workflows.

Per Workstream 2 and 3 specifications.
"""

import os
import shutil
import asyncio
import logging
from pathlib import Path
from typing import Optional
from datetime import datetime

logger = logging.getLogger(__name__)


class RepositoryManager:
    """
    Manages repository cloning and cleanup for conflict detection.

    Features:
    - Async git operations using subprocess
    - Authentication with installation tokens
    - Automatic cleanup to prevent disk space issues
    - Temporary directory management

    Usage:
        manager = RepositoryManager()
        repo_path = await manager.clone(
            clone_url="https://github.com/owner/repo.git",
            token="ghs_...",
            branch="feature-branch"
        )
        try:
            # Perform git operations
            pass
        finally:
            await manager.cleanup(repo_path)
    """

    def __init__(self, base_dir: Optional[Path] = None):
        """
        Initialize repository manager.

        Args:
            base_dir: Base directory for cloned repositories.
                     Defaults to /tmp/mergeweave-repos
        """
        self.base_dir = base_dir or Path("/tmp/mergeweave-repos")
        self.base_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"Repository manager initialized",
            extra={"base_dir": str(self.base_dir)}
        )

    def check_disk_space(self, min_free_gb: float = 1.0) -> tuple[float, float]:
        """
        Check available disk space before cloning.

        Args:
            min_free_gb: Minimum required free space in GB (default: 1.0)

        Returns:
            tuple[float, float]: (free_gb, total_gb) available disk space

        Raises:
            RuntimeError: If insufficient disk space available
        """
        # Check disk space at base_dir location
        stat = shutil.disk_usage(self.base_dir)

        # Calculate free and total space in GB
        free_gb = stat.free / (1024 ** 3)
        total_gb = stat.total / (1024 ** 3)

        logger.debug(
            f"Disk space check: {free_gb:.2f}GB free / {total_gb:.2f}GB total "
            f"(minimum: {min_free_gb}GB)",
            extra={"free_gb": free_gb, "total_gb": total_gb, "required_gb": min_free_gb}
        )

        if free_gb < min_free_gb:
            logger.error(
                f"Insufficient disk space: {free_gb:.2f}GB free, need {min_free_gb}GB",
                extra={"free_gb": free_gb, "required_gb": min_free_gb}
            )
            raise RuntimeError(
                f"Insufficient disk space for repository cloning. "
                f"{free_gb:.2f}GB free, need {min_free_gb}GB."
            )

        return free_gb, total_gb

    async def clone(
        self,
        clone_url: str,
        token: str,
        branch: Optional[str] = None,
        shallow: bool = True
    ) -> Path:
        """
        Clone a repository with authentication.

        Args:
            clone_url: Repository clone URL (https://github.com/owner/repo.git)
            token: GitHub installation access token
            branch: Specific branch to clone (optional)
            shallow: Use shallow clone (--depth=1) for faster cloning

        Returns:
            Path: Path to cloned repository

        Raises:
            RuntimeError: If clone operation fails or insufficient disk space
        """
        # Check disk space before cloning (prevent running out of space)
        self.check_disk_space(min_free_gb=1.0)

        # Generate unique directory name
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        repo_name = clone_url.rstrip("/").split("/")[-1].replace(".git", "")
        repo_dir = self.base_dir / f"{repo_name}_{timestamp}"

        # Build authenticated clone URL
        # Format: https://x-access-token:TOKEN@github.com/owner/repo.git
        if clone_url.startswith("https://github.com/"):
            auth_url = clone_url.replace(
                "https://github.com/",
                f"https://x-access-token:{token}@github.com/"
            )
        else:
            raise ValueError(f"Unsupported clone URL format: {clone_url}")

        # Build git clone command
        cmd = ["git", "clone"]

        if shallow:
            cmd.extend(["--depth", "1"])

        if branch:
            cmd.extend(["--branch", branch])

        cmd.extend([auth_url, str(repo_dir)])

        logger.info(
            f"Cloning repository",
            extra={
                "repo": repo_name,
                "branch": branch,
                "shallow": shallow,
                "target_dir": str(repo_dir)
            }
        )

        try:
            # Execute git clone (token is hidden in logs)
            await self._run_git_command(
                cmd,
                cwd=None,
                hide_token=token
            )

            logger.info(
                f"Repository cloned successfully",
                extra={"repo_path": str(repo_dir)}
            )

            return repo_dir

        except Exception as e:
            logger.error(
                f"Failed to clone repository",
                extra={
                    "repo": repo_name,
                    "branch": branch,
                    "error": str(e)
                },
                exc_info=True
            )

            # Cleanup partial clone
            if repo_dir.exists():
                await self.cleanup(repo_dir)

            raise RuntimeError(f"Failed to clone repository: {str(e)}")

    async def cleanup(self, repo_path: Path):
        """
        Remove cloned repository directory.

        This is critical to prevent disk space exhaustion.
        Always call this in a finally block.

        Args:
            repo_path: Path to repository directory to remove
        """
        if not repo_path or not repo_path.exists():
            return

        try:
            logger.info(
                f"Cleaning up repository",
                extra={"repo_path": str(repo_path)}
            )

            # Run in thread pool to avoid blocking
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                shutil.rmtree,
                repo_path,
                True  # ignore_errors
            )

            logger.info(
                f"Repository cleaned up successfully",
                extra={"repo_path": str(repo_path)}
            )

        except Exception as e:
            logger.error(
                f"Failed to cleanup repository",
                extra={
                    "repo_path": str(repo_path),
                    "error": str(e)
                },
                exc_info=True
            )

    async def fetch_branch(
        self,
        repo_path: Path,
        branch: str,
        remote: str = "origin"
    ):
        """
        Fetch a specific branch from remote.

        Args:
            repo_path: Path to repository
            branch: Branch name to fetch
            remote: Remote name (default: origin)

        Raises:
            RuntimeError: If fetch fails
        """
        cmd = ["git", "fetch", remote, branch]

        logger.debug(
            f"Fetching branch",
            extra={"branch": branch, "remote": remote}
        )

        await self._run_git_command(cmd, cwd=repo_path)

    async def get_branch_sha(
        self,
        repo_path: Path,
        ref: str
    ) -> str:
        """
        Get commit SHA for a git ref.

        Args:
            repo_path: Path to repository
            ref: Git ref (branch name, tag, etc.)

        Returns:
            str: Commit SHA (40 characters)

        Raises:
            RuntimeError: If ref doesn't exist
        """
        cmd = ["git", "rev-parse", ref]

        logger.debug(
            f"Getting SHA for ref",
            extra={"ref": ref}
        )

        result = await self._run_git_command(cmd, cwd=repo_path)
        sha = result.strip()

        if len(sha) != 40:
            raise RuntimeError(f"Invalid SHA returned for ref {ref}: {sha}")

        return sha

    async def attempt_merge(
        self,
        repo_path: Path,
        target_branch: str,
        source_branch: str
    ) -> bool:
        """
        Attempt to merge source into target to detect conflicts.

        Does NOT commit the merge - only tests if it would succeed.

        Args:
            repo_path: Path to repository
            target_branch: Target branch (base)
            source_branch: Source branch to merge

        Returns:
            bool: True if merge succeeds, False if conflicts detected

        Raises:
            RuntimeError: If git operation fails (not conflict-related)
        """
        # Checkout target branch
        await self._run_git_command(
            ["git", "checkout", target_branch],
            cwd=repo_path
        )

        # Attempt merge with --no-commit (test only)
        cmd = ["git", "merge", "--no-commit", "--no-ff", source_branch]

        logger.info(
            f"Attempting test merge",
            extra={
                "target": target_branch,
                "source": source_branch
            }
        )

        try:
            await self._run_git_command(cmd, cwd=repo_path)

            logger.info("Merge would succeed - no conflicts")
            return True

        except RuntimeError as e:
            # Check if this is a merge conflict (expected) or other error
            if "CONFLICT" in str(e) or "Automatic merge failed" in str(e):
                logger.info("Merge conflicts detected")
                return False
            else:
                # Unexpected git error
                logger.error(
                    f"Unexpected git merge error",
                    extra={"error": str(e)},
                    exc_info=True
                )
                raise

    async def get_conflicted_files(
        self,
        repo_path: Path
    ) -> list[str]:
        """
        Get list of files with merge conflicts.

        Must be called after a failed merge attempt.

        Args:
            repo_path: Path to repository

        Returns:
            List of file paths with conflicts
        """
        cmd = ["git", "diff", "--name-only", "--diff-filter=U"]

        result = await self._run_git_command(cmd, cwd=repo_path)

        files = [f.strip() for f in result.strip().split("\n") if f.strip()]

        logger.info(
            f"Found {len(files)} conflicted files",
            extra={"files": files}
        )

        return files

    async def read_file_with_markers(
        self,
        repo_path: Path,
        file_path: str
    ) -> str:
        """
        Read file content including conflict markers.

        Args:
            repo_path: Path to repository
            file_path: Relative path to file within repository

        Returns:
            str: File content with conflict markers preserved

        Raises:
            RuntimeError: If file cannot be read
        """
        full_path = repo_path / file_path

        if not full_path.exists():
            raise RuntimeError(f"File not found: {file_path}")

        try:
            # Try UTF-8 first
            content = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            # Fallback to latin-1 for binary-ish files
            try:
                content = full_path.read_text(encoding="latin-1")
                logger.warning(
                    f"File read with latin-1 encoding (not UTF-8)",
                    extra={"file": file_path}
                )
            except Exception as e:
                raise RuntimeError(
                    f"Cannot read file {file_path}: {str(e)}"
                )

        return content

    async def _run_git_command(
        self,
        cmd: list[str],
        cwd: Optional[Path] = None,
        hide_token: Optional[str] = None
    ) -> str:
        """
        Run git command asynchronously.

        Args:
            cmd: Command and arguments
            cwd: Working directory
            hide_token: Token to hide in logs (for security)

        Returns:
            str: Command stdout

        Raises:
            RuntimeError: If command fails
        """
        # Log command (hide sensitive tokens)
        log_cmd = cmd.copy()
        if hide_token:
            log_cmd = [
                arg.replace(hide_token, "***TOKEN***")
                for arg in log_cmd
            ]

        logger.debug(
            f"Running git command",
            extra={"cmd": " ".join(log_cmd)}
        )

        try:
            # Build clean environment for Git
            # Prevent Git from looking for user config files that may not exist or have wrong permissions
            git_env = os.environ.copy()
            git_env['GIT_CONFIG_NOGLOBAL'] = '1'  # Skip ~/.gitconfig and ~/.config/git/
            git_env['GIT_TERMINAL_PROMPT'] = '0'  # Disable interactive prompts
            git_env['GIT_ASKPASS'] = 'echo'       # Prevent password prompts

            process = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=git_env
            )

            stdout, stderr = await process.communicate()

            if process.returncode != 0:
                # Hide token in error messages
                # Include both stdout and stderr (git merge writes conflicts to stdout)
                stdout_msg = stdout.decode()
                stderr_msg = stderr.decode()
                error_msg = f"{stdout_msg}\n{stderr_msg}".strip()
                if hide_token:
                    error_msg = error_msg.replace(hide_token, "***TOKEN***")

                raise RuntimeError(
                    f"Git command failed (exit {process.returncode}): {error_msg}"
                )

            return stdout.decode()

        except FileNotFoundError:
            raise RuntimeError("git command not found. Is git installed?")
        except Exception as e:
            logger.error(
                f"Git command failed",
                extra={"error": str(e)},
                exc_info=True
            )
            raise RuntimeError(f"Git command failed: {str(e)}")
