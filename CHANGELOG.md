# Changelog

## [0.2.0](https://github.com/saucisson/petrichor/compare/v0.1.0...v0.2.0) (2026-11-06)


### Features

* issue 1 - amorçage environnement et spec ([662e928](https://github.com/saucisson/petrichor/commit/662e92818dae03f0c44a4128c7bbf2e10064e003))
* sync from project-template (ADR-0020 human-approved waiver) ([f5ff898](https://github.com/saucisson/petrichor/commit/f5ff898f7c177f7b433ed935f037b4f6000d37eb))


### Bug Fixes

* address reviewer TO-FIX and SUGGESTION findings ([6450a61](https://github.com/saucisson/petrichor/commit/6450a61a168b61d9f0cba4b086472f0eac3da9bb))
* **ci:** add GITHUB_TOKEN for gitleaks and package-lock.json ([8df55bb](https://github.com/saucisson/petrichor/commit/8df55bbac8f5566364ef62dee8fc34562553baed))
* **ci:** constrain pre-commit and pyyaml versions to avoid SIGSEGV ([d40ea7b](https://github.com/saucisson/petrichor/commit/d40ea7babc59e170c36037bbaf16f012d4cce0a2))
* **ci:** downgrade mutmut to 2.0.0 to avoid problematic dependencies ([c5b6f9d](https://github.com/saucisson/petrichor/commit/c5b6f9d3a766f0457e937cccd60ee2641c78134a))
* **ci:** exclude mutmut/pony from pip-audit, log empty reviewer answers ([c778021](https://github.com/saucisson/petrichor/commit/c778021a6cf011b2467f42ae31dea751b12bb73e))
* **ci:** exclude the project itself from the pip-audit export ([5895256](https://github.com/saucisson/petrichor/commit/5895256a37d90ab202e1c8b1edf9cd7a565b6f1f))
* **ci:** expose GITHUB_TOKEN as env var for gitleaks-action ([41e7ac1](https://github.com/saucisson/petrichor/commit/41e7ac1dbe73e74d559b2c4640501f8e7811b44f))
* **ci:** install uv in the security job before pip-audit ([36d6085](https://github.com/saucisson/petrichor/commit/36d6085c943da1363ac49e6d80f8a40024b1370a))
* **ci:** pass GITHUB_TOKEN to gitleaks-action v2 ([f71f1a3](https://github.com/saucisson/petrichor/commit/f71f1a35ae1283304a4eb4b4dfd330f4ee5a5248))
* **ci:** pin pyyaml to &lt;6.0.3 to avoid SIGSEGV crash ([6556234](https://github.com/saucisson/petrichor/commit/6556234539b5d30de0ea4346306c62c07da32f07))
* **ci:** pin pyyaml to 6.0.1 to avoid SIGSEGV crash ([d6e9851](https://github.com/saucisson/petrichor/commit/d6e9851a675f85733c2fd74a163f35aa2c12bcd9))
* **ci:** restore npm cache with package-lock.json dependency path ([7426ef4](https://github.com/saucisson/petrichor/commit/7426ef486da071749cae96812e1172f08ef2ab52))
* corrige les erreurs Ruff et le crash pyyaml dans pre-commit ([e7012aa](https://github.com/saucisson/petrichor/commit/e7012aa153361a15f43fa12ff173d5800273f3c5))
* make the quality gates green (ruff, coverage, eslint, mypy) ([2eebce8](https://github.com/saucisson/petrichor/commit/2eebce8fa7f6150e21afc70a438ef1ec8fed448a))
* **reviewer:** raise completion token budget for reasoning models ([dd35993](https://github.com/saucisson/petrichor/commit/dd35993e9907cb9bbf2662cc4e219a30219602b4))
* **ruff:** update T202 to YTT202 rule code for Ruff 0.17.3 ([3f83d29](https://github.com/saucisson/petrichor/commit/3f83d29290bcbd9785fae569dd7936eed2080879))
* upgrade uv.lock so quality gates resolve on Python 3.15 ([b37584f](https://github.com/saucisson/petrichor/commit/b37584fd5ced7a6928434bdd8ef0aeabc2c170d6))


### Documentation

* the developer agent opens the PR itself; only the merge is a platform action ([4bbf9ca](https://github.com/saucisson/petrichor/commit/4bbf9ca7d1795e790bf404c77184753e97676dc4))
