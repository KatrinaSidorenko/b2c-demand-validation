# Git workflow

## Branches
- `main` is always stable. Never commit to it directly.
- Create every branch from an up-to-date `main`:
  `git checkout main && git pull && git checkout -b <type>/<short-description>`
- Branch name: `<type>/<short-description>`, lowercase, words separated by `-`.
  - `feat/wiki-client`
  - `fix/top-endpoint-empty-items`
  - `docs/git-workflow-rules`
- If the work belongs to a step in `.logs/`, add the step number: `feat/02-metrics-controller`.

## Change types
| type       | use for                                        |
|------------|------------------------------------------------|
| `feat`     | new functionality                              |
| `fix`      | bug fix                                        |
| `refactor` | code change with no behaviour change           |
| `docs`     | documentation, README, SKILL.md, rules         |
| `test`     | adding or changing tests                       |
| `chore`    | tooling, dependencies, config, `.gitignore`    |

## Commit messages
- Format: `<type>: <short summary>`
- Write the whole message in lowercase and the imperative mood, with no trailing period, in 72 characters or fewer.
  - `feat: add top-per-country endpoint to wiki client`
  - `fix: return empty result for top endpoints with no items`
  - `chore: ignore __pycache__`
- Add a body only when the "why" is not obvious. Leave a blank line after the summary.
- Keep each commit to one logical change.

## Pull requests
- Open one PR per branch, always into `main`.
- PR title: the same `<type>: <short summary>` format, in lowercase.
- The PR body has three parts:
  - **summary**: what changed and why
  - **out of scope**: what was deliberately left out
  - **verification**: how the change was checked
- Merge only after review, then delete the branch.
