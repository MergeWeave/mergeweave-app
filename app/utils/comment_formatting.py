"""
GitHub Comment Formatting Utilities.

Formats conflict detection results and resolution information
for posting as GitHub commit comments.

Per Workstream 2 and 3 specifications.
"""

import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def format_conflict_comment(
    repository: str,
    source_branch: str,
    target_branch: str,
    has_conflicts: bool,
    conflicted_files: Optional[List[str]] = None,
    resolution_id: Optional[str] = None,
    confidence_score: Optional[str] = None,
    app_base_url: Optional[str] = None
) -> str:
    """
    Format merge conflict information as GitHub comment (Markdown).

    Creates user-friendly comment with conflict details and optional
    "Apply Fix" button for automated resolutions.

    Args:
        repository: Repository full name (owner/repo)
        source_branch: Source branch being merged (head)
        target_branch: Target branch (base)
        has_conflicts: Whether conflicts were detected
        conflicted_files: List of files with conflicts
        resolution_id: UUID of CRE resolution (if available)
        confidence_score: CRE confidence score (if available)
        app_base_url: Base URL for callback links (if applying fix)

    Returns:
        str: Formatted Markdown comment

    Example:
        >>> comment = format_conflict_comment(
        ...     repository="owner/repo",
        ...     source_branch="feature/new-thing",
        ...     target_branch="main",
        ...     has_conflicts=True,
        ...     conflicted_files=["src/main.py", "README.md"],
        ...     resolution_id="abc-123",
        ...     confidence_score="0.95"
        ... )
        >>> print(comment)
    """
    if not has_conflicts:
        # No conflicts - simple success message
        return _format_no_conflicts_comment(
            repository=repository,
            source_branch=source_branch,
            target_branch=target_branch
        )

    # Conflicts detected
    if resolution_id and confidence_score and app_base_url:
        # With automated resolution available
        return _format_conflicts_with_resolution(
            repository=repository,
            source_branch=source_branch,
            target_branch=target_branch,
            conflicted_files=conflicted_files or [],
            resolution_id=resolution_id,
            confidence_score=confidence_score,
            app_base_url=app_base_url
        )
    else:
        # Conflicts detected but no resolution available
        return _format_conflicts_no_resolution(
            repository=repository,
            source_branch=source_branch,
            target_branch=target_branch,
            conflicted_files=conflicted_files or []
        )


def _format_no_conflicts_comment(
    repository: str,
    source_branch: str,
    target_branch: str
) -> str:
    """Format comment for successful merge (no conflicts)."""
    return f"""## ✅ No Merge Conflicts Detected

**MergeWeave Analysis Complete**

- **Repository**: `{repository}`
- **Merging**: `{source_branch}` → `{target_branch}`
- **Status**: Clean merge - no conflicts detected

This branch can be safely merged into `{target_branch}`.

---
*Powered by [MergeWeave](https://mergeweave.cloud) - Intelligent Conflict Resolution*
"""


def _format_conflicts_no_resolution(
    repository: str,
    source_branch: str,
    target_branch: str,
    conflicted_files: List[str]
) -> str:
    """Format comment for conflicts without automated resolution."""
    file_count = len(conflicted_files)

    # Format file list (limit to 20 files to avoid massive comments)
    if file_count <= 20:
        file_list = "\n".join([f"- `{file}`" for file in conflicted_files])
    else:
        shown_files = conflicted_files[:20]
        file_list = "\n".join([f"- `{file}`" for file in shown_files])
        file_list += f"\n\n*... and {file_count - 20} more file(s)*"

    return f"""## ⚠️ Merge Conflicts Detected

**MergeWeave Analysis Complete**

- **Repository**: `{repository}`
- **Merging**: `{source_branch}` → `{target_branch}`
- **Conflicts Found**: {file_count} file(s)

### Conflicted Files

{file_list}

### Next Steps

1. Review the conflicted files listed above
2. Manually resolve conflicts in your local repository
3. Commit and push the resolved changes

**Manual Resolution Command:**
```bash
git checkout {target_branch}
git merge {source_branch}
# Resolve conflicts in your editor
git add .
git commit -m "Resolve merge conflicts"
git push
```

---
*Powered by [MergeWeave](https://mergeweave.cloud) - Intelligent Conflict Resolution*
"""


