# Repository setup steps that are not version-controlled

GitHub keeps some repository configuration outside the git tree, so it cannot be committed
alongside the code. This file records what has to be set once, by hand, in the repository
settings — so it is written down somewhere rather than living only in someone's memory.

## Labels

**GitHub labels are not git-versioned.** An issue template's `labels:` front matter applies a
label when the issue is created, but only if that label already exists — GitHub silently skips
one that does not. So the labels below have to be created first, or the templates will file
issues with no label at all.

Create under **Issues → Labels**:

| Label | Suggested colour | Purpose |
| --- | --- | --- |
| `vendor-validation` | `#5319E7` (purple) | A real EDA tool rejected or warned about generated UPF/CPF. Kept **distinct from `bug`** so vendor conformance findings can be filtered and worked through as their own stream, separately from ordinary UI issues. Applied automatically by [`ISSUE_TEMPLATE/upf_cpf_validation.md`](ISSUE_TEMPLATE/upf_cpf_validation.md). |
| `bug` | GitHub default | Applied automatically by [`ISSUE_TEMPLATE/bug_report.md`](ISSUE_TEMPLATE/bug_report.md). Exists by default in a new repository; confirm it is still there. |
| `enhancement` | GitHub default | Applied automatically by [`ISSUE_TEMPLATE/feature_request.md`](ISSUE_TEMPLATE/feature_request.md). Also a default. |

Or with the `gh` CLI:

```bash
gh label create vendor-validation \
  --color 5319E7 \
  --description "EDA tool rejected or warned about generated UPF/CPF"
```

Useful filter once findings start arriving:

```
is:issue is:open label:vendor-validation
```

## Discussions

[`ISSUE_TEMPLATE/config.yml`](ISSUE_TEMPLATE/config.yml) points its "Question or discussion"
contact link at GitHub Discussions. That feature is **off by default** — enable it under
**Settings → General → Features → Discussions**, or remove the `contact_links` entry from
`config.yml` so the link does not 404.

## Blank issues

`config.yml` sets `blank_issues_enabled: false`, so every report arrives through a template.
That is deliberate for a tool whose generated output people are asked to trust — "which
version, which format, what exactly did the tool say" is usually the difference between a
reproducible report and a conversation. Delete that line to allow blank issues again.

## Releases

Tags are pushed from git, but the **release** itself (title, notes, any attached build) is
created in the GitHub UI or via `gh release create`. Use that version's own section of
`CHANGELOG.md` as the notes, not the whole file. `v1.1.0` is a full release:

```bash
gh release create v1.1.0 \
  --title "v1.1.0" \
  --notes-file <(sed -n '/^## \[1.1.0\]/,/^## \[1.0.0-rc1\]/p' CHANGELOG.md | sed '$d')
```

A release candidate (like the earlier `v1.0.0-rc1`) adds `--prerelease`, so it is not
presented as the current stable version.
