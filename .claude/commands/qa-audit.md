---
name: qa-audit
description: Run comprehensive quality audit on PhotoLoop - checks documentation, config consistency, test coverage, and code quality.
---

# PhotoLoop Quality Audit

When this skill is invoked, perform a comprehensive quality review:

## 1. Documentation Check

- Read `CLAUDE.md` and verify:
  - All modules in `src/` are listed in the Architecture section
  - All CLI commands match what's in `src/cli.py`
  - Configuration options match the config dataclass in `src/config.py`
  - Any new features added recently are documented

## 2. Config Consistency

- Compare `/etc/photoloop/config.yaml` with `src/config.py`:
  - All config keys in the YAML should be valid
  - Default values should be sensible
  - No deprecated options still in use

## 3. Test Coverage

- For each Python file in `src/`:
  - Check if a corresponding `tests/test_*.py` exists
  - List any modules missing tests
- Run `pytest tests/ -v --tb=short` and report results

## 4. Code Quality

- Search for `TODO` and `FIXME` comments that might indicate incomplete work
- Check for debug `print()` statements that should be removed
- Look for any hardcoded paths or credentials

## 5. Git Status

- Check for uncommitted changes
- Check if local is ahead/behind remote

## Output Format

Provide a summary report:

```
PhotoLoop QA Audit
==================

Documentation:  [PASS/FAIL] - details
Config:         [PASS/FAIL] - details
Tests:          [PASS/FAIL] - X/Y modules covered, tests passing/failing
Code Quality:   [PASS/WARN/FAIL] - details
Git Status:     [CLEAN/DIRTY] - details

Issues Found:
- Issue 1
- Issue 2

Recommendations:
- Recommendation 1
- Recommendation 2
```
