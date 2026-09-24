// Lints the panel's plain <script>-tag JavaScript (frontend/js/**, frontend/*.js).
// Vendor bundles are excluded on purpose: they are not code anyone here wrote
// or can fix a rule violation in, and running "recommended" on minified,
// third-party code is all noise.
import js from "@eslint/js";
import globals from "globals";

export default [
  {
    // A bare "ignores" entry (no "files") is a *global* ignore in flat
    // config — these paths are excluded from every config below, not just
    // this one. *.min.js also catches frontend/js/bootstrap.bundle.min.js
    // and frontend/js/chart.min.js, which are not under a vendor/ folder.
    ignores: ["frontend/vendor/**", "frontend/js/vendor/**", "**/*.min.js"],
  },
  {
    files: ["frontend/js/**/*.js", "frontend/*.js"],
    languageOptions: {
      ecmaVersion: "latest",
      // Loaded via plain <script src>, not <script type="module"> — no
      // import/export, so parse them as classic scripts.
      sourceType: "script",
      globals: {
        ...globals.browser,
        // frontend/index.html's vendor <script> tags (frontend/vendor/*),
        // each attaching one global the panel code calls directly.
        jQuery: "readonly",
        $: "readonly",
        bootstrap: "readonly",
        Chart: "readonly",
        persianDate: "readonly",
        QRCode: "readonly",
      },
    },
    rules: {
      ...js.configs.recommended.rules,
    },
  },
  {
    // Service workers: their own global scope (self/caches/clients/
    // importScripts), not a browser window.
    files: ["frontend/sw.js", "frontend/kvn-push-sw.js"],
    languageOptions: {
      globals: {
        ...globals.serviceworker,
      },
    },
  },
];
