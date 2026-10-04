/* eslint.config.js: the JavaScript linter, as a floor rather than a style guide.
 *
 * WHY THIS FILE EXISTS (#219): nothing in this repo read a .js file. The display's
 * display.js is 5111 lines and is the whole UI, it has three sibling files beside it,
 * and a ReferenceError for an identifier read out of a scope that does not contain it
 * reached main inside that file, twice, because a helper was closed over its caller's
 * parameter and the second instance was patched without the call site being read.
 * `no-undef` is a lint-level rule, so that class of defect is a lint-level defect and
 * the class fix is a linter.
 *
 * WHAT IS ON, AND WHAT IS DELIBERATELY NOT. Two rules, both at "error":
 *
 *   no-undef        the one that matters. An identifier read out of a scope that does
 *                   not contain it throws the moment that line runs.
 *   no-unused-vars  dead code. Three real instances are on main today and all three are
 *                   reported rather than fixed here, because every one of them is in a
 *                   file this change does not own: an unused `solo` parameter at
 *                   display/static/display.js:2112, a dead `parts` array at
 *                   display/static/display.js:2134, and a counter `i` that is
 *                   incremented and never read at scripts/chartdown_to_atlas.mjs:150.
 *
 * No stylistic rules, no jsdoc, no formatting. This is not Prettier and does not want to
 * be. Prettier's opinions on a 5100-line file that predates the linter would bury a real
 * no-undef under hundreds of complaints about quote style, and a report that is mostly
 * noise is a report people learn to skip, which is the same invisible failure this file
 * exists to stop.
 *
 * RUN IT: `npm install` then `npm run lint`. `--fix` is available by hand and is never
 * run from CI.
 *
 * eslint is pinned to 10.12.0, whose `engines` field wants Node ^20.19 || ^22.13 ||
 * >=24. That is a warning from npm rather than a refusal (`npm config get
 * engine-strict` is false), so it is recorded here as the one statement of this repo's
 * Node floor instead of being copied into a second file that nothing keeps in sync.
 * The workflow's setup-node pins 24 and points at this line.
 */
import globals from 'globals';

/**
 * The two rules, in one object, applied to every JavaScript file in the repo.
 *
 * The two ignore patterns are the whole of this file's leniency, and both are here
 * because of a count rather than a preference. Linting main's five JS files with
 * ESLint's stock options reports 42 findings, every one of them no-unused-vars, and 39
 * of those 42 are this tree saying "deliberately unused" in the two spellings it
 * already uses: an unused catch binding written `catch (e)` or `catch (_)` (38 of them,
 * in four of the five files; the chartdown script has none), and one destructured
 * position it does not want, written `([_, count])`. Not one of those 39 is a defect.
 *
 * Both patterns are anchored with `$` and name exactly the bindings the tree uses. That
 * is not fussiness. `argsIgnorePattern: '^_'` was measured hiding `function f(_data, x)`,
 * `function f(..._rest)` and `function h({ _x, y })` as well, and a parameter misnamed
 * `_data` because someone was unsure is exactly the typo this rule exists to catch;
 * `^_$` still passes this tree with the same three findings. Same reason `err` is not in
 * the catch pattern: all four `catch (err)` bindings in the tree genuinely read `err`, so
 * naming it would be exempting a case that does not exist, which is the same decoration
 * this file argues against further down. Unused *variables* are not exempted at all:
 * `varsIgnorePattern` is unset, so `const _ = 1` is still reported.
 */
const RULES = {
  'no-undef': 'error',
  'no-unused-vars': ['error', { caughtErrorsIgnorePattern: '^(e|_)$', argsIgnorePattern: '^_$' }],
};

