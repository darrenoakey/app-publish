<!-- >>> greenline >>> -->
## Greenline gate — how merges work here

This repo is gated by **greenline**. Read `docs/greenline.md` and `docs/DOCTRINE.md`
before writing code or tests.

**Invariants (never violate):**
- `main` == what prod runs == green, always.
- The canonical checkout is pristine — never edit it by hand.
- All work happens in worktrees branched from last-green.
- Every merge goes through the serialized gate: full `check` + real `deploy`.

**Your workflow:**
1. `greenline worktree <name>` — get a worktree at `/Volumes/Gumby/worktrees/greenline/app-publish/<name>` on branch `gl/<name>`.
2. Do your work there. Co-design tests + code per docs/DOCTRINE.md (parallel-safe, namespaced, no global-state assertions, OS-assigned ports; never mock other services — make real calls fast with a content-addressed record/replay cache).
3. Commit in your worktree. Then `greenline submit` (from that worktree).
4. The gate squash-merges, runs `./run check`, fast-forwards `main`, runs `./run deploy`, and publishes. It rolls back prod automatically if deploy fails.
5. On success: `greenline done` to remove your worktree + branch.

**Never** commit or push on `main` — hooks hard-lock it (reference-transaction
cannot be bypassed with `--no-verify`; pre-commit/pre-push refuse too). Never edit the
canonical checkout. If the gate reports a conflict, rebase your worktree on
`main` and resubmit. If commits somehow reached `main` outside the
gate (legacy workflow, hotfix), run `greenline adopt` to gate them in place —
greenline never discards commits on `main`.

Diagnose with `greenline status` and `greenline doctor` (`--fix` to reconcile).
<!-- <<< greenline <<< -->

## First-release gotchas learned live (2026-10-18, OL Foxfire)

- Apple auto-creates version `1.0` on a new app record. `plan_create_version` then 409s ("cannot create a new version in the current state"); PATCH the existing editable version's `versionString` instead of creating one.
- Review submission on a fresh app fails with `APP_DATA_USAGES_REQUIRED` unless `appDataUsagesPublishState` is PATCHed `published: true` after the `DATA_NOT_COLLECTED` usage exists; `privacy.py` now does both.
- `ageRatingDeclarations` PATCH must include every 2025 question (`userGeneratedContent`, `advertising`, `gunsOrOtherWeapons`, ...). `GAMES_CARD` under `GAMES` is `primarySubcategoryOne`, not `secondaryCategory`. The free price point comes back as `"0.0"`, so compare numerically.
- Screenshot grouping compares alphanumerics only, so fastlane-style `iPhone16ProMax-01.png` and `iPadPro12.9-01.png` match the device prefixes.
- `promotionalText` is capped at 170 characters. First releases have no prior release, so leave `whatsNew` unset rather than calling the LLM.
- altool refuses a second upload of the same `CFBundleVersion`. Once a build is in ASC, run the post-upload API steps (`ensure_build_selected`, metadata, screenshots, review detail, categories, pricing) and `submit` without re-uploading.
- When the ASC API returns `UNEXPECTED_ERROR` on submit but the version shows `READY_FOR_REVIEW`, PATCH the populated `reviewSubmissions/<id>` with `submitted: true` directly; empty earlier draft submissions cannot be cancelled or deleted via the API.
- `web_driver.login_status` for ASC reports `logged_in: False` on a stale tab; navigate to `appstoreconnect.apple.com/apps` first before trusting it.
- Each ASC step takes 30-120 s of real API time. Run every step detached with a sleep; chaining several in one turn blows the 10-minute turn limit.
- App Store Connect can list an ACTIVE profile whose detail and certificate relationship both return 404. Treat only that structured 404 as a stale candidate; propagate every other API error.
- Archives must pass `-derivedDataPath <project>/local/appstore-derived-data`; the shared Xcode DerivedData tree can disappear during fleet builds, while project-local `local/` storage persists and stays gitignored.
- Provisioning profile entitlements are an authorization allowlist, not the app's requested signature. Resolve each target's own entitlement file and exact values; never copy unused profile capabilities. For Xcode settings, use `-alltargets` with projects, a scheme with workspaces, each target's `SRCROOT`/`PROJECT_DIR`, and validated JSON stdout rather than merged output.
- GroupieCam's legacy universal App ID has `GAME_CENTER` enabled, and the portal rejects capability deletion with 403. The signer must omit unrequested Game Center rather than changing the portal or adding Game Center configuration.
- `set_age_rating()` must not return early merely because the legacy alcohol answer exists: newly required `socialMedia` and `socialMediaAgeRestricted` answers can remain null. PATCH the editable appInfo's `ageRatingDeclaration` ID, not a version ID from the validation error, and preserve existing answers.
- Reader's `Info.plist` hardcodes `CFBundleVersion` to 2, so an xcodebuild `CURRENT_PROJECT_VERSION` override does not propagate. Validate the archive's actual version/build tuple rather than the requested state value.