def _format_conflicts_with_resolution(
    repository: str,
    source_branch: str,
    target_branch: str,
    conflicted_files: List[str],
    resolution_id: str,
    confidence_score: str,
    app_base_url: str
) -> str:
    """Format comment for conflicts with automated resolution available."""
    file_count = len(conflicted_files)

    # Format file list (limit to 20 files)
    if file_count <= 20:
        file_list = "\n".join([f"- `{file}`" for file in conflicted_files])
    else:
        shown_files = conflicted_files[:20]
        file_list = "\n".join([f"- `{file}`" for file in shown_files])
        file_list += f"\n\n*... and {file_count - 20} more file(s)*"

    # Parse confidence score for display
    confidence_display = _format_confidence_score(confidence_score)

    # Build apply fix URL
    apply_url = f"{app_base_url.rstrip('/')}/resolutions/{resolution_id}/apply"

    return f"""## 🔧 Merge Conflicts Detected - Automated Fix Available

**MergeWeave Analysis Complete**

- **Repository**: `{repository}`
- **Merging**: `{source_branch}` → `{target_branch}`
- **Conflicts Found**: {file_count} file(s)
- **AI Confidence**: {confidence_display}

### Conflicted Files

{file_list}

### ✨ Automated Resolution Available

Our AI-powered conflict resolution engine has analyzed these conflicts and generated a potential fix.

**Resolution ID**: `{resolution_id}`
**Confidence Score**: {confidence_display}

#### Apply This Fix

> ⚠️ **Review Before Applying**: Always review automated resolutions before applying to production code.

**Option 1: Apply via Web Interface (Recommended)**
1. Review the resolution: [View Resolution Details]({app_base_url.rstrip('/')}/resolutions/{resolution_id})
2. Apply the fix: [Apply Fix]({apply_url})

**Option 2: Manual Resolution**
```bash
git checkout {target_branch}
git merge {source_branch}
# Resolve conflicts in your editor
git add .
git commit -m "Resolve merge conflicts"
git push
```

---
*Powered by [MergeWeave](https://mergeweave.cloud) - Intelligent Conflict Resolution*
*Resolution generated by [Conflict Resolution Engine](https://github.com/mergeweave/cre)*
"""


def _format_confidence_score(score: str) -> str:
    """
    Format confidence score for human-readable display.

    Args:
        score: Confidence score (numeric string or category)

    Returns:
        str: Formatted confidence with emoji
    """
    try:
        # Try to parse as float
        numeric_score = float(score)

        if numeric_score >= 0.9:
            return f"🟢 {numeric_score:.1%} (High)"
        elif numeric_score >= 0.7:
            return f"🟡 {numeric_score:.1%} (Medium)"
        else:
            return f"🔴 {numeric_score:.1%} (Low)"

    except ValueError:
        # Not a numeric score, use as-is
        score_lower = score.lower()

        if "high" in score_lower:
            return f"🟢 {score}"
        elif "medium" in score_lower or "moderate" in score_lower:
            return f"🟡 {score}"
        elif "low" in score_lower:
            return f"🔴 {score}"
        else:
            return score


def format_error_comment(
    repository: str,
    source_branch: str,
    target_branch: str,
    error_message: str
) -> str:
    """
    Format error message as GitHub comment.

    Used when conflict detection or resolution fails unexpectedly.

    Args:
        repository: Repository full name
        source_branch: Source branch
        target_branch: Target branch
        error_message: Error description

    Returns:
        str: Formatted Markdown comment
    """
    return f"""## ❌ MergeWeave Processing Error

**An error occurred while analyzing this merge**

- **Repository**: `{repository}`
- **Merging**: `{source_branch}` → `{target_branch}`
- **Error**: {error_message}

### What happened?

Our conflict detection system encountered an unexpected error while analyzing this merge.
This could be due to:

- Repository access issues
- Very large files or complex merge scenarios
- Temporary service disruption

### Next Steps

1. Try merging manually to see if conflicts exist
2. Check the [MergeWeave Status Page](https://status.mergeweave.cloud) for known issues
3. If the problem persists, please contact support

```bash
git checkout {target_branch}
git merge {source_branch}
```

---
*Powered by [MergeWeave](https://mergeweave.cloud) - Intelligent Conflict Resolution*
"""