export default [
  {
    // node_modules is ESLint's own default ignore, restated so that the absence of
    // anything else in this list is deliberate and visible. Nothing in the tree is
    // allowlisted out of linting: a file added here starts being linted the day it
    // lands, rather than waiting for someone to remember to switch it on.
    ignores: ['node_modules/**'],
  },
  {
    // The floor, armed for every JavaScript file in the repo rather than only for the
    // directories named below. The blocks that follow say what environment a file runs
    // in; this one says which rules apply.
    //
    // `.cjs` is in this list on purpose. ESLint traverses `.cjs` whether or not any
    // config object matches it, so leaving it out does not skip the file: it gives the
    // file NO rules, and a file renamed from .js to .cjs would then be read by no rule
    // at all while still appearing in the report with zero findings. Measured, on a
    // copy of display.js: 486 messages as .js, 486 as .mjs, 0 as .cjs.
    files: ['**/*.js', '**/*.mjs', '**/*.cjs'],
    rules: RULES,
  },
  {
    // display/static/*.js: classic <script> tags served by Flask to a browser.
    // display/templates/index.html loads display.js first and tactics.js second
    // (lines 294 and 295) and both land in the SAME global scope.
    files: ['display/static/**/*.js'],
    languageOptions: {
      // 'latest' rather than a pinned year, and the reason is the same one that decides
      // sourceType below: this repo has no build step and no transpiler, so the only
      // parser that matters is the browser's, and the worst thing a linter can do here
      // is refuse a file the browser would have run. ESLint's default is already
      // `latest`, so this states the choice rather than narrowing it. Nothing in the
      // tree currently uses syntax newer than 2020 (`?.` and `??`); a `#private` class
      // field, or a `/v` regex flag, parses here rather than erroring.
      ecmaVersion: 'latest',
      // "script", not "module": these are classic scripts with top-level const and a
      // leading 'use strict', not ES modules. Calling them modules would let constructs
      // parse here that a browser will not run in a classic script, top-level await most
      // obviously. That direction of error is the one worth making.
      sourceType: 'script',
      globals: globals.browser,
    },
  },
  {
    // The one cross-file global, declared for the two files that actually share a
    // global scope, and not for the whole directory.
    //
    //   _authHeaders   defined at display/static/display.js:2869, read as a bare global
    //                  at tactics.js:675 behind a `typeof` guard. Both are classic
    //                  <script> tags on display/templates/index.html, so it is one
    //                  global scope, and ESLint parses one file at a time and never sees
    //                  the tag. The guard is not defensive noise:
    //                  display/evidence-panel.html:104 loads tactics.js with no
    //                  display.js, and tests/_browser.py drives that page.
    //
    // Scoped to these two files deliberately. atlas.js and mapseditor.js load with
    // neither script (atlas.html and mapeditor.html carry their own <script> tags), so
    // `_authHeaders` does not exist on those pages, and declaring it for them would
    // exempt the exact defect this file exists to find: add a `_authHeaders()` call to
    // mapseditor.js and the linter would stay silent through a guaranteed
    // ReferenceError.
    //
    // Removing this declaration was measured, not argued: without it tactics.js:675
    // reports `'_authHeaders' is not defined` on every run, which is a permanent false
    // positive, and one permanent false positive is enough to teach someone that a
    // no-undef report is usually wrong.
    //
    // `esc` was declared here too, and was removed, because tactics.js:75 already
    // declares its own `const esc = window.esc || <fallback>`. A probe showed
    // tactics.js lints clean without it. Declaring a global nothing reads is decoration.
    //
    // Nothing else is declared, here or anywhere. An undefined name that is not
    // `_authHeaders` is the defect this file exists to find.
    files: ['display/static/display.js', 'display/static/tactics.js'],
    languageOptions: {
      globals: { _authHeaders: 'readonly' },
    },
  },
  {
    // scripts/*.mjs: Node ES modules run from a terminal by whoever is mapping a room,
    // not served to a browser. Module sourceType, so top-level await parses here
    // legitimately instead of arriving as a mystery, and Node globals rather than
    // browser ones.
    files: ['scripts/**/*.mjs'],
    languageOptions: {
      ecmaVersion: 'latest',
      sourceType: 'module',
      globals: globals.node,
    },
  },
];