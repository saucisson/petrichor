# Changelog

## [0.2.0](https://github.com/saucisson/petrichor/compare/v0.1.0...v0.2.0) (2026-11-09)


### Features

* move driver tests to scripts/tests/ per ADR-0021 layout (testpaths of template 2aa4d27) ([458f3bd](https://github.com/saucisson/petrichor/commit/458f3bd25c5986aee86b731441fbd10257f5b2dc))
* **P02:** canonical petri+json v1.0 schema and canonical content hash ([d5460bb](https://github.com/saucisson/petrichor/commit/d5460bb24598ef152b36520a9b28d3490a3d8ff3))
* template sync 2aa4d27 part 1 - scripts CLI package base (ADR-0021) ([37d0190](https://github.com/saucisson/petrichor/commit/37d0190260a16330d57bc275beea0c537df83967))
* template sync 2aa4d27 part 2 - pulls domain: pr + update-pr subcommands (ADR-0021) ([1e6990b](https://github.com/saucisson/petrichor/commit/1e6990bd14ba3962858118c24e9ec863168349f7))
* template sync 2aa4d27 part 3 - ci/claims/merge domains (ADR-0021) ([34e7c75](https://github.com/saucisson/petrichor/commit/34e7c756f6b663dbe8fb4193e018962c4891fc75))
* template sync 2aa4d27 part 4a - day/session domains: day-report + sync-daily + agent-review (ADR-0020/0021) ([04e930f](https://github.com/saucisson/petrichor/commit/04e930f64cfb134b0835ffc5a75bc2fc67d300b9))
* template sync 2aa4d27 part 4b - session/review domains: agent-review + review + review-bundle (ADR-0010/0021) ([e1c66ef](https://github.com/saucisson/petrichor/commit/e1c66efd94500763d7cf848bdf7911b4961c3a1d))
* template sync 2aa4d27 part 4c - night domain (ADR-0017/0021) ([b75b5c8](https://github.com/saucisson/petrichor/commit/b75b5c84501ef87f85ed089b720feb15dc2bc5c3))
* template sync d43d8a6 - update-pr driver (ADR-0019) and night trace ([6c632b6](https://github.com/saucisson/petrichor/commit/6c632b652f2e6dc4df96bc98aa2c0f0f16a20c6b))


### Bug Fixes

* address review round 1 on PR [#62](https://github.com/saucisson/petrichor/issues/62) - runtime Protocol, named timeout, HeadSha type, dispatcher test ([487496a](https://github.com/saucisson/petrichor/commit/487496a5e6bbb90f585dfca283f69d0393d443a0))
* ADR-0100 gate in Makefile typecheck - mypy scope gains src (rebased daily) ([325ec45](https://github.com/saucisson/petrichor/commit/325ec45bd2527f8439aa910edd7dc50d7068bcf3))
* dispatcher calls keyword-only runners with arguments= (template bug) ([a913f21](https://github.com/saucisson/petrichor/commit/a913f2198f8786f4d37310ae7d3f3d95a046a995))
* guard fetch_issue JSON decode (review BLOCKING on PR [#63](https://github.com/saucisson/petrichor/issues/63)) ([b426e06](https://github.com/saucisson/petrichor/commit/b426e0646df1006071565fecfd92b34cea85e952))
* keep agent_review.py until part 4b replaces pr_review.py (make review needs it) ([94379aa](https://github.com/saucisson/petrichor/commit/94379aa50264829fdad3b7cca4c92ff6a0f1e5c7))
* keep scripts/test_*.py lint exemption until scripts/tests/ layout lands on main ([b2436ea](https://github.com/saucisson/petrichor/commit/b2436eacfd8e624cc38f44dd1ac49b06041be568))
* mypy strict scope gains src/ now that PR [#59](https://github.com/saucisson/petrichor/issues/59) landed on daily ([c666749](https://github.com/saucisson/petrichor/commit/c666749af6288fdbecf0408b162273e82e17557e))
* name the HTTP timeout constant in update_pr.py (review SUGGESTION) ([462f4a7](https://github.com/saucisson/petrichor/commit/462f4a7e1abd62192bf0644514df48a039a40952))
* night loop sessions print their final answer as text, not a JSON dump ([77648c1](https://github.com/saucisson/petrichor/commit/77648c10f0cfc191d903d34500aecd96e828463e))
* **P02:** move product code to src/petrichor/ per ADR-0014 (ADR-0100 draft) ([3e7ee34](https://github.com/saucisson/petrichor/commit/3e7ee3469bd28af408f7987a375dd4f8acfeac41))
* **P02:** reject arc endpoints referencing another arc (review counterexample) ([9a5efe6](https://github.com/saucisson/petrichor/commit/9a5efe69abbf20595bbd683336d16c2012bfa1b6))
* **P02:** repair local gate on PR [#59](https://github.com/saucisson/petrichor/issues/59) head - lint long lines, rejection tests on full canonical document ([00904e6](https://github.com/saucisson/petrichor/commit/00904e6d0ba0555d9e3d6c68829256cd675a64a8))
* review round 1 on PR [#64](https://github.com/saucisson/petrichor/issues/64) - restore PollStatus/ItemKind closed sets, ItemIdentifier, named check-run constants ([70a68e0](https://github.com/saucisson/petrichor/commit/70a68e05f09da177a0c8b569c166bebec2f96068))
* review round 1 on PR [#65](https://github.com/saucisson/petrichor/issues/65) - scope to the day domain, add test_day.py for sync-daily ([43d15cd](https://github.com/saucisson/petrichor/commit/43d15cd8312aa5e808f35ad972a91528029f6b0b))
* review round 1 on PR [#67](https://github.com/saucisson/petrichor/issues/67) - request_or_fail + JSON guards in night fetches, IssueIdentifier/PullRequestIdentifier, pr_number rename, named constants, cause preserved ([c0d7b0b](https://github.com/saucisson/petrichor/commit/c0d7b0b06cc77362453beee75cc078f268db4cad))
* review round 2 on PR [#62](https://github.com/saucisson/petrichor/issues/62) - tests for _shared/_github, identifier NewTypes, future annotations ([9cbd650](https://github.com/saucisson/petrichor/commit/9cbd65013c1fc379d9bc0ae80d4f9482f4874c9c))
* review round 3 on PR [#62](https://github.com/saucisson/petrichor/issues/62) - check_runs_allow_merge/extract_head_ref tests, RepositorySlug boundary ([f1263f7](https://github.com/saucisson/petrichor/commit/f1263f734651e0231a43e0d674937f9e5fe87c78))
